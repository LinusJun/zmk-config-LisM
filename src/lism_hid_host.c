/* Experimental standard BLE HOGP relative-mouse client.
 * Bluetooth callbacks only copy bounded events; the coordinator owns all
 * session/GATT/scanner state. HID mutations run only in input_thread. */
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
#include <zmk/events/endpoint_changed.h>
#if IS_ENABLED(CONFIG_ZMK_USB)
#include <zmk/events/usb_conn_state_changed.h>
#endif
#if IS_ENABLED(CONFIG_ZMK_BLE)
#include <zmk/events/ble_active_profile_changed.h>
#endif
#include "lism_hid_host.h"
#include "lism_hid_parser.h"
#include "xiao147_ui.h"

LOG_MODULE_REGISTER(lism_hid_host, LOG_LEVEL_INF);
#define MAX_CHARS 24
#define MAX_REPORT_CHARS 16
#define ADV_CACHE_SIZE 8
#define INPUT_KICK 0x7ffe
#define SAVED_VERSION 1
#define SESSION_TIMEOUT_MS 20000
#define EVENT_BYTES 64

enum phase { IDLE, CONNECTING, SECURITY, SERVICE, CHARS, MAP, DESCRIPTORS,
             REFERENCE, SUBSCRIBE, READY, DISCONNECTING };
enum command { CMD_PAIR, CMD_CANCEL, CMD_SELECT, CMD_FORGET };
enum event_type { EV_CONNECTED, EV_DISCONNECTED, EV_SECURITY, EV_DISCOVERY,
                  EV_READ, EV_SUBSCRIBED, EV_UNSUBSCRIBED, EV_SETTINGS };
struct command_event { enum command kind; uint8_t index; uint32_t token; };
struct saved_peer { uint8_t version; bt_addr_le_t addr; char name[24]; };
struct candidate {
    bt_addr_le_t addr;
    struct lism_hid_candidate public;
    uint32_t seen;
    bool connectable, hid_hint;
};
struct ad_event {
    bt_addr_le_t addr;
    char name[24];
    uint32_t seen;
    int8_t rssi;
    bool connectable, hid_hint;
};
struct report_char {
    uint16_t value, end, ccc, reference;
    uint8_t id, type, properties;
    uint32_t epoch;
    struct bt_conn *conn;
    struct bt_gatt_subscribe_params sub;
};
struct discovery_op {
    struct bt_gatt_discover_params params;
    uint32_t epoch;
    enum phase stage;
    uint8_t report;
};
struct read_op {
    struct bt_gatt_read_params params;
    uint32_t epoch;
    enum phase stage;
    uint16_t received;
    uint8_t report;
};
struct bt_event {
    enum event_type kind;
    struct bt_conn *conn; /* Owned event reference; coordinator always unrefs. */
    uint32_t epoch;
    enum phase stage;
    uint16_t handle, end, value, uuid;
    uint8_t report, properties, error, level, length;
    bool complete;
    uint8_t data[EVENT_BYTES];
};
struct raw_frame {
    uint32_t epoch;
    uint32_t endpoint_generation;
    int endpoint;
    uint8_t id, len;
    uint8_t bytes[LISM_HID_MAX_REPORT_BYTES];
};
static struct saved_peer saved, loaded;
static struct candidate candidates[LISM_HID_CANDIDATES], ad_cache[ADV_CACHE_SIZE];
static struct lism_hid_status status;
static struct bt_conn *peer;
static bt_addr_le_t selected;
static enum phase phase;
static uint32_t pairing_until, deadline, retry_at, selected_seen;
static uint32_t next_candidate_token;
static bool have_saved, loaded_valid, settings_ready, newly_selected, cancelled;
static bool reconnect_suspended, advertisement_pending, security_started;
static bool operation_started, map_finished, mode_set;
static uint16_t service_start, service_end, map_handle, mode_handle;
static uint8_t mode_properties, char_count, report_count, report_index, subscribed;
static struct report_char reports[MAX_REPORT_CHARS];
/* Alternate operation storage: the immediately completed callback never sees
 * its params overwritten while the next request is submitted from another CPU
 * context. All requests are cancelled before retiring a connection/session. */
static struct discovery_op discovery_ops[2];
static struct read_op read_ops[2];
static struct bt_uuid_16 hid_service_uuid = BT_UUID_INIT_16(0x1812);
static uint8_t discovery_slot, read_slot;
static uint8_t map[LISM_HID_MAX_MAP_BYTES], reference_data[2];
static size_t map_len, reference_len;
static struct lism_hid_descriptor descriptor;
static struct lism_hid_state decoded;
static atomic_t epoch, input_ready, input_fault, clear_input;
static atomic_t pending_release, endpoint_generation, endpoint_reset_generation;
static atomic_t event_fault, emergency_command, closed_index;
static atomic_t scan_needed, scan_paused, advertisements_enabled;
static uint8_t own_buttons;
static uint16_t button_reports_seen;
static int input_endpoint = -1;
static int release_destination = -1;
static uint32_t input_generation, input_reset_generation, release_reset_generation;
static bool require_release = true;
K_MUTEX_DEFINE(state_lock);
K_MUTEX_DEFINE(report_lock);
K_MSGQ_DEFINE(frames, sizeof(struct raw_frame), 16, 4);
K_MSGQ_DEFINE(bt_events, sizeof(struct bt_event), 32, 4);
K_MSGQ_DEFINE(advertisements, sizeof(struct ad_event), 8, 4);
K_MSGQ_DEFINE(commands, sizeof(struct command_event), 8, 4);
static void control(struct k_work *work);
static void tick(struct k_work *work);
K_WORK_DEFINE(control_work, control);
K_WORK_DELAYABLE_DEFINE(tick_work, tick);

DEVICE_DEFINE(lism_hid_input, "LisM BLE HID bridge", NULL, NULL, NULL, NULL,
              POST_KERNEL, CONFIG_INPUT_INIT_PRIORITY, NULL);

static void kick_input(void)
{
    /* A failed wake is retried by tick; clear_input remains latched. */
    input_report(DEVICE_GET(lism_hid_input), INPUT_EV_MSC, INPUT_KICK, 0,
                 true, K_NO_WAIT);
}

static void message(const char *text)
{
    k_mutex_lock(&state_lock, K_FOREVER);
    snprintf(status.message, sizeof(status.message), "%s", text);
    k_mutex_unlock(&state_lock);
    LOG_INF("%s", text);
}

