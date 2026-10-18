/* SPDX-License-Identifier: MIT */
#include "radio.h"
#include "codec.h"
#include "integration.h"
#include "../lism_split_sets.h"
#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(lism_esb, CONFIG_ZMK_LOG_LEVEL);

#define LEASE_MS 1500
struct peer {
    struct lism_esb_session session;
    uint32_t generation, received, ack_sequence, command_sequence;
    struct zmk_split_transport_central_command commands[4];
    uint8_t command_head, command_count, snapshot_cursor;
    uint8_t snapshot[16];
    uint8_t batch[LISM_ESB_BODY], batch_length, batch_cursor;
    int64_t last_seen;
    bool provisioned, synced, dirty;
    struct esb_payload ack;
};
static struct peer peers[6];
static atomic_t ready_generation[6], radio_failed;
static uint8_t network[8];
static bool network_ready, enabled;
static K_MUTEX_DEFINE(peer_lock);
static struct bt_uuid_128 config_uuid = BT_UUID_INIT_128(LISM_ESB_UUID);

void lism_esb_peer_ready(uint8_t source) {
    if (lism_esb_keyboard_slot(source))
        atomic_set(&ready_generation[source], lism_split_sets_generation(source));
}
static void fail_peer(uint8_t source) {
    peers[source].provisioned = false;
    memset(peers[source].session.key, 0, 16);
    lism_esb_queue_fault(source);
    LOG_WRN("ESB source %u: cleanup queued", source);
}
static ssize_t read_config(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                            void *buf, uint16_t len, uint16_t offset) {
    int source = lism_split_sets_find_addr(bt_conn_get_dst(conn));
    if (source < 0 || !lism_esb_keyboard_slot(source) ||
        bt_conn_get_security(conn) < BT_SECURITY_L2 || atomic_get(&radio_failed))
        return BT_GATT_ERR(BT_ATT_ERR_AUTHORIZATION);
    uint32_t generation = lism_split_sets_generation(source);
    if ((uint32_t)atomic_get(&ready_generation[source]) != generation ||
        !lism_split_sets_dispatch(source, generation)) return BT_GATT_ERR(BT_ATT_ERR_UNLIKELY);
    uint8_t wire[LISM_ESB_CONFIG_SIZE];
    k_mutex_lock(&peer_lock, K_FOREVER);
    struct peer *p = &peers[source];
    if (!network_ready) {
        if (bt_rand(network, sizeof(network))) goto failed;
        /* Avoid ESB's invalid all-zero address/low-byte combinations. */
        network[0] |= 0x81; network[4] |= 0x81; network_ready = true;
    }
    if (p->generation != generation || !p->provisioned) {
        if (offset) goto failed;
        memset(p, 0, sizeof(*p)); p->generation = generation;
        p->session.slot = source;
        if (bt_rand(&p->session.id, sizeof(p->session.id)) ||
            bt_rand(p->session.key, sizeof(p->session.key))) goto failed;
        if (!p->session.id) goto failed;
        memcpy(p->session.base0, network, 4); memcpy(p->session.base1, network + 4, 4);
        p->session.channel = CONFIG_LISM_SPLIT_ESB_CHANNEL;
        p->last_seen = k_uptime_get(); p->provisioned = p->dirty = true;
        p->command_sequence = 1;
        LOG_INF("ESB source %u: encrypted session provisioned", source);
    }
    int rc = lism_esb_config_encode(&p->session, wire);
    k_mutex_unlock(&peer_lock);
    if (rc < 0) return BT_GATT_ERR(BT_ATT_ERR_UNLIKELY);
    return bt_gatt_attr_read(conn, attr, buf, len, offset, wire, sizeof(wire));
failed:
    k_mutex_unlock(&peer_lock);
    return BT_GATT_ERR(BT_ATT_ERR_UNLIKELY);
}
/* This characteristic is not advertised independently. Only the existing
 * encrypted, registered split connection can obtain a fresh session key. */
BT_GATT_SERVICE_DEFINE(lism_esb_config_service,
    BT_GATT_PRIMARY_SERVICE(&config_uuid),
    BT_GATT_CHARACTERISTIC(&config_uuid.uuid, BT_GATT_CHRC_READ,
        BT_GATT_PERM_READ_ENCRYPT, read_config, NULL, NULL));

