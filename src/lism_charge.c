/* SPDX-License-Identifier: MIT
 * Charger STATUS telemetry, not a charge controller or battery protection BMS.
 * P0.17 is verified only for the configured Seeed XIAO nRF52840 / Plus targets.
 * USB plus asserted STAT alone cannot prove that a battery is connected.
 */
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/conn.h>
#include <zmk/battery.h>
#include <hal/nrf_power.h>
#include "lism_charge.h"

#define CHARGE_SERVICE_UUID BT_UUID_128_ENCODE(0xf4f64a90, 0x712a, 0x4e2e, 0xa275, 0x64809f58e101)
#define CHARGE_STATE_UUID BT_UUID_128_ENCODE(0xf4f64a91, 0x712a, 0x4e2e, 0xa275, 0x64809f58e101)
static struct bt_uuid_128 state_uuid = BT_UUID_INIT_128(CHARGE_STATE_UUID);
static const struct device *const charge_port = DEVICE_DT_GET(DT_NODELABEL(gpio0));
K_MUTEX_DEFINE(charge_lock);
static struct lism_charge_filter local_filter;
static bool gpio_ready;
static bool battery_sample_valid;
static uint8_t battery_sample_percent;
static uint32_t battery_sample_at;
static bool power_seen, power_usb, power_settling;
static uint32_t power_changed_at;
static uint32_t power_epoch, fetch_epoch;
static bool fetch_pending, fetch_started_settled;

/* charge_lock held. A physical source transition invalidates the old ADC
 * result, including an unplug/replug seen before the next battery-worker run.
 * This is telemetry hygiene, NOT battery detection or charger control. */
static void observe_power_locked(bool usb, uint32_t now) {
    if (power_seen && power_usb != usb) {
        battery_sample_valid = false;
        local_filter.state.flags &= ~(LISM_CHARGE_LEVEL_VALID | LISM_CHARGE_STAT_ACTIVE);
        local_filter.low_samples = 0;
        power_changed_at = now;
        power_settling = true;
        power_epoch++;
    }
    power_seen = true;
    power_usb = usb;
}

/* Sole battery worker calls this BEFORE fetching. Never hold charge_lock
 * while ADC work runs. Completion must belong to the same observed epoch. */
void lism_charge_begin_battery_sample(void) {
    k_mutex_lock(&charge_lock, K_FOREVER);
    uint32_t now = k_uptime_get_32();
    observe_power_locked(nrf_power_usbregstatus_vbusdet_get(NRF_POWER), now);
    fetch_epoch = power_epoch;
    fetch_pending = true;
    fetch_started_settled = !power_settling || now - power_changed_at >= 1000U;
    k_mutex_unlock(&charge_lock);
}

/* Called after the sole ZMK battery worker completes a real fetch. A failed
 * fetch must invalidate old cached voltage, even when channel_get still works.
 */
void lism_charge_note_battery_sample(int result) {
    const struct device *battery = DEVICE_DT_GET(DT_CHOSEN(zmk_battery));
    struct sensor_value voltage;
    bool valid = result == 0 && device_is_ready(battery) &&
        sensor_channel_get(battery, SENSOR_CHAN_GAUGE_VOLTAGE, &voltage) == 0;
    int32_t mv = valid ? voltage.val1 * 1000 + voltage.val2 / 1000 : 0;
    valid = valid && mv >= 2800 && mv <= 4350;
    k_mutex_lock(&charge_lock, K_FOREVER);
    uint32_t now = k_uptime_get_32();
    observe_power_locked(nrf_power_usbregstatus_vbusdet_get(NRF_POWER), now);
    /* Do not relabel a transition/transient sample as a new-source sample.
     * Only a subsequent real battery-worker fetch can restore validity. */
    valid = valid && fetch_pending && fetch_epoch == power_epoch && fetch_started_settled;
    fetch_pending = false;
    if (valid) { power_settling = false; }
    battery_sample_valid = valid;
    battery_sample_percent = zmk_battery_state_of_charge();
    battery_sample_at = now;
    if (!valid) { local_filter.state.flags &= ~LISM_CHARGE_LEVEL_VALID; }
    k_mutex_unlock(&charge_lock);
}

struct lism_charge_state lism_charge_local(void) {
    k_mutex_lock(&charge_lock, K_FOREVER);
    bool usb = nrf_power_usbregstatus_vbusdet_get(NRF_POWER);
    observe_power_locked(usb, k_uptime_get_32());
    struct lism_charge_state s = local_filter.state;
    /* Never serve a cached charging bolt after physical USB removal, even
     * when the 250ms sampling worker has not run yet. */
    if (usb) { s.flags |= LISM_CHARGE_USB; }
    else { s.flags &= ~(LISM_CHARGE_USB | LISM_CHARGE_STAT_ACTIVE); }
    if (!gpio_ready || k_uptime_get_32() - local_filter.sampled_at > 1500) {
        s = (struct lism_charge_state){0};
    }
    k_mutex_unlock(&charge_lock);
    return s;
}

