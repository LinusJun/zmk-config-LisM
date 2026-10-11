/* Connected-link RSSI for the XIAO dongle device page. Never block LVGL or BT RX. */
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/hci_types.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/net/buf.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/byteorder.h>

#include <zmk/ble.h>
#include <zmk/endpoints.h>

#include "xiao147_rssi.h"

#define RSSI_SLOTS 4
#define RSSI_SAMPLE_PERIOD K_SECONDS(3)
#define RSSI_MAX_AGE_MS 10000

/* This symbol is internal to the pinned ZMK split/BLE central source.
 * Keep the compatibility dependency here, away from the display code.
 */
extern int peripheral_slot_index_for_conn(struct bt_conn *conn);

struct rssi_sample {
    int8_t value[RSSI_SLOTS];
    bool valid[RSSI_SLOTS];
    uint32_t sampled_at;
    atomic_val_t generation;
    int host_profile;
};

struct split_connection {
    struct bt_conn *conn;
    int slot;
};

struct split_connections {
    struct split_connection items[RSSI_SLOTS - 1];
    uint8_t count;
};

K_MUTEX_DEFINE(rssi_lock);
K_THREAD_STACK_DEFINE(rssi_stack, 2048);
static struct k_work_q rssi_queue;
static atomic_t active = ATOMIC_INIT(0);
static atomic_t generation = ATOMIC_INIT(0);
static struct rssi_sample latest;

/* Bluetooth callbacks must never wait for HCI. Bumping the generation makes
 * any in-flight result unusable, even if it is published during a disconnect.
 */
static void connection_changed(struct bt_conn *conn, uint8_t status)
{
    atomic_inc(&generation);
}

static void connection_disconnected(struct bt_conn *conn, uint8_t reason)
{
    atomic_inc(&generation);
}

BT_CONN_CB_DEFINE(xiao147_rssi_connections) = {
    .connected = connection_changed,
    .disconnected = connection_disconnected,
};

static bool connection_is_ready(struct bt_conn *conn, uint8_t role)
{
    struct bt_conn_info info;
    return conn && bt_conn_get_info(conn, &info) == 0 &&
           info.type == BT_CONN_TYPE_LE &&
           info.state == BT_CONN_STATE_CONNECTED && info.role == role;
}

static int read_connected_rssi(struct bt_conn *conn, int8_t *value)
{
    uint16_t handle;
    if (bt_hci_get_conn_handle(conn, &handle) != 0) { return -1; }
    struct net_buf *request = bt_hci_cmd_create(BT_HCI_OP_READ_RSSI,
                                                 sizeof(struct bt_hci_cp_read_rssi));
    if (!request) { return -1; }
    struct bt_hci_cp_read_rssi *cmd = net_buf_add(request, sizeof(*cmd));
    cmd->handle = sys_cpu_to_le16(handle);
    struct net_buf *response = NULL;
    int err = bt_hci_cmd_send_sync(BT_HCI_OP_READ_RSSI, request, &response);
    /* send_sync owns request even on failure. */
    if (err || !response) {
        if (response) { net_buf_unref(response); }
        return -1;
    }
    if (response->len < sizeof(struct bt_hci_rp_read_rssi)) {
        net_buf_unref(response);
        return -1;
    }
    const struct bt_hci_rp_read_rssi *result = (const void *)response->data;
    bool valid = result->status == 0 &&
                 sys_le16_to_cpu(result->handle) == handle && result->rssi != 127;
    if (valid) { *value = result->rssi; }
    net_buf_unref(response);
    return valid ? 0 : -1;
}

static void collect_split_connection(struct bt_conn *conn, void *context)
{
    struct split_connections *found = context;
    if (found->count >= ARRAY_SIZE(found->items) ||
        !connection_is_ready(conn, BT_CONN_ROLE_CENTRAL)) { return; }
    int slot = peripheral_slot_index_for_conn(conn);
    if (slot < 0 || slot >= RSSI_SLOTS - 1) { return; }
    struct bt_conn *held = bt_conn_ref(conn);
    if (!held) { return; }
    found->items[found->count++] = (struct split_connection){.conn = held, .slot = slot};
}

static bool host_is_selected_connection(struct bt_conn *host, int profile)
{
    const bt_addr_le_t *peer = bt_conn_get_dst(host);
    if (!peer || zmk_ble_profile_index(peer) != profile) { return false; }
    struct zmk_endpoint_instance selected = zmk_endpoint_get_selected();
    if (selected.transport != ZMK_TRANSPORT_BLE ||
        selected.ble.profile_index != profile ||
        zmk_ble_active_profile_index() != profile) { return false; }
    struct bt_conn *current = zmk_ble_active_profile_conn();
    bool same = current == host &&
                zmk_ble_profile_index(bt_conn_get_dst(current)) == profile;
    if (current) { bt_conn_unref(current); }
    return same;
}

