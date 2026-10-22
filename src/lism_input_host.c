/* Standard BLE HOGP client. Two independent sessions share the existing sole
 * split scanner. Bluetooth callbacks copy bounded events, except installation
 * of the connection-local SMP overlay before the coordinator starts security.
 * Keyboard state is composed at the output boundary, never put into the local
 * keymap's non-reference-counted key state. No device/host bond is auto-erased. */
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/device.h>
#include <zephyr/init.h>
#include <zephyr/input/input.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/atomic.h>
#include <zmk/endpoints.h>
#include <zmk/hid.h>
#include <zmk/ble.h>
#include <zmk/events/endpoint_changed.h>
#include <zmk/events/usb_conn_state_changed.h>
#include <zmk/events/ble_active_profile_changed.h>
#include "lism_input_host.h"
#include "lism_input_parser.h"
#include "lism_input_merge.h"
#include "lism_input_security.h"
#include "xiao147_ui.h"
#include "lism_screen.h"

LOG_MODULE_REGISTER(lism_input_host, LOG_LEVEL_INF);
#define SERVICES 2
#define REPORTS 16
#define AD_CACHE 16
#define KICK 0x7ffd
#define SETUP_TIMEOUT 30000
enum stage { IDLE, CONNECTING, SECURITY, SERVICES_FIND, CHARS, MAP, DESCRIPTORS,
    REFERENCE, SUBSCRIBE, READY, CLOSING };
enum ev_type { CONNECTED, DISCONNECTED, SECURED, DISCOVERED, READ, SUBSCRIBED,
    UNSUBSCRIBED, AUTH, SETTINGS_READY, SMP_RECEIVED, SMP_BLOCKED, SMP_FAILED, IDENTITY_RESOLVED };
enum cmd_type { SEARCH, CANCEL, SELECT, FORGET, RETRY, AUTH_REPLY, LEGACY };
struct command { uint32_t token, passkey, ui_epoch, switch_epoch; uint8_t type, slot, index; bool accept; };
struct saved_peer { uint8_t version; bt_addr_le_t addr; char name[24]; };
/* Separate record preserves the exact v1/v2 peer ABI and makes migration strict.
 * An opt-in is persisted only after READY and a verified bond. */
struct legacy_grant { uint8_t version, allowed; bt_addr_le_t addr; };
struct discovery_op { struct bt_gatt_discover_params params; uint32_t epoch;
    uint8_t slot, stage, report; };
struct read_op { struct bt_gatt_read_params params; uint32_t epoch;
    uint16_t received; uint8_t slot, stage, report; };
struct report { struct bt_gatt_subscribe_params sub; struct bt_conn *conn;
    uint32_t epoch; uint16_t value, end, ccc, reference;
    uint8_t slot, service, id, type, properties, boot; uint32_t observed_lengths; };
struct service { uint16_t start, end, map_handle, mode_handle;
    uint8_t mode_properties; bool parsed, boot;
    struct lism_input_descriptor descriptor;
    struct lism_input_state state;
    struct lism_input_event held;
};
struct security_detail {
    uint8_t opcode, io, auth, key_size, block, raw_opcode, raw_reason;
    bool features, local_failure, peer_failure;
};
struct session { struct bt_conn *conn; bt_addr_le_t target; struct saved_peer saved;
    struct security_detail security;
    bool have_saved, pending, suspended, operation, map_done, security_started, forget;
    bool target_known;
    bool legacy_available, legacy_allowed, legacy_used, legacy_revoked;
    bt_addr_le_t legacy_target;
    uint32_t legacy_token;
    uint8_t stage, service_count, service_index, report_count, report_index, subscribed;
    uint8_t discover_next, read_next, reference[2], reference_len, chars;
    uint16_t map_len; uint32_t deadline, retry_at, seen;
    struct service services[SERVICES]; struct report reports[REPORTS];
    struct discovery_op discovers[2]; struct read_op reads[2];
    uint8_t map[LISM_INPUT_MAX_MAP_BYTES];
    atomic_t epoch, input_ready, fault, closed, clear, security_lost;
    uint8_t own_buttons;
};
struct candidate { bt_addr_le_t addr; struct lism_hid_candidate public;
    uint32_t seen, connectable_seen; bool connectable, hint, name_hint, complete; };
struct advertisement { bt_addr_le_t addr; uint32_t seen; int8_t rssi;
    bool connectable, hint, complete; char name[24]; };
struct event { struct bt_conn *conn; uint32_t epoch, passkey;
    bt_addr_le_t identity, rpa;
    uint16_t handle, end, value, uuid; int16_t result;
    uint8_t type, slot, stage, report, properties, error, level, length, auth;
    bool complete; uint8_t data[64]; };
struct frame { uint32_t epoch, generation; int endpoint;
    uint8_t slot, service, id, boot, len; uint8_t data[LISM_INPUT_MAX_REPORT_BYTES]; };
static struct session sessions[LISM_INPUT_PEERS];
static struct candidate candidates[LISM_HID_CANDIDATES], cache[AD_CACHE];
static struct saved_peer loaded[LISM_INPUT_PEERS], legacy;
static struct legacy_grant loaded_grants[LISM_INPUT_PEERS];
static bool loaded_grant_valid[LISM_INPUT_PEERS];
static bool loaded_valid[LISM_INPUT_PEERS], legacy_valid, settings_ready, show_other;
static struct lism_input_status status;
static uint32_t search_until, next_token, prompt_until;
static uint8_t search_slot;
static atomic_t scan_needed, scan_paused, advertisements_enabled, queue_fault;
/* Deliberately OFF after boot; pairing records are retained. */
static atomic_t inputs_enabled,requested_enabled;
/* Every OFF is a fence, even if ON follows before the worker runs. */
static atomic_t disable_generation;
static uint32_t applied_disable_generation;
static struct k_spinlock enable_lock;
static atomic_t endpoint_changed, endpoint_generation;
static atomic_t endpoint_frozen;
static atomic_t observed_endpoint=ATOMIC_INIT(-1);
static int16_t outgoing_pointer[4]; /* report_lock + sole bridge thread */
static struct k_spinlock identity_lock;
static struct { struct bt_conn *conn; bt_addr_le_t saved, target; uint32_t epoch; bool have_saved, target_valid; }
    identities[LISM_INPUT_PEERS];
static struct { struct bt_conn *conn; bt_addr_le_t target, resolved; uint32_t epoch;
    bool allowed, pending, resolved_valid; } legacy_policy[LISM_INPUT_PEERS];
K_MUTEX_DEFINE(status_lock);
K_MUTEX_DEFINE(report_lock);
K_MSGQ_DEFINE(commands, sizeof(struct command), 8, 4);
K_MSGQ_DEFINE(events, sizeof(struct event), 16, 4);
K_MSGQ_DEFINE(advertisements, sizeof(struct advertisement), 8, 4);
K_MSGQ_DEFINE(frames, sizeof(struct frame), 16, 4);
static void control(struct k_work *work);
static void tick(struct k_work *work);
K_WORK_DEFINE(control_work, control);
K_WORK_DELAYABLE_DEFINE(tick_work, tick);
static void scanner_state(void);
static void close_session(unsigned slot, const char *why);
static const struct bt_conn_auth_cb auth_callbacks;
extern bool lism_input_split_identity(const bt_addr_le_t *addr);
/* Generated role registry accessor: nonblocking, synchronized snapshots of all
 * split and computer identities. Never call a mutex-based registry in SMP. */
extern bool lism_input_protected_identity(const bt_addr_le_t *addr);
DEVICE_DEFINE(lism_input_device, "LisM BLE inputs", NULL, NULL, NULL, NULL,
              POST_KERNEL, CONFIG_INPUT_INIT_PRIORITY, NULL);
