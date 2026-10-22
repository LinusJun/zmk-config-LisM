/* SPDX-License-Identifier: MIT
 * The right half owns route transactions. Left sends data directly to the
 * selected owner; a separate private control exchange survives route changes.
 * Raw physical events bypass combos/keymap in dongle mode; never run two maps.
 */
#include "route.h"
#include "auto_policy.h"
#include "mirror.h"
#include "common.h"
#include "link.h"
#include <zephyr/settings/settings.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/crc.h>
#include <zephyr/input/input.h>
#include <zephyr/dt-bindings/input/input-event-codes.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/logging/log.h>
#include <zmk/events/position_state_changed.h>
#include <zmk/behavior.h>
#include <zmk/battery.h>
#include <zmk/ble.h>
#include <zmk/hid.h>
#include <zmk/endpoints.h>
#include <zmk/pointing/input_split.h>
LOG_MODULE_REGISTER(kb_route,LOG_LEVEL_INF);
static struct k_spinlock state_lock;
static struct lism_snapshot physical[2];
static uint8_t current_mode,desired_mode,saved_mode;
static int selected=-1;
static uint32_t epoch=1;
static bool switching,initialized,boot_probe=true;
static int64_t boot_deadline,confirmed_at;
static int64_t quiet_at,left_seen;
static struct kb_left_control left_control;
static bool target_announced;
static bool awaiting_left_applied;
static uint32_t ready_epoch;static uint8_t ready_phase;
void kb_route_left_telemetry(uint8_t*battery,uint8_t*online){k_spinlock_key_t k=k_spin_lock(&state_lock);*battery=physical[0].battery;*online=left_seen && k_uptime_get()-left_seen<LISM_LEASE_MS;k_spin_unlock(&state_lock,k);}

void kb_route_left_alive(void) {k_spinlock_key_t k=k_spin_lock(&state_lock);left_seen=k_uptime_get();k_spin_unlock(&state_lock,k);}
static atomic_t requested=ATOMIC_INIT(-1),fenced,ble_fenced;
static bool fence_sent,ble_fence_sent;
static uint8_t neutral_queued;
static void route_work(struct k_work *w);
K_WORK_DELAYABLE_DEFINE(worker,route_work);
void kb_route_left_control(const struct kb_left_control*p){
 if(!kb_left_packet_valid(&p->physical) || p->phase>KB_LEFT_APPLIED || p->target>1)return;
 k_spinlock_key_t k=k_spin_lock(&state_lock);left_control=*p;left_seen=k_uptime_get();
 physical[0]=(struct lism_snapshot){.keys=kb_left_keys(&p->physical),.buttons=p->physical.buttons,.battery=p->physical.battery};
 bool applied=awaiting_left_applied && !switching && p->physical.authority==kb_private_authority() && p->physical.epoch==epoch && p->target==current_mode && p->phase==KB_LEFT_APPLIED;
 if(applied)awaiting_left_applied=false;
 k_spin_unlock(&state_lock,k);
 if(applied)LOG_INF("Left route APPLIED authority=%u epoch=%u target=%u",p->physical.authority,p->physical.epoch,p->target);
 k_work_reschedule(&worker,K_NO_WAIT);
}

