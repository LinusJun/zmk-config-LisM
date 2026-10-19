/* SPDX-License-Identifier: MIT */
#include "common.h"
#include "link.h"
#include <zephyr/dt-bindings/input/input-event-codes.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/logging/log.h>
#include <zmk/split/transport/central.h>
#include <zmk/split/transport/peripheral.h>
#include <zmk/split/peripheral.h>
#include <zmk/battery.h>
#include <zmk/events/split_peripheral_status_changed.h>
#if IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
#include <zmk/events/split_central_status_changed.h>
#endif
LOG_MODULE_REGISTER(lism_link,LOG_LEVEL_INF);
static int64_t seen[2];
static bool online[2];
static struct lism_snapshot held[2];
static bool battery_seen[2];
static atomic_t ack_pending;
static K_MUTEX_DEFINE(state_mutex);
/* An output-switch release (and other unsupported key behaviors) can return
 * -ENOTSUP after consuming a valid physical key event. A transport rejection
 * such as -EINVAL must still leave the physical state pending. */
static bool applied(int rc,const struct zmk_split_transport_peripheral_event *e) {
    return rc>=0 || (rc==-ENOTSUP && e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT);
}
static void tick(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(link_tick,tick);

bool lism_connected(unsigned source) {
    if(source>=2 || !lism_radio_ready())return false;
    k_mutex_lock(&state_mutex,K_FOREVER);bool value=online[source];k_mutex_unlock(&state_mutex);return value;
}
void lism_ack_seen(void) {atomic_set(&ack_pending,1);}
static void connectivity(uint8_t source,bool value) {
    if(online[source]==value)return;
    online[source]=value;
    LOG_INF("ESB %s %s",source?"right":"left",value?"online":"offline");
    lism_notify_status();
#if IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
    raise_zmk_split_central_status_changed((struct zmk_split_central_status_changed){.slot=source,.connected=value});
#endif
#if !IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
    raise_zmk_split_peripheral_status_changed((struct zmk_split_peripheral_status_changed){.connected=value});
#endif
}
static void track(struct lism_snapshot *s,const struct zmk_split_transport_peripheral_event *e) {
    if(e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT && e->data.key_position_event.position<LISM_KEYS) {
        uint64_t mask=UINT64_C(1)<<e->data.key_position_event.position;
        if(e->data.key_position_event.pressed)s->keys|=mask;else s->keys&=~mask;
    } else if(e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT &&
        e->data.input_event.type==INPUT_EV_KEY && e->data.input_event.code>=INPUT_BTN_LEFT &&
        e->data.input_event.code<INPUT_BTN_LEFT+5) {
        unsigned mask=BIT(e->data.input_event.code-INPUT_BTN_LEFT);
        if(e->data.input_event.value)s->buttons|=mask;else s->buttons&=~mask;
    }
}
void lism_track_event(const struct zmk_split_transport_peripheral_event *e) {
    k_mutex_lock(&state_mutex,K_FOREVER);track(&held[CONFIG_ZMK_SPLIT_ESB_PERIPHERAL_ID],e);k_mutex_unlock(&state_mutex);
}
bool lism_valid_event(uint8_t pipe,const struct esb_event_envelope *env) {
    if(pipe>=2 || env->payload.source!=pipe)return false;
    const struct zmk_split_transport_peripheral_event *e=&env->payload.event;
    size_t n;
    switch((int)e->type) {
    case LISM_SNAPSHOT_TYPE:n=sizeof(struct lism_snapshot);break;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT:
        n=sizeof(e->data.key_position_event);
        if(e->data.key_position_event.position>=LISM_KEYS || e->data.key_position_event.pressed>1)return false;
        break;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT:
        n=sizeof(e->data.input_event);
        if(pipe!=1 || e->data.input_event.reg!=1 || e->data.input_event.sync>1)return false;
        if(e->data.input_event.type!=INPUT_EV_REL && e->data.input_event.type!=INPUT_EV_KEY)return false;
        if(e->data.input_event.type==INPUT_EV_REL && e->data.input_event.code!=INPUT_REL_X &&
            e->data.input_event.code!=INPUT_REL_Y && e->data.input_event.code!=INPUT_REL_WHEEL &&
            e->data.input_event.code!=INPUT_REL_HWHEEL)return false;
        if(e->data.input_event.type==INPUT_EV_KEY && (e->data.input_event.code<INPUT_BTN_LEFT ||
            e->data.input_event.code>=INPUT_BTN_LEFT+5 || e->data.input_event.value<0 || e->data.input_event.value>1))return false;
        break;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT:
        n=sizeof(e->data.battery_event);if(e->data.battery_event.level>100)return false;break;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_SENSOR_EVENT:
        if(!ZMK_KEYMAP_HAS_SENSORS || e->data.sensor_event.sensor_index>=ZMK_KEYMAP_SENSORS_LEN)return false;
        n=sizeof(e->data.sensor_event);break;
    default:return false;
    }
    return env->prefix.payload_size==sizeof(env->payload.source)+sizeof(e->type)+n;
}
#if IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
extern const struct zmk_split_transport_central esb_central;
static int reconcile(unsigned source,const struct lism_snapshot *wanted) {
    if(wanted->keys>>LISM_KEYS || wanted->buttons&~0x1f || wanted->battery>100)return -EINVAL;
    if(!source && wanted->buttons)return -EINVAL;
    for(unsigned i=0;i<LISM_KEYS;i++) {
        if(((held[source].keys^wanted->keys)>>i)&1) {
            struct zmk_split_transport_peripheral_event e={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,
                .data.key_position_event={.position=i,.pressed=(wanted->keys>>i)&1}};
            int rc=zmk_split_transport_central_peripheral_event_handler(&esb_central,source,e);
            if(!applied(rc,&e))return rc;
            track(&held[source],&e);
        }
    }
    for(unsigned i=0;i<5;i++)if(((held[source].buttons^wanted->buttons)>>i)&1) {
        struct zmk_split_transport_peripheral_event e={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT,
            .data.input_event={.reg=source,.type=INPUT_EV_KEY,.code=INPUT_BTN_LEFT+i,.value=(wanted->buttons>>i)&1,.sync=1}};
        int rc=zmk_split_transport_central_peripheral_event_handler(&esb_central,source,e);
        if(rc<0)return rc;
        track(&held[source],&e);
    }
    if(!battery_seen[source] || held[source].battery!=wanted->battery) {
        struct zmk_split_transport_peripheral_event e={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT,
            .data.battery_event={.level=wanted->battery}};
        int rc=zmk_split_transport_central_peripheral_event_handler(&esb_central,source,e);
        if(rc<0)return rc;
        held[source].battery=wanted->battery;battery_seen[source]=true;
    }
    return 0;
}
int lism_handle_event(uint8_t source,const struct zmk_split_transport_peripheral_event *e) {
    if(source>=2)return -EINVAL;
    k_mutex_lock(&state_mutex,K_FOREVER);int rc;
    if((int)e->type==LISM_SNAPSHOT_TYPE) {
        struct lism_snapshot s;memcpy(&s,&e->data,sizeof(s));
        rc=reconcile(source,&s);
        if(rc>=0) {seen[source]=k_uptime_get();connectivity(source,true);}
    } else {
        rc=zmk_split_transport_central_peripheral_event_handler(&esb_central,source,*e);
        if(applied(rc,e)) {
            track(&held[source],e);seen[source]=k_uptime_get();connectivity(source,true);rc=0;
            if(e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT) {
                held[source].battery=e->data.battery_event.level;battery_seen[source]=true;
            }
        }
    }
    k_mutex_unlock(&state_mutex);return rc;
}
#else
int lism_handle_event(uint8_t source,const struct zmk_split_transport_peripheral_event *e) {return -ENOTSUP;}
#endif
static void tick(struct k_work *work) {
    ARG_UNUSED(work);
#if IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
    k_mutex_lock(&state_mutex,K_FOREVER);
    for(unsigned i=0;i<2;i++)if(online[i] && k_uptime_get()-seen[i]>=LISM_LEASE_MS) {
        const struct lism_snapshot empty={.battery=held[i].battery};
        if(reconcile(i,&empty)>=0)connectivity(i,false);
    }
    k_mutex_unlock(&state_mutex);
#else
    const unsigned id=CONFIG_ZMK_SPLIT_ESB_PERIPHERAL_ID;
    struct zmk_split_transport_peripheral_event e={.type=LISM_SNAPSHOT_TYPE};
    /* Lock order is TX then state, identical to native key producers. */
    lism_tx_lock();k_mutex_lock(&state_mutex,K_FOREVER);
    if(atomic_set(&ack_pending,0)) {seen[id]=k_uptime_get();connectivity(id,true);}
    if(online[id] && k_uptime_get()-seen[id]>=LISM_LEASE_MS)connectivity(id,false);
    held[id].battery=zmk_battery_state_of_charge();
    memcpy(&e.data,&held[id],sizeof(struct lism_snapshot));k_mutex_unlock(&state_mutex);
    zmk_split_peripheral_report_event(&e);lism_tx_unlock();
#endif
    k_work_reschedule(&link_tick,K_MSEC(200));
}
static int link_init(void) {k_work_reschedule(&link_tick,K_MSEC(200));return 0;}
SYS_INIT(link_init,APPLICATION,99);