static bool elapsed(uint32_t now, uint32_t when) { return (int32_t)(now - when) >= 0; }
static void kick(void) {
    input_report(DEVICE_GET(lism_input_device), INPUT_EV_MSC, KICK, 0, true, K_NO_WAIT);
}
static void message(int slot, const char *text) {
    k_mutex_lock(&status_lock, K_FOREVER);
    if (slot >= 0) { snprintf(status.peers[slot].message, 48, "%s", text); }
    snprintf(status.message, sizeof(status.message), "%s", text);
    k_mutex_unlock(&status_lock);
    /* Never log reports, PINs or passkey values. */
    LOG_INF("Input %d: %s", slot, text);
}
static bool identity_conflict(const bt_addr_le_t *addr,unsigned slot) {
    if (!addr || !bt_addr_le_cmp(addr,BT_ADDR_LE_ANY) || lism_input_split_identity(addr)) { return true; }
    for (unsigned i=0;i < ZMK_BLE_PROFILE_COUNT;i++) {
        const bt_addr_le_t *host=zmk_ble_profile_address(i);
        if (host && !bt_addr_le_cmp(host,addr)) { return true; }
    }
    for (unsigned i=0;i < LISM_INPUT_PEERS;i++) {
        if (i == slot) { continue; }
        const struct session *s=&sessions[i];
        if ((s->have_saved && !bt_addr_le_cmp(addr,&s->saved.addr)) ||
            (s->target_known && !bt_addr_le_cmp(addr,&s->target))) { return true; }
    }
    return false;
}
static void publish(void) {
    k_spinlock_key_t key = k_spin_lock(&identity_lock);
    for (unsigned i = 0; i < LISM_INPUT_PEERS; i++) {
        identities[i].conn = sessions[i].conn;
        identities[i].have_saved = sessions[i].have_saved;
        identities[i].target_valid = sessions[i].pending || sessions[i].conn;
        identities[i].saved = sessions[i].saved.addr;
        identities[i].target = sessions[i].target;
        identities[i].epoch=atomic_get(&sessions[i].epoch);
        struct session *s=&sessions[i];
        bool active=s->stage == CONNECTING || s->stage == SECURITY ||
                    (s->stage >= SERVICES_FIND && s->stage <= READY);
        bool allowed=active && s->legacy_allowed && !s->legacy_revoked &&
                     !bt_addr_le_cmp(&s->target,&s->legacy_target);
        /* A connected callback can run inside create(), before s->conn is set.
         * Preserve its exact binding only for the same epoch and identity. */
        bool same_mapping=legacy_policy[i].epoch == (uint32_t)atomic_get(&s->epoch) &&
            (!bt_addr_le_cmp(&legacy_policy[i].target,&s->target) ||
             (legacy_policy[i].resolved_valid && !bt_addr_le_cmp(&legacy_policy[i].resolved,&s->target)));
        if (!allowed || !same_mapping) {
            legacy_policy[i].conn=NULL;
            legacy_policy[i].resolved_valid=false;
        }
        if (allowed && s->conn) { legacy_policy[i].conn=s->conn; }
        legacy_policy[i].allowed=allowed;
        legacy_policy[i].pending=allowed && s->pending;
        /* A synchronous identity callback can precede its coordinator event.
         * Keep that exact conn+epoch alias until the event applies it. */
        if (legacy_policy[i].resolved_valid && !bt_addr_le_cmp(&legacy_policy[i].resolved,&s->target)) {
            legacy_policy[i].resolved_valid=false;
        }
        if (!legacy_policy[i].resolved_valid) { legacy_policy[i].target=s->target; }
        legacy_policy[i].epoch=atomic_get(&s->epoch);
    }
    k_spin_unlock(&identity_lock, key);
}
static void bind_legacy_connection(struct bt_conn *conn,unsigned slot) {
    struct bt_conn_info info;
    if (bt_conn_get_info(conn,&info) || info.type != BT_CONN_TYPE_LE || info.role != BT_CONN_ROLE_CENTRAL) { return; }
    k_spinlock_key_t key=k_spin_lock(&identity_lock);
    if (!identities[slot].conn && identities[slot].target_valid &&
        identities[slot].epoch == (uint32_t)atomic_get(&sessions[slot].epoch) &&
        !bt_addr_le_cmp(bt_conn_get_dst(conn),&identities[slot].target)) {
        identities[slot].conn=conn;
    }
    if (legacy_policy[slot].allowed && legacy_policy[slot].pending &&
        legacy_policy[slot].epoch == (uint32_t)atomic_get(&sessions[slot].epoch) &&
        !bt_addr_le_cmp(bt_conn_get_dst(conn),&legacy_policy[slot].target)) {
        legacy_policy[slot].conn=conn; legacy_policy[slot].pending=false;
    }
    k_spin_unlock(&identity_lock,key);
}
bool lism_input_legacy_key_allowed(uint8_t id,const bt_addr_le_t *addr) {
    if (!IS_ENABLED(CONFIG_LISM_INPUT_LEGACY_COMPAT) || id != BT_ID_DEFAULT || !addr) return false;
    bool allowed=false;
    k_spinlock_key_t key=k_spin_lock(&identity_lock);
    for (unsigned i=0;i<LISM_INPUT_PEERS;i++) {
        const bt_addr_le_t *exact=legacy_policy[i].resolved_valid ?
            &legacy_policy[i].resolved : &legacy_policy[i].target;
        if (legacy_policy[i].allowed && legacy_policy[i].conn &&
            legacy_policy[i].epoch == (uint32_t)atomic_get(&sessions[i].epoch) &&
            !bt_addr_le_cmp(addr,exact)) { allowed=true; break; }
    }
    k_spin_unlock(&identity_lock,key);
    return allowed;
}
bool lism_input_non_split_identity(const bt_addr_le_t *addr) {
#if !defined(CONFIG_LISM_INPUT_LEGACY_COMPAT) || !CONFIG_LISM_INPUT_LEGACY_COMPAT
    ARG_UNUSED(addr);return true;
#else
    if (!addr || lism_input_computer_identity(addr))return true;
    bool found=false;
    k_spinlock_key_t key=k_spin_lock(&identity_lock);
    for(unsigned i=0;i<LISM_INPUT_PEERS;i++) {
        found |= (identities[i].have_saved && !bt_addr_le_cmp(addr,&identities[i].saved)) ||
                 (identities[i].target_valid && !bt_addr_le_cmp(addr,&identities[i].target)) ||
                 (legacy_policy[i].resolved_valid && !bt_addr_le_cmp(addr,&legacy_policy[i].resolved));
    }
    k_spin_unlock(&identity_lock,key);return found;
#endif
}
bool lism_input_legacy_allowed(struct bt_conn *conn) {
#if !defined(CONFIG_LISM_INPUT_LEGACY_COMPAT) || !CONFIG_LISM_INPUT_LEGACY_COMPAT
    ARG_UNUSED(conn); return false;
#else
    struct bt_conn_info info;
    if (!conn || bt_conn_get_info(conn,&info) || info.type != BT_CONN_TYPE_LE || info.role != BT_CONN_ROLE_CENTRAL) { return false; }
    bool allowed=false;
    k_spinlock_key_t key=k_spin_lock(&identity_lock);
    for (unsigned i=0;i < LISM_INPUT_PEERS;i++) {
        if (legacy_policy[i].allowed && legacy_policy[i].conn == conn &&
            legacy_policy[i].epoch == (uint32_t)atomic_get(&sessions[i].epoch) &&
            !bt_addr_le_cmp(bt_conn_get_dst(conn),legacy_policy[i].resolved_valid ?
                           &legacy_policy[i].resolved : &legacy_policy[i].target)) { allowed=true; break; }
    }
    k_spin_unlock(&identity_lock,key);
    return allowed;
#endif
}
bool lism_input_identity_allowed(struct bt_conn *conn,const bt_addr_le_t *addr) {
#if !defined(CONFIG_LISM_INPUT_LEGACY_COMPAT) || !CONFIG_LISM_INPUT_LEGACY_COMPAT
    ARG_UNUSED(conn); ARG_UNUSED(addr); return true;
#else
    if (!conn || !addr) { return false; }
    bool protected=lism_input_protected_identity(addr), owned=false, conflict=false;
    k_spinlock_key_t key=k_spin_lock(&identity_lock);
    for (unsigned i=0;i < LISM_INPUT_PEERS;i++) {
        if (identities[i].conn != conn) { continue; }
        owned=true;
        conflict=protected || !bt_addr_le_cmp(addr,BT_ADDR_LE_ANY);
        for (unsigned j=0;j < LISM_INPUT_PEERS;j++) {
            if (i == j) { continue; }
            conflict |= (identities[j].have_saved && !bt_addr_le_cmp(addr,&identities[j].saved)) ||
                        (identities[j].target_valid && !bt_addr_le_cmp(addr,&identities[j].target)) ||
                        (legacy_policy[j].resolved_valid && !bt_addr_le_cmp(addr,&legacy_policy[j].resolved));
        }
        break;
    }
    k_spin_unlock(&identity_lock,key);
    return !owned || !conflict;
#endif
}
static void legacy_status(unsigned slot) {
    struct session *s=&sessions[slot];
    k_mutex_lock(&status_lock,K_FOREVER);
    status.peers[slot].legacy_available=s->legacy_available;
    status.peers[slot].legacy_allowed=s->legacy_allowed;
    status.peers[slot].legacy_used=s->legacy_used;
    status.peers[slot].legacy_token=s->legacy_token;
    status.generation++;
    k_mutex_unlock(&status_lock);
}
static void legacy_token_refresh(unsigned slot) {
    sessions[slot].legacy_token=++next_token;
    if (!sessions[slot].legacy_token) { sessions[slot].legacy_token=++next_token; }
    legacy_status(slot);
}
static int owned_connection(struct bt_conn *conn) {
    int slot = -1;
    k_spinlock_key_t key = k_spin_lock(&identity_lock);
    for (unsigned i = 0; i < LISM_INPUT_PEERS; i++) {
        if (conn && (identities[i].conn == conn || (!identities[i].conn &&
            identities[i].target_valid &&
            !bt_addr_le_cmp(bt_conn_get_dst(conn), &identities[i].target)))) { slot = i; break; }
    }
    k_spin_unlock(&identity_lock, key);
    return slot;
}
bool lism_hid_host_claim_ad(const bt_addr_le_t *addr) {
    bool owned = false;
    k_spinlock_key_t key = k_spin_lock(&identity_lock);
    for (unsigned i = 0; addr && i < LISM_INPUT_PEERS; i++) {
        owned |= (identities[i].have_saved && !bt_addr_le_cmp(addr, &identities[i].saved)) ||
                 (identities[i].target_valid && !bt_addr_le_cmp(addr, &identities[i].target));
    }
    k_spin_unlock(&identity_lock, key);
    return owned;
}
bool lism_hid_host_scan_needed(void) { return atomic_get(&scan_needed); }
bool lism_hid_host_scan_paused(void) { return atomic_get(&scan_paused); }
static bool queue_event(struct event *event, struct bt_conn *conn) {
    bool diagnostic=event->type == SMP_RECEIVED || event->type == SMP_BLOCKED || event->type == SMP_FAILED;
    /* Diagnostic metadata is best effort. Reserve space for actual connection,
     * auth and GATT events; a dropped observation must never fail pairing. */
    if (diagnostic && event->slot < LISM_INPUT_PEERS && k_msgq_num_free_get(&events) < 8) {
        atomic_set(&sessions[event->slot].security_lost,1); return false;
    }
    event->conn = conn ? bt_conn_ref(conn) : NULL;
    if (k_msgq_put(&events, event, K_NO_WAIT)) {
        if (event->conn) { bt_conn_unref(event->conn); }
        if (diagnostic) {
            if (event->slot < LISM_INPUT_PEERS) { atomic_set(&sessions[event->slot].security_lost,1); }
            return false;
        }
        if (event->slot < LISM_INPUT_PEERS) {
            struct session *s = &sessions[event->slot];
            if (event->type == DISCONNECTED || (event->type == CONNECTED && event->error)) {
                atomic_set(&s->closed, 1);
            }
            atomic_set(&s->fault, 1);
        } else { atomic_set(&queue_fault, 1); }
        k_work_submit(&control_work); return false;
    }
    k_work_submit(&control_work); return true;
}
static uint16_t short_uuid(const struct bt_uuid *uuid) {
    return uuid && uuid->type == BT_UUID_TYPE_16 ? BT_UUID_16(uuid)->val : 0;
}
static uint8_t discovered(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                          struct bt_gatt_discover_params *params) {
    struct discovery_op *op = CONTAINER_OF(params, struct discovery_op, params);
    if (op->epoch != (uint32_t)atomic_get(&sessions[op->slot].epoch)) { return BT_GATT_ITER_STOP; }
    struct event event = {.type=DISCOVERED, .slot=op->slot, .epoch=op->epoch,
        .stage=op->stage, .report=op->report, .complete=attr == NULL};
    if (attr) {
        event.handle = attr->handle;
        if (op->stage == SERVICES_FIND) {
            event.end = ((const struct bt_gatt_service_val *)attr->user_data)->end_handle;
        } else if (op->stage == CHARS) {
            const struct bt_gatt_chrc *ch = attr->user_data;
            event.uuid=short_uuid(ch->uuid); event.value=ch->value_handle; event.properties=ch->properties;
        } else { event.uuid=short_uuid(attr->uuid); }
    }
    if (!queue_event(&event, conn)) { return BT_GATT_ITER_STOP; }
    return event.complete ? BT_GATT_ITER_STOP : BT_GATT_ITER_CONTINUE;
}
static uint8_t read_result(struct bt_conn *conn, uint8_t error,
    struct bt_gatt_read_params *params, const void *data, uint16_t length) {
    struct read_op *op = CONTAINER_OF(params, struct read_op, params);
    if (op->epoch != (uint32_t)atomic_get(&sessions[op->slot].epoch)) { return BT_GATT_ITER_STOP; }
    struct event event = {.type=READ, .slot=op->slot, .epoch=op->epoch,
        .stage=op->stage, .report=op->report, .error=error, .complete=!data};
    if (error || !data) { queue_event(&event,conn); return BT_GATT_ITER_STOP; }
    unsigned limit = op->stage == MAP ? LISM_INPUT_MAX_MAP_BYTES : 2;
    if (op->received > limit || length > limit - op->received) {
        event.error=0xff; queue_event(&event,conn); return BT_GATT_ITER_STOP;
    }
    op->received += length;
    for (unsigned at=0; at < length;) {
        event.length=MIN(sizeof(event.data),length-at);
        memcpy(event.data,(const uint8_t *)data+at,event.length);
        if (!queue_event(&event,conn)) { return BT_GATT_ITER_STOP; }
        at += event.length;
    }
    return BT_GATT_ITER_CONTINUE;
}
static uint8_t notification(struct bt_conn *conn, struct bt_gatt_subscribe_params *params,
                            const void *data, uint16_t length) {
    struct report *r=CONTAINER_OF(params,struct report,sub);
    struct session *s=&sessions[r->slot];
    if (conn != r->conn || r->epoch != (uint32_t)atomic_get(&s->epoch)) { return BT_GATT_ITER_STOP; }
    if (!data) {
        struct event event={.type=UNSUBSCRIBED,.slot=r->slot,.epoch=r->epoch};
        queue_event(&event,conn); return BT_GATT_ITER_STOP;
    }
    if (!atomic_get(&inputs_enabled) || !atomic_get(&s->input_ready) || atomic_get(&endpoint_frozen)) { return BT_GATT_ITER_CONTINUE; }
    if (!length || length > LISM_INPUT_MAX_REPORT_BYTES) {
        atomic_set(&s->fault,1); atomic_clear(&s->input_ready); k_work_submit(&control_work);
        return BT_GATT_ITER_STOP;
    }
    uint32_t format=BIT(MIN(length,31));
    if(!(r->observed_lengths&format)){
        r->observed_lengths|=format;
        LOG_INF("Input %u HID format service=%u report=%u bytes=%u boot=%u",r->slot,r->service,r->id,length,r->boot);
    }
    struct frame frame={.slot=r->slot,.service=r->service,.id=r->id,.boot=r->boot,
        .epoch=r->epoch,.generation=lism_input_merge_generation(),
        .endpoint=xiao147_pointer_endpoint(),.len=length};
    memcpy(frame.data,data,length);
    if (k_msgq_put(&frames,&frame,K_NO_WAIT)) {
        atomic_set(&s->fault,1); atomic_clear(&s->input_ready); k_work_submit(&control_work);
    } else { kick(); }
    return BT_GATT_ITER_CONTINUE;
}
static void subscribed_cb(struct bt_conn *conn,uint8_t error,struct bt_gatt_subscribe_params *params) {
    struct report *r=CONTAINER_OF(params,struct report,sub);
    struct event event={.type=SUBSCRIBED,.slot=r->slot,.epoch=r->epoch,.error=error,
        .report=(uint8_t)(r-sessions[r->slot].reports)};
    if (conn == r->conn) { queue_event(&event,conn); }
}
static void connected(struct bt_conn *conn,uint8_t error) {
    int slot=owned_connection(conn); if (slot < 0) { return; }
    struct event event={.type=CONNECTED,.slot=slot,.error=error};
    /* SMP channel exists here. Overlay never changes split or computer auth. */
    if (!error) {
        bind_legacy_connection(conn,slot);
        event.result=bt_conn_auth_cb_overlay(conn,&auth_callbacks);
    }
    queue_event(&event,conn);
}
static void disconnected(struct bt_conn *conn,uint8_t reason) {
    int slot=owned_connection(conn); if (slot < 0) { return; }
    atomic_clear(&sessions[slot].input_ready); atomic_set(&sessions[slot].clear,1); kick();
    struct event event={.type=DISCONNECTED,.slot=slot,.error=reason}; queue_event(&event,conn);
}
static void security_changed(struct bt_conn *conn,bt_security_t level,enum bt_security_err error) {
    int slot=owned_connection(conn); if (slot < 0) { return; }
    struct event event={.type=SECURED,.slot=slot,.level=level,.error=error,
        .epoch=atomic_get(&sessions[slot].epoch)};
    struct bt_conn_info info;
    event.result=bt_conn_get_info(conn,&info);
    if (!event.result) {
        event.complete=(info.security.flags & BT_SECURITY_FLAG_SC) != 0;
        event.length=info.security.enc_key_size;
    }
    queue_event(&event,conn);
}
static void identity_resolved(struct bt_conn *conn,const bt_addr_le_t *rpa,const bt_addr_le_t *identity) {
#if !defined(CONFIG_LISM_INPUT_LEGACY_COMPAT) || !CONFIG_LISM_INPUT_LEGACY_COMPAT
    ARG_UNUSED(conn); ARG_UNUSED(rpa); ARG_UNUSED(identity); return;
#else
    struct bt_conn_info info;
    if (!rpa || !identity || bt_conn_get_info(conn,&info) || info.type != BT_CONN_TYPE_LE ||
        info.role != BT_CONN_ROLE_CENTRAL || bt_addr_le_cmp(bt_conn_get_dst(conn),identity) ||
        !lism_input_identity_allowed(conn,identity)) { return; }
    struct event event={.type=IDENTITY_RESOLVED,.slot=0xff,.identity=*identity,.rpa=*rpa};
    k_spinlock_key_t key=k_spin_lock(&identity_lock);
    for (unsigned i=0;i < LISM_INPUT_PEERS;i++) {
        if (identities[i].conn != conn || identities[i].epoch != (uint32_t)atomic_get(&sessions[i].epoch) ||
            bt_addr_le_cmp(&identities[i].target,rpa)) { continue; }
        event.slot=i; event.epoch=identities[i].epoch;
        if (legacy_policy[i].allowed && legacy_policy[i].conn == conn &&
            legacy_policy[i].epoch == event.epoch && !bt_addr_le_cmp(&legacy_policy[i].target,rpa)) {
            legacy_policy[i].resolved=*identity; legacy_policy[i].resolved_valid=true;
            event.complete=true;
        }
        break;
    }
    k_spin_unlock(&identity_lock,key);
    if (event.slot < LISM_INPUT_PEERS) { queue_event(&event,conn); }
#endif
}
void lism_input_smp_received(struct bt_conn *conn,uint8_t opcode,const void *data,size_t length) {
    /* Only public capability metadata is copied. Reject every secret-bearing
     * SMP opcode before reading its payload, even if the hook is misused. */
    size_t expected=(opcode == 1 || opcode == 2) ? 6 : (opcode == 5 || opcode == 11) ? 1 : 0;
    if (!expected || length != expected || !data) { return; }
    int slot=owned_connection(conn); if (slot < 0) { return; }
    struct event event={.type=SMP_RECEIVED,.slot=slot,.properties=opcode,.length=expected,
        .epoch=atomic_get(&sessions[slot].epoch)};
    memcpy(event.data,data,expected); queue_event(&event,conn);
}
void lism_input_smp_blocked(struct bt_conn *conn,uint8_t block) {
    int slot=owned_connection(conn); if (slot < 0) { return; }
    if (block < LISM_INPUT_SMP_KEY_SIZE || block > LISM_INPUT_SMP_SC_REQUIRED) { return; }
    struct event event={.type=SMP_BLOCKED,.slot=slot,.properties=block,
        .epoch=atomic_get(&sessions[slot].epoch)}; queue_event(&event,conn);
}
void lism_input_smp_failed(struct bt_conn *conn,uint8_t opcode,uint8_t reason) {
    int slot=owned_connection(conn); if (slot < 0 || !reason) { return; }
    struct event event={.type=SMP_FAILED,.slot=slot,.properties=opcode,.error=reason,
        .epoch=atomic_get(&sessions[slot].epoch)}; queue_event(&event,conn);
}
static bool input_param_request(struct bt_conn *conn,struct bt_le_conn_param *p) {
    if (owned_connection(conn)<0) return true;
    /* Refuse aggressive updates; slave latency does not reserve ESB airtime. */
    return p->interval_min>=24 && p->interval_max<=80 && p->latency<=30 && p->timeout>=400;
}
static void input_param_updated(struct bt_conn *conn,uint16_t interval,uint16_t latency,uint16_t timeout) {
    int slot=owned_connection(conn); if (slot<0) return;
    LOG_INF("Input %d link interval=%u*1.25ms latency=%u timeout=%u*10ms",slot,interval,latency,timeout);
}
BT_CONN_CB_DEFINE(lism_input_callbacks)={.connected=connected,.disconnected=disconnected,
    .security_changed=security_changed,.identity_resolved=identity_resolved,
    .le_param_req=input_param_request,.le_param_updated=input_param_updated};