extern struct k_work_q hog_work_q;
struct route_command {uint8_t pipe;uint32_t epoch,peer;struct zmk_split_transport_central_command command;};
K_MSGQ_DEFINE(commands,sizeof(struct route_command),8,4);
static int handle_settings(const char *name,size_t len,settings_read_cb cb,void *arg) {
    if(strcmp(name,"mode") || len!=1)return -ENOENT;
    uint8_t v;int rc=cb(arg,&v,1);if(rc!=1 || v>1)return -EINVAL;
    saved_mode=v; /* boot always prefers a live ESB receiver, not last route */return 0;
}
SETTINGS_STATIC_HANDLER_DEFINE(kb_route,"kb_route",NULL,handle_settings,NULL,NULL);
bool kb_route_ble_enabled(void) {k_spinlock_key_t k=k_spin_lock(&state_lock);bool v=initialized && current_mode==KB_ROUTE_BLE && !switching;k_spin_unlock(&state_lock,k);return v;}
bool kb_route_output_enabled(void) {return kb_route_ble_enabled();}
bool kb_route_accept_pointer(void) {k_spinlock_key_t k=k_spin_lock(&state_lock);bool v=initialized && !switching && current_mode<=1;k_spin_unlock(&state_lock,k);return v;}
void kb_route_snapshot(struct kb_route_view *out) {k_spinlock_key_t k=k_spin_lock(&state_lock);*out=(struct kb_route_view){.mode=(switching || boot_probe)?KB_ROUTE_SWITCHING:current_mode,.profile=zmk_ble_active_profile_index(),.last_mode=current_mode,.epoch=epoch,.seen=k_uptime_get()};k_spin_unlock(&state_lock,k);}
void kb_route_request(uint8_t mode) {if(mode>1)return;atomic_set(&requested,mode);k_work_reschedule(&worker,K_NO_WAIT);}
int kb_route_select(uint8_t profile) {if(profile>=5)return -EINVAL;if(!kb_route_ble_enabled())return -EACCES;atomic_set(&requested,10+profile);k_work_reschedule(&worker,K_NO_WAIT);return 0;}
static void physical_event(unsigned source,const struct zmk_split_transport_peripheral_event *e) {
    struct lism_snapshot *p=&physical[source];
    if(e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT) {
        unsigned pos=e->data.key_position_event.position;if(pos>=LISM_KEYS)return;
        uint64_t mask=UINT64_C(1)<<pos;if(e->data.key_position_event.pressed)p->keys|=mask;else p->keys&=~mask;
    } else if(e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT && e->data.input_event.type==INPUT_EV_KEY && e->data.input_event.code>=INPUT_BTN_LEFT && e->data.input_event.code<INPUT_BTN_LEFT+5) {
        unsigned mask=BIT(e->data.input_event.code-INPUT_BTN_LEFT);if(e->data.input_event.value)p->buttons|=mask;else p->buttons&=~mask;
    } else if(e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT)p->battery=e->data.battery_event.level;
}
int kb_route_relay(unsigned source,const struct zmk_split_transport_peripheral_event *e) {if(source!=1)return -EINVAL;return kb_uplink_send(source,e);}
/* Offline mode selector: right-hand Device hold (base Esc) + H / semicolon.
 * Consume the complete chord and wait for all physical releases before commit.
 * In direct mode the normal keymap is the sole behavior owner. */
static uint64_t offline_consumed;
static int64_t device_down;
static bool dongle_confirmed(void) {k_spinlock_key_t k=k_spin_lock(&state_lock);bool v=confirmed_at && k_uptime_get()-confirmed_at<2000;k_spin_unlock(&state_lock,k);return v;}
bool kb_route_capture(zmk_event_t *event) {
    struct zmk_position_state_changed *p=as_zmk_position_state_changed(event);if(!p || p->position>=LISM_KEYS)return false;
    unsigned source=p->source==ZMK_POSITION_STATE_CHANGE_SOURCE_LOCAL?1:p->source;if(source>=2)return true;
    struct zmk_split_transport_peripheral_event e={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,.data.key_position_event={.position=p->position,.pressed=p->state}};
    k_spinlock_key_t k=k_spin_lock(&state_lock);physical_event(source,&e);bool muted=switching || !initialized || current_mode>1,can_offline=initialized && !switching;uint8_t route=current_mode;k_spin_unlock(&state_lock,k);
    if(route==KB_ROUTE_BLE && !muted)return false;
    if(source==1 && p->position==41)device_down=p->state?k_uptime_get():0;
    uint64_t bit=UINT64_C(1)<<p->position;
    if(source==1 && (offline_consumed&bit)) {if(!p->state)offline_consumed&=~bit;return true;}
    if(source==1 && can_offline && !dongle_confirmed() && p->state && device_down && k_uptime_get()-device_down>=200 && (p->position==15 || p->position==19)) {
        offline_consumed|=bit;kb_route_request(p->position==15?KB_ROUTE_BLE:KB_ROUTE_DONGLE);return true;
    }
    if(muted)return true;
    kb_route_relay(source,&e);return true;
}
/* Native link dispatch calls this before raising position/input events. Keys
 * are captured at event-manager entry; battery and zip input remain raw here. */