static void invalidate_input(void)
{
    atomic_clear(&input_ready);
    atomic_set(&clear_input, 1);
    kick_input();
}

static void schedule_release(void)
{
    if (!atomic_get(&pending_release)) {
        release_destination = input_endpoint;
        release_reset_generation = input_reset_generation;
        atomic_set(&pending_release, 1);
    }
}

static bool retry_release(void)
{
    if (!atomic_get(&pending_release)) { return true; }
    /* A changed/disconnected endpoint ends retries to that selected target;
     * never send its retry to an unrelated new host. Framework old-host clear
     * is best-effort, NOT proof the previous host received a release. This MVP
     * cannot route a late release to an inactive BLE profile. An unrelated
     * transport observer event alone does not end the current-host retry. */
    int destination = xiao147_pointer_endpoint();
    if (destination < 0 || destination != release_destination ||
        (uint32_t)atomic_get(&endpoint_reset_generation) != release_reset_generation) {
        atomic_clear(&pending_release);
        return true;
    }
    zmk_hid_mouse_movement_set(0, 0);
    zmk_hid_mouse_scroll_set(0, 0);
    /* A failed release retains an independent latch after logical ownership
     * counts have already been decremented. Retry never decrements again. */
    if (xiao147_pointer_endpoint() != destination ||
        (uint32_t)atomic_get(&endpoint_reset_generation) != release_reset_generation) {
        atomic_clear(&pending_release);
        return true;
    }
    if (zmk_endpoint_send_mouse_report() >= 0) { atomic_clear(&pending_release); }
    return !atomic_get(&pending_release);
}

static void release_owned(void)
{
    bool had_owned = own_buttons != 0;
    for (int i = 0; i < ZMK_HID_MOUSE_NUM_BUTTONS; i++) {
        if (own_buttons & BIT(i)) { zmk_hid_mouse_button_release(i); }
    }
    own_buttons = 0;
    button_reports_seen = 0;
    memset(&decoded, 0, sizeof(decoded));
    require_release = true;
    if (had_owned) { schedule_release(); }
    (void)retry_release();
}

static int16_t clamp_axis(int32_t value, int32_t limit)
{
    return CLAMP(value, -limit, limit);
}

static bool frame_current(const struct raw_frame *frame, int destination, uint32_t generation)
{
    return atomic_get(&input_ready) && !atomic_get(&clear_input) && destination >= 0 &&
        frame->epoch == (uint32_t)atomic_get(&epoch) && frame->endpoint == destination &&
        frame->endpoint_generation == generation &&
        generation == (uint32_t)atomic_get(&endpoint_generation) &&
        xiao147_pointer_endpoint() == destination;
}

static void bridge_input(struct input_event *event, void *unused)
{
    ARG_UNUSED(unused);
    if (event->type != INPUT_EV_MSC || event->code != INPUT_KICK) { return; }
    k_mutex_lock(&report_lock, K_FOREVER);
    int destination = xiao147_pointer_endpoint();
    uint32_t generation = atomic_get(&endpoint_generation);
    uint32_t session = atomic_get(&epoch);
    bool transitioned = atomic_set(&clear_input, 0) || input_endpoint != destination ||
                        input_generation != generation;
    if (transitioned) {
        release_owned();
        input_endpoint = destination;
        input_generation = generation;
        input_reset_generation = atomic_get(&endpoint_reset_generation);
        /* Endpoint/ownership transitions invalidate the entire old backlog,
         * including old all-up/press pairs stamped with the same host index. */
        k_msgq_purge(&frames);
    }
    if (!transitioned) { (void)retry_release(); }
    if (atomic_get(&pending_release)) {
        k_msgq_purge(&frames);
        k_mutex_unlock(&report_lock);
        return;
    }
    struct raw_frame frame;
    while (k_msgq_get(&frames, &frame, K_NO_WAIT) == 0) {
        if (session != (uint32_t)atomic_get(&epoch)) {
            release_owned(); k_msgq_purge(&frames); break;
        }
        if (!frame_current(&frame, destination, generation)) {
            /* Old queued frames may be discarded individually, but an actual
             * current ownership/session transition cancels the whole batch. */
            if (!atomic_get(&input_ready) || atomic_get(&clear_input) ||
                generation != (uint32_t)atomic_get(&endpoint_generation) ||
                xiao147_pointer_endpoint() != destination) {
                release_owned(); k_msgq_purge(&frames); break;
            }
            continue;
        }
        struct lism_hid_event out;
        enum lism_hid_error err = lism_hid_decode(&descriptor, &decoded, frame.id,
                                                  frame.bytes, frame.len, &out);
        if (!frame_current(&frame, destination, generation)) {
            release_owned(); k_msgq_purge(&frames); break;
        }
        if (err != LISM_HID_OK) {
            release_owned();
            atomic_set(&input_fault, 1);
            atomic_clear(&input_ready);
            k_work_submit(&control_work);
            break;
        }
        if (require_release) {
            uint16_t required = 0;
            for (unsigned i = 0; i < descriptor.report_count; i++) {
                if (!descriptor.reports[i].button_mask) { continue; }
                required |= (uint16_t)BIT(i);
                if (descriptor.reports[i].id == frame.id) {
                    button_reports_seen |= (uint16_t)BIT(i);
                }
            }
            /* An axis-only report cannot claim unknown button reports are
             * released. Every declared button report must have been observed
             * in this generation, with the combined current state all-up. */
            if ((button_reports_seen & required) != required) { continue; }
            if (out.buttons) { continue; }
            require_release = false;
        }
        uint8_t desired = out.buttons & BIT_MASK(ZMK_HID_MOUSE_NUM_BUTTONS);
        if (!frame_current(&frame, destination, generation)) {
            release_owned(); k_msgq_purge(&frames); break;
        }
        uint8_t change = own_buttons ^ desired;
        for (int i = 0; i < ZMK_HID_MOUSE_NUM_BUTTONS; i++) {
            if (change & BIT(i)) {
                if (desired & BIT(i)) { zmk_hid_mouse_button_press(i); }
                else { zmk_hid_mouse_button_release(i); }
            }
        }
        own_buttons = desired;
        /* Preserve the mouse's processed wheel directions; no second natural-
         * scroll inversion or keyboard-layer trackball processors. */
        zmk_hid_mouse_movement_set(clamp_axis(out.dx, 32767), clamp_axis(out.dy, 32767));
        zmk_hid_mouse_scroll_set(clamp_axis(out.pan, 32767), clamp_axis(out.wheel, 32767));
        if (!frame_current(&frame, destination, generation)) {
            zmk_hid_mouse_movement_set(0, 0); zmk_hid_mouse_scroll_set(0, 0);
            release_owned(); k_msgq_purge(&frames); break;
        }
        int result = zmk_endpoint_send_mouse_report();
        zmk_hid_mouse_movement_set(0, 0);
        zmk_hid_mouse_scroll_set(0, 0);
        if (result < 0) {
            /* Even a failed all-up report needs retry: own_buttons may already
             * be zero although the host last saw a successful pressed state. */
            schedule_release();
            release_owned();
            atomic_set(&input_fault, 1);
            atomic_clear(&input_ready);
            k_work_submit(&control_work);
            break;
        }
        if (!frame_current(&frame, destination, generation)) {
            release_owned(); k_msgq_purge(&frames); break;
        }
    }
    k_mutex_unlock(&report_lock);
}
INPUT_CALLBACK_DEFINE(DEVICE_GET(lism_hid_input), bridge_input, NULL);