static void poll_rssi(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(rssi_work, poll_rssi);

static void poll_rssi(struct k_work *work)
{
    if (!atomic_get(&active)) { return; }
    atomic_val_t poll_generation = atomic_get(&generation);
    struct rssi_sample next = {.host_profile = -1};
    struct split_connections split = {0};
    bt_conn_foreach(BT_CONN_TYPE_LE, collect_split_connection, &split);
    for (int i = 0; i < split.count; i++) {
        struct bt_conn *conn = split.items[i].conn;
        int slot = split.items[i].slot;
        int8_t rssi;
        if (read_connected_rssi(conn, &rssi) == 0 &&
            connection_is_ready(conn, BT_CONN_ROLE_CENTRAL) &&
            peripheral_slot_index_for_conn(conn) == slot) {
            next.value[slot + 1] = rssi;
            next.valid[slot + 1] = true;
        }
        bt_conn_unref(conn);
    }

    struct zmk_endpoint_instance endpoint = zmk_endpoint_get_selected();
    if (endpoint.transport == ZMK_TRANSPORT_BLE &&
        zmk_ble_active_profile_index() == endpoint.ble.profile_index) {
        struct bt_conn *host = zmk_ble_active_profile_conn();
        if (host) {
            int8_t rssi;
            int profile = endpoint.ble.profile_index;
            if (connection_is_ready(host, BT_CONN_ROLE_PERIPHERAL) &&
                read_connected_rssi(host, &rssi) == 0 &&
                connection_is_ready(host, BT_CONN_ROLE_PERIPHERAL) &&
                host_is_selected_connection(host, profile)) {
                next.value[0] = rssi;
                next.valid[0] = true;
                next.host_profile = profile;
            }
            bt_conn_unref(host);
        }
    }
    next.sampled_at = k_uptime_get_32();
    next.generation = poll_generation;
    k_mutex_lock(&rssi_lock, K_FOREVER);
    if (atomic_get(&active) && poll_generation == atomic_get(&generation)) {
        latest = next;
    }
    k_mutex_unlock(&rssi_lock);
    if (atomic_get(&active)) {
        k_work_reschedule_for_queue(&rssi_queue, &rssi_work, RSSI_SAMPLE_PERIOD);
    }
}

void xiao147_rssi_set_active(bool enabled)
{
    k_mutex_lock(&rssi_lock, K_FOREVER);
    atomic_val_t previous = atomic_set(&active, enabled);
    if (previous == enabled) {
        k_mutex_unlock(&rssi_lock);
        return;
    }
    atomic_inc(&generation);
    memset(&latest, 0, sizeof(latest));
    latest.host_profile = -1;
    k_mutex_unlock(&rssi_lock);
    if (enabled) { k_work_reschedule_for_queue(&rssi_queue, &rssi_work, K_NO_WAIT); }
    else { k_work_cancel_delayable(&rssi_work); }
}

void xiao147_rssi_invalidate_slot(uint8_t slot)
{
    if (slot >= RSSI_SLOTS - 1) { return; }
    k_mutex_lock(&rssi_lock, K_FOREVER);
    atomic_inc(&generation);
    latest.valid[slot + 1] = false;
    k_mutex_unlock(&rssi_lock);
}

void xiao147_rssi_snapshot(int8_t values[RSSI_SLOTS], bool valid[RSSI_SLOTS])
{
    k_mutex_lock(&rssi_lock, K_FOREVER);
    memcpy(values, latest.value, sizeof(latest.value));
    memcpy(valid, latest.valid, sizeof(latest.valid));
    if (k_uptime_get_32() - latest.sampled_at > RSSI_MAX_AGE_MS) {
        memset(valid, 0, sizeof(latest.valid));
    }
    int sampled_profile = latest.host_profile;
    atomic_val_t sampled_generation = latest.generation;
    k_mutex_unlock(&rssi_lock);
    if (sampled_generation != atomic_get(&generation)) {
        memset(valid, 0, sizeof(latest.valid));
    }
    struct zmk_endpoint_instance endpoint = zmk_endpoint_get_selected();
    if (endpoint.transport != ZMK_TRANSPORT_BLE ||
        endpoint.ble.profile_index != sampled_profile) { valid[0] = false; }
}

static int xiao147_rssi_init(void)
{
    k_work_queue_start(&rssi_queue, rssi_stack, K_THREAD_STACK_SIZEOF(rssi_stack),
                       K_PRIO_PREEMPT(10), NULL);
    return 0;
}
SYS_INIT(xiao147_rssi_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