bool kb_route_input(unsigned source,const struct zmk_split_transport_peripheral_event *e) {
    if(source>=2)return true;
    k_spinlock_key_t k=k_spin_lock(&state_lock);
    bool direct=initialized && !switching && current_mode==KB_ROUTE_BLE;
    bool relay=source==1 && initialized && !switching && current_mode==KB_ROUTE_DONGLE;
    if(e->type!=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT)physical_event(source,e);
    k_spin_unlock(&state_lock,k);
    if(e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT)return false;
    if(!direct) {if(relay)kb_route_relay(source,e);return true;}
    return false;
}
static const struct device fence_dev={.name="kb_route_fence"};
static void input_fence(struct input_event *e,void *arg) {ARG_UNUSED(e);ARG_UNUSED(arg);kb_route_pointer_reset();kb_route_listener_reset();atomic_set(&fenced,1);}
INPUT_CALLBACK_DEFINE(&fence_dev,input_fence,NULL);
static void hog_fence(struct k_work *w) {ARG_UNUSED(w);atomic_set(&ble_fenced,1);}
K_WORK_DEFINE(hog_fence_work,hog_fence);
static void disconnect_host(struct bt_conn *c,void *arg) {unsigned *count=arg;struct bt_conn_info info;if(!bt_conn_get_info(c,&info) && info.state!=BT_CONN_STATE_DISCONNECTED) {(*count)++;bt_conn_disconnect(c,BT_HCI_ERR_REMOTE_USER_TERM_CONN);}}
static bool all_released(void) {k_spinlock_key_t k=k_spin_lock(&state_lock);bool v=!(physical[0].keys|physical[1].keys|physical[0].buttons|physical[1].buttons);k_spin_unlock(&state_lock,k);return v;}
static void emit_snapshot(unsigned source,bool empty) {
    if(source!=1)return;
    struct zmk_split_transport_peripheral_event e={.type=LISM_SNAPSHOT_TYPE};
    k_spinlock_key_t k=k_spin_lock(&state_lock);struct lism_snapshot s=physical[source];
    /* A right heartbeat cannot renew a stale left press. */
    if(empty || (!source && k_uptime_get()-left_seen>=LISM_LEASE_MS))s.keys=0,s.buttons=0;
    k_spin_unlock(&state_lock,k);memcpy(&e.data,&s,sizeof(s));kb_uplink_send(source,&e);
}
static void publish(void) {
    struct kb_route_view v;kb_route_snapshot(&v);struct zmk_split_transport_peripheral_event e={.type=KB_ROUTE_EVENT};
    struct kb_route_status s={.version=KB_ROUTE_VERSION,.mode=target_announced?desired_mode:v.mode,.profile=v.profile,.epoch=v.epoch,.authority=kb_private_authority(),.phase=((!switching && !boot_probe) || target_announced)?KB_LEFT_COMMIT:KB_LEFT_FREEZE,.target=desired_mode};
    BUILD_ASSERT(sizeof(s)<=sizeof(e.data));memcpy(&e.data,&s,sizeof(s));int rc=kb_uplink_send(1,&e);if(!rc && boot_probe && !boot_deadline)boot_deadline=k_uptime_get()+4500;
}
static void run_commands(void) {
    static struct route_command c;static bool pending;
    for(unsigned n=0;n<4;n++) {
        if(!pending){if(k_msgq_get(&commands,&c,K_NO_WAIT))break;pending=true;}
        if(c.epoch!=epoch || c.peer!=kb_uplink_peer_boot()){pending=false;continue;}
        if(c.pipe==0) {pending=false;continue;} /* left talks directly to its owner */
        pending=false;
        if(c.command.type==ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR) {
            __typeof__(c.command.data.invoke_behavior) b;memcpy(&b,&c.command.data.invoke_behavior,sizeof(b));
            if(!strcmp(b.behavior_dev,"KB_ROUTE")) {if(b.state) {if((b.param1==20 || b.param1==21) && b.param2==epoch){k_spinlock_key_t k=k_spin_lock(&state_lock);uint8_t phase=b.param1-20;uint8_t expected=(boot_probe || (switching && !target_announced))?KB_LEFT_FREEZE:KB_LEFT_COMMIT;if(phase!=expected){k_spin_unlock(&state_lock,k);continue;}confirmed_at=k_uptime_get();ready_epoch=epoch;ready_phase=phase;k_spin_unlock(&state_lock,k);continue;}if(b.param1<2)kb_route_request(b.param1);else if(b.param1>=10 && b.param1<15)kb_route_select(b.param1-10);}continue;}
            /* Preserve source-specific bootloader/reset commands, never execute
             * a stale arbitrary menu/host action on this separate BLE host. */
            if(!strcmp(b.behavior_dev,"bootload") || !strcmp(b.behavior_dev,"sysreset")) {
                struct zmk_behavior_binding binding={.behavior_dev=b.behavior_dev,.param1=b.param1,.param2=b.param2};
                struct zmk_behavior_binding_event ev={.position=b.position,.source=ZMK_POSITION_STATE_CHANGE_SOURCE_LOCAL,.timestamp=k_uptime_get()};zmk_behavior_invoke_binding(&binding,ev,b.state);
            }
        }
    }
}
bool kb_uplink_valid(uint8_t pipe,const uint8_t *frame,size_t len) {
    if(pipe>=2 || len>sizeof(struct esb_command_envelope)+sizeof(struct esb_msg_postfix))return false;
    struct esb_command_envelope env={0};memcpy(&env,frame,len-sizeof(struct esb_msg_postfix));
    if(env.payload.source!=pipe)return false;
    const struct zmk_split_transport_central_command *c=&env.payload.cmd;size_t n;
    switch(c->type) {
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_POLL_EVENTS:n=0;break;
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_PHYSICAL_LAYOUT:n=sizeof(c->data.set_physical_layout);break;
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_HID_INDICATORS:n=sizeof(c->data.set_hid_indicators);break;
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR:n=sizeof(c->data.invoke_behavior);if(c->data.invoke_behavior.state>1 || !memchr(c->data.invoke_behavior.behavior_dev,0,16))return false;break;
    default:return false;
    }
    return env.prefix.payload_size==1+sizeof(c->type)+n;
}
bool kb_uplink_command(uint8_t pipe,const uint8_t *frame,size_t len,uint32_t wire_epoch,uint32_t peer) {
    ARG_UNUSED(len);struct esb_command_envelope env={0};memcpy(&env,frame,len-sizeof(struct esb_msg_postfix));
    struct route_command c={.pipe=pipe,.epoch=wire_epoch,.peer=peer,.command=env.payload.cmd};bool accepted=!k_msgq_put(&commands,&c,K_NO_WAIT);if(accepted)k_work_reschedule(&worker,K_NO_WAIT);return accepted;
}
static void route_work(struct k_work *w) {
    ARG_UNUSED(w);
    /* A private observation has its own lease; it never renews delivered data.
     * Expiry permits a right-hand route change after the left loses power. */
    k_spinlock_key_t lease_key=k_spin_lock(&state_lock);
    if(left_seen && k_uptime_get()-left_seen>=LISM_LEASE_MS){physical[0].keys=0;physical[0].buttons=0;}
    k_spin_unlock(&state_lock,lease_key);
    run_commands();int r=atomic_get(&requested);
    if(r>=0 && (!switching || r<2)) {atomic_cas(&requested,r,-1);boot_probe=false;}
    else r=-1;
    if(r>=0) {if(r<2) {if(switching && desired_mode!=r){epoch++;target_announced=false;ready_epoch=0;quiet_at=k_uptime_get();}desired_mode=r;selected=-1;}else if(r<15 && current_mode==KB_ROUTE_BLE) {desired_mode=KB_ROUTE_BLE;selected=r-10;} }
    if(!initialized)return;
    if(boot_probe) {
        if(!boot_deadline)goto again;
        int decision=kb_boot_decision(k_uptime_get(),boot_deadline,dongle_confirmed());
        if(decision<0)goto again;
        boot_probe=false;desired_mode=decision;
        LOG_INF("Boot ESB probe decision=%u (0=dongle,1=direct BLE)",desired_mode);
    }
    if(!switching && (desired_mode!=current_mode || (selected>=0 && selected!=zmk_ble_active_profile_index()))) {
        /* The triggering Device hold and every other held control release on
         * the OLD owner first. A new key while fencing postpones commit. */
        if(!all_released())goto again;
        k_spinlock_key_t k=k_spin_lock(&state_lock);switching=true;epoch++;quiet_at=k_uptime_get();k_spin_unlock(&state_lock,k);
        fence_sent=ble_fence_sent=false;target_announced=false;ready_epoch=0;atomic_clear(&fenced);atomic_clear(&ble_fenced);
        kb_left_set_route(epoch,KB_LEFT_FREEZE,desired_mode);
        kb_uplink_reset();
        emit_snapshot(0,true);emit_snapshot(1,true);publish();
        neutral_queued=current_mode==KB_ROUTE_BLE?0:7;
        kb_route_ble_refresh();
    }
    if(switching) {
        if(neutral_queued!=7) {
            zmk_hid_keyboard_clear();zmk_hid_consumer_clear();zmk_hid_mouse_clear();
            extern int zmk_hog_send_keyboard_report_to_profile(struct zmk_hid_keyboard_report_body*,uint8_t);
            extern int zmk_hog_send_consumer_report_to_profile(struct zmk_hid_consumer_report_body*,uint8_t);
            extern int zmk_hog_send_mouse_report_to_profile(struct zmk_hid_mouse_report_body*,uint8_t);
            uint8_t p=zmk_ble_active_profile_index();int rc;
            if(!(neutral_queued&1)){rc=zmk_hog_send_keyboard_report_to_profile(&zmk_hid_get_keyboard_report()->body,p);if(!rc || rc==-ENOTCONN)neutral_queued|=1;}
            if(!(neutral_queued&2)){rc=zmk_hog_send_consumer_report_to_profile(&zmk_hid_get_consumer_report()->body,p);if(!rc || rc==-ENOTCONN)neutral_queued|=2;}
            if(!(neutral_queued&4)){rc=zmk_hog_send_mouse_report_to_profile(&zmk_hid_get_mouse_report()->body,p);if(!rc || rc==-ENOTCONN)neutral_queued|=4;}
            if(neutral_queued!=7)goto again;
        }
        if(!fence_sent && !input_report(&fence_dev,INPUT_EV_MSC,0x7ffe,0,true,K_NO_WAIT))fence_sent=true;
        if(!ble_fence_sent && k_work_submit_to_queue(&hog_work_q,&hog_fence_work)>=0)ble_fence_sent=true;
        if(!atomic_get(&fenced) || !atomic_get(&ble_fenced) || !all_released() || !kb_route_queue_idle() || !kb_route_holdtap_idle() || !kb_route_combo_idle() || k_uptime_get()-quiet_at<1700)goto again;
        /* Freeze confirmation is physical state from a private control packet,
         * never a link ACK. A powered-off left does not disable the right. */
        bool left_live=left_seen && k_uptime_get()-left_seen<LISM_LEASE_MS;
        if(left_live && (left_control.physical.authority!=kb_private_authority() || left_control.physical.epoch!=epoch || left_control.phase!=KB_LEFT_FREEZE || kb_left_keys(&left_control.physical) || left_control.physical.buttons))goto again;
        if(!target_announced && current_mode==KB_ROUTE_DONGLE && !(ready_epoch==epoch && ready_phase==KB_LEFT_FREEZE)){
            /* A live old receiver must finish even a long macro. If its RF
             * lease is gone, wait beyond its 2.5s output gate expiry. */
            if(kb_uplink_connected() || k_uptime_get()-quiet_at<4500)goto again;
        }
        if(!target_announced){target_announced=true;publish();goto again;}
        /* Open the receiver's target gate and wait for its application READY
         * before granting the left data route. Offline BLE needs no receiver. */
        if(desired_mode==KB_ROUTE_DONGLE && !(ready_epoch==epoch && ready_phase==KB_LEFT_COMMIT && dongle_confirmed()))goto again;
        unsigned count=0;bt_conn_foreach(BT_CONN_TYPE_LE,disconnect_host,&count);if(count)goto again;
        zmk_hid_keyboard_clear();zmk_hid_consumer_clear();zmk_hid_mouse_clear();
        if(selected>=0)zmk_ble_prof_select(selected);
        k_spinlock_key_t k=k_spin_lock(&state_lock);
        /* Atomic final check: a key arriving during disconnect/profile work
         * stays quarantined until released, never appears on the new host. */
        if(physical[0].keys || physical[1].keys || physical[0].buttons || physical[1].buttons) {k_spin_unlock(&state_lock,k);goto again;}
        selected=-1;current_mode=desired_mode;switching=false;awaiting_left_applied=left_live;k_spin_unlock(&state_lock,k);
        kb_left_set_route(epoch,KB_LEFT_COMMIT,current_mode);settings_save_one("kb_route/mode",&current_mode,1);kb_route_ble_refresh();publish();
        LOG_INF("Keyboard route committed mode=%u profile=%u epoch=%u",current_mode,zmk_ble_active_profile_index(),epoch);
    }
again:
    if(switching)kb_left_set_route(epoch,KB_LEFT_FREEZE,desired_mode);
    else if(current_mode<=1){
        bool prepared=left_control.physical.authority==kb_private_authority() && left_control.physical.epoch==epoch && left_control.target==current_mode;
        kb_left_set_route(epoch,prepared?KB_LEFT_COMMIT:KB_LEFT_FREEZE,current_mode);
    }
    k_work_reschedule(&worker,K_MSEC(20));
}
static void periodic(struct k_work *w) {
    ARG_UNUSED(w);if(initialized) {
        k_spinlock_key_t k=k_spin_lock(&state_lock);physical[1].battery=zmk_battery_state_of_charge();
        bool left_fresh=k_uptime_get()-left_seen<LISM_LEASE_MS;k_spin_unlock(&state_lock,k);
        publish();kb_mirror_emit();if(!switching && current_mode<=1){(void)left_fresh;emit_snapshot(1,current_mode!=KB_ROUTE_DONGLE);}
    }
    k_work_reschedule(k_work_delayable_from_work(w),K_MSEC(200));
}
K_WORK_DELAYABLE_DEFINE(heartbeat,periodic);
static int init(void) {current_mode=KB_ROUTE_UNKNOWN;desired_mode=KB_ROUTE_DONGLE;boot_deadline=0;initialized=true;k_work_reschedule(&worker,K_MSEC(100));k_work_reschedule(&heartbeat,K_MSEC(200));kb_route_ble_refresh();return 0;}
SYS_INIT(init,APPLICATION,99);

bool kb_route_left_allowed(const struct kb_left_packet*p){
 k_spinlock_key_t k=k_spin_lock(&state_lock);bool allowed=initialized && !switching && current_mode==KB_ROUTE_BLE && p->authority==kb_private_authority() && p->epoch==epoch;k_spin_unlock(&state_lock,k);return allowed;
}

uint32_t kb_route_command_epoch(void){k_spinlock_key_t k=k_spin_lock(&state_lock);uint32_t e=epoch;k_spin_unlock(&state_lock,k);return e;}