static void auth_event(struct bt_conn *conn,uint8_t type,uint32_t passkey) {
    int slot=owned_connection(conn); if (slot < 0) { bt_conn_auth_cancel(conn); return; }
    struct event event={.type=AUTH,.slot=slot,.auth=type,.passkey=passkey,
        .epoch=atomic_get(&sessions[slot].epoch)}; queue_event(&event,conn);
}
static void auth_display(struct bt_conn *conn,unsigned int key) { auth_event(conn,LISM_INPUT_AUTH_DISPLAY,key); }
static void auth_confirm(struct bt_conn *conn,unsigned int key) { auth_event(conn,LISM_INPUT_AUTH_CONFIRM,key); }
static void auth_entry(struct bt_conn *conn) { auth_event(conn,LISM_INPUT_AUTH_ENTRY,0); }
static void auth_cancel(struct bt_conn *conn) { auth_event(conn,LISM_INPUT_AUTH_NONE,0); }
static const struct bt_conn_auth_cb auth_callbacks={.passkey_display=auth_display,
    .passkey_confirm=auth_confirm,.passkey_entry=auth_entry,.cancel=auth_cancel};

static void release_slot(unsigned slot) {
    struct session *s=&sessions[slot];
    s->own_buttons=0;
    for (unsigned i=0;i < SERVICES;i++) {
        memset(&s->services[i].state,0,sizeof(s->services[i].state));
        memset(&s->services[i].held,0,sizeof(s->services[i].held));
    }
    lism_input_merge_clear(slot,lism_input_merge_generation());
}
void lism_input_endpoint_transition_begin(void) {
    /* Endpoint clear/commit and input source mutation are one thread-level
     * transaction. Never a spinlock across USB/GATT send or an ISR callback. */
    k_mutex_lock(&report_lock,K_FOREVER);
}
void lism_input_endpoint_transition_end(void) { k_mutex_unlock(&report_lock); }
void lism_input_merge_endpoint_changing(void) {
    /* Runs before generation advances, so no frame can claim the new epoch
     * while the old endpoint is still being cleared. */
    atomic_set(&endpoint_frozen,1);
}
void lism_input_merge_endpoint_changed(uint32_t generation) {
    ARG_UNUSED(generation);
    atomic_set(&endpoint_changed,1); kick();
}
static bool current_frame(const struct frame *f) {
    return atomic_get(&inputs_enabled) && f->slot < LISM_INPUT_PEERS && f->service < SERVICES && f->endpoint >= 0 &&
        f->epoch == (uint32_t)atomic_get(&sessions[f->slot].epoch) &&
        atomic_get(&sessions[f->slot].input_ready) && !atomic_get(&sessions[f->slot].clear) &&
        f->generation == lism_input_merge_generation() &&
        f->endpoint == xiao147_pointer_endpoint() && !atomic_get(&endpoint_changed) &&
        !atomic_get(&endpoint_frozen);
}
static bool boot_decode(struct service *svc,const struct frame *f,struct lism_input_event *out) {
    *out=svc->held;
    out->dx=out->dy=out->wheel=out->pan=0;
    if (f->boot == 1) {
        if (f->len != 8 || f->data[1]) { return false; }
        memset(out->keys,0,sizeof(out->keys)); out->keyboard_rollover=false;
        out->modifiers=f->data[0]; out->updated=LISM_INPUT_UPDATED_KEYBOARD;
        for (unsigned i=2;i < 8;i++) {
            uint8_t key=f->data[i];
            if (key && key < 4) { out->keyboard_rollover=true; }
            else if (key >= 4) { out->keys[key/8] |= BIT(key%8); }
        }
    } else if (f->boot == 2) {
        if (f->len != 3 && f->len != 4) { return false; }
        out->buttons=f->data[0] & 7; out->dx=(int8_t)f->data[1]; out->dy=(int8_t)f->data[2];
        out->wheel=f->len == 4 ? (int8_t)f->data[3] : 0; out->updated=LISM_INPUT_UPDATED_MOUSE;
    } else { return false; }
    return true;
}
static void union_peer(const struct session *s,struct lism_input_merge_state *held,uint8_t *buttons) {
    memset(held,0,sizeof(*held)); *buttons=0;
    for (unsigned k=0;k < s->service_count;k++) {
        const struct lism_input_event *e=&s->services[k].held;
        for (unsigned i=0;i < sizeof(held->keys);i++) { held->keys[i] |= e->keys[i]; }
        held->buttons |= e->buttons; held->modifiers |= e->modifiers; held->rollover |= e->keyboard_rollover; *buttons |= e->buttons;
        for (unsigned j=0;j < e->consumer_count;j++) {
            bool found=false;
            for (unsigned n=0;n < held->consumer_count;n++) { found |= held->consumer[n] == e->consumer[j]; }
            if (!found && held->consumer_count < ARRAY_SIZE(held->consumer)) {
                held->consumer[held->consumer_count++]=e->consumer[j];
            }
        }
    }
}
static void bridge_retry_handler(struct k_work *work) {ARG_UNUSED(work);kick();}
K_WORK_DELAYABLE_DEFINE(bridge_retry_work,bridge_retry_handler);
static void bridge_retry(void) {k_work_reschedule(&bridge_retry_work,K_MSEC(5));}
static uint8_t bridge_send_pending(uint8_t pending,int32_t pointer[4]) {
    if (pending & 1) {
        if (zmk_endpoint_send_report(HID_USAGE_KEY)<0) return pending;
        pending&=~1;
    }
    if (pending & 2) {
        if (zmk_endpoint_send_report(HID_USAGE_CONSUMER)<0) return pending;
        pending&=~2;
    }
    if (pending & 4) {
        for(unsigned i=0;i<4;i++)outgoing_pointer[i]=CLAMP(pointer[i],-32767,32767);
        zmk_hid_mouse_movement_set(CLAMP(pointer[0],-32767,32767),CLAMP(pointer[1],-32767,32767));
        zmk_hid_mouse_scroll_set(CLAMP(pointer[2],-32767,32767),CLAMP(pointer[3],-32767,32767));
        int rc=zmk_endpoint_send_mouse_report();
        memset(outgoing_pointer,0,sizeof(outgoing_pointer));
        zmk_hid_mouse_movement_set(0,0);zmk_hid_mouse_scroll_set(0,0);
        if (rc<0) return pending;
        pending&=~4;memset(pointer,0,sizeof(int32_t)*4);
    }
    return pending;
}
void lism_input_merge_external_mouse(struct zmk_hid_mouse_report*copy){
    copy->body.d_x=outgoing_pointer[0];copy->body.d_y=outgoing_pointer[1];
    copy->body.d_scroll_x=outgoing_pointer[2];copy->body.d_scroll_y=outgoing_pointer[3];
}
static void bridge_input(struct input_event *event,void *unused) {
    ARG_UNUSED(unused);
    if (event->type != INPUT_EV_MSC || event->code != KICK) { return; }
    static int destination=-1;
    static uint32_t generation;
    static uint8_t pending;
    static int32_t pointer[4];
    static uint32_t cleared_disable_generation;
    k_mutex_lock(&report_lock,K_FOREVER);
    lism_input_usb_bridge(true);
    uint32_t current=lism_input_merge_generation();
    int endpoint=xiao147_pointer_endpoint();
    bool changed=atomic_set(&endpoint_changed,0) || current != generation || endpoint != destination;
    if (changed) {
        /* Drop old backlog, but accept the first new authoritative input frame;
         * many keyboards never send a spontaneous all-up notification. */
        for (unsigned i=0;i < LISM_INPUT_PEERS;i++) { release_slot(i); atomic_clear(&sessions[i].clear); }
        /* New-generation first keydown may already be queued before this
         * wake. Keep it; current_frame rejects only genuinely stale frames. */
        memset(pointer,0,sizeof(pointer));
        pending=7; generation=current; destination=endpoint;
        atomic_set(&observed_endpoint,endpoint);
    }
    for (unsigned i=0;i < LISM_INPUT_PEERS;i++) {
        if (atomic_set(&sessions[i].clear,0)) { release_slot(i); pending |= 7; memset(pointer,0,sizeof(pointer)); }
    }
    uint32_t disabled=(uint32_t)atomic_get(&disable_generation);
    if(disabled!=cleared_disable_generation){
        for(unsigned i=0;i<LISM_INPUT_PEERS;i++)release_slot(i);
        memset(pointer,0,sizeof(pointer));pending=7;k_msgq_purge(&frames);
        cleared_disable_generation=disabled;
    }
    if(!atomic_get(&inputs_enabled))k_msgq_purge(&frames);
    if (endpoint < 0) { pending=0; k_msgq_purge(&frames); lism_input_usb_bridge(false);k_mutex_unlock(&report_lock); return; }
    /* Retry the previous immutable edge before consuming another frame. USB
     * is nonblocking in this scope; DMA storage remains semaphore-owned. */
    pending=bridge_send_pending(pending,pointer);
    if (pending) goto done;
    struct frame frame;
    for (unsigned budget=0;budget<8 && !k_msgq_get(&frames,&frame,K_NO_WAIT);budget++) {
        if (!current_frame(&frame)) { continue; }
        struct session *s=&sessions[frame.slot];
        struct service *svc=&s->services[frame.service];
        struct lism_input_event out;
        bool valid=frame.boot ? boot_decode(svc,&frame,&out) :
            lism_input_decode(&svc->descriptor,&svc->state,frame.id,frame.data,frame.len,&out) == LISM_INPUT_OK;
        if (!valid || !current_frame(&frame)) {
            release_slot(frame.slot); pending |= 7;
            atomic_set(&s->fault,1); atomic_clear(&s->input_ready); k_work_submit(&control_work); continue;
        }
        svc->held=out;
        struct lism_input_merge_state held; uint8_t buttons;
        union_peer(s,&held,&buttons);
        if (lism_input_merge_set(frame.slot,frame.generation,&held) < 0) { continue; }
        if (!current_frame(&frame)) { release_slot(frame.slot); pending |= 7; continue; }
        s->own_buttons=buttons;
        if (!current_frame(&frame)) { release_slot(frame.slot); pending |= 7; continue; }
        if (out.updated & LISM_INPUT_UPDATED_KEYBOARD) { pending |= 1; }
        if (out.updated & LISM_INPUT_UPDATED_CONSUMER) { pending |= 2; }
        if (out.updated & LISM_INPUT_UPDATED_MOUSE) {
            pointer[0]=out.dx;pointer[1]=out.dy;pointer[2]=out.pan;pointer[3]=out.wheel;
            pending|=4;
        }
        pending=bridge_send_pending(pending,pointer);
        if (pending) break; /* Never overwrite an unsent key or pointer edge. */
    }
done:
    if (generation != lism_input_merge_generation() || destination != xiao147_pointer_endpoint())
        atomic_set(&endpoint_changed,1);
    atomic_set(&endpoint_generation,pending != 0);
    bool more=k_msgq_num_used_get(&frames)!=0;
    lism_input_usb_bridge(false);
    k_mutex_unlock(&report_lock);
    if (more || pending) bridge_retry();
}
INPUT_CALLBACK_DEFINE(DEVICE_GET(lism_input_device),bridge_input,NULL);
static int endpoint_event(const zmk_event_t *event) {
    /* Endpoint switch is reset by the output hook before old-host clear.
     * USB disconnect/reconnect and same-profile BLE reconnect need it too. */
    k_mutex_lock(&report_lock,K_FOREVER);
    int now=xiao147_pointer_endpoint();
    bool committed=as_zmk_endpoint_changed(event) != NULL;
    /* USB supply events while BLE remains selected are not an endpoint
     * transition and must not discard held external keys without a release. */
    if (committed || now != atomic_get(&observed_endpoint)) {
        if (!committed) { lism_input_merge_endpoint_reset(); }
        atomic_set(&observed_endpoint,now);
        atomic_clear(&endpoint_frozen);
        atomic_set(&endpoint_changed,1); kick();
    }
    k_mutex_unlock(&report_lock);
    return ZMK_EV_EVENT_BUBBLE;
}
ZMK_LISTENER(lism_input_endpoint,endpoint_event);
ZMK_SUBSCRIPTION(lism_input_endpoint,zmk_endpoint_changed);
ZMK_SUBSCRIPTION(lism_input_endpoint,zmk_usb_conn_state_changed);
ZMK_SUBSCRIPTION(lism_input_endpoint,zmk_ble_active_profile_changed);