static int bridge_endpoint_event(const zmk_event_t *event)
{
    /* A selected host may disconnect/reconnect without a new mouse report.
     * Clear source ownership in input_thread, never mutate HID in an event
     * callback. Keep the mouse's BLE/GATT session and input_ready intact. */
    atomic_inc(&endpoint_generation);
    if (as_zmk_endpoint_changed(event) || xiao147_pointer_endpoint() < 0) {
        atomic_inc(&endpoint_reset_generation);
    }
    atomic_set(&clear_input, 1);
    kick_input();
    return ZMK_EV_EVENT_BUBBLE;
}
ZMK_LISTENER(lism_hid_endpoint, bridge_endpoint_event);
ZMK_SUBSCRIPTION(lism_hid_endpoint, zmk_endpoint_changed);
#if IS_ENABLED(CONFIG_ZMK_USB)
ZMK_SUBSCRIPTION(lism_hid_endpoint, zmk_usb_conn_state_changed);
#endif
#if IS_ENABLED(CONFIG_ZMK_BLE)
ZMK_SUBSCRIPTION(lism_hid_endpoint, zmk_ble_active_profile_changed);
#endif


static bool elapsed(uint32_t now, uint32_t when)
{
    return (int32_t)(now - when) >= 0;
}

/* No mutex, settings write, GATT operation or peer mutation in BT callbacks. */
static bool queue_bt(struct bt_event *event, struct bt_conn *conn)
{
    event->conn = conn ? bt_conn_ref(conn) : NULL;
    if (k_msgq_put(&bt_events, event, K_NO_WAIT)) {
        if (conn && (event->kind == EV_DISCONNECTED ||
                     (event->kind == EV_CONNECTED && event->error))) {
            /* Even an overflowing queue cannot lose the teardown completion. */
            atomic_set(&closed_index, bt_conn_index(conn) + 1);
        }
        if (event->conn) { bt_conn_unref(event->conn); }
        atomic_set(&event_fault, 1);
        k_work_submit(&control_work);
        return false;
    }
    k_work_submit(&control_work);
    return true;
}
static uint16_t short_uuid(const struct bt_uuid *uuid)
{
    return uuid && uuid->type == BT_UUID_TYPE_16 ? BT_UUID_16(uuid)->val : 0;
}
static uint8_t discovered(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                          struct bt_gatt_discover_params *params)
{
    struct discovery_op *op = CONTAINER_OF(params, struct discovery_op, params);
    if (op->epoch != (uint32_t)atomic_get(&epoch)) { return BT_GATT_ITER_STOP; }
    struct bt_event event = {.kind = EV_DISCOVERY, .epoch = op->epoch,
        .stage = op->stage, .report = op->report, .complete = attr == NULL};
    if (attr) {
        event.handle = attr->handle;
        if (op->stage == SERVICE) {
            const struct bt_gatt_service_val *service = attr->user_data;
            event.end = service->end_handle;
            event.complete = true; /* One HID service, no second discovery. */
        } else if (op->stage == CHARS) {
            const struct bt_gatt_chrc *chrc = attr->user_data;
            event.uuid = short_uuid(chrc->uuid);
            event.value = chrc->value_handle;
            event.properties = chrc->properties;
        } else { event.uuid = short_uuid(attr->uuid); }
    }
    if (!queue_bt(&event, conn)) { return BT_GATT_ITER_STOP; }
    return event.complete ? BT_GATT_ITER_STOP : BT_GATT_ITER_CONTINUE;
}
static uint8_t read_result(struct bt_conn *conn, uint8_t error,
                           struct bt_gatt_read_params *params, const void *data,
                           uint16_t length)
{
    struct read_op *op = CONTAINER_OF(params, struct read_op, params);
    if (op->epoch != (uint32_t)atomic_get(&epoch)) { return BT_GATT_ITER_STOP; }
    struct bt_event event = {.kind = EV_READ, .epoch = op->epoch,
        .stage = op->stage, .report = op->report, .error = error, .complete = !data};
    unsigned limit = op->stage == MAP ? LISM_HID_MAX_MAP_BYTES : 2;
    if (error || !data) {
        queue_bt(&event, conn);
        return BT_GATT_ITER_STOP;
    }
    if (length > limit - op->received) {
        event.error = 0xff;
        queue_bt(&event, conn);
        return BT_GATT_ITER_STOP;
    }
    op->received += length;
    const uint8_t *bytes = data;
    for (unsigned at = 0; at < length;) {
        event.length = MIN(EVENT_BYTES, length - at);
        memcpy(event.data, bytes + at, event.length);
        if (!queue_bt(&event, conn)) { return BT_GATT_ITER_STOP; }
        at += event.length;
    }
    return BT_GATT_ITER_CONTINUE;
}
static uint8_t notification(struct bt_conn *conn, struct bt_gatt_subscribe_params *params,
                            const void *data, uint16_t length)
{
    struct report_char *report = CONTAINER_OF(params, struct report_char, sub);
    if (conn != report->conn || report->epoch != (uint32_t)atomic_get(&epoch)) {
        return BT_GATT_ITER_STOP;
    }
    if (!data) {
        struct bt_event event = {.kind = EV_UNSUBSCRIBED, .epoch = report->epoch};
        queue_bt(&event, conn);
        return BT_GATT_ITER_STOP;
    }
    if (!atomic_get(&input_ready)) { return BT_GATT_ITER_CONTINUE; }
    if (!length || length > LISM_HID_MAX_REPORT_BYTES) {
        atomic_set(&input_fault, 1);
        atomic_clear(&input_ready);
        k_work_submit(&control_work);
        return BT_GATT_ITER_STOP;
    }
    struct raw_frame frame = {.epoch = report->epoch, .id = report->id,
        .endpoint_generation = atomic_get(&endpoint_generation),
        .len = length, .endpoint = xiao147_pointer_endpoint()};
    memcpy(frame.bytes, data, length);
    if (k_msgq_put(&frames, &frame, K_NO_WAIT)) {
        atomic_set(&input_fault, 1);
        atomic_clear(&input_ready);
        k_work_submit(&control_work);
    } else { kick_input(); }
    return BT_GATT_ITER_CONTINUE;
}
static void subscribed_cb(struct bt_conn *conn, uint8_t error,
                          struct bt_gatt_subscribe_params *params)
{
    struct report_char *report = CONTAINER_OF(params, struct report_char, sub);
    struct bt_event event = {.kind = EV_SUBSCRIBED, .epoch = report->epoch,
        .report = report - reports, .error = error};
    if (conn == report->conn && report->epoch == (uint32_t)atomic_get(&epoch)) {
        queue_bt(&event, conn);
    }
}
static void connected(struct bt_conn *conn, uint8_t error)
{
    struct bt_event event = {.kind = EV_CONNECTED, .error = error};
    queue_bt(&event, conn);
}
static void disconnected(struct bt_conn *conn, uint8_t reason)
{
    struct bt_event event = {.kind = EV_DISCONNECTED, .error = reason};
    queue_bt(&event, conn);
}
static void security_changed(struct bt_conn *conn, bt_security_t level, enum bt_security_err error)
{
    struct bt_event event = {.kind = EV_SECURITY, .error = error, .level = level};
    queue_bt(&event, conn);
}
BT_CONN_CB_DEFINE(mouse_callbacks) = {
    .connected = connected, .disconnected = disconnected,
    .security_changed = security_changed,
};

