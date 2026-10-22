#include <kb_route/route.h>
#include <kb_route/mirror.h>
/* SPDX-License-Identifier: MIT */
#include "common.h"
#include "link.h"
#if IS_ENABLED(CONFIG_LISM_BLE_INPUT_BRIDGE)
#include <lism_screen.h>
#endif
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
static uint32_t max_valid_gap[2],lease_clears[2];
static int64_t next_link_diag;
static bool online[2];
static struct lism_snapshot held[2];
static bool battery_seen[2];
static atomic_t ack_pending;
static K_MUTEX_DEFINE(state_mutex);
static int64_t next_dispatch_error_log;
static uint32_t trackball_frames;
static int64_t next_trackball_log;
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
#if IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL) && !IS_ENABLED(CONFIG_LISM_KB_HUB)
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
#if defined(CONFIG_SHIELD_LISM_L)
    kb_left_track(e);
#endif
}
bool lism_valid_event(uint8_t pipe,const struct esb_event_envelope *env) {
    if(pipe>=2 || env->payload.source!=pipe)return false;
    const struct zmk_split_transport_peripheral_event *e=&env->payload.event;
    size_t n;
    switch((int)e->type) {
    case KB_LEFT_DATA:n=sizeof(struct kb_left_packet);if(pipe)return false;break;
    case KB_LEFT_CONTROL:n=sizeof(struct kb_left_control);if(pipe || !IS_ENABLED(CONFIG_LISM_KB_HUB))return false;break;
    case LISM_SNAPSHOT_TYPE:n=sizeof(struct lism_snapshot);break;
    case KB_MIRROR_EVENT:n=sizeof(struct kb_mirror);if(pipe!=1)return false;break;
    case KB_ROUTE_EVENT:
        n=sizeof(struct kb_route_status);if(pipe!=1)return false;break;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT:
        n=sizeof(e->data.key_position_event);
        if(e->data.key_position_event.position>=LISM_KEYS || e->data.key_position_event.pressed>1)return false;
        break;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT:
        n=sizeof(e->data.input_event);
        if(pipe!=1 || e->data.input_event.reg!=1 || e->data.input_event.sync>1)return false;
        if(e->data.input_event.type!=INPUT_EV_REL && e->data.input_event.type!=INPUT_EV_KEY)return false;
        /* The unchanged right-hand zip_xyz packs signed X/Y into REL_Z.
         * Its full int32 payload must reach the receiver's zip_zxy decoder. */
        if(e->data.input_event.type==INPUT_EV_REL && e->data.input_event.code!=INPUT_REL_X &&
            e->data.input_event.code!=INPUT_REL_Y && e->data.input_event.code!=INPUT_REL_Z &&
            e->data.input_event.code!=INPUT_REL_WHEEL &&
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
#if IS_ENABLED(CONFIG_LISM_BLE_INPUT_BRIDGE)
/* state_mutex owns physical held state plus this ownership barrier. */
static uint64_t quarantine[2];
static bool page_pending,page_target;
static bool menu_key(unsigned source,unsigned pos) {
    return (!source && pos>=42 && pos<=45) || (source==1 && pos==37);
}
static void apply_page(void) {
    if(lism_screen_take_toggle()) {
        page_target=page_pending?!page_target:!lism_screen_active();
        page_pending=page_target!=lism_screen_active();
    }
    if(!page_pending)return;
    bool value=page_target;
    for(unsigned source=0;source<2;source++)for(unsigned pos=0;pos<LISM_KEYS;pos++) {
        uint64_t mask=UINT64_C(1)<<pos;
        if(menu_key(source,pos) && (held[source].keys&mask)) {
            /* Release controls delivered to the old owner once. Physical
             * state stays held; require a real release before a new press. */
            if(value && !(quarantine[source]&mask)) {
                struct zmk_split_transport_peripheral_event e={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,
                    .data.key_position_event={.position=pos,.pressed=false}};
                if(!kb_route_dongle_key_allowed()) {
        if(e.type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT && e.data.key_position_event.pressed)return 0;
        if(e.type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT && !(e.data.input_event.type==INPUT_EV_KEY && e.data.input_event.value==0))return 0;
    }
    int rc=zmk_split_transport_central_peripheral_event_handler(&esb_central,source,e);
                if(!applied(rc,&e))return;
            }
            quarantine[source]|=mask;
        }
    }
    lism_screen_commit(value);page_pending=false;
}
static int dispatch(unsigned source,struct zmk_split_transport_peripheral_event e) {
    apply_page();
    if(e.type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT && menu_key(source,e.data.key_position_event.position)) {
        unsigned pos=e.data.key_position_event.position;uint64_t mask=UINT64_C(1)<<pos;
        if(quarantine[source]&mask) {
            if(!e.data.key_position_event.pressed)quarantine[source]&=~mask;
            return 0;
        }
        if(lism_screen_active()) {
            if(e.data.key_position_event.pressed)lism_screen_nav(pos);
            return 0;
        }
    }
    if(!kb_route_dongle_key_allowed()) {
        if(e.type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT && e.data.key_position_event.pressed)return 0;
        if(e.type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT && !(e.data.input_event.type==INPUT_EV_KEY && e.data.input_event.value==0))return 0;
    }
    int rc=zmk_split_transport_central_peripheral_event_handler(&esb_central,source,e);
    apply_page();return rc;
}
#else
static int dispatch(unsigned source,struct zmk_split_transport_peripheral_event e) {
#if IS_ENABLED(CONFIG_LISM_KB_HUB)
    if(kb_route_input(source,&e))return 0;
#endif
    return zmk_split_transport_central_peripheral_event_handler(&esb_central,source,e);
}
#endif

static int reconcile(unsigned source,const struct lism_snapshot *wanted) {
    if(wanted->keys>>LISM_KEYS || wanted->buttons&~0x1f || wanted->battery>100)return -EINVAL;
    if(!source && wanted->buttons)return -EINVAL;
    for(unsigned i=0;i<LISM_KEYS;i++) {
        if(((held[source].keys^wanted->keys)>>i)&1) {
            struct zmk_split_transport_peripheral_event e={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,
                .data.key_position_event={.position=i,.pressed=(wanted->keys>>i)&1}};
            int rc=dispatch(source,e);
            if(!applied(rc,&e))return rc;
            track(&held[source],&e);
        }
    }
    for(unsigned i=0;i<5;i++)if(((held[source].buttons^wanted->buttons)>>i)&1) {
        struct zmk_split_transport_peripheral_event e={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT,
            .data.input_event={.reg=source,.type=INPUT_EV_KEY,.code=INPUT_BTN_LEFT+i,.value=(wanted->buttons>>i)&1,.sync=1}};
        int rc=dispatch(source,e);
        if(rc<0)return rc;
        track(&held[source],&e);
    }
    if(!battery_seen[source] || held[source].battery!=wanted->battery) {
        struct zmk_split_transport_peripheral_event e={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT,
            .data.battery_event={.level=wanted->battery}};
        int rc=dispatch(source,e);
        if(rc<0)return rc;
        held[source].battery=wanted->battery;battery_seen[source]=true;
    }
    return 0;
}
int lism_handle_event(uint8_t source,const struct zmk_split_transport_peripheral_event *e) {
    if(source>=2)return -EINVAL;
    if((int)e->type==KB_LEFT_CONTROL){
#if IS_ENABLED(CONFIG_LISM_KB_HUB)
        struct kb_left_control c;memcpy(&c,&e->data,sizeof(c));kb_route_left_control(&c);
#endif
        return 0;
    }
    if((int)e->type==KB_LEFT_DATA){
        struct kb_left_packet p;memcpy(&p,&e->data,sizeof(p));
        if(source || !kb_left_packet_valid(&p))return -EINVAL;
        if(!kb_route_left_allowed(&p))return 0;
        struct lism_snapshot s={.keys=kb_left_keys(&p),.buttons=p.buttons,.battery=p.battery};
        struct zmk_split_transport_peripheral_event decoded={.type=LISM_SNAPSHOT_TYPE};memcpy(&decoded.data,&s,sizeof(s));
        return lism_handle_event(0,&decoded);
    }
#if !IS_ENABLED(CONFIG_LISM_KB_HUB)
    if((int)e->type==KB_MIRROR_EVENT){struct kb_mirror m;memcpy(&m,&e->data,sizeof(m));kb_mirror_received(&m);
        if(!kb_route_dongle_key_allowed() && m.left_online && m.left_battery<=100){struct lism_snapshot s={.battery=m.left_battery};struct zmk_split_transport_peripheral_event b={.type=LISM_SNAPSHOT_TYPE};memcpy(&b.data,&s,sizeof(s));lism_handle_event(0,&b);}return 0;}
    if((int)e->type==KB_ROUTE_EVENT) {
        struct kb_route_status s;memcpy(&s,&e->data,sizeof(s));
        k_mutex_lock(&state_mutex,K_FOREVER);
        /* Release the old keymap BEFORE the status gate closes. */
        if(s.version==KB_ROUTE_VERSION && s.mode<=KB_ROUTE_SWITCHING && s.profile<5 && s.epoch && s.mode!=KB_ROUTE_DONGLE) {
            for(unsigned i=0;i<2;i++){struct lism_snapshot empty={.battery=held[i].battery};if(reconcile(i,&empty)<0){k_mutex_unlock(&state_mutex);return -EAGAIN;}}
        }
        kb_route_status_received(&s);
#if IS_ENABLED(CONFIG_LISM_BLE_INPUT_BRIDGE)
        if(s.version==KB_ROUTE_VERSION && s.mode==KB_ROUTE_BLE && s.profile<5 && s.epoch) {
            /* In keyboard-direct mode this receiver becomes a status display.
             * Drop a pending menu toggle and return to the mirrored home page. */
            page_pending=false;page_target=false;lism_screen_take_toggle();
            if(lism_screen_active())lism_screen_commit(false);
        }
#endif
        k_mutex_unlock(&state_mutex);return 0;
    }
#endif
    k_mutex_lock(&state_mutex,K_FOREVER);int rc;
    if((int)e->type==LISM_SNAPSHOT_TYPE) {
        struct lism_snapshot s;memcpy(&s,&e->data,sizeof(s));
#if !IS_ENABLED(CONFIG_LISM_KB_HUB)
        if(!kb_route_dongle_key_allowed())s.keys=0,s.buttons=0;
#endif
        rc=reconcile(source,&s);
        if(rc>=0) {if(online[source])max_valid_gap[source]=MAX(max_valid_gap[source],(uint32_t)(k_uptime_get()-seen[source]));seen[source]=k_uptime_get();connectivity(source,true);}
    } else {
#if !IS_ENABLED(CONFIG_LISM_KB_HUB)
        if(!kb_route_dongle_key_allowed() &&
           ((e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT && e->data.key_position_event.pressed) ||
            (e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT && !(e->data.input_event.type==INPUT_EV_KEY && e->data.input_event.value==0)))) {
            k_mutex_unlock(&state_mutex);return 0;
        }
#endif
        rc=dispatch(source,*e);
        if(applied(rc,e)) {
            track(&held[source],e);if(online[source])max_valid_gap[source]=MAX(max_valid_gap[source],(uint32_t)(k_uptime_get()-seen[source]));seen[source]=k_uptime_get();connectivity(source,true);rc=0;
            if(e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT &&
                e->data.input_event.type==INPUT_EV_REL && e->data.input_event.code==INPUT_REL_Z) {
                trackball_frames++;
                if(k_uptime_get()>=next_trackball_log) {
                    next_trackball_log=k_uptime_get()+5000;
                    LOG_INF("ESB trackball REL_Z queued: %u",trackball_frames);
                }
            }
            if(e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT) {
                held[source].battery=e->data.battery_event.level;battery_seen[source]=true;
            }
        }
    }
#if IS_ENABLED(CONFIG_LISM_KB_HUB)
    if(!source && rc>=0)kb_route_left_alive();
#endif
    if(rc<0 && k_uptime_get()>=next_dispatch_error_log) {
        next_dispatch_error_log=k_uptime_get()+5000;
        LOG_INF("ESB dispatch rejected src=%u type=%u rc=%d",source,(unsigned)e->type,rc);
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
    #if IS_ENABLED(CONFIG_LISM_BLE_INPUT_BRIDGE)
    apply_page();
    #endif
#if !IS_ENABLED(CONFIG_LISM_KB_HUB)
    if(!kb_route_dongle_key_allowed())for(unsigned i=0;i<2;i++) {
        const struct lism_snapshot empty={.battery=held[i].battery};reconcile(i,&empty);
    }
#endif
    for(unsigned i=0;i<2;i++)if(online[i] && k_uptime_get()-seen[i]>=LISM_LEASE_MS) {
        const struct lism_snapshot empty={.battery=held[i].battery};
        if(reconcile(i,&empty)>=0) {lease_clears[i]++;connectivity(i,false);}
    }
    if(k_uptime_get()>=next_link_diag) {
        next_link_diag=k_uptime_get()+5000;
        LOG_INF("ESB valid RX max_gap_ms left=%u right=%u lease_clears=%u,%u",max_valid_gap[0],max_valid_gap[1],lease_clears[0],lease_clears[1]);
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
