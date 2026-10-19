/* SPDX-License-Identifier: MIT */
#include "radio.h"
#include "codec.h"
#if defined(CONFIG_LISM_STANDALONE)
#include "../standalone/mode.h"
#endif
#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/dt-bindings/input/input-event-codes.h>
#include <zephyr/sys/atomic.h>
#include <zmk/split/transport/peripheral.h>
#include <zmk/events/split_peripheral_status_changed.h>
#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(lism_esb, CONFIG_ZMK_LOG_LEVEL);

extern struct zmk_split_transport_peripheral *zmk_split_transport_peripheral_bt(void);
extern struct zmk_split_transport_peripheral lism_esb_peripheral;
static struct bt_uuid_128 config_uuid = BT_UUID_INIT_128(LISM_ESB_UUID);
static struct bt_gatt_discover_params discover;
static struct bt_gatt_read_params reader;
static struct bt_conn *reading_conn;
static bool reading;
static uint8_t config_bytes[LISM_ESB_CONFIG_SIZE], config_length;
static struct lism_esb_session proposed;
static atomic_t config_pending, control_lost, fault, radio_failed, status_connected;
#if defined(CONFIG_LISM_STANDALONE)
/* Only the radio owner publishes freshness. A new control session or a closed
 * transport immediately invalidates it; duplicate ACKs cannot renew it. */
static atomic_t ack_time, ack_valid, input_gate;
bool lism_standalone_esb_fresh(void) {
    return atomic_get(&ack_valid) && !atomic_get(&fault) && !atomic_get(&control_lost) &&
        !atomic_get(&radio_failed) &&
        (uint32_t)((uint32_t)k_uptime_get()-(uint32_t)atomic_get(&ack_time))<750;
}
bool lism_standalone_esb_running(void) {
    return atomic_get(&input_gate) && lism_standalone_esb_fresh();
}
#define INPUT_ALLOWED() atomic_get(&input_gate)
#else
#define INPUT_ALLOWED() true
#endif
static K_MUTEX_DEFINE(io_lock);
static struct lism_esb_session session;
static bool enabled, online, waiting, synced, pending_relative;
static uint8_t held[16], battery = 100;
static uint32_t next_sequence, command_ack, last_ack;
static int64_t last_received, last_sent, pending_since;
static struct lism_esb_message outgoing;
static struct esb_payload outgoing_packet;
static K_MUTEX_DEFINE(event_lock);
struct queued_event { struct zmk_split_transport_peripheral_event event; int64_t time; };
static struct queued_event deferred_event;
static bool has_deferred_event;
K_MSGQ_DEFINE(events, sizeof(struct queued_event), 48, 4);
struct input_key { uint8_t reg; uint16_t code; bool pressed; };
static struct input_key input_keys[8];