struct ad_fields { char name[24]; bool hid; };
static bool parse_ad(struct bt_data *data, void *context)
{
    struct ad_fields *fields = context;
    if (data->type == BT_DATA_NAME_COMPLETE || data->type == BT_DATA_NAME_SHORTENED) {
        size_t length = MIN(data->data_len, sizeof(fields->name) - 1);
        for (size_t i = 0; i < length; i++) {
            fields->name[i] = data->data[i] >= 32 && data->data[i] < 127 ? data->data[i] : '?';
        }
        fields->name[length] = 0;
    } else if (data->type == BT_DATA_UUID16_ALL || data->type == BT_DATA_UUID16_SOME) {
        for (size_t i = 0; i + 1 < data->data_len; i += 2) {
            if (sys_get_le16(data->data + i) == 0x1812) { fields->hid = true; }
        }
    } else if (data->type == BT_DATA_GAP_APPEARANCE && data->data_len == 2) {
        uint16_t appearance = sys_get_le16(data->data);
        fields->hid |= appearance == 0x03c0 || appearance == 0x03c2 || appearance == 0x03c9;
        /* Generic HID, mouse or touchpad hint; Report Map is still decisive. */
    }
    return true;
}
static void scan_received(const struct bt_le_scan_recv_info *info, struct net_buf_simple *data)
{
    if (!atomic_get(&advertisements_enabled)) { return; }
    struct ad_fields fields = {0};
    struct net_buf_simple copy = *data;
    bt_data_parse(&copy, parse_ad, &fields);
    struct ad_event event = {.seen = k_uptime_get_32(), .rssi = info->rssi,
        .connectable = (info->adv_props & BT_GAP_ADV_PROP_CONNECTABLE) != 0,
        .hid_hint = fields.hid};
    bt_addr_le_copy(&event.addr, info->addr);
    memcpy(event.name, fields.name, sizeof(event.name));
    /* Low-priority advertisements may be dropped under load; never displace
     * control / connection / GATT events or mutate candidate slots in RX. */
    if (!k_msgq_put(&advertisements, &event, K_NO_WAIT)) { k_work_submit(&control_work); }
}
static struct bt_le_scan_cb scan_callbacks = {.recv = scan_received};

static bool current_event(const struct bt_event *event)
{
    return event->conn == peer && peer &&
        event->epoch == (uint32_t)atomic_get(&epoch) && phase != DISCONNECTING;
}
static void cancel_requests(struct bt_conn *conn)
{
    for (unsigned i = 0; i < 2; i++) {
        bt_gatt_cancel(conn, &discovery_ops[i].params);
        bt_gatt_cancel(conn, &read_ops[i].params);
    }
}
static void scanner_state(void)
{
    bool need = settings_ready && !peer && !reconnect_suspended &&
                (have_saved || pairing_until != 0);
    atomic_set(&scan_needed, need);
    atomic_set(&advertisements_enabled, need);
    if (!atomic_get(&scan_paused)) { lism_hid_scan_resume(); }
}
bool lism_hid_host_scan_needed(void) { return atomic_get(&scan_needed) != 0; }
bool lism_hid_host_scan_paused(void) { return atomic_get(&scan_paused) != 0; }