struct ad_fields { char name[24]; bool hint, complete; };
static bool uuid_hid(const uint8_t *uuid) {
    static const uint8_t expected[16]={0xfb,0x34,0x9b,0x5f,0x80,0,0,0x80,0,0x10,0,0,0x12,0x18,0,0};
    return !memcmp(uuid,expected,sizeof(expected));
}
static bool parse_ad(struct bt_data *data,void *context) {
    struct ad_fields *f=context;
    if ((data->type == BT_DATA_NAME_COMPLETE || data->type == BT_DATA_NAME_SHORTENED) &&
        data->data_len && (!f->complete || data->type == BT_DATA_NAME_COMPLETE)) {
        unsigned len=MIN(data->data_len,sizeof(f->name)-1);
        for (unsigned i=0;i < len;i++) { f->name[i]=data->data[i] >= 32 && data->data[i] < 127 ? data->data[i] : '?'; }
        f->name[len]=0; f->complete=data->type == BT_DATA_NAME_COMPLETE;
    } else if (data->type == BT_DATA_UUID16_ALL || data->type == BT_DATA_UUID16_SOME) {
        for (unsigned i=0;!(data->data_len%2) && i+1 < data->data_len;i+=2) { f->hint |= sys_get_le16(data->data+i) == 0x1812; }
    } else if (data->type == BT_DATA_UUID128_ALL || data->type == BT_DATA_UUID128_SOME) {
        for (unsigned i=0;!(data->data_len%16) && i+15 < data->data_len;i+=16) { f->hint |= uuid_hid(data->data+i); }
    } else if (data->type == BT_DATA_SVC_DATA16 && data->data_len >= 2) { f->hint |= sys_get_le16(data->data) == 0x1812; }
    else if (data->type == BT_DATA_GAP_APPEARANCE && data->data_len == 2) {
        f->hint |= (sys_get_le16(data->data) >> 6) == 15; /* Generic HID family. */
    }
    return true;
}
static void scan_received(const struct bt_le_scan_recv_info *info,struct net_buf_simple *data) {
    if (!atomic_get(&advertisements_enabled)) { return; }
    struct ad_fields fields={0}; struct net_buf_simple copy=*data;
    bt_data_parse(&copy,parse_ad,&fields);
    struct advertisement ad={.seen=k_uptime_get_32(),.rssi=info->rssi,
        .connectable=(info->adv_props & BT_GAP_ADV_PROP_CONNECTABLE) != 0,
        .hint=fields.hint,.complete=fields.complete};
    ad.addr=*info->addr; memcpy(ad.name,fields.name,sizeof(ad.name));
    if (!k_msgq_put(&advertisements,&ad,K_NO_WAIT)) { k_work_submit(&control_work); }
}
static struct bt_le_scan_cb scan_callbacks={.recv=scan_received};
static bool name_hint(const char *name) {
    static const char *const words[]={"mouse","keyboard","trackpad","touchpad","g705","cheerpod","cheerpod2"};
    while (*name) {
        char token[24]; unsigned n=0;
        while (*name && !((*name >= 'A' && *name <= 'Z') || (*name >= 'a' && *name <= 'z') || (*name >= '0' && *name <= '9'))) { name++; }
        while ((*name >= 'A' && *name <= 'Z') || (*name >= 'a' && *name <= 'z') || (*name >= '0' && *name <= '9')) {
            char c=*name++; if (n+1 < sizeof(token)) { token[n++]=c >= 'A' && c <= 'Z' ? c+32 : c; }
        }
        token[n]=0;
        for (unsigned i=0;i < ARRAY_SIZE(words);i++) { if (!strcmp(token,words[i])) { return true; } }
    }
    return false;
}
static bool busy_setup(void) {
    for (unsigned i=0;i < LISM_INPUT_PEERS;i++) {
        if (sessions[i].conn && sessions[i].stage != READY) { return true; }
    }
    return false;
}
static void scanner_state(void) {
    if(!atomic_get(&inputs_enabled)){
        search_until=0;atomic_clear(&scan_needed);atomic_clear(&advertisements_enabled);lism_hid_scan_pause();return;
    }
    bool manual=settings_ready && search_until && lism_screen_active();
    bool reconnect=false;
    if(search_until && !lism_screen_active())search_until=0;
    bool need=manual;
    for (unsigned i=0;i < LISM_INPUT_PEERS;i++) {
        reconnect |= settings_ready && (sessions[i].have_saved || sessions[i].target_known) &&
                !sessions[i].conn && !sessions[i].suspended;
    }
    /* Saved disconnected inputs get a bounded reconnect window. Once all
     * have a connection there is no background scan, including on Home. */
    need |= reconnect && (k_uptime_get_32()%10000 < 2000);
    atomic_set(&scan_needed,need); atomic_set(&advertisements_enabled,need);
    if (!atomic_get(&scan_paused)) { lism_hid_scan_resume(); }
}
static void advertisement(const struct advertisement *ad) {
    if (!settings_ready || !atomic_get(&inputs_enabled)) { return; }
    for (unsigned i=0;i < LISM_INPUT_PEERS;i++) {
        struct session *s=&sessions[i];
        if ((s->have_saved && !bt_addr_le_cmp(&ad->addr,&s->saved.addr)) ||
            (s->target_known && !bt_addr_le_cmp(&ad->addr,&s->target))) {
            if (!s->conn && !s->suspended && ad->connectable) { s->target=ad->addr; s->pending=true; s->seen=ad->seen; }
            return;
        }
        if (s->conn && !bt_addr_le_cmp(&ad->addr,&s->target)) { return; }
    }
    if (!lism_screen_active() || !search_until || elapsed(k_uptime_get_32(),search_until)) { return; }
    int index=-1,empty=-1,oldest=0;
    for (unsigned i=0;i < AD_CACHE;i++) {
        if (cache[i].public.valid && !bt_addr_le_cmp(&cache[i].addr,&ad->addr)) { index=i; break; }
        if (!cache[i].public.valid && empty < 0) { empty=i; }
        if ((int32_t)(cache[i].seen-cache[oldest].seen) < 0) { oldest=i; }
    }
    if (index < 0) { index=empty >= 0 ? empty : oldest; memset(&cache[index],0,sizeof(cache[index])); cache[index].addr=ad->addr; }
    struct candidate *c=&cache[index];
    if (c->public.valid && ad->seen-c->seen > 3000) {
        c->connectable=c->hint=c->name_hint=c->complete=false; c->public.name[0]=0;
    }
    c->public.valid=true; c->seen=ad->seen; c->public.rssi=ad->rssi;
    c->connectable |= ad->connectable; c->hint |= ad->hint; c->name_hint |= name_hint(ad->name);
    if (ad->connectable) { c->connectable_seen=ad->seen; }
    if (ad->name[0] && (!c->complete || ad->complete)) {
        snprintf(c->public.name,sizeof(c->public.name),"%s",ad->name); c->complete=ad->complete;
    }
    if (!c->connectable || !(c->hint || c->name_hint || show_other)) { return; }
    bt_addr_to_str(&c->addr.a,c->public.address,sizeof(c->public.address));
    int slot=-1;
    for (unsigned i=0;i < LISM_HID_CANDIDATES;i++) {
        if (candidates[i].public.valid && !bt_addr_le_cmp(&candidates[i].addr,&c->addr)) { slot=i; break; }
        if (!candidates[i].public.valid && slot < 0) { slot=i; }
    }
    if (slot >= 0) {
        if (identity_conflict(&c->addr,search_slot)) { return; }
        uint32_t token=candidates[slot].public.token;
        if (!candidates[slot].public.valid) { token=++next_token; if (!token) { token=++next_token; } }
        candidates[slot]=*c; candidates[slot].public.token=token;
        if (!candidates[slot].public.name[0]) { snprintf(candidates[slot].public.name,24,"Unnamed BLE device"); }
    }
}
static void cancel_requests(struct session *s) {
    if (!s->conn) { return; }
    for (unsigned i=0;i < 2;i++) { bt_gatt_cancel(s->conn,&s->discovers[i].params); bt_gatt_cancel(s->conn,&s->reads[i].params); }
}
static void clear_prompt(unsigned slot) {
    k_mutex_lock(&status_lock,K_FOREVER);
    if (status.auth_type && status.auth_slot == slot) { status.auth_type=LISM_INPUT_AUTH_NONE; status.passkey=0; prompt_until=0; }
    k_mutex_unlock(&status_lock);
}
static void finish(unsigned slot) {
    struct session *s=&sessions[slot];
    if (!s->conn) { return; }
    cancel_requests(s); struct bt_conn *old=s->conn; s->conn=NULL;
    atomic_clear(&s->input_ready); atomic_set(&s->clear,1); atomic_inc(&s->epoch); kick();
    s->stage=IDLE; s->pending=s->operation=false; s->retry_at=k_uptime_get_32()+3000;
    clear_prompt(slot);
    k_mutex_lock(&status_lock,K_FOREVER);
    status.peers[slot].connected=status.peers[slot].ready=false; status.generation++;
    k_mutex_unlock(&status_lock);
    atomic_clear(&scan_paused); publish(); bt_conn_unref(old); scanner_state();
}
static void close_session(unsigned slot,const char *why) {
    struct session *s=&sessions[slot];
    if (s->stage != CLOSING) {
        message(slot,why); atomic_clear(&s->input_ready); atomic_set(&s->clear,1); atomic_inc(&s->epoch); kick();
        s->stage=CLOSING; s->pending=false; s->operation=false; cancel_requests(s); clear_prompt(slot);
        if (!s->have_saved) { s->suspended=true; }
        k_mutex_lock(&status_lock,K_FOREVER); status.peers[slot].ready=false; k_mutex_unlock(&status_lock);
        publish();
    }
    if (!s->conn) { s->stage=IDLE; s->retry_at=k_uptime_get_32()+3000; atomic_clear(&scan_paused); return; }
    (void)bt_conn_disconnect(s->conn,BT_HCI_ERR_REMOTE_USER_TERM_CONN);
    s->deadline=k_uptime_get_32()+3000;
}
static void advance(struct session *s,uint8_t stage) { s->stage=stage; s->operation=false; s->deadline=k_uptime_get_32()+SETUP_TIMEOUT; }
static void begin_discovery(unsigned slot,uint8_t type,uint16_t start,uint16_t end,const struct bt_uuid *uuid) {
    struct session *s=&sessions[slot];
    if (!start || start > end) { close_session(slot,"Invalid HID attribute range"); return; }
    struct discovery_op *op=&s->discovers[s->discover_next++ & 1];
    *op=(struct discovery_op){.slot=slot,.epoch=atomic_get(&s->epoch),.stage=s->stage,.report=s->report_index};
    op->params=(struct bt_gatt_discover_params){.type=type,.start_handle=start,.end_handle=end,.uuid=uuid,.func=discovered};
    s->operation=true;
    int rc=bt_gatt_discover(s->conn,&op->params);
    if (rc) { close_session(slot,"HID discovery request failed; retry"); }
}
static void begin_read(unsigned slot,uint16_t handle) {
    struct session *s=&sessions[slot]; struct read_op *op=&s->reads[s->read_next++ & 1];
    *op=(struct read_op){.slot=slot,.epoch=atomic_get(&s->epoch),.stage=s->stage,.report=s->report_index};
    op->params.func=read_result; op->params.handle_count=1; op->params.single.handle=handle;
    s->operation=true;
    if (!handle || bt_gatt_read(s->conn,&op->params)) { close_session(slot,"HID read request failed; retry"); }
}
static void count_bonds(const struct bt_bond_info *info,void *context) { ARG_UNUSED(info); (*(unsigned *)context)++; }
static bool bond_found;
static void find_bond(const struct bt_bond_info *info,void *context) {
    if (!bt_addr_le_cmp(&info->addr,context)) { bond_found=true; }
}
static bool bonded(const bt_addr_le_t *addr) {
    bond_found=false; bt_foreach_bond(BT_ID_DEFAULT,find_bond,(void *)addr); return bond_found;
}
static void connect_target(unsigned slot) {
    if(!atomic_get(&inputs_enabled))return;
    if (lism_esb_coex_pressure()) return;
    struct session *s=&sessions[slot]; uint32_t now=k_uptime_get_32();
    if (s->conn || !s->pending || s->suspended || busy_setup() || !elapsed(now,s->retry_at) || now-s->seen > 2000) { return; }
    if (identity_conflict(&s->target,slot)) { s->pending=false; s->suspended=true; message(slot,"Identity belongs to keyboard / computer"); return; }
    if (!bonded(&s->target)) {
        unsigned count=0; bt_foreach_bond(BT_ID_DEFAULT,count_bonds,&count);
        if (count >= CONFIG_BT_MAX_PAIRED) { s->suspended=true; s->pending=false; message(slot,"Pairing storage full; forget one device"); return; }
    }
    atomic_set(&scan_paused,1);
    int rc=lism_hid_scan_pause();
    if (rc < 0 && rc != -EALREADY) { atomic_clear(&scan_paused); s->retry_at=now+2000; return; }
    s->pending=false; atomic_inc(&s->epoch); atomic_clear(&s->input_ready); atomic_set(&s->clear,1); kick();
    k_mutex_lock(&report_lock,K_FOREVER);
    memset(s->services,0,sizeof(s->services));
    k_mutex_unlock(&report_lock);
    memset(s->reports,0,sizeof(s->reports));
    memset(&s->security,0,sizeof(s->security));
    s->legacy_used=false; legacy_status(slot);
    atomic_clear(&s->security_lost);
    s->service_count=s->service_index=s->report_count=s->report_index=s->subscribed=s->chars=0;
    s->map_done=s->security_started=s->operation=false; s->map_len=s->reference_len=0;
    s->forget=false; s->stage=CONNECTING; s->deadline=now+SETUP_TIMEOUT;
    /* Publish target before create: a fast connected callback may run before
     * create has returned the conn pointer to this coordinator. */
    s->pending=true; publish(); s->pending=false;
    message(slot,"Connecting BLE input...");
    const struct bt_conn_le_create_param create={.options=BT_CONN_LE_OPT_NONE,
        .interval=160,.window=8,.timeout=300}; /* 5ms/100ms, bounded 3s initiation */
    rc=bt_conn_le_create(&s->target,&create,BT_LE_CONN_PARAM(24,40,0,400),&s->conn);
    if (rc) { s->conn=NULL; s->stage=IDLE; s->retry_at=now+3000; atomic_clear(&scan_paused); message(slot,"Connection failed; tap Retry"); }
    publish();
}
static bool save_identity(unsigned slot) {
    struct session *s=&sessions[slot]; const bt_addr_le_t *addr=bt_conn_get_dst(s->conn);
    if (identity_conflict(addr,slot)) { close_session(slot,"Resolved identity belongs to another role"); return false; }
    if (!bonded(addr)) { return false; }
    s->saved.version=2; s->saved.addr=*addr;
    k_mutex_lock(&status_lock,K_FOREVER);
    snprintf(s->saved.name,sizeof(s->saved.name),"%s",status.peers[slot].name);
    s->have_saved=status.peers[slot].saved=true;
    k_mutex_unlock(&status_lock);
    char key[24]; snprintf(key,sizeof(key),"lism_inputs/peer%u",slot);
    bool stored=settings_save_one(key,&s->saved,sizeof(s->saved)) == 0;
    if (stored && s->legacy_allowed && !s->legacy_revoked) {
        /* Resolve a private advertising address only after this exact granted
         * connection has reached READY with a verified bond and no role conflict. */
        struct legacy_grant grant={.version=1,.allowed=1,.addr=*addr};
        snprintf(key,sizeof(key),"lism_inputs/legacy%u",slot);
        stored=settings_save_one(key,&grant,sizeof(grant)) == 0;
        if (stored) { s->legacy_target=*addr; }
    }
    publish(); return stored;
}
static bool supported_report(const struct service *svc,uint8_t id) {
    for (unsigned i=0;i < svc->descriptor.report_count;i++) {
        if (svc->descriptor.reports[i].id == id && svc->descriptor.reports[i].updates) { return true; }
    }
    return false;
}
static void ready(unsigned slot) {
    struct session *s=&sessions[slot]; uint32_t caps=0;
    for (unsigned i=0;i < s->service_count;i++) { caps |= s->services[i].descriptor.capabilities; }
    bool boot_keyboard=false,boot_mouse=false;
    for (unsigned i=0;i < s->report_count;i++) {
        boot_keyboard |= s->services[s->reports[i].service].boot && s->reports[i].boot == 1;
        boot_mouse |= s->services[s->reports[i].service].boot && s->reports[i].boot == 2;
    }
    s->stage=READY;
    k_mutex_lock(&status_lock,K_FOREVER);
    struct lism_input_peer_status *out=&status.peers[slot];
    out->ready=true;
    out->type=(caps & LISM_INPUT_CAP_KEYBOARD) || boot_keyboard ? LISM_INPUT_DEVICE_KEYBOARD :
        (caps & LISM_INPUT_CAP_MOUSE) || boot_mouse ? LISM_INPUT_DEVICE_MOUSE : LISM_INPUT_DEVICE_UNKNOWN;
    /* Classification comes from the HID descriptor, never brand/name. This
     * chooses an icon only; raw digitizer gestures are not forwarded. */
    if (out->type == LISM_INPUT_DEVICE_MOUSE && (caps & LISM_INPUT_CAP_TOUCHPAD)) {
        out->type=LISM_INPUT_DEVICE_TOUCHPAD;
    }
    status.generation++;
    k_mutex_unlock(&status_lock);
    bool stored=save_identity(slot);
    if (s->stage != READY) { return; }
    atomic_set(&s->input_ready,1); clear_prompt(slot);
    message(slot,stored ? "Ready: standard HID input" : "Ready; identity save failed (RAM only)"); scanner_state();
}
static void security_observed(struct session *s,const struct event *e) {
    struct security_detail *d=&s->security;
    if (e->type == SMP_RECEIVED) {
        if ((e->properties == 1 || e->properties == 2) && e->length == 6) {
            *d=(struct security_detail){.opcode=e->properties,.io=e->data[0],
                .auth=e->data[2],.key_size=e->data[3],.features=true};
            LOG_INF("Input %u SMP features epoch=%u op=%u io=%u auth=0x%02x key_size=%u",
                e->slot,e->epoch,d->opcode,d->io,d->auth,d->key_size);
        } else if (e->properties == 11 && e->length == 1) {
            *d=(struct security_detail){.opcode=11,.auth=e->data[0]};
            LOG_INF("Input %u SMP security request epoch=%u auth=0x%02x",e->slot,e->epoch,d->auth);
        } else if (e->properties == 5 && e->length == 1) {
            d->peer_failure=true; d->raw_opcode=5; d->raw_reason=e->data[0];
            LOG_INF("Input %u SMP peer failure epoch=%u reason=0x%02x",e->slot,e->epoch,d->raw_reason);
        }
    } else if (e->type == SMP_BLOCKED) {
        d->block=e->properties;
        LOG_INF("Input %u SMP local policy epoch=%u block=%u",e->slot,e->epoch,d->block);
    } else if (e->type == SMP_FAILED) {
        d->local_failure=true; d->raw_opcode=e->properties; d->raw_reason=e->error;
        LOG_INF("Input %u SMP local failure epoch=%u op=%u reason=0x%02x",
            e->slot,e->epoch,d->raw_opcode,d->raw_reason);
    }
}
static void security_failure(const struct session *s,uint8_t error,uint8_t level,char text[48]) {
    const struct security_detail *d=&s->security;
    if (!error && level < BT_SECURITY_L2) {
        snprintf(text,48,"Encryption below L2; tap Retry");
    } else if (atomic_get(&s->security_lost)) {
        snprintf(text,48,"Security failed (%u); details lost; Retry",error);
    } else if (error == BT_SECURITY_ERR_AUTH_REQUIREMENT && d->local_failure && d->block == LISM_INPUT_SMP_SC_REQUIRED) {
        snprintf(text,48,"Secure Connections required (4)");
    } else if (error == BT_SECURITY_ERR_AUTH_REQUIREMENT && d->local_failure && d->block == LISM_INPUT_SMP_KEY_SIZE) {
        snprintf(text,48,"Encryption key size %u rejected (4)",d->key_size);
    } else if (error == BT_SECURITY_ERR_AUTH_REQUIREMENT && d->local_failure && d->block == LISM_INPUT_SMP_STORED_BOND) {
        snprintf(text,48,"Existing bond policy rejected (4)");
    } else if (error == BT_SECURITY_ERR_AUTH_REQUIREMENT && d->local_failure && d->block == LISM_INPUT_SMP_AUTH_LEVEL) {
        snprintf(text,48,"Requested security not supported (4)");
    } else if (error == BT_SECURITY_ERR_AUTH_REQUIREMENT && d->peer_failure) {
        snprintf(text,48,"Peer rejected security (4/SMP %02X)",d->raw_reason);
    } else if (error == BT_SECURITY_ERR_AUTH_REQUIREMENT) {
        snprintf(text,48,"Security requirements mismatch (4); Retry");
    } else {
        snprintf(text,48,"Security failed (%u); tap Retry",error);
    }
}
static void event_received(struct event *e) {
    if (e->type == SETTINGS_READY) {
        settings_ready=true;
        if (!loaded_valid[0] && legacy_valid) { loaded[0]=legacy; loaded[0].version=2; loaded_valid[0]=true; }
        for (unsigned i=0;i < LISM_INPUT_PEERS;i++) {
            if (!loaded_valid[i]) { continue; }
            if (identity_conflict(&loaded[i].addr,i)) {
                message(i,"Saved identity conflicts with another role"); continue;
            }
            sessions[i].saved=loaded[i]; sessions[i].have_saved=true;
            sessions[i].target=loaded[i].addr;
            if (IS_ENABLED(CONFIG_LISM_INPUT_LEGACY_COMPAT) &&
                (IS_ENABLED(CONFIG_LISM_INPUT_AUTO_PAIR) ||
                 (loaded_grant_valid[i] && loaded_grants[i].allowed == 1 &&
                  !bt_addr_le_cmp(&loaded_grants[i].addr,&loaded[i].addr)))) {
                sessions[i].legacy_allowed=true;
                sessions[i].legacy_target=loaded[i].addr;
                legacy_token_refresh(i);
            }
            k_mutex_lock(&status_lock,K_FOREVER);
            status.peers[i].saved=true; snprintf(status.peers[i].name,24,"%s",loaded[i].name);
            k_mutex_unlock(&status_lock);
        }
        publish(); scanner_state(); return;
    }
    if (e->slot >= LISM_INPUT_PEERS) { return; }
    struct session *s=&sessions[e->slot];
    if (!s->conn || e->conn != s->conn) { return; }
    if (e->type == IDENTITY_RESOLVED) {
        if (e->epoch != (uint32_t)atomic_get(&s->epoch) || s->stage == CLOSING ||
            identity_conflict(&e->identity,e->slot) || bt_addr_le_cmp(&s->target,&e->rpa) ||
            bt_addr_le_cmp(bt_conn_get_dst(s->conn),&e->identity)) { return; }
        bool migrated=false;
        k_spinlock_key_t key=k_spin_lock(&identity_lock);
        migrated=e->complete && legacy_policy[e->slot].allowed &&
            legacy_policy[e->slot].conn == s->conn && legacy_policy[e->slot].epoch == e->epoch &&
            legacy_policy[e->slot].resolved_valid &&
            !bt_addr_le_cmp(&legacy_policy[e->slot].target,&e->rpa) &&
            !bt_addr_le_cmp(&legacy_policy[e->slot].resolved,&e->identity);
        k_spin_unlock(&identity_lock,key);
        if (s->legacy_allowed && !migrated) { return; }
        if (migrated) { s->legacy_target=e->identity; legacy_token_refresh(e->slot); }
        s->target=e->identity; s->target_known=true; publish();
        return;
    }
    if (e->type == DISCONNECTED || (e->type == CONNECTED && e->error)) {
        bool failure=s->stage == CLOSING || (s->stage != READY && e->error);
        if (!failure) { message(e->slot,"Disconnected; waiting for device"); }
        else if (s->stage != CLOSING) {
            char text[48]; snprintf(text,sizeof(text),"Connect ended (0x%02X); tap Retry",e->error); message(e->slot,text);
        }
        finish(e->slot); return;
    }
    if(!atomic_get(&inputs_enabled)){
        if(s->stage!=CLOSING)close_session(e->slot,"Third-party BLE off; bond retained");
        return;
    }
    if (e->type == CONNECTED) {
        if (s->stage == CLOSING) { close_session(e->slot,"Cancelled connection closing"); return; }
        if (e->result) { close_session(e->slot,"Pairing UI unavailable; retry connection"); return; }
        if (identity_conflict(bt_conn_get_dst(s->conn),e->slot)) {
            close_session(e->slot,"Resolved identity belongs to another role"); return;
        }
        k_mutex_lock(&status_lock,K_FOREVER); status.peers[e->slot].connected=true; k_mutex_unlock(&status_lock);
        atomic_clear(&scan_paused); advance(s,SECURITY); scanner_state(); return;
    }
    if (e->type == SECURED) {
        if (s->stage != SECURITY || e->epoch != (uint32_t)atomic_get(&s->epoch)) { return; }
        LOG_INF("Input %u security result epoch=%u level=%u error=%u",e->slot,
            (uint32_t)atomic_get(&s->epoch),e->level,e->error);
        if (e->error || e->level < BT_SECURITY_L2) {
            if (IS_ENABLED(CONFIG_LISM_INPUT_LEGACY_COMPAT) &&
                e->error == BT_SECURITY_ERR_AUTH_REQUIREMENT && !atomic_get(&s->security_lost) &&
                s->security.local_failure && s->security.block == LISM_INPUT_SMP_SC_REQUIRED &&
                (s->target_known || s->have_saved) && !identity_conflict(&s->target,e->slot)) {
                s->legacy_available=true; s->legacy_target=s->target;
                legacy_token_refresh(e->slot);
            }
            char text[48]; security_failure(s,e->error,e->level,text); close_session(e->slot,text);
        } else if (e->result || e->length < 16 || (!e->complete && !lism_input_legacy_allowed(s->conn))) {
            close_session(e->slot,"Security policy verification failed; Retry");
        } else {
            s->legacy_used=!e->complete; legacy_status(e->slot);
            clear_prompt(e->slot); advance(s,SERVICES_FIND);
        }
        return;
    }
    if (e->epoch != (uint32_t)atomic_get(&s->epoch) || s->stage == CLOSING) { return; }
    if (e->type == SMP_RECEIVED || e->type == SMP_BLOCKED || e->type == SMP_FAILED) {
        if (s->stage == SECURITY || s->stage == CONNECTING) { security_observed(s,e); }
        return;
    }
    if (e->type == AUTH) {
        /* A cancellation belongs to its connection, not whichever other peer
         * currently owns the shared pairing prompt. It must not extend setup. */
        if (e->auth == LISM_INPUT_AUTH_NONE) { clear_prompt(e->slot); return; }
        /* Queued prompts from this connection may outlive successful SMP. */
        if (s->stage != SECURITY) { return; }
        k_mutex_lock(&status_lock,K_FOREVER);
        status.auth_type=e->auth; status.auth_slot=e->slot; status.passkey=e->passkey;
        status.auth_token=++next_token; prompt_until=e->auth ? k_uptime_get_32()+60000 : 0;
        s->deadline=k_uptime_get_32()+65000;
        k_mutex_unlock(&status_lock); return;
    }
    if (e->type == UNSUBSCRIBED) { close_session(e->slot,"Input notifications stopped; Retry"); return; }
    if (e->type != SUBSCRIBED && e->stage != s->stage) { return; }
    struct service *svc=&s->services[s->service_index];
    if (e->type == DISCOVERED) {
        if (s->stage == SERVICES_FIND) {
            if (e->complete) {
                if (!s->service_count) { close_session(e->slot,"Not a standard BLE HID device"); }
                else { s->service_index=0; advance(s,CHARS); }
            } else if (!e->handle || e->handle >= e->end || s->service_count == SERVICES) {
                close_session(e->slot,"Unsupported HID service count/range");
            } else {
                s->services[s->service_count].start=e->handle+1;
                s->services[s->service_count++].end=e->end;
            }
        } else if (s->stage == CHARS) {
            if (e->complete) {
                if (s->report_count && !s->reports[s->report_count-1].end) { s->reports[s->report_count-1].end=svc->end; }
                s->map_done=false; s->map_len=0; advance(s,MAP);
            } else {
                if (++s->chars > 48 || e->handle < svc->start || e->value <= e->handle || e->value > svc->end) { close_session(e->slot,"Invalid HID characteristics"); return; }
                if (s->report_count && !s->reports[s->report_count-1].end) { s->reports[s->report_count-1].end=e->handle-1; }
                if (e->uuid == 0x2a4b) {
                    if (svc->map_handle) { close_session(e->slot,"Duplicate HID map"); return; } svc->map_handle=e->value;
                } else if (e->uuid == 0x2a4e) { svc->mode_handle=e->value; svc->mode_properties=e->properties; }
                else if (e->uuid == 0x2a4d || e->uuid == 0x2a22 || e->uuid == 0x2a33) {
                    if (s->report_count == REPORTS) { close_session(e->slot,"Too many HID input reports"); return; }
                    s->reports[s->report_count++]=(struct report){.slot=e->slot,.service=s->service_index,
                        .value=e->value,.properties=e->properties,.boot=e->uuid == 0x2a22 ? 1 : e->uuid == 0x2a33 ? 2 : 0};
                }
            }
        } else if (s->stage == DESCRIPTORS && e->report == s->report_index) {
            struct report *r=&s->reports[s->report_index];
            if (e->complete) {
                if (r->boot) { r->id=0; r->type=1; advance(s,SUBSCRIBE); }
                else { advance(s,REFERENCE); }
            } else if (e->handle <= r->value || e->handle > r->end) { close_session(e->slot,"Invalid HID descriptor range"); }
            else if (e->uuid == 0x2908) {
                if (r->reference) { close_session(e->slot,"Duplicate Report Reference"); } else { r->reference=e->handle; }
            } else if (e->uuid == 0x2902) {
                if (r->ccc) { close_session(e->slot,"Duplicate input CCC"); } else { r->ccc=e->handle; }
            }
        }
    } else if (e->type == READ) {
        if (e->error) { close_session(e->slot,"HID read failed; Retry / check device"); return; }
        if (s->stage == MAP) {
            if (e->length > sizeof(s->map)-s->map_len) { close_session(e->slot,"HID map exceeds memory limit"); return; }
            memcpy(s->map+s->map_len,e->data,e->length); s->map_len += e->length;
            if (e->complete) { s->map_done=true; s->operation=false; }
        } else if (s->stage == REFERENCE && e->report == s->report_index) {
            if (e->length > sizeof(s->reference)-s->reference_len) { close_session(e->slot,"Invalid Report Reference length"); return; }
            memcpy(s->reference+s->reference_len,e->data,e->length); s->reference_len += e->length;
            if (e->complete) {
                if (s->reference_len != 2 || s->reference[1] < 1 || s->reference[1] > 3) { close_session(e->slot,"Invalid Report Reference"); return; }
                struct report *r=&s->reports[s->report_index]; r->id=s->reference[0]; r->type=s->reference[1];
                for (unsigned i=0;i < s->report_index;i++) {
                    struct report *other=&s->reports[i];
                    if (!other->boot && other->service == r->service && other->reference && other->id == r->id && other->type == r->type) { close_session(e->slot,"Duplicate HID report ID/type"); return; }
                }
                advance(s,SUBSCRIBE);
            }
        }
    } else if (e->type == SUBSCRIBED && s->stage == SUBSCRIBE && e->report == s->report_index) {
        if (e->error) { close_session(e->slot,"Input subscription rejected; Retry"); }
        else { s->subscribed++; s->report_index++; advance(s,DESCRIPTORS); }
    }
}
static void progress(unsigned slot) {
    struct session *s=&sessions[slot];
    for (unsigned steps=0;s->conn && steps < 64;steps++) {
        if (s->operation || s->stage == READY || s->stage == CONNECTING || s->stage == CLOSING) { return; }
        struct service *svc=&s->services[s->service_index];
        if (s->stage == SECURITY) {
            if (s->security_started) { return; } s->security_started=true;
            if (bt_conn_get_security(s->conn) >= BT_SECURITY_L2) {
                /* Peer-initiated security can finish before the coordinator
                 * reaches SECURITY. Apply exactly the same gate on that path. */
                struct bt_conn_info info;
                if (bt_conn_get_info(s->conn,&info) || info.security.enc_key_size < 16 ||
                    (!(info.security.flags & BT_SECURITY_FLAG_SC) && !lism_input_legacy_allowed(s->conn))) {
                    close_session(slot,"Security policy verification failed; Retry"); return;
                }
                s->legacy_used=!(info.security.flags & BT_SECURITY_FLAG_SC);
                legacy_status(slot); advance(s,SERVICES_FIND); continue;
            }
            message(slot,"Pairing; watch screen for PIN / confirm");
            if (bt_conn_set_security(s->conn,BT_SECURITY_L2)) { close_session(slot,"Could not start secure pairing; Retry"); }
            return;
        } else if (s->stage == SERVICES_FIND) {
            begin_discovery(slot,BT_GATT_DISCOVER_PRIMARY,1,0xffff,BT_UUID_HIDS); return;
        } else if (s->stage == CHARS) {
            message(slot,"Discovering standard HID services...");
            begin_discovery(slot,BT_GATT_DISCOVER_CHARACTERISTIC,svc->start,svc->end,NULL); return;
        } else if (s->stage == MAP) {
            if (!s->map_done && svc->map_handle) { begin_read(slot,svc->map_handle); return; }
            k_mutex_lock(&report_lock,K_FOREVER);
            svc->parsed=s->map_len && lism_input_parse(s->map,s->map_len,&svc->descriptor) == LISM_INPUT_OK && svc->descriptor.run_count;
            k_mutex_unlock(&report_lock);
            if (!svc->parsed) {
                bool have_boot=false;
                for (unsigned i=0;i < s->report_count;i++) { have_boot |= s->reports[i].service == s->service_index && s->reports[i].boot; }
                svc->boot=have_boot && svc->mode_handle;
                /* A valid unsupported service can coexist with a supported
                 * second HID service. Never reinterpret arbitrary bytes. */
            }
            if ((svc->parsed || svc->boot) && svc->mode_handle) {
                uint8_t mode=svc->boot ? 0 : 1;
                if (!(svc->mode_properties & BT_GATT_CHRC_WRITE_WITHOUT_RESP) ||
                    bt_gatt_write_without_response(s->conn,svc->mode_handle,&mode,1,false)) {
                    close_session(slot,"Cannot select standard HID protocol"); return;
                }
            }
            if (++s->service_index < s->service_count) { advance(s,CHARS); continue; }
            s->service_index=0; s->report_index=0; advance(s,DESCRIPTORS); continue;
        } else if (s->stage == DESCRIPTORS) {
            if (s->report_index == s->report_count) {
                if (!s->subscribed) { close_session(slot,"No supported keyboard / mouse input"); }
                else { ready(slot); } return;
            }
            struct report *r=&s->reports[s->report_index]; svc=&s->services[r->service];
            if (!(r->properties & BT_GATT_CHRC_NOTIFY) || r->value >= r->end ||
                (!svc->parsed && !svc->boot) || (r->boot && !svc->boot) || (!r->boot && svc->boot)) {
                s->report_index++; continue;
            }
            begin_discovery(slot,BT_GATT_DISCOVER_DESCRIPTOR,r->value+1,r->end,NULL); return;
        } else if (s->stage == REFERENCE) {
            struct report *r=&s->reports[s->report_index];
            if (!r->reference) { s->report_index++; advance(s,DESCRIPTORS); continue; }
            s->reference_len=0; begin_read(slot,r->reference); return;
        } else if (s->stage == SUBSCRIBE) {
            struct report *r=&s->reports[s->report_index]; svc=&s->services[r->service];
            if (r->type != 1 || (!r->boot && !supported_report(svc,r->id))) { s->report_index++; advance(s,DESCRIPTORS); continue; }
            if (!r->ccc) { close_session(slot,"Input report has no notification CCC"); return; }
            r->conn=s->conn; r->epoch=atomic_get(&s->epoch);
            r->sub=(struct bt_gatt_subscribe_params){.notify=notification,.subscribe=subscribed_cb,
                .value_handle=r->value,.ccc_handle=r->ccc,.value=BT_GATT_CCC_NOTIFY,.min_security=BT_SECURITY_L2};
            atomic_set_bit(r->sub.flags,BT_GATT_SUBSCRIBE_FLAG_VOLATILE); s->operation=true;
            if (bt_gatt_subscribe(s->conn,&r->sub)) { close_session(slot,"Input subscription request failed"); }
            return;
        } else { return; }
    }
}
static void command_received(const struct command *cmd) {
    if(cmd->type!=FORGET && cmd->type!=CANCEL &&
       cmd->switch_epoch!=(uint32_t)atomic_get(&disable_generation))return;
    uint32_t now=k_uptime_get_32();
    if(!atomic_get(&inputs_enabled) && cmd->type!=FORGET && cmd->type!=CANCEL)return;
    if (cmd->type == SEARCH) {
        if(!lism_screen_active() || cmd->ui_epoch!=lism_screen_generation())return;
        if (cmd->slot >= LISM_INPUT_PEERS || sessions[cmd->slot].conn || sessions[cmd->slot].have_saved) {
            message(-1,"Choose an empty input slot first"); return;
        }
        if (busy_setup()) { message(-1,"Finish current pairing first"); return; }
        search_slot=cmd->slot; show_other=cmd->accept; search_until=now+30000;
        memset(candidates,0,sizeof(candidates)); memset(cache,0,sizeof(cache));
        k_mutex_lock(&status_lock,K_FOREVER);
        status.generation++; status.selected=search_slot; status.show_other=show_other;
        k_mutex_unlock(&status_lock);
        message(-1,show_other ? "All connectable BLE; HID checked on select" : "Choose a BLE keyboard / mouse");
    } else if (cmd->type == SELECT) {
        if(!lism_screen_active() || cmd->ui_epoch!=lism_screen_generation())return;
        if (!search_until || elapsed(now,search_until) || cmd->index >= LISM_HID_CANDIDATES || busy_setup()) { return; }
        struct candidate *c=&candidates[cmd->index]; struct session *s=&sessions[search_slot];
        if (!c->public.valid || !cmd->token || c->public.token != cmd->token ||
            now-c->seen > 3000 || now-c->connectable_seen > 3000 || s->conn || s->have_saved) {
            message(-1,"Selection expired; search again"); return;
        }
        if (identity_conflict(&c->addr,search_slot)) { message(-1,"Device already assigned to another role"); return; }
        s->legacy_available=s->legacy_allowed=s->legacy_used=s->legacy_revoked=false;
        memset(&s->legacy_target,0,sizeof(s->legacy_target)); legacy_token_refresh(search_slot);
        s->target=c->addr; s->target_known=true; s->pending=true; s->suspended=false; s->seen=c->connectable_seen; s->retry_at=now;
        if (IS_ENABLED(CONFIG_LISM_INPUT_AUTO_PAIR)) {
            s->legacy_allowed=true; s->legacy_target=s->target;
            legacy_token_refresh(search_slot);
        }
        search_until=0;
        k_mutex_lock(&status_lock,K_FOREVER);
        snprintf(status.peers[search_slot].name,24,"%s",c->public.name); status.generation++;
        k_mutex_unlock(&status_lock); publish();
    } else if (cmd->type == CANCEL) {
        search_until=0;
        for (unsigned i=0;i < LISM_INPUT_PEERS;i++) {
            struct session *s=&sessions[i];
            if (s->conn && s->stage != READY) {s->suspended=true;close_session(i,"Pairing cancelled");}
            if (!s->have_saved && s->stage != READY) {
                s->pending=false;s->target_known=false;s->suspended=true;
                s->legacy_allowed=false;publish();
            }
        }
        message(-1,"Search / pairing cancelled");
    } else if (cmd->type == AUTH_REPLY) {
        k_mutex_lock(&status_lock,K_FOREVER);
        bool valid=status.auth_type && cmd->token == status.auth_token && !elapsed(now,prompt_until);
        uint8_t slot=status.auth_slot, type=status.auth_type;
        k_mutex_unlock(&status_lock);
        if (!valid || slot >= LISM_INPUT_PEERS || !sessions[slot].conn || sessions[slot].stage != SECURITY) { return; }
        int rc=0;
        if (!cmd->accept) { rc=bt_conn_auth_cancel(sessions[slot].conn); }
        else if (type == LISM_INPUT_AUTH_CONFIRM) { rc=bt_conn_auth_passkey_confirm(sessions[slot].conn); }
        else if (type == LISM_INPUT_AUTH_ENTRY && cmd->passkey <= 999999) { rc=bt_conn_auth_passkey_entry(sessions[slot].conn,cmd->passkey); }
        else { return; }
        if (rc) { close_session(slot,"Pairing answer expired; Retry"); }
        clear_prompt(slot);
    } else if (cmd->slot < LISM_INPUT_PEERS) {
        struct session *s=&sessions[cmd->slot];
        if (cmd->type == LEGACY) {
            if (cmd->accept && !IS_ENABLED(CONFIG_LISM_INPUT_LEGACY_COMPAT)) {
                message(cmd->slot,"Legacy compatibility not available"); return;
            }
            bt_addr_le_t exact=s->have_saved ? s->saved.addr : s->target;
            if (!cmd->token || cmd->token != s->legacy_token ||
                (!s->target_known && !s->have_saved) || identity_conflict(&exact,cmd->slot) ||
                bt_addr_le_cmp(&exact,&s->legacy_target) ||
                (cmd->accept && (!s->legacy_available || s->conn))) {
                message(cmd->slot,"Compatibility choice expired; Retry"); return;
            }
            if (!cmd->accept) {
                /* Revoke the live gate immediately even if storage is faulty.
                 * Never claim persistent revocation until deletion succeeds. */
                s->legacy_revoked=true; s->suspended=true;
                close_session(cmd->slot,"Disabling legacy compatibility");
                char key[24]; snprintf(key,sizeof(key),"lism_inputs/legacy%u",cmd->slot);
                int rc=settings_delete(key);
                if (rc && rc != -ENOENT) {
                    legacy_token_refresh(cmd->slot);
                    message(cmd->slot,"Disable save failed; retry Disable"); return;
                }
                s->legacy_allowed=false; s->legacy_used=false;
                loaded_grant_valid[cmd->slot]=false;
                legacy_token_refresh(cmd->slot); publish();
                message(cmd->slot,"Legacy disabled; existing bond retained");
            } else {
                s->legacy_allowed=true; s->legacy_revoked=false; s->suspended=false; s->retry_at=now;
                legacy_token_refresh(cmd->slot); publish();
                message(cmd->slot,"Legacy allowed for this device; tap Retry");
            }
        } else if (cmd->type == RETRY) { s->suspended=false; s->retry_at=now; message(cmd->slot,"Waiting for device pairing advertisement"); }
        else if (cmd->type == FORGET) {
            search_until=0; s->suspended=true;
            bt_addr_le_t exact=s->conn ? *bt_conn_get_dst(s->conn) : s->have_saved ? s->saved.addr : s->target;
            if (identity_conflict(&exact,cmd->slot)) {
                message(cmd->slot,"Protected identity; pairing not removed"); return;
            }
            close_session(cmd->slot,"Forgetting only this input device");
            if (s->legacy_allowed || loaded_grant_valid[cmd->slot]) {
                char grant_key[24]; snprintf(grant_key,sizeof(grant_key),"lism_inputs/legacy%u",cmd->slot);
                int grant_delete=settings_delete(grant_key);
                if (grant_delete && grant_delete != -ENOENT) {
                    message(cmd->slot,"Forget compatibility save failed; retry"); return;
                }
            }
            s->legacy_available=s->legacy_allowed=s->legacy_used=s->legacy_revoked=false;
            loaded_grant_valid[cmd->slot]=false; legacy_token_refresh(cmd->slot); publish();
            int unpair_result=bt_unpair(BT_ID_DEFAULT,&exact);
            if (unpair_result && unpair_result != -ENOENT) {
                message(cmd->slot,"Forget failed; tap Forget again"); return;
            }
            char key[24]; snprintf(key,sizeof(key),"lism_inputs/peer%u",cmd->slot);
            int delete_result=settings_delete(key);
            if (delete_result && delete_result != -ENOENT) {
                message(cmd->slot,"Forget storage failed; tap Forget again"); return;
            }
            /* Remove old migration record only when it is the exact peer the
             * user explicitly forgot. Other slot/split/host bonds stay intact. */
            if (legacy_valid && !bt_addr_le_cmp(&legacy.addr,&exact)) {
                delete_result=settings_delete("lism_hid/peer");
                if (delete_result && delete_result != -ENOENT) {
                    message(cmd->slot,"Legacy save removal failed; Forget again"); return;
                }
                legacy_valid=false;
            }
            s->have_saved=false; s->target_known=false;
            k_mutex_lock(&status_lock,K_FOREVER); status.peers[cmd->slot].saved=false; status.generation++; k_mutex_unlock(&status_lock);
            publish(); message(cmd->slot,"Input forgotten; select Search to add");
        }
    }
    scanner_state();
}
static void control(struct k_work *work) {
    ARG_UNUSED(work);
    uint32_t disabled=(uint32_t)atomic_get(&disable_generation);
    if(disabled!=applied_disable_generation){
            atomic_clear(&inputs_enabled);search_until=0;
            lism_hid_scan_pause();
            for(unsigned i=0;i<LISM_INPUT_PEERS;i++){
                struct session *s=&sessions[i];s->pending=false;
                if(!s->have_saved){s->target_known=false;s->suspended=true;}
                if(s->conn && s->stage!=CLOSING)close_session(i,"Third-party BLE off; bond retained");
                atomic_clear(&s->input_ready);atomic_set(&s->clear,1);
            }
            kick();message(-1,"Third-party BLE off; no scanning");
            applied_disable_generation=disabled;
    }
    k_spinlock_key_t enable_key=k_spin_lock(&enable_lock);
    bool turn_on=atomic_get(&requested_enabled) && !atomic_get(&inputs_enabled) &&
       disabled==(uint32_t)atomic_get(&disable_generation);
    if(turn_on)atomic_set(&inputs_enabled,1);
    k_spin_unlock(&enable_lock,enable_key);
    if(turn_on){
        for(unsigned i=0;i<LISM_INPUT_PEERS;i++)if(sessions[i].have_saved){sessions[i].suspended=false;sessions[i].retry_at=k_uptime_get_32();}
        message(-1,"Third-party BLE on; reconnecting saved inputs");
    }
    /* Admission guard: release only third-party sources and pause reconnection
     * on observed timeslot starvation. Never alter ESB leases or host bonds. */
    if (atomic_get(&inputs_enabled) && lism_esb_coex_pressure()) {
        search_until=0;
        for (unsigned i=0;i<LISM_INPUT_PEERS;i++) if (sessions[i].conn) {
            sessions[i].suspended=true;
            close_session(i,"ESB priority: input paused; Retry");
        }
    }
    struct command cmd; for (unsigned budget=0;budget<4 && !k_msgq_get(&commands,&cmd,K_NO_WAIT);budget++) { command_received(&cmd); }
    struct event e; for (unsigned budget=0;budget<8 && !k_msgq_get(&events,&e,K_NO_WAIT);budget++) { event_received(&e); if (e.conn) { bt_conn_unref(e.conn); } }
    bool fault=atomic_set(&queue_fault,0);
    uint32_t now=k_uptime_get_32();
    for (unsigned i=0;i < LISM_INPUT_PEERS;i++) {
        struct session *s=&sessions[i];
        if (atomic_set(&s->closed,0)) { finish(i); }
        if (fault || atomic_set(&s->fault,0)) { close_session(i,"Input queue/report fault; Retry"); }
        if (s->conn && s->stage != READY && elapsed(now,s->deadline)) { close_session(i,"Input setup timeout; Retry"); }
    }
    if (search_until && elapsed(now,search_until)) { search_until=0; message(-1,"Search ended; tap Search to retry"); }
    if (prompt_until && elapsed(now,prompt_until)) {
        unsigned slot=status.auth_slot;
        if (slot < LISM_INPUT_PEERS) { bt_conn_auth_cancel(sessions[slot].conn); close_session(slot,"Pairing prompt timed out; Retry"); }
    }
    struct advertisement ad; for (unsigned budget=0;budget<4 && !k_msgq_get(&advertisements,&ad,K_NO_WAIT);budget++) { advertisement(&ad); }
    if(atomic_get(&inputs_enabled))for (unsigned i=0;i < LISM_INPUT_PEERS;i++) { connect_target(i); progress(i); }
    scanner_state();
    if (k_msgq_num_used_get(&commands) || k_msgq_num_used_get(&events) || k_msgq_num_used_get(&advertisements)) k_work_submit(&control_work);
    k_mutex_lock(&status_lock,K_FOREVER);
    status.searching=search_until != 0;
    status.seconds_left=search_until && !elapsed(now,search_until) ? MIN(60,(search_until-now+999)/1000) : 0;
    for (unsigned i=0;i < LISM_HID_CANDIDATES;i++) { status.candidates[i]=candidates[i].public; }
    k_mutex_unlock(&status_lock);
    bool cleanup_pending=false;
    for (unsigned i=0;i < LISM_INPUT_PEERS;i++) { cleanup_pending |= atomic_get(&sessions[i].clear) != 0; }
    if (cleanup_pending || atomic_get(&endpoint_generation) || atomic_get(&endpoint_changed) || k_msgq_num_used_get(&frames)) { kick(); }
}
static void tick(struct k_work *work) {
    ARG_UNUSED(work); k_work_submit(&control_work); k_work_reschedule(&tick_work,K_MSEC(200));
}
static bool valid_saved(struct saved_peer *peer,size_t length,settings_read_cb read,void *context,uint8_t version) {
    if (length != sizeof(*peer) || read(context,peer,sizeof(*peer)) != sizeof(*peer) ||
        peer->version != version || !bt_addr_le_cmp(&peer->addr,BT_ADDR_LE_ANY)) { return false; }
    peer->name[23]=0; return true;
}
static int settings_set(const char *name,size_t length,settings_read_cb read,void *context) {
    int grant_slot=!strcmp(name,"legacy0") ? 0 : !strcmp(name,"legacy1") ? 1 : -1;
    if (grant_slot >= 0) {
        struct legacy_grant *grant=&loaded_grants[grant_slot];
        loaded_grant_valid[grant_slot]=length == sizeof(*grant) &&
            read(context,grant,sizeof(*grant)) == sizeof(*grant) && grant->version == 1 &&
            grant->allowed == 1 && bt_addr_le_cmp(&grant->addr,BT_ADDR_LE_ANY);
        return loaded_grant_valid[grant_slot] ? 0 : -EINVAL;
    }
    int slot=!strcmp(name,"peer0") ? 0 : !strcmp(name,"peer1") ? 1 : -1;
    if (slot < 0) { return -ENOENT; }
    loaded_valid[slot]=valid_saved(&loaded[slot],length,read,context,2); return loaded_valid[slot] ? 0 : -EINVAL;
}
static int legacy_set(const char *name,size_t length,settings_read_cb read,void *context) {
    if (strcmp(name,"peer")) { return -ENOENT; }
    legacy_valid=valid_saved(&legacy,length,read,context,1); return legacy_valid ? 0 : -EINVAL;
}
static int input_settings_commit(void) {
    struct event e={.type=SETTINGS_READY,.slot=0xff}; queue_event(&e,NULL);
    k_work_reschedule(&tick_work,K_MSEC(200)); return 0;
}
SETTINGS_STATIC_HANDLER_DEFINE(lism_inputs,"lism_inputs",NULL,settings_set,input_settings_commit,NULL);
SETTINGS_STATIC_HANDLER_DEFINE(lism_inputs_legacy,"lism_hid",NULL,legacy_set,NULL,NULL);
void lism_input_host_snapshot(struct lism_input_status *out) {
    k_mutex_lock(&status_lock,K_FOREVER); *out=status; k_mutex_unlock(&status_lock);out->enabled=atomic_get(&requested_enabled);
}
static void request(struct command cmd) {
    cmd.switch_epoch=atomic_get(&disable_generation);
    if (k_msgq_put(&commands,&cmd,K_NO_WAIT)) { atomic_set(&queue_fault,1); }
    k_work_submit(&control_work);
}
void lism_input_host_search(uint8_t slot,bool other) { request((struct command){.type=SEARCH,.slot=slot,.accept=other,.ui_epoch=lism_screen_generation()}); }
void lism_input_host_cancel(void) { request((struct command){.type=CANCEL}); }
void lism_input_host_select(uint8_t index,uint32_t token) { request((struct command){.type=SELECT,.index=index,.token=token,.ui_epoch=lism_screen_generation()}); }
void lism_input_host_forget(uint8_t slot) { request((struct command){.type=FORGET,.slot=slot}); }
void lism_input_host_retry(uint8_t slot) { request((struct command){.type=RETRY,.slot=slot}); }
void lism_input_host_legacy(uint8_t slot,uint32_t token,bool allow) {
    request((struct command){.type=LEGACY,.slot=slot,.token=token,.accept=allow});
}
void lism_input_host_auth(uint32_t token,bool accept,uint32_t passkey) { request((struct command){.type=AUTH_REPLY,.token=token,.accept=accept,.passkey=passkey}); }
static int init(void) {
    int rc=bt_le_scan_cb_register(&scan_callbacks);
    if (rc) { message(-1,"BLE input observer registration failed"); return rc; }
    message(-1,"BLE inputs: choose an input slot"); return 0;
}
SYS_INIT(init,APPLICATION,CONFIG_ZMK_BLE_INIT_PRIORITY);

