/* SPDX-License-Identifier: MIT */
#include "mode.h"
#include "chord.h"
#include <zmk/events/position_state_changed.h>
#include <zmk/events/battery_state_changed.h>
#include <zmk/ble.h>
#include <zmk/hid.h>
#include <zmk/pointing/input_split.h>
#include <zmk/endpoints.h>
extern struct k_work_q hog_work_q;
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/settings/settings.h>
#include <zephyr/input/input.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(lism_mode,CONFIG_ZMK_LOG_LEVEL);
static uint8_t mode,target;
static bool ready,transition,fence_started,ble_fence_started;
static atomic_t ble_fenced,input_fenced;
static struct lism_mode_chord chord;
static uint8_t physical[16],applied[16],applied_source[128];
static int64_t closed_at;
static K_MUTEX_DEFINE(route_lock);
static void mode_work_fn(struct k_work *work);
static void chord_work_fn(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(mode_work,mode_work_fn);
K_WORK_DELAYABLE_DEFINE(chord_work,chord_work_fn);
bool lism_standalone_direct(void) {return ready && mode!=0;}
bool lism_standalone_running(void) {return ready && !transition;}
static bool any(const uint8_t *bits) {for(unsigned i=0;i<16;i++)if(bits[i])return true;return false;}
static void raw(uint32_t position,bool pressed) {
    struct zmk_split_transport_peripheral_event event={
        .type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,
        .data.key_position_event={.position=position,.pressed=pressed}};
    lism_standalone_esb_report(&event);
}
static void emit(void *ctx,uint32_t position,bool pressed,int64_t time) {
    ARG_UNUSED(ctx);if(position>=128 || transition || !ready)return;
    raw(position,pressed); /* State snapshot stays current in both modes. */
    if(mode) {
        applied_source[position]=ZMK_POSITION_STATE_CHANGE_SOURCE_LOCAL;
        if(pressed)applied[position/8]|=BIT(position%8);else applied[position/8]&=~BIT(position%8);
        lism_standalone_replay(&(struct zmk_position_state_changed){
            .source=ZMK_POSITION_STATE_CHANGE_SOURCE_LOCAL,.position=position,.state=pressed,.timestamp=time});
    }
}
static void request(void *ctx,uint8_t selection) {
    ARG_UNUSED(ctx);if(selection>4 || !ready || transition)return;
    if(selection && !lism_standalone_companion_ready()) {
        LOG_WRN("Standalone companion not provisioned; keep dongle mode");return;
    }
    target=selection;k_work_reschedule(&mode_work,K_NO_WAIT);
}
static void schedule_chord(void) {
    int64_t deadline=lism_mode_chord_deadline(&chord);
    if(deadline>=0)k_work_reschedule(&chord_work,K_MSEC(MAX(1,deadline-k_uptime_get())));
    else k_work_cancel_delayable(&chord_work);
}
static void chord_work_fn(struct k_work *work) {
    ARG_UNUSED(work);k_mutex_lock(&route_lock,K_FOREVER);
    lism_mode_chord_tick(&chord,k_uptime_get());schedule_chord();k_mutex_unlock(&route_lock);
}
bool lism_standalone_event(zmk_event_t *event) {
    struct zmk_position_state_changed *pos=as_zmk_position_state_changed(event);
    if(!pos)return false;
    k_mutex_lock(&route_lock,K_FOREVER);
    if(pos->position<128 && pos->source==ZMK_POSITION_STATE_CHANGE_SOURCE_LOCAL) {
        lism_standalone_note_activity();
        if(pos->state)physical[pos->position/8]|=BIT(pos->position%8);
        else physical[pos->position/8]&=~BIT(pos->position%8);
        if(ready){lism_mode_chord_event(&chord,pos->position,pos->state,pos->timestamp);schedule_chord();}
    } else if(pos->position<128 && pos->source==0 && mode && ready && !transition) {
        applied_source[pos->position]=pos->source;
        if(pos->state)applied[pos->position/8]|=BIT(pos->position%8);
        else applied[pos->position/8]&=~BIT(pos->position%8);
        lism_standalone_replay(pos);
    }
    k_mutex_unlock(&route_lock);return true;
}
/* A marker traverses the same input queue as the local trackball. It is sent
 * to a private device, so no mouse listener can interpret it as user input. */
static const struct device fence_device={.name="lism_mode_fence"};
static void input_fence(struct input_event *event,void *arg) {
    ARG_UNUSED(event);ARG_UNUSED(arg);lism_standalone_pointer_reset();
    lism_standalone_listener_reset();atomic_set(&input_fenced,1);
}
INPUT_CALLBACK_DEFINE(&fence_device,input_fence,NULL);
static void ble_fence(struct k_work *work) {
    ARG_UNUSED(work);lism_standalone_hog_purge();atomic_set(&ble_fenced,1);
}
K_WORK_DEFINE(ble_fence_work,ble_fence);
static void disconnect_all(struct bt_conn *conn,void *arg) {
    unsigned *count=arg;struct bt_conn_info info;
    if(!bt_conn_get_info(conn,&info) && info.state!=BT_CONN_STATE_DISCONNECTED) {
        ++*count;bt_conn_disconnect(conn,BT_HCI_ERR_REMOTE_USER_TERM_CONN);
    }
}
static void mode_work_fn(struct k_work *work) {
    ARG_UNUSED(work);k_mutex_lock(&route_lock,K_FOREVER);
    if(!ready || (!transition && target==mode))goto done;
    if(!transition) {
        /* The selector chord must be released. Other held local keys postpone
         * the switch; the old route continues to receive their releases. */
        if(any(physical))goto retry;
        transition=true;closed_at=k_uptime_get();
        for(unsigned p=0;p<128;p++)if(applied[p/8]&BIT(p%8))
            lism_standalone_replay(&(struct zmk_position_state_changed){.source=applied_source[p],
                .position=p,.state=false,.timestamp=k_uptime_get()});
        memset(applied,0,sizeof(applied));
        lism_standalone_esb_enable(false);
        lism_standalone_central_enable(false);
        lism_standalone_host_update();bt_le_adv_stop();
        atomic_clear(&ble_fenced);atomic_clear(&input_fenced);
        ble_fence_started=fence_started=false;
    }
    unsigned count=0;bt_conn_foreach(BT_CONN_TYPE_LE,disconnect_all,&count);
    if(count)goto retry;
    if(!ble_fence_started) {
        if(k_work_submit_to_queue(&hog_work_q,&ble_fence_work)<0)goto retry;
        ble_fence_started=true;
    }
    if(!fence_started && k_uptime_get()-closed_at>=300) {
        if(input_report(&fence_device,INPUT_EV_MSC,0x7ffe,0,true,K_NO_WAIT))goto retry;
        fence_started=true;
    }
    if(!lism_standalone_central_drained() || !atomic_get(&ble_fenced) || !atomic_get(&input_fenced) || any(physical) || k_uptime_get()-closed_at<300)goto retry;
    zmk_hid_keyboard_clear();zmk_hid_consumer_clear();zmk_hid_mouse_clear();
    mode=target;
    /* An explicit mode choice only; pairing keys and profiles are untouched. */
    if(settings_save_one("lism_mode/target",&mode,sizeof(mode)))LOG_WRN("Mode not persisted");
    if(mode){zmk_ble_prof_select(mode-1);zmk_endpoint_set_preferred_transport(ZMK_TRANSPORT_BLE);}
    lism_mode_chord_init(&chord,emit,request,NULL);
    transition=false;
    lism_standalone_esb_enable(!mode);
    lism_standalone_central_enable(mode!=0);
    lism_standalone_host_update();lism_standalone_link_wake();
    LOG_INF("Mode: %s %u",mode?"direct BLE":"dongle ESB",mode);
    goto done;
retry:k_work_reschedule(&mode_work,K_MSEC(30));
done:k_mutex_unlock(&route_lock);
}
static int load(const char *name,size_t len,settings_read_cb read,void *arg) {
    if(strcmp(name,"target"))return -ENOENT;
    if(len!=1 || read(arg,&target,1)!=1 || target>4){target=0;return -EINVAL;}return 0;
}
static void boot_fn(struct k_work *work) {
    ARG_UNUSED(work);k_mutex_lock(&route_lock,K_FOREVER);
    if(target && !lism_standalone_companion_ready())target=0;
    mode=target;
    lism_mode_chord_init(&chord,emit,request,NULL);
    if(mode){zmk_ble_prof_select(mode-1);zmk_endpoint_set_preferred_transport(ZMK_TRANSPORT_BLE);}
    ready=true;
    lism_standalone_esb_enable(!mode);lism_standalone_central_enable(mode!=0);
    lism_standalone_host_update();lism_standalone_link_wake();k_mutex_unlock(&route_lock);
}
K_WORK_DEFINE(boot_work,boot_fn);
static int commit(void) {k_work_submit(&boot_work);return 0;}
SETTINGS_STATIC_HANDLER_DEFINE(lism_mode,"lism_mode",NULL,load,commit,NULL);
static int battery_cb(const zmk_event_t *eh) {
    const struct zmk_battery_state_changed *ev=as_zmk_battery_state_changed(eh);
    if(ev)lism_standalone_esb_report(&(struct zmk_split_transport_peripheral_event){
        .type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT,.data.battery_event.level=ev->state_of_charge});
    return ZMK_EV_EVENT_BUBBLE;
}
ZMK_LISTENER(lism_mode_battery,battery_cb);
ZMK_SUBSCRIPTION(lism_mode_battery,zmk_battery_state_changed);

void lism_standalone_route_input(struct input_event *evt) {
    k_mutex_lock(&route_lock,K_FOREVER);
    if(ready && !transition) {
        if(mode)zmk_input_split_report_peripheral_event(1,evt->type,evt->code,evt->value,evt->sync);
        else lism_standalone_esb_report(&(struct zmk_split_transport_peripheral_event){
            .type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT,
            .data.input_event={.reg=1,.type=evt->type,.code=evt->code,.value=evt->value,.sync=evt->sync}});
    }
    k_mutex_unlock(&route_lock);
}

int lism_standalone_profile_select(uint8_t index) {
    if(index>=4)return -ERANGE;
    k_mutex_lock(&route_lock,K_FOREVER);request(NULL,index+1);k_mutex_unlock(&route_lock);return 0;
}
