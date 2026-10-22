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
#include <zephyr/random/random.h>
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
static uint32_t ready_epoch,ready_boot;static uint8_t ready_phase,ready_station=KB_STATION_NONE;
enum station_stage {ST_STABLE,ST_DRAIN,ST_HUNT,ST_PREPARE,ST_READY,ST_LEFT};
static enum station_stage stage=ST_STABLE;
static uint8_t station=KB_STATION_NONE,preferred_station,desired_station=KB_STATION_NONE;
static uint8_t gate_station=KB_STATION_NONE,drain_station=KB_STATION_NONE;
static uint32_t receiver_boot,gate_boot,drain_boot;
static bool selection_pending,settings_pending;
static uint8_t hunt_station,offer_station=KB_STATION_NONE;
static uint32_t probe_cookie,offer_boot;
static int64_t hunt_started,station_started,next_probe,target_started;
static bool left_disabled=true;
static int64_t hunt_deadline;
static int64_t blocked_until[2];
static bool monitor_probe;
static void finish_commit_locked(void){
    current_mode=desired_mode;station=desired_mode==KB_ROUTE_DONGLE?desired_station:kb_uplink_station_current();
    switching=boot_probe=false;stage=ST_STABLE;selection_pending=false;awaiting_left_applied=false;settings_pending=true;
    if(current_mode==KB_ROUTE_DONGLE)preferred_station=station;
}
void kb_route_left_telemetry(uint8_t*battery,uint8_t*online){k_spinlock_key_t k=k_spin_lock(&state_lock);*battery=physical[0].battery;*online=left_seen && k_uptime_get()-left_seen<LISM_LEASE_MS;k_spin_unlock(&state_lock,k);}

void kb_route_left_alive(void) {k_spinlock_key_t k=k_spin_lock(&state_lock);left_seen=k_uptime_get();k_spin_unlock(&state_lock,k);}
static atomic_t requested=ATOMIC_INIT(-1),fenced,ble_fenced;
static bool fence_sent,ble_fence_sent;
static uint8_t neutral_queued;
static void route_work(struct k_work *w);
K_WORK_DELAYABLE_DEFINE(worker,route_work);
void kb_route_left_control(const struct kb_left_control*p){
 if(!kb_left_packet_valid(&p->physical) || p->phase>KB_LEFT_APPLIED || p->target>1 || p->prepared>1)return;
 k_spinlock_key_t k=k_spin_lock(&state_lock);left_control=*p;left_seen=k_uptime_get();
 physical[0]=(struct lism_snapshot){.keys=kb_left_keys(&p->physical),.buttons=p->physical.buttons,.battery=p->physical.battery};
 bool applied=awaiting_left_applied && p->physical.authority==kb_private_authority() && p->physical.epoch==epoch && p->target==desired_mode && p->physical.station==(desired_mode==KB_ROUTE_DONGLE?desired_station:KB_STATION_NONE) && p->physical.receiver_boot==(desired_mode==KB_ROUTE_DONGLE?receiver_boot:0) && p->phase==KB_LEFT_APPLIED;
 if(applied){awaiting_left_applied=false;if(stage==ST_LEFT && !physical[1].keys && !physical[1].buttons)finish_commit_locked();}
 k_spin_unlock(&state_lock,k);
 if(applied)LOG_INF("Left route APPLIED authority=%u epoch=%u target=%u",p->physical.authority,p->physical.epoch,p->target);
 k_work_reschedule(&worker,K_NO_WAIT);
}

