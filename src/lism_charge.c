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
#include <zephyr/logging/log.h>
#include <errno.h>
#include "lism_charge.h"
#include "lism_battery_presence.h"
LOG_MODULE_REGISTER(lism_charge, LOG_LEVEL_INF);

#define CHARGE_SERVICE_UUID BT_UUID_128_ENCODE(0xf4f64a90, 0x712a, 0x4e2e, 0xa275, 0x64809f58e101)
#define CHARGE_STATE_UUID BT_UUID_128_ENCODE(0xf4f64a91, 0x712a, 0x4e2e, 0xa275, 0x64809f58e101)
#define CHARGE_DIAG_UUID BT_UUID_128_ENCODE(0xf4f64a92, 0x712a, 0x4e2e, 0xa275, 0x64809f58e101)
static struct bt_uuid_128 state_uuid = BT_UUID_INIT_128(CHARGE_STATE_UUID);
static struct bt_uuid_128 diag_uuid = BT_UUID_INIT_128(CHARGE_DIAG_UUID);
static const struct device *const charge_port = DEVICE_DT_GET(DT_NODELABEL(gpio0));
K_MUTEX_DEFINE(charge_lock);
static struct lism_charge_filter local_filter;
static struct lism_battery_presence presence;
static bool gpio_ready;
static bool battery_sample_valid;
static uint8_t battery_sample_percent;
static uint32_t battery_sample_at;
static bool power_seen, power_usb, power_settling;
static uint32_t power_changed_at;
static uint32_t power_epoch, fetch_epoch;
static bool fetch_pending, fetch_started_settled;
static uint32_t last_adc_request_at;
static uint16_t diagnostic_mv;
static int diagnostic_result = -EAGAIN, diagnostic_stat = -EAGAIN;
extern void lism_charge_request_battery_sample(void);

/* charge_lock held. A physical source transition invalidates the old ADC
 * result, including an unplug/replug seen before the next battery-worker run.
 * This is telemetry hygiene, NOT battery detection or charger control. */