static int queue_snapshot(struct peer *p, const uint8_t *bitmap) {
    if (!p->snapshot_cursor) memcpy(p->snapshot, bitmap, 16);
    else if (memcmp(p->snapshot, bitmap, 16)) return -EINVAL;
    for (; p->snapshot_cursor < 128; ++p->snapshot_cursor) {
        unsigned pos = p->snapshot_cursor;
        if (!(bitmap[pos / 8] & BIT(pos % 8))) continue;
        struct zmk_split_transport_peripheral_event ev = {
            .type = ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,
            .data.key_position_event = {.position = pos, .pressed = true}};
        int rc = lism_esb_queue_event(p->session.slot, p->generation, &ev);
        if (rc) return rc;
    }
    p->synced = true; return 0;
}
static int queue_batch(struct peer *p, const struct lism_esb_message *m) {
    struct zmk_split_transport_peripheral_event event;
    /* Validate the WHOLE batch before exposing its first event. */
    for (unsigned offset = 4; offset < m->length;) {
        uint8_t n = m->body[offset++];
        if (!n || offset + n > m->length ||
            lism_esb_decode_event(m->body + offset, n, &event)) return -EINVAL;
        if (event.type == ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT &&
            event.data.input_event.reg != (p->session.slot & 1)) return -EINVAL;
        offset += n;
    }
    if (!p->batch_cursor) {
        memcpy(p->batch, m->body, m->length); p->batch_length = m->length; p->batch_cursor = 4;
    } else if (p->batch_length != m->length || memcmp(p->batch, m->body, m->length)) return -EINVAL;
    while (p->batch_cursor < m->length) {
        uint8_t n = m->body[p->batch_cursor];
        lism_esb_decode_event(m->body + p->batch_cursor + 1, n, &event);
        int rc = lism_esb_queue_event(p->session.slot, p->generation, &event);
        if (rc) return rc;
        p->batch_cursor += n + 1;
    }
    p->batch_cursor = 0; return 0;
}
void lism_esb_transport_receive(const struct esb_payload *packet, int64_t now) {
    uint8_t source = packet->pipe;
    if (!lism_esb_keyboard_slot(source)) return;
    k_mutex_lock(&peer_lock, K_FOREVER);
    struct peer *p = &peers[source]; struct lism_esb_message message;
    if (!p->provisioned || !lism_split_sets_dispatch(source, p->generation) ||
        lism_esb_open(&p->session, packet->data, packet->length, &message) ||
        message.kind == LISM_ESB_ACK || message.length < 4) goto done;
    /* Authenticated old/reordered packets may not extend the liveness lease. */
    if (message.sequence < p->received || message.sequence > p->received + 1) goto done;
    if (message.sequence == p->received) goto done;
    uint32_t command_ack = lism_esb_u32(message.body);
    if (command_ack > p->command_sequence) goto done;
    int rc = 0;
    if (!p->synced) {
        if (message.sequence != 1 || message.kind != LISM_ESB_SNAPSHOT || message.length != 20)
            goto done;
        rc = queue_snapshot(p, message.body + 4);
    } else if (message.kind == LISM_ESB_EVENT) {
        rc = message.length > 4 ? queue_batch(p, &message) : -EINVAL;
    } else if (message.kind != LISM_ESB_HEARTBEAT || message.length != 4) rc = -EINVAL;
    if (rc == -EAGAIN || rc == -ENOMSG) goto done; /* retry SAME immutable frame */
    if (rc) { fail_peer(source); goto done; }
    p->last_seen = now; p->received = message.sequence; p->dirty = true;
    if (p->command_count && command_ack == p->command_sequence) {
        p->command_head = (p->command_head + 1) % ARRAY_SIZE(p->commands);
        --p->command_count;
        if (++p->command_sequence == 0) fail_peer(source);
    }
done:
    k_mutex_unlock(&peer_lock);
}
void lism_esb_transport_poll(int64_t now) {
    lism_esb_control_start();
    k_mutex_lock(&peer_lock, K_FOREVER);
    for (uint8_t source = 0; source < 6; ++source) {
        struct peer *p = &peers[source];
        if (!p->provisioned) continue;
        if (!lism_split_sets_dispatch(source, p->generation)) {
            p->provisioned = false; memset(p->session.key, 0, 16);
        } else if (now - p->last_seen >= LEASE_MS) fail_peer(source);
    }
    k_mutex_unlock(&peer_lock);
}
bool lism_esb_transport_plan(struct lism_esb_radio_plan *plan, int64_t now) {
    ARG_UNUSED(now);
    if (!enabled || atomic_get(&radio_failed)) return false;
    k_mutex_lock(&peer_lock, K_FOREVER);
    plan->count = 0;
    for (uint8_t source = 0; source < 6; ++source) {
        struct peer *p = &peers[source];
        if (!p->provisioned) continue;
        if (p->dirty) {
            struct lism_esb_message m = {.kind = LISM_ESB_ACK, .length = 9,
                                        .sequence = ++p->ack_sequence};
            if (!m.sequence) { fail_peer(source); continue; }
            lism_esb_put_u32(m.body, p->received);
            lism_esb_put_u32(m.body + 4, p->command_count ? p->command_sequence : 0);
            m.body[8] = 0;
            if (p->command_count) {
                int n = lism_esb_encode_command(&p->commands[p->command_head], m.body + 9);
                if (n < 0) { fail_peer(source); continue; }
                m.body[8] = n; m.length += n;
            }
            int n = lism_esb_seal(&p->session, &m, p->ack.data);
            if (n < 0) { fail_peer(source); continue; }
            p->ack.length = n; p->ack.pipe = source; p->ack.noack = false; p->dirty = false;
        }
        plan->packets[plan->count++] = p->ack;
    }
    memcpy(plan->base0, network, 4); memcpy(plan->base1, network + 4, 4);
    plan->channel = CONFIG_LISM_SPLIT_ESB_CHANNEL;
    k_mutex_unlock(&peer_lock);
    return plan->count > 0;
}
void lism_esb_transport_error(int error) {
    ARG_UNUSED(error);
    LOG_ERR("ESB radio stopped (%d)", error);
    atomic_set(&radio_failed, 1);
    k_mutex_lock(&peer_lock, K_FOREVER);
    for (unsigned i = 0; i < 6; ++i) if (peers[i].provisioned) fail_peer(i);
    k_mutex_unlock(&peer_lock);
}
static int send_command(uint8_t source, struct zmk_split_transport_central_command command) {
    if (!lism_esb_keyboard_slot(source)) return lism_esb_control_command(source, command);
    uint8_t encoded[32]; int n = lism_esb_encode_command(&command, encoded);
    if (n < 0) return n;
    k_mutex_lock(&peer_lock, K_FOREVER);
    struct peer *p = &peers[source]; int rc = 0;
    if (!p->provisioned) rc = -ENOTCONN;
    else if (p->command_count == ARRAY_SIZE(p->commands)) { fail_peer(source); rc = -ENOBUFS; }
    else {
        p->commands[(p->command_head + p->command_count) % ARRAY_SIZE(p->commands)] = command;
        ++p->command_count; p->dirty = true;
    }
    k_mutex_unlock(&peer_lock); return rc;
}
static int get_sources(uint8_t *sources) {
    int count = 0;
    k_mutex_lock(&peer_lock, K_FOREVER);
    for (unsigned i = 0; i < 6; ++i) if (peers[i].provisioned && peers[i].synced) sources[count++] = i;
    k_mutex_unlock(&peer_lock);
    uint8_t ble_sources[6]; int n = lism_esb_control_sources(ble_sources);
    for (int i = 0; i < n; ++i)
        if (!lism_esb_keyboard_slot(ble_sources[i])) sources[count++] = ble_sources[i];
    return count;
}
static int set_enabled(bool value) { enabled = value; return 0; }
static struct zmk_split_transport_status get_status(void) {
    uint8_t sources[6]; int n = get_sources(sources);
    return (struct zmk_split_transport_status){.available = true, .enabled = enabled,
        .connections = n ? ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_SOME_CONNECTED :
                           ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_DISCONNECTED};
}
static const struct zmk_split_transport_central_api api = {
    .send_command = send_command, .get_available_source_ids = get_sources,
    .set_enabled = set_enabled, .get_status = get_status,
};
ZMK_SPLIT_TRANSPORT_CENTRAL_REGISTER(lism_esb_central, &api, 0);