extern struct k_work_q hog_work_q;
struct route_command {uint8_t pipe,station;uint32_t epoch,peer;struct zmk_split_transport_central_command command;};
K_MSGQ_DEFINE(commands,sizeof(struct route_command),8,4);
static int handle_settings(const char *name,size_t len,settings_read_cb cb,void *arg) {
    if(!strcmp(name,"station")){uint8_t s;if(len!=1 || cb(arg,&s,1)!=1 || s>=2)return -EINVAL;preferred_station=s;return 0;}
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
int kb_route_select_dongle(uint8_t slot){if(slot>=2)return -EINVAL;atomic_set(&requested,30+slot);k_work_reschedule(&worker,K_NO_WAIT);return 0;}
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
    if(source==1 && can_offline && !dongle_confirmed() && p->state && device_down && k_uptime_get()-device_down>=200 && (p->position==15 || p->position==19 || p->position==39 || p->position==40)) {
        offline_consumed|=bit;if(p->position>=39)kb_route_select_dongle(p->position-39);else kb_route_request(p->position==15?KB_ROUTE_BLE:KB_ROUTE_DONGLE);return true;
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
    uint8_t dest=kb_uplink_station_current(),mode,phase,target,owner;
    uint32_t boot;
    if(stage==ST_HUNT)return;
    if(stage==ST_DRAIN){
        if(drain_station>=2 || dest!=drain_station)return;
        mode=KB_ROUTE_SWITCHING;phase=KB_LEFT_FREEZE;target=KB_ROUTE_BLE;owner=KB_STATION_NONE;boot=drain_boot;
    }else if(stage==ST_PREPARE){
        mode=KB_ROUTE_SWITCHING;phase=KB_LEFT_FREEZE;target=KB_ROUTE_DONGLE;owner=desired_station;boot=receiver_boot;
    }else if(stage==ST_READY || stage==ST_LEFT){
        mode=desired_mode;phase=KB_LEFT_COMMIT;target=desired_mode;owner=target==KB_ROUTE_DONGLE?desired_station:KB_STATION_NONE;boot=receiver_boot;
    }else {
        mode=current_mode;phase=KB_LEFT_COMMIT;target=current_mode;owner=target==KB_ROUTE_DONGLE?station:KB_STATION_NONE;boot=receiver_boot;
    }
    if(!boot || mode>KB_ROUTE_SWITCHING)return;
    struct zmk_split_transport_peripheral_event event={.type=KB_ROUTE_EVENT};
    struct kb_route_status state={.version=KB_ROUTE_VERSION,.mode=mode,.profile=zmk_ble_active_profile_index(),.epoch=epoch,.authority=kb_private_authority(),.phase=phase,.target=target,.station=dest,.owner_station=owner,.receiver_boot=boot};
    memcpy(&event.data,&state,sizeof(state));kb_uplink_send(1,&event);
}

static void run_commands(void) {
    static struct route_command c;static bool pending;
    for(unsigned n=0;n<4;n++){
        if(!pending){if(k_msgq_get(&commands,&c,K_NO_WAIT))break;pending=true;}
        pending=false;
        if(c.pipe!=1 || c.epoch!=epoch || c.station!=kb_uplink_station_current() || c.peer!=kb_uplink_peer_boot())continue;
        if(c.command.type!=ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR)continue;
        __typeof__(c.command.data.invoke_behavior) b;memcpy(&b,&c.command.data.invoke_behavior,sizeof(b));
        if(!strcmp(b.behavior_dev,"KB_ROUTE")){
            if(!b.state)continue;
            if(b.param1==20 || b.param1==21){
                unsigned expected=(stage==ST_DRAIN || stage==ST_PREPARE)?KB_LEFT_FREEZE:KB_LEFT_COMMIT;
                uint32_t expected_boot=stage==ST_DRAIN?drain_boot:receiver_boot;
                if(b.position!=c.station || b.param2!=epoch || b.param1-20!=expected || !expected_boot || c.peer!=expected_boot)continue;
                k_spinlock_key_t k=k_spin_lock(&state_lock);confirmed_at=k_uptime_get();ready_epoch=epoch;ready_phase=expected;ready_station=c.station;ready_boot=c.peer;k_spin_unlock(&state_lock,k);continue;
            }
            if(c.peer!=receiver_boot || stage!=ST_STABLE)continue;
            if(b.param1<2)kb_route_request(b.param1);
            else if(b.param1>=10 && b.param1<15)kb_route_select(b.param1-10);
            else if(b.param1>=30 && b.param1<32)kb_route_select_dongle(b.param1-30);
        }else if(stage==ST_STABLE && c.peer==receiver_boot && (!strcmp(b.behavior_dev,"bootload") || !strcmp(b.behavior_dev,"sysreset"))){
            struct zmk_behavior_binding binding={.behavior_dev=b.behavior_dev,.param1=b.param1,.param2=b.param2};
            struct zmk_behavior_binding_event event={.position=b.position,.source=ZMK_POSITION_STATE_CHANGE_SOURCE_LOCAL,.timestamp=k_uptime_get()};
            zmk_behavior_invoke_binding(&binding,event,b.state);
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
    struct esb_command_envelope env={0};memcpy(&env,frame,len-sizeof(struct esb_msg_postfix));
    if(kb_uplink_is_probe(frame,len)){
        __typeof__(env.payload.cmd.data.invoke_behavior) b;memcpy(&b,&env.payload.cmd.data.invoke_behavior,sizeof(b));
        return pipe==1 && b.state==1 && b.param2==peer && kb_route_probe_offer(b.position,b.param1,peer);
    }
    struct route_command c={.pipe=pipe,.station=kb_uplink_station_current(),.epoch=wire_epoch,.peer=peer,.command=env.payload.cmd};
    bool accepted=!k_msgq_put(&commands,&c,K_NO_WAIT);if(accepted)k_work_reschedule(&worker,K_NO_WAIT);return accepted;
}


bool kb_uplink_is_probe(const uint8_t *frame,size_t len){
    if(len<sizeof(struct esb_msg_prefix)+1+sizeof(enum zmk_split_transport_central_command_type)+sizeof(((struct zmk_split_transport_central_command*)0)->data.invoke_behavior)+sizeof(struct esb_msg_postfix) || len>sizeof(struct esb_command_envelope)+sizeof(struct esb_msg_postfix))return false;
    struct esb_command_envelope e={0};memcpy(&e,frame,len-sizeof(struct esb_msg_postfix));
    return e.payload.cmd.type==ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR && !strcmp(e.payload.cmd.data.invoke_behavior.behavior_dev,"KB_PROBE3");
}
bool kb_route_probe_offer(uint8_t slot,uint32_t cookie,uint32_t boot){
    k_spinlock_key_t key=k_spin_lock(&state_lock);
    bool valid=boot && cookie && cookie==probe_cookie && slot==hunt_station && slot==kb_uplink_station_current() && (stage==ST_HUNT || stage==ST_DRAIN || (stage==ST_STABLE && current_mode==KB_ROUTE_BLE && monitor_probe));
    if(valid){offer_station=slot;offer_boot=boot;}
    k_spin_unlock(&state_lock,key);if(valid)k_work_reschedule(&worker,K_NO_WAIT);return valid;
}
void kb_route_probe_transmitted(uint8_t slot,uint32_t cookie){
    k_spinlock_key_t key=k_spin_lock(&state_lock);
    if(slot==hunt_station && cookie==probe_cookie && cookie && !station_started){station_started=k_uptime_get();if(stage==ST_HUNT && !hunt_started)hunt_started=station_started;}
    k_spin_unlock(&state_lock,key);
}
static void probe_window(uint8_t slot,int64_t now){
    ARG_UNUSED(now);
    uint32_t cookie;do{cookie=sys_rand32_get();}while(!cookie);
    k_spinlock_key_t key=k_spin_lock(&state_lock);hunt_station=slot;probe_cookie=cookie;offer_station=KB_STATION_NONE;offer_boot=0;station_started=0;next_probe=0;k_spin_unlock(&state_lock,key);
    kb_uplink_station_request(slot);
}
static void send_probe(int64_t now){
    if(kb_uplink_station_current()!=hunt_station || now<next_probe)return;
    struct kb_discovery probe={.cookie=probe_cookie,.authority=kb_private_authority(),.version=KB_PROTOCOL_VERSION,.station=hunt_station};
    if(!probe.authority)return;
    struct zmk_split_transport_peripheral_event event={.type=KB_PROBE_EVENT};memcpy(&event.data,&probe,sizeof(probe));
    if(!kb_uplink_send(1,&event))next_probe=now+150;
}
static bool left_prepared(uint8_t mode,uint8_t slot,uint32_t boot,uint8_t phase){
    k_spinlock_key_t key=k_spin_lock(&state_lock);
    bool live=left_seen && k_uptime_get()-left_seen<KB_LEFT_CONTROL_LEASE_MS;
    bool ready=live && (left_control.physical.authority==kb_private_authority() && left_control.physical.epoch==epoch && left_control.target==mode && left_control.physical.station==slot && left_control.physical.receiver_boot==boot && left_control.prepared && left_control.phase==phase && (phase==KB_LEFT_APPLIED || (!kb_left_keys(&left_control.physical) && !left_control.physical.buttons)));
    k_spin_unlock(&state_lock,key);return ready;
}
static bool neutral_ble(void){
    if(neutral_queued==7)return true;
    zmk_hid_keyboard_clear();zmk_hid_consumer_clear();zmk_hid_mouse_clear();
    extern int zmk_hog_send_keyboard_report_to_profile(struct zmk_hid_keyboard_report_body*,uint8_t);
    extern int zmk_hog_send_consumer_report_to_profile(struct zmk_hid_consumer_report_body*,uint8_t);
    extern int zmk_hog_send_mouse_report_to_profile(struct zmk_hid_mouse_report_body*,uint8_t);
    uint8_t p=zmk_ble_active_profile_index();int rc;
    if(!(neutral_queued&1)){rc=zmk_hog_send_keyboard_report_to_profile(&zmk_hid_get_keyboard_report()->body,p);if(!rc || rc==-ENOTCONN)neutral_queued|=1;}
    if(!(neutral_queued&2)){rc=zmk_hog_send_consumer_report_to_profile(&zmk_hid_get_consumer_report()->body,p);if(!rc || rc==-ENOTCONN)neutral_queued|=2;}
    if(!(neutral_queued&4)){rc=zmk_hog_send_mouse_report_to_profile(&zmk_hid_get_mouse_report()->body,p);if(!rc || rc==-ENOTCONN)neutral_queued|=4;}
    return neutral_queued==7;
}
static void begin_transaction(int64_t now){
    k_spinlock_key_t key=k_spin_lock(&state_lock);
    switching=true;boot_probe=false;epoch++;if(!epoch)epoch++;
    stage=ST_DRAIN;quiet_at=now;drain_station=gate_station;drain_boot=gate_boot;ready_epoch=0;confirmed_at=0;
    awaiting_left_applied=false;monitor_probe=false;probe_cookie=0;left_disabled=true;
    k_spin_unlock(&state_lock,key);
    fence_sent=ble_fence_sent=false;atomic_clear(&fenced);atomic_clear(&ble_fenced);
    neutral_queued=current_mode==KB_ROUTE_BLE?0:7;
    kb_uplink_reset();kb_left_set_route(epoch,KB_LEFT_FREEZE,KB_ROUTE_BLE,KB_STATION_NONE,0);
    if(drain_station<KB_STATION_COUNT)probe_window(drain_station,now);
    publish();kb_route_ble_refresh();
}
static bool application_fenced(int64_t now){
    if(!neutral_ble())return false;
    if(!fence_sent && !input_report(&fence_dev,INPUT_EV_MSC,0x7ffe,0,true,K_NO_WAIT))fence_sent=true;
    if(!ble_fence_sent && k_work_submit_to_queue(&hog_work_q,&hog_fence_work)>=0)ble_fence_sent=true;
    if(!atomic_get(&fenced) || !atomic_get(&ble_fenced) || !all_released() || !kb_route_queue_idle() || !kb_route_holdtap_idle() || !kb_route_combo_idle())return false;
    if(now-quiet_at<(current_mode==KB_ROUTE_UNKNOWN?4500:1700))return false;
    if(left_prepared(KB_ROUTE_BLE,KB_STATION_NONE,0,KB_LEFT_FREEZE)){left_disabled=false;return true;}
    /* No COMMIT is sent during DRAIN. A silent left self-freezes after
     * 1500 ms; include 700 ms for an in-flight radio/control exchange.
     * Continue right-only, keeping the left frozen until a real PREPARE. */
    return now-quiet_at>=2200;
}
static void sync_left(void);
static bool prepare_local_target(void){
    unsigned count=0;bt_conn_foreach(BT_CONN_TYPE_LE,disconnect_host,&count);if(count)return false;
    zmk_hid_keyboard_clear();zmk_hid_consumer_clear();zmk_hid_mouse_clear();
    if(selected>=0)zmk_ble_prof_select(selected);
    if(!all_released())return false;
    selected=-1;
    k_spinlock_key_t key=k_spin_lock(&state_lock);
    stage=ST_LEFT;target_started=k_uptime_get();awaiting_left_applied=!left_disabled;
    if(!awaiting_left_applied)finish_commit_locked();
    k_spin_unlock(&state_lock,key);
    sync_left();
    publish();return true;
}
static void sync_left(void){
    if(stage==ST_DRAIN || stage==ST_HUNT)kb_left_set_route(epoch,KB_LEFT_FREEZE,KB_ROUTE_BLE,KB_STATION_NONE,0);
    else{
        uint8_t mode=stage==ST_STABLE?current_mode:desired_mode;
        uint8_t slot=mode==KB_ROUTE_DONGLE?(stage==ST_STABLE?station:desired_station):KB_STATION_NONE;
        uint32_t boot=mode==KB_ROUTE_DONGLE?receiver_boot:0;
        if(mode>1 || !kb_station_tuple_valid(mode,slot,boot))return;
        k_spinlock_key_t key=k_spin_lock(&state_lock);
        bool live=left_seen && k_uptime_get()-left_seen<KB_LEFT_CONTROL_LEASE_MS;
        bool prepared=live && left_control.physical.authority==kb_private_authority() && left_control.physical.epoch==epoch && left_control.target==mode && left_control.physical.station==slot && left_control.physical.receiver_boot==boot && left_control.prepared;
        if(stage==ST_STABLE && left_disabled && prepared && left_control.phase==KB_LEFT_FREEZE && !kb_left_keys(&left_control.physical) && !left_control.physical.buttons)left_disabled=false;
        uint8_t phase=(stage==ST_PREPARE || stage==ST_READY || left_disabled || !prepared)?KB_LEFT_FREEZE:KB_LEFT_COMMIT;
        k_spin_unlock(&state_lock,key);
        kb_left_set_route(epoch,phase,mode,slot,boot);
    }
}
static void route_work(struct k_work *w){
    ARG_UNUSED(w);int64_t now=k_uptime_get();
    k_spinlock_key_t key=k_spin_lock(&state_lock);
    if(left_seen && now-left_seen>=LISM_LEASE_MS){physical[0].keys=0;physical[0].buttons=0;}
    k_spin_unlock(&state_lock,key);
    run_commands();int request=atomic_set(&requested,-1);
    if(request>=0){
        /* Keep the in-flight tuple immutable while a physical key is held.
         * A newer request may replace this pending intent, never its epoch. */
        if(switching && !all_released()){atomic_cas(&requested,-1,request);goto again;}
        if(request<2){desired_mode=request;selected=-1;desired_station=preferred_station;selection_pending=!(stage==ST_STABLE && current_mode==request && (request==KB_ROUTE_BLE || dongle_confirmed()));}
        else if(request>=30 && request<32){desired_mode=KB_ROUTE_DONGLE;desired_station=request-30;selected=-1;selection_pending=!(stage==ST_STABLE && current_mode==KB_ROUTE_DONGLE && station==desired_station && dongle_confirmed());}
        else if(request>=10 && request<15 && current_mode==KB_ROUTE_BLE){desired_mode=KB_ROUTE_BLE;selected=request-10;selection_pending=selected!=zmk_ble_active_profile_index();}
        if(selection_pending && switching && all_released())begin_transaction(now);
    }
    if(!initialized)goto again;
    if(stage==ST_STABLE && !selection_pending && current_mode==KB_ROUTE_DONGLE && !dongle_confirmed()){desired_mode=KB_ROUTE_DONGLE;desired_station=station;selection_pending=true;}
    if(stage==ST_STABLE && selection_pending){if(!all_released())goto again;begin_transaction(now);}
    if(stage==ST_DRAIN){
        /* Presence probes never renew a receiver input lease. A fresh nonce
         * answered by a different boot proves the old receiver session died,
         * even when ESB hardware still acknowledges old-boot FREEZE frames. */
        if(drain_station<KB_STATION_COUNT)send_probe(now);
        bool rebooted=offer_station==drain_station && offer_boot && offer_boot!=drain_boot && hunt_station==drain_station;
        if(!application_fenced(now))goto again;
        if(drain_station<2 && !rebooted && !(ready_epoch==epoch && ready_phase==KB_LEFT_FREEZE && ready_station==drain_station && ready_boot==drain_boot)){
            if(kb_uplink_connected() || now-quiet_at<4500)goto again;
        }
        gate_station=KB_STATION_NONE;gate_boot=0;
        if(desired_mode==KB_ROUTE_BLE){prepare_local_target();goto again;}
        stage=ST_HUNT;hunt_started=0;hunt_deadline=now+9000;uint8_t first=desired_station<2?desired_station:preferred_station;
        if(blocked_until[first]>now && blocked_until[first^1]<=now)first^=1;
        probe_window(first,now);goto again;
    }
    if(stage==ST_HUNT){
        if(offer_station==hunt_station && offer_boot && kb_uplink_station_current()==hunt_station){
            desired_station=offer_station;receiver_boot=offer_boot;
            key=k_spin_lock(&state_lock);epoch++;if(!epoch)epoch++;ready_epoch=0;confirmed_at=0;probe_cookie=0;stage=ST_PREPARE;target_started=now;k_spin_unlock(&state_lock,key);
            kb_uplink_reset();sync_left();publish();goto again;
        }
        if((hunt_started && now-hunt_started>=4500) || now>=hunt_deadline){desired_mode=KB_ROUTE_BLE;receiver_boot=0;desired_station=KB_STATION_NONE;prepare_local_target();goto again;}
        if(station_started && now-station_started>=900){uint8_t next=hunt_station^1;if(blocked_until[next]>now && blocked_until[hunt_station]<=now)next=hunt_station;probe_window(next,now);}
        send_probe(now);goto again;
    }
    if(stage==ST_PREPARE){
        if(now-target_started>=4500 && !dongle_confirmed()){blocked_until[desired_station]=now+10000;begin_transaction(now);goto again;}
        bool prepared=left_prepared(KB_ROUTE_DONGLE,desired_station,receiver_boot,KB_LEFT_FREEZE);
        if(prepared)left_disabled=false;
        else if(now-target_started>=2200)left_disabled=true;
        if(!all_released() || (!prepared && now-target_started<2200) || ready_epoch!=epoch || ready_phase!=KB_LEFT_FREEZE || ready_station!=desired_station || ready_boot!=receiver_boot || !dongle_confirmed())goto again;
        stage=ST_READY;target_started=now;gate_station=desired_station;gate_boot=receiver_boot;ready_epoch=0;publish();goto again;
    }
    if(stage==ST_READY){
        if(now-target_started>=4500 && !dongle_confirmed()){blocked_until[desired_station]=now+10000;begin_transaction(now);goto again;}
        if(ready_epoch==epoch && ready_phase==KB_LEFT_COMMIT && ready_station==desired_station && ready_boot==receiver_boot && dongle_confirmed())prepare_local_target();
        goto again;
    }
    if(stage==ST_LEFT){
        if(awaiting_left_applied && now-target_started>=2200){begin_transaction(now);goto again;}
        if(desired_mode==KB_ROUTE_DONGLE && !dongle_confirmed() && now-target_started>=4500){blocked_until[desired_station]=now+10000;begin_transaction(now);goto again;}
        bool left_ready=!awaiting_left_applied || left_prepared(desired_mode,desired_mode==KB_ROUTE_DONGLE?desired_station:KB_STATION_NONE,desired_mode==KB_ROUTE_DONGLE?receiver_boot:0,KB_LEFT_APPLIED);
        key=k_spin_lock(&state_lock);if(left_ready && !physical[1].keys && !physical[1].buttons)finish_commit_locked();k_spin_unlock(&state_lock,key);
    }
    if(stage==ST_STABLE && current_mode==KB_ROUTE_BLE){
        /* Display-only discovery cannot change the keyboard output route. */
        if(!dongle_confirmed()){
            if(!monitor_probe){monitor_probe=true;probe_window(preferred_station,now);}
            if(offer_station==hunt_station && offer_boot){receiver_boot=offer_boot;station=offer_station;monitor_probe=false;probe_cookie=0;ready_epoch=0;publish();}
            else{if(station_started && now-station_started>=1200)probe_window(hunt_station^1,now);send_probe(now);}
        }else monitor_probe=false;
    }
again:
    sync_left();
    if(settings_pending && stage==ST_STABLE){settings_pending=false;settings_save_one("kb_route/mode",&current_mode,1);if(current_mode==KB_ROUTE_DONGLE)settings_save_one("kb_route/station",&preferred_station,1);kb_route_ble_refresh();publish();LOG_INF("Route committed mode=%u station=%u epoch=%u",current_mode,station,epoch);}
    k_work_reschedule(&worker,K_MSEC(20));
}
static void periodic(struct k_work *w) {
    ARG_UNUSED(w);if(initialized) {
        k_spinlock_key_t k=k_spin_lock(&state_lock);physical[1].battery=zmk_battery_state_of_charge();
        bool left_fresh=k_uptime_get()-left_seen<LISM_LEASE_MS;k_spin_unlock(&state_lock,k);
        publish();kb_mirror_emit();if(!switching && current_mode<=1 && receiver_boot && !monitor_probe){(void)left_fresh;emit_snapshot(1,current_mode!=KB_ROUTE_DONGLE);}
    }
    k_work_reschedule(k_work_delayable_from_work(w),K_MSEC(200));
}
K_WORK_DELAYABLE_DEFINE(heartbeat,periodic);
static int init(void){current_mode=KB_ROUTE_UNKNOWN;desired_mode=KB_ROUTE_DONGLE;desired_station=preferred_station;boot_deadline=0;initialized=true;selection_pending=true;k_work_reschedule(&worker,K_MSEC(100));k_work_reschedule(&heartbeat,K_MSEC(200));kb_route_ble_refresh();return 0;}

SYS_INIT(init,APPLICATION,99);

bool kb_route_left_allowed(const struct kb_left_packet*p){
 k_spinlock_key_t k=k_spin_lock(&state_lock);bool allowed=initialized && !switching && current_mode==KB_ROUTE_BLE && !left_disabled && left_control.phase==KB_LEFT_APPLIED && left_control.physical.epoch==epoch && p->authority==kb_private_authority() && p->epoch==epoch && p->station==KB_STATION_NONE && !p->receiver_boot;k_spin_unlock(&state_lock,k);return allowed;
}

uint32_t kb_route_command_epoch(void){k_spinlock_key_t k=k_spin_lock(&state_lock);uint32_t e=epoch;k_spin_unlock(&state_lock,k);return e;}

bool kb_route_mirror_enabled(void){return kb_route_ble_enabled() && !monitor_probe && receiver_boot && dongle_confirmed();}
