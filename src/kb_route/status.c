/* SPDX-License-Identifier: MIT */
#include "route.h"
#include <zephyr/sys/atomic.h>
#include <zmk/split/central.h>
#include <zmk/behavior.h>
static struct kb_route_view view={.mode=KB_ROUTE_UNKNOWN};
static struct k_spinlock lock;static uint32_t authority,wire_boot;
static atomic_t request=ATOMIC_INIT(-1),echo_epoch,echo_phase;
static uint32_t echo_generation=1,established_echo_generation,echo_receiver_boot;
static bool echo_dongle_commit;
static void command_work(struct k_work *w);
K_WORK_DELAYABLE_DEFINE(send_command,command_work);
static void command_work(struct k_work *w) {
    ARG_UNUSED(w);int n=atomic_get(&request);uint32_t echo=atomic_get(&echo_epoch);if(n<0 && !echo)return;
    uint32_t phase=atomic_get(&echo_phase);
    k_spinlock_key_t key=k_spin_lock(&lock);uint32_t generation=echo_generation;
    bool session=echo==view.epoch && echo_receiver_boot==kb_receiver_boot() && wire_boot==kb_receiver_peer_boot(1) && k_uptime_get()-view.seen<2500;
    bool liveness=n<0 && session && echo_dongle_commit && view.mode==KB_ROUTE_DONGLE && phase==KB_LEFT_COMMIT && established_echo_generation==generation;
    if(n<0 && !session){if(!++echo_generation)++echo_generation;established_echo_generation=0;}
    k_spin_unlock(&lock,key);
    /* First COMMIT/any handoff still fences held inputs. Once that exact
     * session's COMMIT was queued at idle, heartbeat ACKs do not transfer
     * ownership and must continue while a normal key/macro remains active. */
    if((n<0 && !session) || (!liveness && (!kb_route_queue_idle() || !kb_route_holdtap_idle() || !kb_route_combo_idle()))){k_work_reschedule(&send_command,K_MSEC(20));return;}
    struct zmk_behavior_binding b={.behavior_dev="KB_ROUTE",.param1=n<0?20+phase:n,.param2=n<0?echo:0};
    struct zmk_behavior_binding_event e={.source=1,.position=CONFIG_LISM_DONGLE_STATION,.timestamp=k_uptime_get()};
    int rc=zmk_split_central_invoke_behavior(1,&b,e,true);
    if(rc>=0){
        if(n>=0)atomic_cas(&request,n,-1);
        else{
            key=k_spin_lock(&lock);
            /* A FREEZE or tuple round-trip during enqueue invalidates this
             * completion, even when the numeric epoch has returned to echo. */
            if(generation==echo_generation && echo==atomic_get(&echo_epoch) && phase==(uint32_t)atomic_get(&echo_phase)){
                if(echo_dongle_commit && view.mode==KB_ROUTE_DONGLE && phase==KB_LEFT_COMMIT && echo_receiver_boot==kb_receiver_boot() && wire_boot==kb_receiver_peer_boot(1) && k_uptime_get()-view.seen<2500)established_echo_generation=generation;
                atomic_cas(&echo_epoch,echo,0);
            }
            k_spin_unlock(&lock,key);
        }
    }
    if(atomic_get(&request)>=0 || atomic_get(&echo_epoch))k_work_reschedule(&send_command,K_MSEC(50));
}
void kb_route_request(uint8_t mode) {if(mode>1)return;atomic_set(&request,mode);k_work_reschedule(&send_command,K_NO_WAIT);}
int kb_route_select(uint8_t profile) {if(profile>=5)return -EINVAL;atomic_set(&request,10+profile);k_work_reschedule(&send_command,K_NO_WAIT);return 0;}
int kb_route_select_dongle(uint8_t station){if(station>=KB_STATION_COUNT)return -EINVAL;atomic_set(&request,30+station);k_work_reschedule(&send_command,K_NO_WAIT);return 0;}
static uint32_t local_route_generation=1;
uint32_t kb_receiver_route_generation(void){k_spinlock_key_t k=k_spin_lock(&lock);uint32_t g=local_route_generation;k_spin_unlock(&lock,k);return g;}
bool kb_route_status_valid(const struct kb_route_status*s){
    if(s->version!=KB_ROUTE_VERSION || s->mode>KB_ROUTE_SWITCHING || s->profile>=5 || !s->epoch || !s->authority || s->phase>KB_LEFT_COMMIT || s->target>1)return false;
    if(s->station!=CONFIG_LISM_DONGLE_STATION || !s->receiver_boot || s->receiver_boot!=kb_receiver_boot())return false;
    if(s->target==KB_ROUTE_DONGLE && s->owner_station>=KB_STATION_COUNT)return false;
    if(s->target==KB_ROUTE_BLE && s->owner_station!=KB_STATION_NONE)return false;
    if(s->mode==KB_ROUTE_DONGLE && (s->phase!=KB_LEFT_COMMIT || s->owner_station!=s->station))return false;
    k_spinlock_key_t key=k_spin_lock(&lock);bool valid=s->authority!=authority || s->epoch>=view.epoch;k_spin_unlock(&lock,key);return valid;
}
bool kb_route_discovery_release(const struct kb_discovery*p){
    k_spinlock_key_t key=k_spin_lock(&lock);bool close=view.mode==KB_ROUTE_DONGLE && (p->authority!=authority || wire_boot!=kb_receiver_peer_boot(1));k_spin_unlock(&lock,key);return close;
}
int kb_route_discovery_received(const struct kb_discovery*p){
    /* An offer is presence only. It must not renew ownership or an input lease. */
    if(kb_route_discovery_release(p)){k_spinlock_key_t key=k_spin_lock(&lock);view.mode=KB_ROUTE_SWITCHING;if(!++local_route_generation)++local_route_generation;if(!++echo_generation)++echo_generation;established_echo_generation=0;echo_dongle_commit=false;k_spin_unlock(&lock,key);kb_route_flush_commands();}
    struct zmk_behavior_binding binding={.behavior_dev="KB_PROBE3",.param1=p->cookie,.param2=kb_receiver_boot()};
    struct zmk_behavior_binding_event event={.source=1,.position=CONFIG_LISM_DONGLE_STATION,.timestamp=k_uptime_get()};
    return zmk_split_central_invoke_behavior(1,&binding,event,true);
}
void kb_route_status_received(const struct kb_route_status *s) {
    if(!kb_route_status_valid(s))return;
    k_spinlock_key_t key=k_spin_lock(&lock);bool changed=authority!=s->authority || view.epoch!=s->epoch || wire_boot!=kb_receiver_peer_boot(1);k_spin_unlock(&lock,key);
    if(changed)kb_route_flush_commands();
    key=k_spin_lock(&lock);
    bool commit=s->mode==KB_ROUTE_DONGLE && s->target==KB_ROUTE_DONGLE && s->phase==KB_LEFT_COMMIT;
    bool expired=k_uptime_get()-view.seen>2500;
    if(changed || view.mode!=s->mode || expired){if(!++local_route_generation)++local_route_generation;}
    if(changed || view.mode!=s->mode || !commit || !echo_dongle_commit || expired || echo_receiver_boot!=s->receiver_boot){if(!++echo_generation)++echo_generation;established_echo_generation=0;}
    echo_dongle_commit=commit;echo_receiver_boot=s->receiver_boot;
    authority=s->authority;wire_boot=kb_receiver_peer_boot(1);view=(struct kb_route_view){.mode=s->mode,.profile=s->profile,.last_mode=s->mode,.epoch=s->epoch,.seen=k_uptime_get()};k_spin_unlock(&lock,key);
    atomic_set(&echo_phase,s->phase);atomic_set(&echo_epoch,s->epoch);k_work_reschedule(&send_command,K_NO_WAIT);
}
void kb_route_snapshot(struct kb_route_view *out) {
    k_spinlock_key_t key=k_spin_lock(&lock);*out=view;k_spin_unlock(&lock,key);
    if(k_uptime_get()-out->seen>2500)out->mode=KB_ROUTE_UNKNOWN;
}
bool kb_route_dongle_key_allowed(void) {struct kb_route_view s;kb_route_snapshot(&s);return s.mode==KB_ROUTE_DONGLE;}