static void sample_charge(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(charge_sample_work, sample_charge);
static void sample_charge(struct k_work *work) {
    ARG_UNUSED(work);
    /* Physical VBUS also covers wall chargers without USB enumeration and
     * split peripherals without the USB stack. Read-only: no power controls. */
    /* An explicit installation declaration is necessary under USB: the official
     * reference notes that an unconnected BAT node can mimic a real battery.
     * No cached 'once battery powered' guess survives a switch-off/hot removal.
     */
    bool installed = IS_ENABLED(CONFIG_LISM_CHARGE_BATTERY_CONNECTED);
    k_mutex_lock(&charge_lock, K_FOREVER);
    bool usb = nrf_power_usbregstatus_vbusdet_get(NRF_POWER);
    int stat = gpio_ready ? gpio_pin_get_raw(charge_port, 17) : -ENODEV;
    observe_power_locked(usb, k_uptime_get_32());
    bool valid = battery_sample_valid && k_uptime_get_32() - battery_sample_at <=
        CONFIG_ZMK_BATTERY_REPORT_INTERVAL * 1000U + 5000U;
    lism_charge_observe(&local_filter, k_uptime_get_32(), usb, stat, installed,
                        (!usb || installed) && valid, battery_sample_percent);
    k_mutex_unlock(&charge_lock);
    k_work_reschedule(&charge_sample_work, K_MSEC(250));
}

#if !IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
static ssize_t read_charge(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                           void *buf, uint16_t len, uint16_t offset) {
    struct lism_charge_state s = lism_charge_local();
    uint8_t wire[LISM_CHARGE_WIRE_SIZE] = {LISM_CHARGE_VERSION, s.flags, s.percent, 0};
    return bt_gatt_attr_read(conn, attr, buf, len, offset, wire, sizeof(wire));
}
BT_GATT_SERVICE_DEFINE(lism_charge_service,
    BT_GATT_PRIMARY_SERVICE(BT_UUID_DECLARE_128(CHARGE_SERVICE_UUID)),
    BT_GATT_CHARACTERISTIC(&state_uuid.uuid, BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ_ENCRYPT, read_charge, NULL, NULL));
#endif

#if IS_ENABLED(CONFIG_LISM_XIAO147_TOUCH)
#include "xiao147_peripheral_roles.h"
extern int peripheral_slot_index_for_conn(struct bt_conn *conn);
struct charge_peer {
    struct bt_conn *conn; /* Own one reference until no request is pending. */
    struct bt_gatt_read_params read;
    bool pending, valid;
    struct lism_charge_state state;
    uint32_t sampled_at, next_at, requested_at;
};
static struct charge_peer peers[XIAO147_PERIPHERAL_COUNT];
K_MUTEX_DEFINE(peers_lock);
K_THREAD_STACK_DEFINE(charge_client_stack, 1536);
static struct k_work_q charge_client_queue;

static bool matching_peer(struct bt_conn *conn, unsigned slot) {
    struct bt_conn_info info;
    return conn && bt_conn_get_info(conn, &info) == 0 &&
        info.type == BT_CONN_TYPE_LE && info.state == BT_CONN_STATE_CONNECTED &&
        info.role == BT_CONN_ROLE_CENTRAL &&
        peripheral_slot_index_for_conn(conn) == (int)slot;
}

struct lism_charge_state lism_charge_peripheral(uint8_t slot) {
    struct lism_charge_state s = {0};
    if (slot >= ARRAY_SIZE(peers)) { return s; }
    k_mutex_lock(&peers_lock, K_FOREVER);
    struct charge_peer *p = &peers[slot];
    if (p->valid && k_uptime_get_32() - p->sampled_at < LISM_CHARGE_STALE_MS &&
        matching_peer(p->conn, slot)) { s = p->state; }
    k_mutex_unlock(&peers_lock);
    return s;
}

static uint8_t charge_read_done(struct bt_conn *conn, uint8_t err,
                                struct bt_gatt_read_params *params,
                                const void *data, uint16_t len) {
    struct charge_peer *p = CONTAINER_OF(params, struct charge_peer, read);
    unsigned slot = p - peers;
    struct lism_charge_state s;
    k_mutex_lock(&peers_lock, K_FOREVER);
    p->valid = !err && k_uptime_get_32() - p->requested_at <= 5000 &&
        p->conn == conn && matching_peer(conn, slot) &&
        lism_charge_decode(data, len, &s);
    if (p->valid) { p->state = s; p->sampled_at = k_uptime_get_32(); }
    /* Old firmware/no service is not an error loop. Retry after one minute. */
    p->next_at = k_uptime_get_32() + (p->valid ? 3000 : 60000);
    p->pending = false;
    k_mutex_unlock(&peers_lock);
    /* Pinned Zephyr permits params to be reused from callback entry. STOP
     * prevents continuation/long reads from using them after this callback. */
    return BT_GATT_ITER_STOP;
}

static void poll_peer(struct bt_conn *conn, void *unused) {
    ARG_UNUSED(unused);
    int slot = peripheral_slot_index_for_conn(conn);
    if (slot < 0 || slot >= ARRAY_SIZE(peers) || !matching_peer(conn, slot) ||
        bt_conn_get_security(conn) < BT_SECURITY_L2) { return; }
    k_mutex_lock(&peers_lock, K_FOREVER);
    struct charge_peer *p = &peers[slot];
    if (p->pending) { k_mutex_unlock(&peers_lock); return; }
    if (p->conn != conn) {
        if (p->conn) { bt_conn_unref(p->conn); }
        *p = (struct charge_peer){.conn = bt_conn_ref(conn), .next_at = k_uptime_get_32()};
    }
    uint32_t now = k_uptime_get_32();
    if ((int32_t)(now - p->next_at) < 0) { k_mutex_unlock(&peers_lock); return; }
    p->read = (struct bt_gatt_read_params){.func = charge_read_done, .handle_count = 0,
        .by_uuid = {.start_handle = 1, .end_handle = 0xffff, .uuid = &state_uuid.uuid}};
    p->pending = true;
    p->requested_at = now;
    k_mutex_unlock(&peers_lock);
    int rc = bt_gatt_read(conn, &p->read);
    if (rc) {
        k_mutex_lock(&peers_lock, K_FOREVER);
        p->pending = false; p->valid = false; p->next_at = now + 3000;
        k_mutex_unlock(&peers_lock);
    }
}

static void charge_disconnected(struct bt_conn *conn, uint8_t reason) {
    ARG_UNUSED(reason);
    k_mutex_lock(&peers_lock, K_FOREVER);
    for (unsigned i = 0; i < ARRAY_SIZE(peers); ++i) {
        if (peers[i].conn == conn) { peers[i].valid = false; }
        /* Never reset an in-flight parameter object on disconnect/timeout. */
    }
    k_mutex_unlock(&peers_lock);
}
BT_CONN_CB_DEFINE(lism_charge_connections) = {.disconnected = charge_disconnected};

static void poll_charges(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(charge_poll_work, poll_charges);
static void poll_charges(struct k_work *work) {
    ARG_UNUSED(work);
    k_mutex_lock(&peers_lock, K_FOREVER);
    for (unsigned i = 0; i < ARRAY_SIZE(peers); ++i) {
        if (peers[i].conn && !peers[i].pending && !matching_peer(peers[i].conn, i)) {
            bt_conn_unref(peers[i].conn); peers[i] = (struct charge_peer){0};
        }
    }
    k_mutex_unlock(&peers_lock);
    bt_conn_foreach(BT_CONN_TYPE_LE, poll_peer, NULL);
    k_work_reschedule_for_queue(&charge_client_queue, &charge_poll_work, K_SECONDS(1));
}
#endif

static int charge_init(void) {
    if (!device_is_ready(charge_port)) { return -ENODEV; }
    /* Existing half/joystick status owns configuration; only the Plus dongle
     * needs us to configure STAT. Never install another IRQ callback or drive it.
     */
    if (IS_ENABLED(CONFIG_LISM_XIAO147_TOUCH)) {
        int rc = gpio_pin_configure(charge_port, 17, GPIO_INPUT | GPIO_PULL_UP);
        if (rc) { return rc; }
    }
    gpio_ready = true;
    k_work_schedule(&charge_sample_work, K_SECONDS(1));
#if IS_ENABLED(CONFIG_LISM_XIAO147_TOUCH)
    /* ATT allocation must not block the shared input/system workqueue. */
    k_work_queue_start(&charge_client_queue, charge_client_stack,
        K_THREAD_STACK_SIZEOF(charge_client_stack), K_PRIO_PREEMPT(10), NULL);
    k_work_schedule_for_queue(&charge_client_queue, &charge_poll_work, K_SECONDS(3));
#endif
    return 0;
}
SYS_INIT(charge_init, APPLICATION, 90);