static void observe_power_locked(bool usb, uint32_t now) {
    lism_presence_source(&presence, usb, now);
    if (!power_seen || power_usb != usb) {
        battery_sample_valid = false;
        local_filter.state = (struct lism_charge_state){
            .flags = LISM_CHARGE_OBSERVED | (usb ? LISM_CHARGE_USB : 0)};
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
    int channel_result = result ? result : !device_is_ready(battery) ? -ENODEV :
        sensor_channel_get(battery, SENSOR_CHAN_GAUGE_VOLTAGE, &voltage);
    bool valid = channel_result == 0;
    int32_t mv = valid ? voltage.val1 * 1000 + voltage.val2 / 1000 : 0;
    valid = valid && mv >= 2800 && mv <= 4300;
    k_mutex_lock(&charge_lock, K_FOREVER);
    uint32_t now = k_uptime_get_32();
    observe_power_locked(nrf_power_usbregstatus_vbusdet_get(NRF_POWER), now);
    diagnostic_mv = mv > 0 && mv <= 65535 ? mv : 0;
    diagnostic_result = channel_result ? channel_result : valid ? 0 : -ERANGE;
    /* Do not relabel a transition/transient sample as a new-source sample.
     * Only a subsequent real battery-worker fetch can restore validity. */
    valid = valid && fetch_pending && fetch_epoch == power_epoch && fetch_started_settled;
    fetch_pending = false;
    if (valid) { power_settling = false; }
    /* Never process an epoch-mismatched or unsettled reading as evidence. */
    if (fetch_started_settled && fetch_epoch == power_epoch) {
        /* Out-of-range voltage is a hard veto, not an ADC retry. */
        if (!channel_result && (mv < 2800 || mv > 4300)) {
            lism_presence_drop(&presence, LISM_PRESENCE_RANGE);
        } else { lism_presence_sample(&presence, now, valid, mv); }
    } else if (fetch_epoch != power_epoch) {
        presence.last_reason = LISM_PRESENCE_EPOCH;
    }
    battery_sample_valid = valid;
    battery_sample_percent = zmk_battery_state_of_charge();
    battery_sample_at = now;
    if (!valid || !presence.present) {
        local_filter.state.flags &= ~(LISM_CHARGE_LEVEL_VALID |
            LISM_CHARGE_BATTERY_CONFIRMED | LISM_CHARGE_BATTERY_INFERRED);
    }
    k_mutex_unlock(&charge_lock);
}

/* charge_lock held: state and optional diagnostics share one snapshot. */
static struct lism_charge_state charge_local_locked(void) {
    bool usb = nrf_power_usbregstatus_vbusdet_get(NRF_POWER);
    observe_power_locked(usb, k_uptime_get_32());
    struct lism_charge_state s = local_filter.state;
    /* Never serve a cached charging bolt after physical USB removal, even
     * when the 250ms sampling worker has not run yet. */
    if (usb) { s.flags |= LISM_CHARGE_USB; }
    else { s.flags &= ~(LISM_CHARGE_USB | LISM_CHARGE_STAT_ACTIVE); }
    if (!gpio_ready || k_uptime_get_32() - local_filter.sampled_at > 1500) {
        s = (struct lism_charge_state){.flags = LISM_CHARGE_OBSERVED |
            (usb ? LISM_CHARGE_USB : 0)};
    }
    uint32_t ttl = usb ? 3500U : CONFIG_ZMK_BATTERY_REPORT_INTERVAL * 1000U + 5000U;
    if (!battery_sample_valid || k_uptime_get_32() - battery_sample_at > ttl) {
        s.flags &= ~(LISM_CHARGE_LEVEL_VALID | LISM_CHARGE_BATTERY_CONFIRMED |
                     LISM_CHARGE_BATTERY_INFERRED);
    }
    return s;
}

struct lism_charge_state lism_charge_local(void) {
    k_mutex_lock(&charge_lock, K_FOREVER);
    struct lism_charge_state s = charge_local_locked();
    k_mutex_unlock(&charge_lock);
    return s;
}

static void sample_charge(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(charge_sample_work, sample_charge);
static void sample_charge(struct k_work *work) {
    ARG_UNUSED(work);
    /* Physical VBUS also covers wall chargers without USB enumeration and
     * split peripherals without the USB stack. Read-only: no power controls. */
    /* USB sampling requests go to the existing sole battery work item, not
     * another ADC reader. This keeps touch/IMU work free of ADC sleeps. */
    k_mutex_lock(&charge_lock, K_FOREVER);
    bool usb = nrf_power_usbregstatus_vbusdet_get(NRF_POWER);
    int stat = gpio_ready ? gpio_pin_get_raw(charge_port, 17) : -ENODEV;
    diagnostic_stat = stat;
    observe_power_locked(usb, k_uptime_get_32());
    uint32_t now = k_uptime_get_32();
    bool valid = battery_sample_valid && now - battery_sample_at <=
        (usb ? 3500U : CONFIG_ZMK_BATTERY_REPORT_INTERVAL * 1000U + 5000U);
    if (!valid && !power_settling) {
        /* A completed failed fetch already revoked evidence with its actual
         * reason. Only a once-valid, genuinely expired sample is a GAP. */
        if (battery_sample_valid) { lism_presence_retry(&presence, now, LISM_PRESENCE_GAP); }
        else if (presence.handoff && now - presence.seed_at > 7000U) {
            lism_presence_drop(&presence, LISM_PRESENCE_TIMEOUT);
        }
    }
    bool known = valid && presence.present;
    lism_charge_observe(&local_filter, now, usb, stat, known, known, battery_sample_percent);
    /* A 60s normal BAS period made the pre-USB evidence older than the 5s
     * handoff window almost every time a keyboard was plugged in. Keep a
     * recent battery-powered observation through the SAME ADC work owner.
     * USB cold boots still remain unknown; STAT LOW alone is not a battery. */
    uint32_t sample_interval = usb || now - power_changed_at < 5000U ? 1000U : 2000U;
    bool request_adc = now - last_adc_request_at >= sample_interval;
    if (request_adc) { last_adc_request_at = now; }
    k_mutex_unlock(&charge_lock);
    if (request_adc) { lism_charge_request_battery_sample(); }
    k_work_reschedule(&charge_sample_work, K_MSEC(250));
}

#if !IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL) || IS_ENABLED(CONFIG_LISM_STANDALONE_RIGHT)
static ssize_t read_charge(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                           void *buf, uint16_t len, uint16_t offset) {
    struct lism_charge_state s = lism_charge_local();
    uint8_t wire[LISM_CHARGE_WIRE_SIZE] = {LISM_CHARGE_VERSION, s.flags, s.percent, 0};
    return bt_gatt_attr_read(conn, attr, buf, len, offset, wire, sizeof(wire));
}
static ssize_t read_charge_diagnostics(struct bt_conn *conn,const struct bt_gatt_attr *attr,
                                      void *buf,uint16_t len,uint16_t offset) {
    k_mutex_lock(&charge_lock,K_FOREVER);
    struct lism_charge_state s=charge_local_locked();
    uint8_t wire[LISM_CHARGE_DIAG_SIZE]={2,s.flags,s.percent};
    uint32_t now=k_uptime_get_32();
    wire[3]=(battery_sample_valid?1:0)|(presence.present?2:0)|(presence.inferred?4:0)|
            (presence.handoff?8:0)|(presence.candidate?16:0)|(presence.reference_valid?32:0)|
            (power_settling?64:0);
    wire[4]=diagnostic_mv;wire[5]=diagnostic_mv>>8;
    wire[6]=diagnostic_stat==0?0:diagnostic_stat==1?1:255;
    wire[7]=(uint8_t)(int8_t)(diagnostic_result < -128 ? -128 : diagnostic_result > 127 ? 127 : diagnostic_result);
    uint32_t age=battery_sample_at?(now-battery_sample_at)/100:65535;
    if(age>65535)age=65535;
    wire[8]=age;wire[9]=age>>8;
    uint32_t uptime=now/1000;wire[10]=uptime;wire[11]=uptime>>8;
    wire[12]=presence.handoff_mv;wire[13]=presence.handoff_mv>>8;
    wire[14]=presence.last_reason;wire[15]=presence.candidate_count;
    uint32_t seed_age=presence.handoff?(now-presence.seed_at)/100:65535;
    if(seed_age>65535)seed_age=65535;
    wire[16]=seed_age;wire[17]=seed_age>>8;
    k_mutex_unlock(&charge_lock);
    return bt_gatt_attr_read(conn,attr,buf,len,offset,wire,sizeof(wire));
}
BT_GATT_SERVICE_DEFINE(lism_charge_service,
    BT_GATT_PRIMARY_SERVICE(BT_UUID_DECLARE_128(CHARGE_SERVICE_UUID)),
    BT_GATT_CHARACTERISTIC(&state_uuid.uuid, BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ_ENCRYPT, read_charge, NULL, NULL),
    BT_GATT_CHARACTERISTIC(&diag_uuid.uuid,BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ_ENCRYPT,read_charge_diagnostics,NULL,NULL));
#endif

#if IS_ENABLED(CONFIG_LISM_XIAO147_TOUCH)
#include "xiao147_peripheral_roles.h"
extern int peripheral_slot_index_for_conn(struct bt_conn *conn);
struct charge_peer {
    struct bt_conn *conn; /* Own one reference until no request is pending. */
    struct bt_gatt_read_params read;
    bool pending, valid, observed;
    struct lism_charge_state state;
    uint32_t sampled_at, next_at, requested_at, diag_at;
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
    if (matching_peer(p->conn, slot)) {
        if (p->observed) { s.flags = LISM_CHARGE_OBSERVED; }
        if (p->valid && k_uptime_get_32() - p->sampled_at < LISM_CHARGE_STALE_MS) {
            s = p->state;
        }
    }
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
    if (p->valid) { p->observed = true; p->state = s; p->sampled_at = k_uptime_get_32(); }
    /* Old firmware/no service is not an error loop. Retry after one minute. */
    p->next_at = k_uptime_get_32() + (p->valid ? 3000 : 60000);
    p->pending = false;
    k_mutex_unlock(&peers_lock);
    /* Pinned Zephyr permits params to be reused from callback entry. STOP
     * prevents continuation/long reads from using them after this callback. */
    return BT_GATT_ITER_STOP;
}
static uint8_t charge_diagnostics_done(struct bt_conn *conn,uint8_t err,
                                      struct bt_gatt_read_params *params,
                                      const void *data,uint16_t len) {
    struct charge_peer *p=CONTAINER_OF(params,struct charge_peer,read);
    unsigned slot=p-peers;
    k_mutex_lock(&peers_lock,K_FOREVER);
    bool valid=p->conn==conn && matching_peer(conn,slot) && !err && data &&
        k_uptime_get_32()-p->requested_at<=5000 &&
        ((len==LISM_CHARGE_DIAG_V1_SIZE && ((const uint8_t *)data)[0]==1) ||
         (len==LISM_CHARGE_DIAG_SIZE && ((const uint8_t *)data)[0]==2));
    if(valid) {
        const uint8_t *b=data;
        LOG_INF("Charge slot %u flags=0x%02x percent=%u evidence=0x%02x mv=%u STAT=%u adc_rc=%d age_ms=%u uptime_s_mod65536=%u",
                slot,b[1],b[2],b[3],b[4]|(b[5]<<8),b[6],(int8_t)b[7],
                (b[8]|(b[9]<<8))*100U,b[10]|(b[11]<<8));
        if (b[0]==2) {
            LOG_INF("Charge slot %u recovery baseline_mv=%u last_reason=%u votes=%u seed_age_ms=%u",
                    slot,b[12]|(b[13]<<8),b[14],b[15],(b[16]|(b[17]<<8))*100U);
        }
    } else {
        p->diag_at=k_uptime_get_32()+60000;
        LOG_INF("Charge slot %u diagnostics unavailable err=%u len=%u",slot,err,len);
    }
    p->pending=false;
    k_mutex_unlock(&peers_lock);
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
    bool diagnostics=p->valid && (p->state.flags & LISM_CHARGE_USB) &&
        !(p->state.flags & LISM_CHARGE_LEVEL_VALID) && (int32_t)(now-p->diag_at)>=0;
    if (!diagnostics && (int32_t)(now - p->next_at) < 0) { k_mutex_unlock(&peers_lock); return; }
    p->read = (struct bt_gatt_read_params){.func = diagnostics?charge_diagnostics_done:charge_read_done,
        .handle_count = 0,.by_uuid = {.start_handle = 1, .end_handle = 0xffff,
                                     .uuid = diagnostics?&diag_uuid.uuid:&state_uuid.uuid}};
    if(diagnostics)p->diag_at=now+10000;
    p->pending = true;
    p->requested_at = now;
    k_mutex_unlock(&peers_lock);
    int rc = bt_gatt_read(conn, &p->read);
    if (rc) {
        k_mutex_lock(&peers_lock, K_FOREVER);
        p->pending = false;
        /* Optional diagnostics must never invalidate a good state report. */
        if (diagnostics) { p->diag_at = now + 60000; }
        else { p->valid = false; p->next_at = now + 3000; }
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