bool kb_route_left_allowed(const struct kb_left_packet*p){
 k_spinlock_key_t k=k_spin_lock(&lock);bool allowed=view.mode==KB_ROUTE_DONGLE && p->authority==authority && p->epoch==view.epoch && p->station==CONFIG_LISM_DONGLE_STATION && p->receiver_boot==kb_receiver_boot() && k_uptime_get()-view.seen<2500;k_spin_unlock(&lock,k);return allowed;
}
uint32_t kb_receiver_route_epoch(void){k_spinlock_key_t k=k_spin_lock(&lock);uint32_t e=view.epoch;k_spin_unlock(&lock,k);return e;}

#include <zmk/hid.h>
extern bool lism_input_usb_nonblocking(void);
bool lism_input_merge_local_allowed(void){return kb_route_dongle_key_allowed();}
bool lism_input_merge_external_frame(void){return lism_input_usb_nonblocking();}
static bool neutral(const void*p,size_t n){const uint8_t*b=p;for(size_t i=0;i<n;i++)if(b[i])return false;return true;}
bool kb_route_receiver_output_allowed(uint16_t usage){
    if(kb_route_dongle_key_allowed() || lism_input_usb_nonblocking())return true;
    if(usage==HID_USAGE_KEY)return neutral(&zmk_hid_get_keyboard_report()->body,sizeof(zmk_hid_get_keyboard_report()->body));
    if(usage==HID_USAGE_CONSUMER)return neutral(&zmk_hid_get_consumer_report()->body,sizeof(zmk_hid_get_consumer_report()->body));
    if(!usage)return neutral(&zmk_hid_get_mouse_report()->body,sizeof(zmk_hid_get_mouse_report()->body));
    return false;
}