static void config_work_fn(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(config_work, config_work_fn);
static void finish_read(void) {
    reading = false;
    if (reading_conn) { bt_conn_unref(reading_conn); reading_conn = NULL; }
    k_work_reschedule(&config_work, K_MSEC(500));
}
static uint8_t read_result(struct bt_conn *conn, uint8_t err,
                            struct bt_gatt_read_params *params, const void *data, uint16_t length) {
    ARG_UNUSED(params);
    k_mutex_lock(&io_lock, K_FOREVER);
    if (conn != reading_conn || !reading) { k_mutex_unlock(&io_lock); return BT_GATT_ITER_STOP; }
    uint8_t result = BT_GATT_ITER_CONTINUE;
    if (err || !data || config_length + length > sizeof(config_bytes)) {
        if (!err && !data && config_length == sizeof(config_bytes) &&
            !atomic_get(&config_pending) &&
            !lism_esb_config_decode(&proposed, config_bytes, sizeof(config_bytes))) {
            bool right = IS_ENABLED(CONFIG_SHIELD_LISM_R);
            if ((proposed.slot & 1) == right) {
#if defined(CONFIG_LISM_STANDALONE)
                lism_standalone_peer_poll(conn);
                if(!lism_standalone_control_selected(conn)) {
                    finish_read();k_mutex_unlock(&io_lock);return BT_GATT_ITER_STOP;
                }
#endif
                atomic_set(&config_pending, 1);
            }
        }
        finish_read(); result = BT_GATT_ITER_STOP;
    } else { memcpy(config_bytes + config_length, data, length); config_length += length; }
    k_mutex_unlock(&io_lock); return result;
}
static uint8_t discovered(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                           struct bt_gatt_discover_params *params) {
    ARG_UNUSED(params);
    k_mutex_lock(&io_lock, K_FOREVER);
    if (conn != reading_conn || !reading) { k_mutex_unlock(&io_lock); return BT_GATT_ITER_STOP; }
    if (!attr) finish_read();
    else {
        const struct bt_gatt_chrc *ch = attr->user_data;
        config_length = 0;
        reader = (struct bt_gatt_read_params){.func = read_result, .handle_count = 1,
            .single = {.handle = ch->value_handle, .offset = 0}};
        if (bt_gatt_read(conn, &reader)) finish_read();
    }
    k_mutex_unlock(&io_lock); return BT_GATT_ITER_STOP;
}
static void find_control(struct bt_conn *conn, void *data) {
    struct bt_conn_info info;
    struct bt_conn **result = data;
    if (!*result && !bt_conn_get_info(conn, &info) && info.state == BT_CONN_STATE_CONNECTED &&
        info.role == BT_CONN_ROLE_PERIPHERAL && bt_conn_get_security(conn) >= BT_SECURITY_L2
#if defined(CONFIG_LISM_STANDALONE)
        && !lism_standalone_direct() && lism_standalone_running() && lism_standalone_control_conn(conn)
#endif
        )
        *result = bt_conn_ref(conn);
}
static void config_work_fn(struct k_work *work) {
    ARG_UNUSED(work);
    if (atomic_get(&radio_failed)) return;
    k_mutex_lock(&io_lock, K_FOREVER);
    if (!reading && !atomic_get(&config_pending)) {
        bt_conn_foreach(BT_CONN_TYPE_LE, find_control, &reading_conn);
        if (reading_conn) {
            reading = true;
            discover = (struct bt_gatt_discover_params){.uuid = &config_uuid.uuid,
                .func = discovered, .start_handle = BT_ATT_FIRST_ATTRIBUTE_HANDLE,
                .end_handle = BT_ATT_LAST_ATTRIBUTE_HANDLE, .type = BT_GATT_DISCOVER_CHARACTERISTIC};
            if (bt_gatt_discover(reading_conn, &discover)) finish_read();
        }
    }
    k_mutex_unlock(&io_lock);
    k_work_reschedule(&config_work, K_MSEC(500));
}
static void disconnected(struct bt_conn *conn, uint8_t reason) {
    ARG_UNUSED(reason);
#if defined(CONFIG_LISM_STANDALONE)
    if(!lism_standalone_control_conn(conn))return;
#endif
    /* Cancel before reusing the persistent parameter structs. Callbacks and
     * this callback are serialized by the BLE RX thread. */
    bt_gatt_cancel(conn, &discover); bt_gatt_cancel(conn, &reader);
    k_mutex_lock(&io_lock, K_FOREVER);
    if (reading_conn == conn) finish_read();
    atomic_set(&control_lost, 1);
    atomic_clear(&config_pending);
    k_mutex_unlock(&io_lock);
}
BT_CONN_CB_DEFINE(lism_esb_control_callbacks) = {.disconnected = disconnected};
static void disconnect_control(struct bt_conn *conn, void *unused) {
    ARG_UNUSED(unused);
#if defined(CONFIG_LISM_STANDALONE)
    if(!lism_standalone_control_conn(conn))return;
#endif
    bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
}
static void drop_session(void) {
    k_mutex_lock(&event_lock, K_FOREVER);
    bool was_online = online;
    online = waiting = synced = false; memset(session.key, 0, sizeof(session.key));
    atomic_clear(&status_connected);
#if defined(CONFIG_LISM_STANDALONE)
    atomic_clear(&ack_valid);
    atomic_clear(&input_gate);
#endif
    k_msgq_purge(&events);
    k_mutex_unlock(&event_lock);
    if (was_online) raise_zmk_split_peripheral_status_changed(
        (struct zmk_split_peripheral_status_changed){.connected = false});
}
static void enqueue_state(void) {
    for (unsigned i = 0; i < ARRAY_SIZE(input_keys); ++i) {
        if (!input_keys[i].pressed) continue;
        struct queued_event q = {.time = k_uptime_get(), .event = {
            .type = ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT,
            .data.input_event = {.reg = input_keys[i].reg, .sync = 1,
                .type = INPUT_EV_KEY, .code = input_keys[i].code, .value = 1}}};
        if (k_msgq_put(&events, &q, K_NO_WAIT)) atomic_set(&fault, 1);
    }
    struct queued_event q = {.time = k_uptime_get(), .event = {
        .type = ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT,
        .data.battery_event.level = battery}};
    if (k_msgq_put(&events, &q, K_NO_WAIT)) atomic_set(&fault, 1);
}
static void start_session(const struct lism_esb_session *s, int64_t now) {
    k_mutex_lock(&event_lock, K_FOREVER);
    session = *s; k_msgq_purge(&events); has_deferred_event = false; pending_relative = false;
    next_sequence = 1; command_ack = last_ack = 0; last_received = now;
    outgoing = (struct lism_esb_message){.kind = LISM_ESB_SNAPSHOT, .length = 20,
                                       .sequence = next_sequence++};
    lism_esb_put_u32(outgoing.body, command_ack);
    if(INPUT_ALLOWED())memcpy(outgoing.body + 4, held, 16);
    online = waiting = true; synced = false; pending_since = now; last_sent = now - 10;
    atomic_clear(&status_connected);
#if defined(CONFIG_LISM_STANDALONE)
    atomic_clear(&ack_valid);
#endif
    if(INPUT_ALLOWED())enqueue_state();
    k_mutex_unlock(&event_lock);
    LOG_INF("ESB source %u: session started", s->slot);
}
void lism_esb_transport_receive(const struct esb_payload *packet, int64_t now) {
    struct lism_esb_message m;
    if (!enabled || !online || packet->pipe != session.slot ||
        lism_esb_open(&session, packet->data, packet->length, &m) ||
        m.kind != LISM_ESB_ACK || m.length < 9 || m.length != 9 + m.body[8] ||
        m.sequence < last_ack || lism_esb_u32(m.body) > outgoing.sequence) return;
    bool fresh_ack=m.sequence>last_ack;
    if (fresh_ack) { last_received = now; last_ack = m.sequence; }
    if (waiting && lism_esb_u32(m.body) == outgoing.sequence) {
        waiting = false;
        if (!synced) {
            synced = true;
            atomic_set(&status_connected, 1);
            raise_zmk_split_peripheral_status_changed(
                (struct zmk_split_peripheral_status_changed){.connected = true});
        }
    }
    uint32_t cmd_seq = lism_esb_u32(m.body + 4);
    if (cmd_seq > command_ack) {
        if (cmd_seq != command_ack + 1 || !m.body[8]) { atomic_set(&fault, 1); return; }
        struct zmk_split_transport_central_command cmd;
        int rc = lism_esb_decode_command(m.body + 9, m.body[8], &cmd);
        if (!rc && cmd.type != ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_POLL_EVENTS)
            rc = zmk_split_transport_peripheral_command_handler(&lism_esb_peripheral, cmd);
        if (rc < 0) { atomic_set(&fault, 1); return; }
        command_ack = cmd_seq;
    }
#if defined(CONFIG_LISM_STANDALONE)
    /* A commissioning ACK(received=0) is not an accepted snapshot. */
    if(fresh_ack && synced && !atomic_get(&fault)) {
        atomic_set(&ack_time,(uint32_t)now);atomic_set(&ack_valid,1);
    }
#endif
}
void lism_esb_transport_poll(int64_t now) {
    const struct zmk_split_transport_peripheral *bt = zmk_split_transport_peripheral_bt();
    if (enabled && bt->api->get_status().available && !bt->api->get_status().enabled)
        bt->api->set_enabled(true);
    if (atomic_set(&control_lost, 0)) drop_session();
    k_mutex_lock(&io_lock, K_FOREVER);
    if (atomic_get(&config_pending)
#if defined(CONFIG_LISM_STANDALONE)
        && !lism_standalone_direct() && lism_standalone_running()
#endif
       ) {
        if (!online || proposed.id != session.id || memcmp(proposed.key, session.key, 16))
            start_session(&proposed, now);
        atomic_clear(&config_pending);
    }
    k_mutex_unlock(&io_lock);
    if (atomic_set(&fault, 0) || (online && (now - last_received > 1500 ||
        (waiting && outgoing.kind == LISM_ESB_EVENT &&
         now - pending_since > (pending_relative ? 80 : 250))))) {
        drop_session(); bt_conn_foreach(BT_CONN_TYPE_LE, disconnect_control, NULL);
    }
}
bool lism_esb_transport_plan(struct lism_esb_radio_plan *plan, int64_t now) {
    if (!enabled || !online || atomic_get(&radio_failed) || now - last_sent < 4) return false;
    if (!waiting) {
        struct queued_event q; bool found = has_deferred_event;
        if (found) { q = deferred_event; has_deferred_event = false; }
        else found = !k_msgq_get(&events, &q, K_NO_WAIT);
        if (!found && now - last_sent < 100) return false;
        if (!next_sequence || next_sequence == UINT32_MAX) { atomic_set(&fault, 1); return false; }
        outgoing = (struct lism_esb_message){.sequence = next_sequence++, .length = 4,
            .kind = found ? LISM_ESB_EVENT : LISM_ESB_HEARTBEAT};
        lism_esb_put_u32(outgoing.body, command_ack);
        pending_relative = false;
        while (found) {
            bool relative = q.event.type == ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT &&
                            q.event.data.input_event.type == INPUT_EV_REL;
            /* A dropped half of an input sync group could leave old deltas
             * pending. Expiry fences the entire session instead. */
            if (relative && now - q.time > 80) { atomic_set(&fault, 1); return false; }
            uint8_t encoded[16]; int n = lism_esb_encode_event(&q.event, encoded);
            if (n < 0) { atomic_set(&fault, 1); return false; }
            if ((size_t)outgoing.length + 1 + (size_t)n > sizeof(outgoing.body)) {
                deferred_event = q; has_deferred_event = true; break;
            }
            outgoing.body[outgoing.length++] = n;
            memcpy(outgoing.body + outgoing.length, encoded, n); outgoing.length += n;
            pending_relative |= relative;
            found = !k_msgq_get(&events, &q, K_NO_WAIT);
        }
        waiting = true; pending_since = now;
    }
    int n = lism_esb_seal(&session, &outgoing, outgoing_packet.data);
    if (n < 0) { atomic_set(&fault, 1); return false; }
    outgoing_packet.length = n; outgoing_packet.pipe = session.slot; outgoing_packet.noack = false;
    plan->packets[0] = outgoing_packet; plan->count = 1;
    memcpy(plan->base0, session.base0, 4); memcpy(plan->base1, session.base1, 4);
    plan->channel = session.channel; last_sent = now;
    return true;
}
void lism_esb_transport_error(int error) {
    ARG_UNUSED(error);
    LOG_ERR("ESB radio stopped (%d)", error);
    atomic_set(&radio_failed, 1); drop_session();
    bt_conn_foreach(BT_CONN_TYPE_LE, disconnect_control, NULL);
}
static int report_event(const struct zmk_split_transport_peripheral_event *event) {
    int rc = 0;
    k_mutex_lock(&event_lock, K_FOREVER);
    if (event->type == ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT) {
        uint8_t pos = event->data.key_position_event.position;
        if (pos >= 128) { k_mutex_unlock(&event_lock); return -EINVAL; }
        if (event->data.key_position_event.pressed) held[pos / 8] |= BIT(pos % 8);
        else held[pos / 8] &= ~BIT(pos % 8);
    } else if (event->type == ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT) {
        battery = MIN(100, event->data.battery_event.level);
    } else if (event->type == ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT &&
               event->data.input_event.type == INPUT_EV_KEY) {
        unsigned free_slot = ARRAY_SIZE(input_keys), selected = ARRAY_SIZE(input_keys);
        for (unsigned i = 0; i < ARRAY_SIZE(input_keys); ++i) {
            if (!input_keys[i].pressed) free_slot = i;
            if (input_keys[i].code == event->data.input_event.code &&
                input_keys[i].reg == event->data.input_event.reg) selected = i;
        }
        if (selected == ARRAY_SIZE(input_keys)) selected = free_slot;
        if (selected == ARRAY_SIZE(input_keys)) { atomic_set(&fault, 1); rc = -ENOBUFS; }
        else input_keys[selected] = (struct input_key){.reg = event->data.input_event.reg,
            .code = event->data.input_event.code, .pressed = event->data.input_event.value != 0};
    }
    if (online && INPUT_ALLOWED() && !rc) {
        struct queued_event q = {.event = *event, .time = k_uptime_get()};
        rc = k_msgq_put(&events, &q, K_NO_WAIT);
        if (rc) atomic_set(&fault, 1);
    }
    k_mutex_unlock(&event_lock); return rc;
}
static int set_enabled(bool value) { enabled = value; return 0; }
static struct zmk_split_transport_status get_status(void) {
    return (struct zmk_split_transport_status){.available = true, .enabled = enabled,
        .connections = atomic_get(&status_connected) ? ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_ALL_CONNECTED :
                                ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_DISCONNECTED};
}
static const struct zmk_split_transport_peripheral_api api = {
    .report_event = report_event, .set_enabled = set_enabled, .get_status = get_status,
};
ZMK_SPLIT_TRANSPORT_PERIPHERAL_REGISTER(lism_esb_peripheral, &api, 0);
static int start_control(void) { k_work_reschedule(&config_work, K_MSEC(500)); return 0; }
SYS_INIT(start_control, APPLICATION, 99);

#if defined(CONFIG_LISM_STANDALONE)
int lism_standalone_esb_report(const struct zmk_split_transport_peripheral_event *event) {return report_event(event);}
void lism_standalone_esb_enable(bool value) {
    enabled=value;
    if(!value){atomic_clear(&ack_valid);atomic_set(&control_lost,1);}
}
void lism_standalone_esb_gate(bool run) {
    k_mutex_lock(&event_lock,K_FOREVER);
    bool previous=atomic_set(&input_gate,run);
    if(run && !previous && online) {
        /* Resnapshot current held keys, never replay movement from probing. */
        for(unsigned p=0;p<128;p++)if(held[p/8]&BIT(p%8)) {
            struct queued_event q={.time=k_uptime_get(),.event={
                .type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,
                .data.key_position_event={.position=p,.pressed=true}}};
            if(k_msgq_put(&events,&q,K_NO_WAIT))atomic_set(&fault,1);
        }
        enqueue_state();
    }
    if(!run){k_msgq_purge(&events);has_deferred_event=false;}
    k_mutex_unlock(&event_lock);
}
#endif