static bool lism_hid_claimed_identity(const bt_addr_le_t *addr);
bool lism_hid_host_claim_ad(const bt_addr_le_t *addr)
{
    /* Never read coordinator-owned structs in a Bluetooth callback. Saved and
     * selected identity are published under a short spinlock below. */
    return lism_hid_claimed_identity(addr);
}
static struct k_spinlock identity_lock;
static bt_addr_le_t claimed_saved, claimed_target;
static bool claim_saved, claim_target;
static bool lism_hid_claimed_identity(const bt_addr_le_t *addr)
{
    k_spinlock_key_t key = k_spin_lock(&identity_lock);
    bool claimed = addr && ((claim_saved && !bt_addr_le_cmp(addr, &claimed_saved)) ||
                             (claim_target && !bt_addr_le_cmp(addr, &claimed_target)));
    k_spin_unlock(&identity_lock, key);
    return claimed;
}
static void publish_identities(void)
{
    k_spinlock_key_t key = k_spin_lock(&identity_lock);
    claim_saved = have_saved;
    claim_target = peer != NULL;
    if (claim_saved) { bt_addr_le_copy(&claimed_saved, &saved.addr); }
    if (claim_target) { bt_addr_le_copy(&claimed_target, &selected); }
    k_spin_unlock(&identity_lock, key);
}
static bool is_bond;
static void match_bond(const struct bt_bond_info *info, void *context)
{
    if (!bt_addr_le_cmp(&info->addr, context)) { is_bond = true; }
}
static void save_identity(void)
{
    /* Persist only after service/map/reference/CCCs are validated and READY.
     * Never reserve a failed/unsupported candidate as the saved mouse. */
    const bt_addr_le_t *addr = bt_conn_get_dst(peer);
    is_bond = false;
    bt_foreach_bond(BT_ID_DEFAULT, match_bond, (void *)addr);
    if (!is_bond) { return; }
    saved.version = SAVED_VERSION;
    bt_addr_le_copy(&saved.addr, addr);
    k_mutex_lock(&state_lock, K_FOREVER);
    snprintf(saved.name, sizeof(saved.name), "%s", status.name);
    have_saved = status.saved = true;
    k_mutex_unlock(&state_lock);
    publish_identities();
    if (settings_save_one("lism_hid/peer", &saved, sizeof(saved))) {
        message("Mouse ready; identity save failed");
    }
}
static void finish_session(void)
{
    if (!peer) { return; }
    /* Called only after a failed create or actual disconnect completion. A
     * retained peer reference prevents pool-slot reuse until this teardown. */
    cancel_requests(peer);
    struct bt_conn *old = peer;
    peer = NULL;
    invalidate_input();
    atomic_inc(&epoch);
    phase = IDLE;
    operation_started = false;
    advertisement_pending = false;
    newly_selected = false;
    atomic_clear(&scan_paused);
    retry_at = k_uptime_get_32() + 5000;
    k_mutex_lock(&state_lock, K_FOREVER);
    status.connected = status.ready = false;
    k_mutex_unlock(&state_lock);
    publish_identities();
    bt_conn_unref(old);
    scanner_state();
}
static void begin_close(const char *why)
{
    if (phase != DISCONNECTING) {
        invalidate_input();
        atomic_inc(&epoch); /* Revoke queued notifications immediately. */
        advertisement_pending = false;
        cancelled = true;
        phase = DISCONNECTING;
        operation_started = false;
        if (peer) { cancel_requests(peer); }
        message(why);
        k_mutex_lock(&state_lock, K_FOREVER);
        status.ready = false;
        k_mutex_unlock(&state_lock);
    }
    if (!peer) {
        phase = IDLE;
        retry_at = k_uptime_get_32() + 5000;
        atomic_clear(&scan_paused);
        scanner_state();
        return;
    }
    int result = bt_conn_disconnect(peer, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
    if (result < 0) {
        /* Zephyr 4.1 bt_conn_disconnect also cancels this connection's pending
         * initiator. Completion may be connected(error), not disconnected().
         * Keep its reference and retry; never free a potentially live peer. */
        LOG_WRN("Mouse disconnect/cancel returned %d; retaining session", result);
    }
    deadline = k_uptime_get_32() + 3000;
}
static void fail(const char *why) { begin_close(why); }
static void advance(enum phase next)
{
    operation_started = false;
    phase = next;
}
static bool mouse_report(uint8_t id)
{
    for (unsigned i = 0; i < descriptor.report_count; i++) {
        if (descriptor.reports[i].id == id && descriptor.reports[i].mouse_fields) { return true; }
    }
    return false;
}
static void begin_discovery(uint8_t type, uint16_t start, uint16_t end, const struct bt_uuid *uuid)
{
    if (!start || start > end) { fail("Invalid HID attribute range"); return; }
    struct discovery_op *op = &discovery_ops[(discovery_slot++) & 1];
    *op = (struct discovery_op){.epoch = atomic_get(&epoch), .stage = phase, .report = report_index};
    op->params = (struct bt_gatt_discover_params){.type = type, .start_handle = start,
        .end_handle = end, .uuid = uuid, .func = discovered};
    operation_started = true;
    if (bt_gatt_discover(peer, &op->params)) { fail("HID discovery request failed"); }
}
static void begin_read(uint16_t handle)
{
    struct read_op *op = &read_ops[(read_slot++) & 1];
    *op = (struct read_op){.epoch = atomic_get(&epoch), .stage = phase, .report = report_index};
    op->params.func = read_result;
    op->params.handle_count = 1;
    op->params.single.handle = handle;
    operation_started = true;
    if (!handle || bt_gatt_read(peer, &op->params)) { fail("HID read request failed"); }
}
static void connect_target(void)
{
    if (peer || !advertisement_pending || reconnect_suspended) { return; }
    uint32_t now = k_uptime_get_32();
    if (!elapsed(now, retry_at) || now - selected_seen > 2000) { return; }
    advertisement_pending = false;
    atomic_set(&scan_paused, 1);
    int paused = lism_hid_scan_pause();
    if (paused < 0 && paused != -EALREADY) {
        atomic_clear(&scan_paused);
        retry_at = now + 3000;
        message("Scanner busy; await fresh advertisement");
        return;
    }
    atomic_inc(&epoch);
    invalidate_input();
    k_mutex_lock(&report_lock, K_FOREVER);
    memset(&descriptor, 0, sizeof(descriptor));
    memset(&decoded, 0, sizeof(decoded));
    k_mutex_unlock(&report_lock);
    memset(reports, 0, sizeof(reports));
    service_start = service_end = map_handle = mode_handle = 0;
    char_count = report_count = report_index = subscribed = 0;
    mode_set = map_finished = security_started = operation_started = cancelled = false;
    map_len = reference_len = 0;
    phase = CONNECTING;
    deadline = now + SESSION_TIMEOUT_MS;
    atomic_clear(&advertisements_enabled);
    message("Connecting selected BLE HID device...");
    int result = bt_conn_le_create(&selected, BT_CONN_LE_CREATE_CONN,
                                    BT_LE_CONN_PARAM(9, 18, 30, 400), &peer);
    publish_identities();
    if (result) {
        peer = NULL; phase = IDLE;
        retry_at = now + 5000;
        newly_selected = false;
        atomic_clear(&scan_paused);
        message("Connection failed; await advertisement");
        scanner_state();
    }
}
static void handle_advertisement(const struct ad_event *event)
{
    if (peer || !settings_ready || reconnect_suspended) { return; }
    if (have_saved && !bt_addr_le_cmp(&event->addr, &saved.addr)) {
        if (event->connectable) {
            bt_addr_le_copy(&selected, &event->addr);
            selected_seen = event->seen;
            advertisement_pending = true;
            k_mutex_lock(&state_lock, K_FOREVER);
            snprintf(status.name, sizeof(status.name), "%s", saved.name);
            k_mutex_unlock(&state_lock);
        }
        return;
    }
    uint32_t now = k_uptime_get_32();
    if (!pairing_until || elapsed(now, pairing_until)) { return; }
    int cache = -1, empty = -1, oldest = 0;
    for (unsigned i = 0; i < ADV_CACHE_SIZE; i++) {
        if (ad_cache[i].public.valid && !bt_addr_le_cmp(&ad_cache[i].addr, &event->addr)) { cache = i; break; }
        if (!ad_cache[i].public.valid && empty < 0) { empty = i; }
        if ((int32_t)(ad_cache[i].seen - ad_cache[oldest].seen) < 0) { oldest = i; }
    }
    if (cache < 0) {
        cache = empty >= 0 ? empty : oldest;
        memset(&ad_cache[cache], 0, sizeof(ad_cache[cache]));
        bt_addr_le_copy(&ad_cache[cache].addr, &event->addr);
    }
    struct candidate *c = &ad_cache[cache];
    if (c->public.valid && event->seen - c->seen > 2000) { c->connectable = c->hid_hint = false; }
    c->public.valid = true; c->seen = event->seen;
    c->connectable |= event->connectable;
    c->hid_hint |= event->hid_hint;
    c->public.rssi = event->rssi;
    if (event->name[0]) { snprintf(c->public.name, sizeof(c->public.name), "%s", event->name); }
    if (!c->connectable || !c->hid_hint) { return; }
    if (!c->public.name[0]) { snprintf(c->public.name, sizeof(c->public.name), "BLE HID device"); }
    bt_addr_to_str(&event->addr.a, c->public.address, sizeof(c->public.address));
    int slot = -1;
    for (unsigned i = 0; i < LISM_HID_CANDIDATES; i++) {
        if (candidates[i].public.valid && !bt_addr_le_cmp(&candidates[i].addr, &c->addr)) { slot = i; break; }
        if (!candidates[i].public.valid && slot < 0) { slot = i; }
    }
    if (slot >= 0) {
        k_mutex_lock(&state_lock, K_FOREVER);
        uint32_t token = candidates[slot].public.token;
        if (!candidates[slot].public.valid) {
            token = ++next_candidate_token;
            if (!token) { token = ++next_candidate_token; }
        }
        candidates[slot] = *c; /* Fixed slot, no RSSI re-sorting or replacement. */
        candidates[slot].public.token = token;
        k_mutex_unlock(&state_lock);
    }
}
static void handle_command(struct command_event request)
{
    uint32_t now = k_uptime_get_32();
    if (request.kind == CMD_PAIR) {
        if (peer) { message("Mouse busy; cancel or forget first"); return; }
        reconnect_suspended = false;
        if (have_saved) { message("Waiting for saved mouse advertisement"); scanner_state(); return; }
        k_mutex_lock(&state_lock, K_FOREVER);
        memset(candidates, 0, sizeof(candidates));
        memset(ad_cache, 0, sizeof(ad_cache));
        status.generation++;
        if (!status.generation) { status.generation++; }
        pairing_until = now + 60000; status.pairing = true;
        k_mutex_unlock(&state_lock);
        message("Select HID device; names alone are ignored");
    } else if (request.kind == CMD_SELECT) {
        bool valid = pairing_until && !elapsed(now, pairing_until) && !peer &&
            request.index < LISM_HID_CANDIDATES;
        if (valid) {
            struct candidate *c = &candidates[request.index];
            valid = c->public.valid && c->public.token && request.token == c->public.token &&
                    c->connectable && c->hid_hint && now - c->seen <= 2000;
            if (valid) {
                bt_addr_le_copy(&selected, &c->addr);
                selected_seen = c->seen;
                advertisement_pending = newly_selected = true;
                retry_at = now;
                k_mutex_lock(&state_lock, K_FOREVER);
                snprintf(status.name, sizeof(status.name), "%s", c->public.name);
                status.pairing = false; pairing_until = 0;
                k_mutex_unlock(&state_lock);
            }
        }
        if (!valid) { message("Candidate expired; scan again"); }
    } else {
        reconnect_suspended = true;
        advertisement_pending = false;
        k_mutex_lock(&state_lock, K_FOREVER);
        pairing_until = 0; status.pairing = false;
        k_mutex_unlock(&state_lock);
        begin_close(request.kind == CMD_FORGET ? "Forgetting selected mouse" : "Pairing cancelled");
        if (request.kind == CMD_FORGET && have_saved) {
            /* Exact saved identity only; never unpair all split/host bonds. */
            bt_unpair(BT_ID_DEFAULT, &saved.addr);
            settings_delete("lism_hid/peer");
            k_mutex_lock(&state_lock, K_FOREVER);
            have_saved = status.saved = false;
            k_mutex_unlock(&state_lock);
            publish_identities();
            message("Mouse pairing forgotten");
        }
    }
    scanner_state();
}
static void handle_event(struct bt_event *event)
{
    if (event->kind == EV_SETTINGS) {
        settings_ready = true;
        if (loaded_valid) {
            saved = loaded; have_saved = true;
            k_mutex_lock(&state_lock, K_FOREVER);
            status.saved = true;
            snprintf(status.name, sizeof(status.name), "%s", saved.name);
            k_mutex_unlock(&state_lock);
        }
        publish_identities(); scanner_state(); return;
    }
    if (!peer || event->conn != peer) { return; }
    if (event->kind == EV_DISCONNECTED || (event->kind == EV_CONNECTED && event->error)) {
        finish_session(); message("Mouse disconnected; await advertisement"); return;
    }
    if (event->kind == EV_CONNECTED) {
        if (cancelled || phase == DISCONNECTING) { begin_close("Cancelled connection closed"); return; }
        if (phase != CONNECTING) { return; }
        atomic_clear(&scan_paused);
        k_mutex_lock(&state_lock, K_FOREVER);
        status.connected = true; status.unsupported = false;
        k_mutex_unlock(&state_lock);
        advance(SECURITY); scanner_state(); return;
    }
    if (event->kind == EV_SECURITY) {
        if (cancelled || phase != SECURITY) { return; }
        if (event->error || event->level < BT_SECURITY_L2) { fail("Pairing failed / PIN unsupported"); }
        else { advance(SERVICE); }
        return;
    }
    if (!current_event(event)) { return; }
    if (event->kind == EV_UNSUBSCRIBED) { fail("Mouse notification ended"); return; }
    if (event->stage != phase && event->kind != EV_SUBSCRIBED) { return; }
    if (event->kind == EV_DISCOVERY) {
        if (phase == SERVICE) {
            if (!event->handle || event->handle >= event->end) { fail("No valid BLE HID service"); return; }
            service_start = event->handle + 1; service_end = event->end; advance(CHARS);
        } else if (phase == CHARS) {
            if (event->complete) {
                if (report_count && !reports[report_count - 1].end) { reports[report_count - 1].end = service_end; }
                if (!map_handle || !report_count) { fail("No HID Report Map / reports"); }
                else { advance(MAP); }
            } else {
                if (++char_count > MAX_CHARS || event->handle < service_start || event->value <= event->handle ||
                    event->value > service_end) { fail("Invalid HID characteristics"); return; }
                if (report_count && !reports[report_count - 1].end) { reports[report_count - 1].end = event->handle - 1; }
                if (event->uuid == 0x2a4b) {
                    if (map_handle) { fail("Duplicate HID Report Map"); return; }
                    map_handle = event->value;
                } else if (event->uuid == 0x2a4e) {
                    mode_handle = event->value; mode_properties = event->properties;
                } else if (event->uuid == 0x2a4d) {
                    if (report_count == MAX_REPORT_CHARS) { fail("Too many HID reports"); return; }
                    reports[report_count++] = (struct report_char){.value = event->value,
                        .properties = event->properties};
                }
            }
        } else if (phase == DESCRIPTORS && event->report == report_index) {
            struct report_char *r = &reports[report_index];
            if (event->complete) { advance(REFERENCE); }
            else if (event->handle <= r->value || event->handle > r->end) { fail("Invalid report descriptor range"); }
            else if (event->uuid == 0x2908) {
                if (r->reference) { fail("Duplicate Report Reference"); } else { r->reference = event->handle; }
            } else if (event->uuid == 0x2902) {
                if (r->ccc) { fail("Duplicate report CCC"); } else { r->ccc = event->handle; }
            }
        }
    } else if (event->kind == EV_READ) {
        if (event->error) { fail("HID read failed / invalid length"); return; }
        if (phase == MAP) {
            if (event->length > sizeof(map) - map_len) { fail("HID map exceeds 1024 bytes"); return; }
            memcpy(map + map_len, event->data, event->length); map_len += event->length;
            if (event->complete) { map_finished = true; operation_started = false; }
        } else if (phase == REFERENCE && event->report == report_index) {
            if (event->length > sizeof(reference_data) - reference_len) { fail("Malformed Report Reference"); return; }
            memcpy(reference_data + reference_len, event->data, event->length); reference_len += event->length;
            if (event->complete) {
                if (reference_len != 2 || reference_data[1] < 1 || reference_data[1] > 3) {
                    fail("Missing / invalid Report Reference"); return;
                }
                struct report_char *r = &reports[report_index];
                r->id = reference_data[0]; r->type = reference_data[1];
                for (unsigned i = 0; i < report_index; i++) {
                    if (reports[i].reference && reports[i].id == r->id && reports[i].type == r->type) {
                        fail("Duplicate HID report ID/type"); return;
                    }
                }
                advance(SUBSCRIBE);
            }
        }
    } else if (event->kind == EV_SUBSCRIBED && phase == SUBSCRIBE && event->report == report_index) {
        if (event->error) { fail("Mouse subscribe failed"); }
        else { subscribed++; report_index++; advance(DESCRIPTORS); }
    }
}
static void progress(void)
{
    for (unsigned steps = 0; peer && !cancelled && steps < 64; steps++) {
        if (operation_started || phase == READY || phase == CONNECTING || phase == DISCONNECTING) { return; }
        if (phase == SECURITY) {
            if (security_started) { return; }
            security_started = true;
            if (bt_conn_get_security(peer) >= BT_SECURITY_L2) { advance(SERVICE); continue; }
            if (bt_conn_set_security(peer, BT_SECURITY_L2)) { fail("Pairing failed; PIN unsupported"); }
            return;
        }
        if (phase == SERVICE) {
            begin_discovery(BT_GATT_DISCOVER_PRIMARY, 1, 0xffff, &hid_service_uuid.uuid); return;
        }
        if (phase == CHARS) {
            begin_discovery(BT_GATT_DISCOVER_CHARACTERISTIC, service_start, service_end, NULL); return;
        }
        if (phase == MAP) {
            if (!map_finished) { message("Reading HID Report Map..."); begin_read(map_handle); return; }
            k_mutex_lock(&report_lock, K_FOREVER);
            enum lism_hid_error error = lism_hid_parse(map, map_len, &descriptor);
            k_mutex_unlock(&report_lock);
            if (error || !descriptor.field_count) {
                k_mutex_lock(&state_lock, K_FOREVER); status.unsupported = true; k_mutex_unlock(&state_lock);
                fail("Unsupported HID (no relative mouse)"); return;
            }
            if (mode_handle && !mode_set) {
                if (!(mode_properties & BT_GATT_CHRC_WRITE_WITHOUT_RESP)) { fail("Protocol Mode cannot write"); return; }
                uint8_t mode = 1;
                if (bt_gatt_write_without_response(peer, mode_handle, &mode, 1, false)) {
                    fail("Cannot select HID Report Mode"); return;
                }
                mode_set = true;
            }
            advance(DESCRIPTORS); continue;
        }
        if (phase == DESCRIPTORS) {
            if (report_index == report_count) {
                if (!subscribed) { fail("No supported mouse notifications"); return; }
                phase = READY; newly_selected = false;
                k_mutex_lock(&state_lock, K_FOREVER);
                status.ready = true;
                bool trimmed_buttons = false;
                for (unsigned i = 0; i < descriptor.report_count; i++) {
                    if (descriptor.reports[i].button_mask &
                        (uint8_t)~BIT_MASK(ZMK_HID_MOUSE_NUM_BUTTONS)) { trimmed_buttons = true; }
                }
                status.unsupported = trimmed_buttons || (descriptor.capabilities &
                    (LISM_HID_CAP_DIGITIZER | LISM_HID_CAP_KEYBOARD | LISM_HID_CAP_VENDOR |
                     LISM_HID_CAP_UNSUPPORTED)) != 0;
                k_mutex_unlock(&state_lock);
                save_identity();
                atomic_set(&input_ready, 1);
                message(trimmed_buttons ? "Mouse ready; buttons 6-8 not forwarded" :
                        "Mouse ready; raw gestures not forwarded");
                scanner_state(); return;
            }
            struct report_char *r = &reports[report_index];
            if (!(r->properties & BT_GATT_CHRC_NOTIFY) || r->value >= r->end) { report_index++; continue; }
            begin_discovery(BT_GATT_DISCOVER_DESCRIPTOR, r->value + 1, r->end, NULL); return;
        }
        if (phase == REFERENCE) {
            if (!reports[report_index].reference) { fail("Missing Report Reference"); return; }
            reference_len = 0; begin_read(reports[report_index].reference); return;
        }
        if (phase == SUBSCRIBE) {
            struct report_char *r = &reports[report_index];
            if (r->type != 1 || !mouse_report(r->id)) { report_index++; advance(DESCRIPTORS); continue; }
            if (!r->ccc) { fail("Missing mouse CCC"); return; }
            r->epoch = atomic_get(&epoch); r->conn = peer;
            r->sub = (struct bt_gatt_subscribe_params){.notify = notification, .subscribe = subscribed_cb,
                .value_handle = r->value, .ccc_handle = r->ccc, .value = BT_GATT_CCC_NOTIFY,
                .min_security = BT_SECURITY_L2};
            atomic_set_bit(r->sub.flags, BT_GATT_SUBSCRIBE_FLAG_VOLATILE);
            operation_started = true;
            if (bt_gatt_subscribe(peer, &r->sub)) { fail("Mouse subscribe request failed"); }
            return;
        }
        return;
    }
}
static void control(struct k_work *work)
{
    ARG_UNUSED(work);
    /* Commands are FIFO, not a last-writer global that advertisements can
     * erase. Emergency cancellation survives even a full command queue. */
    int emergency = atomic_set(&emergency_command, 0);
    if (emergency) {
        struct command_event discarded;
        while (!k_msgq_get(&commands, &discarded, K_NO_WAIT)) {}
        handle_command((struct command_event){.kind = emergency == 2 ? CMD_FORGET : CMD_CANCEL});
    }
    struct command_event command;
    while (!k_msgq_get(&commands, &command, K_NO_WAIT)) { handle_command(command); }
    struct bt_event event;
    while (!k_msgq_get(&bt_events, &event, K_NO_WAIT)) {
        handle_event(&event);
        if (event.conn) { bt_conn_unref(event.conn); }
    }
    int closed = atomic_set(&closed_index, 0);
    if (peer && closed == bt_conn_index(peer) + 1) { finish_session(); }
    int event_error = atomic_set(&event_fault, 0);
    int input_error = atomic_set(&input_fault, 0);
    if (event_error || input_error) { fail("Mouse queue/input fault"); }
    struct ad_event advertisement;
    while (!k_msgq_get(&advertisements, &advertisement, K_NO_WAIT)) { handle_advertisement(&advertisement); }
    uint32_t now = k_uptime_get_32();
    if (pairing_until && elapsed(now, pairing_until)) {
        k_mutex_lock(&state_lock, K_FOREVER); pairing_until = 0; status.pairing = false; k_mutex_unlock(&state_lock);
        message("Scan expired; tap Start scan");
    }
    if (peer && elapsed(now, deadline)) {
        if (phase == DISCONNECTING) { begin_close("Waiting for mouse teardown"); }
        else if (phase != READY) { fail("Mouse setup timed out / PIN unsupported"); }
    }
    connect_target();
    progress();
    if (atomic_get(&clear_input) || atomic_get(&pending_release) ||
        k_msgq_num_used_get(&frames)) { kick_input(); }
    scanner_state();
}
static void tick(struct k_work *work)
{
    ARG_UNUSED(work);
    k_work_submit(&control_work);
    k_work_reschedule(&tick_work, K_MSEC(500));
}
static int setting_set(const char *name, size_t length, settings_read_cb read, void *context)
{
    if (strcmp(name, "peer")) { return -ENOENT; }
    if (length != sizeof(loaded) || read(context, &loaded, sizeof(loaded)) != sizeof(loaded) ||
        loaded.version != SAVED_VERSION || !bt_addr_le_cmp(&loaded.addr, BT_ADDR_LE_ANY)) { return -EINVAL; }
    loaded.name[sizeof(loaded.name) - 1] = 0;
    loaded_valid = true;
    return 0;
}
static int hid_settings_commit(void)
{
    struct bt_event event = {.kind = EV_SETTINGS};
    queue_bt(&event, NULL);
    k_work_reschedule(&tick_work, K_MSEC(500));
    return 0;
}
SETTINGS_STATIC_HANDLER_DEFINE(lism_hid, "lism_hid", NULL, setting_set, hid_settings_commit, NULL);

void lism_hid_host_snapshot(struct lism_hid_status *out)
{
    k_mutex_lock(&state_lock, K_FOREVER);
    *out = status;
    uint32_t now = k_uptime_get_32();
    out->seconds_left = pairing_until && !elapsed(now, pairing_until) ?
        MIN(60, (pairing_until - now + 999) / 1000) : 0;
    for (unsigned i = 0; i < LISM_HID_CANDIDATES; i++) { out->candidates[i] = candidates[i].public; }
    k_mutex_unlock(&state_lock);
}
static void request(enum command kind, uint8_t index, uint32_t token)
{
    struct command_event command = {.kind = kind, .index = index, .token = token};
    if (k_msgq_put(&commands, &command, K_NO_WAIT)) {
        if (kind == CMD_FORGET) { atomic_set(&emergency_command, 2); }
        else if (kind == CMD_CANCEL) {
            (void)atomic_cas(&emergency_command, 0, 1);
        } else { atomic_set(&event_fault, 1); }
    }
    k_work_submit(&control_work);
}
void lism_hid_host_pair(void) { request(CMD_PAIR, 0, 0); }
void lism_hid_host_cancel(void) { request(CMD_CANCEL, 0, 0); }
void lism_hid_host_select(uint8_t index, uint32_t token) { request(CMD_SELECT, index, token); }
void lism_hid_host_forget(void) { request(CMD_FORGET, 0, 0); }
static int init(void)
{
    bt_le_scan_cb_register(&scan_callbacks);
    message("BLE relative mouse bridge (test)");
    return 0;
}
SYS_INIT(init, APPLICATION, CONFIG_ZMK_BLE_INIT_PRIORITY);