bool lism_input_host_enabled(void){return atomic_get(&inputs_enabled);}
void lism_input_host_set_enabled(bool enabled){
    k_spinlock_key_t key=k_spin_lock(&enable_lock);
    atomic_set(&requested_enabled,enabled);
    /* Disable admission immediately; the coordinator performs stop/disconnect
     * and neutral reports without blocking LVGL or touching host BLE bonds. */
    if(!enabled){atomic_inc(&disable_generation);atomic_clear(&inputs_enabled);atomic_clear(&scan_needed);atomic_clear(&advertisements_enabled);}
    k_spin_unlock(&enable_lock,key);
    k_work_submit(&control_work);
}

/* This diagnostic thread never runs in the input/display/ESB work queues.
 * Read one connected generic peer every three seconds, and none while OFF. */
#include <zephyr/bluetooth/hci.h>
#include "kb_route/signal.h"
static struct {struct bt_conn *conn;uint32_t epoch,disabled;int8_t dbm;int64_t seen;} rssi_cache[LISM_INPUT_PEERS];
static void rssi_worker(void*a,void*b,void*c){
    ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);unsigned slot=0;
    for(;;){
        k_sleep(K_SECONDS(3));unsigned i=slot++%LISM_INPUT_PEERS;
        if(!lism_input_host_enabled())continue;
        struct bt_conn*conn=NULL;uint32_t epoch=0,disabled=atomic_get(&disable_generation);
        k_spinlock_key_t k=k_spin_lock(&identity_lock);
        if(identities[i].conn && atomic_get(&sessions[i].input_ready)){
            conn=bt_conn_ref(identities[i].conn);epoch=identities[i].epoch;
        }
        k_spin_unlock(&identity_lock,k);if(!conn)continue;
        uint16_t handle;struct net_buf*rsp=NULL;int rc=bt_hci_get_conn_handle(conn,&handle);
        if(!rc && lism_input_host_enabled() && disabled==(uint32_t)atomic_get(&disable_generation)){
            struct net_buf*cmd=bt_hci_cmd_create(BT_HCI_OP_READ_RSSI,sizeof(struct bt_hci_cp_read_rssi));
            if(cmd){struct bt_hci_cp_read_rssi*cp=net_buf_add(cmd,sizeof(*cp));cp->handle=sys_cpu_to_le16(handle);rc=bt_hci_cmd_send_sync(BT_HCI_OP_READ_RSSI,cmd,&rsp);}else rc=-ENOMEM;
        }else rc=-ECANCELED;
        if(!rc && rsp && rsp->len>=sizeof(struct bt_hci_rp_read_rssi)){
            struct bt_hci_rp_read_rssi*rp=(void*)rsp->data;
            k=k_spin_lock(&identity_lock);
            if(!rp->status && rp->rssi<0 && rp->rssi>=-126 && sys_le16_to_cpu(rp->handle)==handle &&
               lism_input_host_enabled() && disabled==(uint32_t)atomic_get(&disable_generation) &&
               identities[i].conn==conn && identities[i].epoch==epoch && atomic_get(&sessions[i].input_ready)){
                rssi_cache[i].conn=conn;rssi_cache[i].epoch=epoch;rssi_cache[i].disabled=disabled;
                rssi_cache[i].dbm=rp->rssi;rssi_cache[i].seen=k_uptime_get();
            }
            k_spin_unlock(&identity_lock,k);
        }
        if(rsp){net_buf_unref(rsp);}
        bt_conn_unref(conn);
    }
}
K_THREAD_DEFINE(lism_input_rssi_thread,1280,rssi_worker,NULL,NULL,NULL,10,0,0);
bool lism_input_host_signal(int8_t*dbm){
    if(!lism_input_host_enabled())return false;
    bool found=false;int weakest=0;k_spinlock_key_t k=k_spin_lock(&identity_lock);
    for(unsigned i=0;i<LISM_INPUT_PEERS;i++){
        if(rssi_cache[i].seen && k_uptime_get()-rssi_cache[i].seen<8000 &&
           rssi_cache[i].disabled==(uint32_t)atomic_get(&disable_generation) &&
           identities[i].conn==rssi_cache[i].conn && identities[i].epoch==rssi_cache[i].epoch && atomic_get(&sessions[i].input_ready)){
            if(!found || rssi_cache[i].dbm<weakest){weakest=rssi_cache[i].dbm;}
            found=true;
        }
    }
    k_spin_unlock(&identity_lock,k);*dbm=weakest;return found;
}
