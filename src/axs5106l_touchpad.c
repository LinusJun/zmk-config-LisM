/* AXS5106L prefix layout follows Seeed's xiao_nrf52840_147_touch example:
 * count at byte 1, six-byte contact records beginning at byte 2.
 * 32-byte reads probe the vendor's claimed five-point capability; this is NOT
 * an established five-point protocol or a controller configuration command.
 * A failed extended read locks this boot to the original 14-byte reader.
 */
#define DT_DRV_COMPAT lism_axs5106l_touchpad

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

#include <zephyr/device.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/input/input.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/dt-bindings/input/input-event-codes.h>

#include "xiao147_ui.h"
#include "xiao147_rotation.h"

#ifdef CONFIG_LISM_XIAO147_RAW_CAPTURE
#include <stdio.h>
#include <string.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/atomic.h>
#include "xiao147_raw_capture.h"
#include "xiao147_raw_control.h"
#endif

LOG_MODULE_REGISTER(lism_axs5106l, LOG_LEVEL_INF);

#define AXS_TOUCH_REPORT_REG 0x01
#define AXS_MAX_POINTS 5
#define AXS_LEGACY_REPORT_LEN 14
#define AXS_TOUCH_REPORT_LEN (2 + AXS_MAX_POINTS * 6)
#define AXS_REGISTER_SETTLE_US 100
#define TOUCH_POLL_MS 16
#define TOUCH_JUMP_LIMIT 80
#define TAP_MAX_MS 240
#define TAP_MAX_MOVE 12
#define SCROLL_PIXELS_PER_TICK 8

struct axs_touch_config { struct i2c_dt_spec bus; };
struct point { int16_t x; int16_t y; };

struct axs_touch_data {
    struct k_work_delayable poll_work;
    const struct device *dev;
    enum xiao147_page last_page;
    uint8_t fingers;
    uint8_t max_fingers;
    enum xiao147_orientation last_orientation;
    struct point last;
    struct point start;
    uint32_t touch_start_ms;
    int16_t scroll_x_remainder;
    int16_t scroll_y_remainder;
    bool moved;
    bool scrolled;
    bool swallow_until_release;
    uint32_t read_errors;
    uint32_t bad_reports;
    bool id_logged;
    uint8_t reported_fingers;
    uint32_t last_trace_ms;
    uint32_t wheel_events;
    uint32_t input_errors;
    uint32_t last_report_trace_ms;
    uint32_t count_histogram[AXS_MAX_POINTS + 1];
    uint32_t reads_extended;
    uint32_t reads_legacy;
    uint32_t extended_failures;
    uint32_t last_summary_ms;
    bool legacy_only;
#ifdef CONFIG_LISM_XIAO147_RAW_CAPTURE
    struct xiao147_raw_capture capture;
    struct xiao147_raw_control control;
    uint8_t info_stage;
    uint8_t info_id[3];
    uint16_t info_fw;
    bool info_valid;
#endif
};

static bool point_valid(const uint8_t report[AXS_TOUCH_REPORT_LEN], uint8_t index)
{
    const uint8_t off = 2 + index * 6;
    uint16_t x = ((uint16_t)(report[off] & 0x0f) << 8) | report[off + 1];
    uint16_t y = ((uint16_t)(report[off + 2] & 0x0f) << 8) | report[off + 3];
    return x < 172 && y < 320;
}

static struct point decode_point(const uint8_t report[AXS_TOUCH_REPORT_LEN], uint8_t index,
                                 enum xiao147_orientation orientation)
{
    const uint8_t off = 2 + index * 6;
    uint16_t raw_x = ((uint16_t)(report[off] & 0x0f) << 8) | report[off + 1];
    uint16_t raw_y = ((uint16_t)(report[off + 2] & 0x0f) << 8) | report[off + 3];
    struct xiao147_xy p = xiao147_touch_to_logical(orientation, raw_x, raw_y);
    return (struct point){.x = p.x, .y = p.y};
}

static void report_button(const struct device *dev, uint16_t code)
{
    input_report_key(dev, code, 1, true, K_NO_WAIT);
    input_report_key(dev, code, 0, true, K_NO_WAIT);
}

static void report_pointer(const struct device *dev, int16_t dx, int16_t dy)
{
    if (dx != 0) { input_report_rel(dev, INPUT_REL_X, dx, dy == 0, K_NO_WAIT); }
    if (dy != 0) { input_report_rel(dev, INPUT_REL_Y, dy, true, K_NO_WAIT); }
}

static void report_scroll(struct axs_touch_data *data, int16_t dx, int16_t dy)
{
    data->scroll_x_remainder += dx;
    data->scroll_y_remainder += dy;
    int16_t sx = data->scroll_x_remainder / SCROLL_PIXELS_PER_TICK;
    int16_t sy = data->scroll_y_remainder / SCROLL_PIXELS_PER_TICK;
    data->scroll_x_remainder -= sx * SCROLL_PIXELS_PER_TICK;
    data->scroll_y_remainder -= sy * SCROLL_PIXELS_PER_TICK;
    /* HID wheel sign is inverted relative to content motion. */
    int horizontal = 0, vertical = 0;
    if (sx != 0) { horizontal = input_report_rel(data->dev, INPUT_REL_HWHEEL, -sx, sy == 0, K_NO_WAIT); }
    if (sy != 0) { vertical = input_report_rel(data->dev, INPUT_REL_WHEEL, -sy, true, K_NO_WAIT); }
    if (sx || sy) {
        data->scrolled = true; /* Even one tick cancels the right-click tap. */
        data->wheel_events++;
        if (horizontal < 0 || vertical < 0) { data->input_errors++; }
        if (k_uptime_get_32() - data->last_trace_ms >= 1000) {
            LOG_INF("Touch wheel %d %d, input result %d %d, events %u failures %u",
                    -sx, -sy, horizontal, vertical, data->wheel_events, data->input_errors);
            data->last_trace_ms = k_uptime_get_32();
        }
    }
}

static void end_contact(struct axs_touch_data *data, bool allow_tap)
{
    if (allow_tap && data->fingers != 0 && !data->moved && !data->scrolled &&
        !data->swallow_until_release &&
        k_uptime_get_32() - data->touch_start_ms <= TAP_MAX_MS) {
        report_button(data->dev, data->max_fingers >= 2 ? INPUT_BTN_1 : INPUT_BTN_0);
    }
    data->fingers = 0;
    data->max_fingers = 0;
    data->moved = false;
    data->scrolled = false;
    data->scroll_x_remainder = 0;
    data->scroll_y_remainder = 0;
    data->swallow_until_release = false;
}

static void cancel_contact(struct axs_touch_data *data)
{
    bool active = data->fingers != 0 || data->swallow_until_release;
    if (data->last_page != XIAO147_PAGE_STATUS && data->fingers != 0) {
        /* Cancel is not a release: an I2C glitch must not toggle a menu row. */
        xiao147_ui_touch(XIAO147_TOUCH_CANCEL, 0, 0, data->last_orientation);
    }
    end_contact(data, false);
    /* The unread sample is not evidence of release. Preserve cancellation
     * through repeated errors, until a valid zero-contact report arrives. */
    data->swallow_until_release = active;
}

static int axs_read_register(const struct i2c_dt_spec *bus, uint8_t reg,
                             uint8_t *report, size_t len)
{
    int ret = i2c_write_dt(bus, &reg, 1);
    if (ret != 0) { return ret; }
    /* ChipSourceTek's companion CST5106L document (linked from its AXS5106L
     * product page) has an indexed excerpt labeling STOP -> data preparation
     * Twr >45us; the full PDF could not be verified. Use 100us
     * margin, including ID and legacy fallback. This is a timing trial, NOT
     * a documented command for enabling multitouch on this panel firmware.
     * Keep STOP-separated transactions; do not change to repeated START. */
    k_busy_wait(AXS_REGISTER_SETTLE_US);
    return i2c_read_dt(bus, report, len);
}

#ifdef CONFIG_LISM_XIAO147_RAW_CAPTURE
static atomic_t capture_gate;
static const struct device *const capture_console = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

bool xiao147_touch_capture_active(void)
{
    return atomic_get(&capture_gate) != 0;
}

static int axs_raw_read(void *context, uint8_t reg, uint8_t *buffer, size_t length)
{
    struct axs_touch_data *data = context;
    const struct axs_touch_config *cfg = data->dev->config;
    /* Even the independent engine cannot issue an arbitrary register read. */
    bool allowed = (reg == 0x08 && length == 3) || (reg == 0x05 && length == 2) ||
        (reg == 0xfe && length == 2) || (reg == 0x0b && length == 3) ||
        (reg == 0x03 && length == 1) || (reg == 0x01 && length == 14) ||
        (reg == 0x02 && length >= 2 && length <= 26 && length % 2 == 0);
    if (!allowed) { return -EINVAL; }
    return axs_read_register(&cfg->bus, reg, buffer, length);
}

static int axs_raw_write(void *context, uint8_t reg, const uint8_t *payload, size_t length)
{
    struct axs_touch_data *data = context;
    const struct axs_touch_config *cfg = data->dev->config;
    /* No calibration, sleep, debug, MTP, INT or open/short-test writes. */
    bool allowed = (reg == 0xfe && length == 3 && payload[0] == 0x5a &&
                    payload[1] == 0xa5 && (payload[2] == 0x00 || payload[2] == 0x20)) ||
        (reg == 0x00 && length == 1 && (payload[0] == 1 || payload[0] == 2)) ||
        (reg == 0x03 && length == 1 && payload[0] == 1);
    if (!allowed) { return -EINVAL; }
    uint8_t command[4] = {reg};
    memcpy(command + 1, payload, length);
    return i2c_write_dt(&cfg->bus, command, length + 1);
}

static void axs_raw_event(void *context, enum xiao147_raw_event event, int detail)
{
    struct axs_touch_data *data = context;
    LOG_INF("AXS_RAW_EVENT %d detail=%d", event, detail);
    if (event == XIAO147_RAW_CHANNELS) {
        LOG_INF("AXS_RAW_LAYOUT bytes=%02x,%02x,%02x n=%d",
                data->capture.channel_layout[0], data->capture.channel_layout[1],
                data->capture.channel_layout[2], detail);
    } else if (event == XIAO147_RAW_FAULT) {
        LOG_ERR("AXS_RAW_FAULT: touch/air-mouse isolated; disconnect ALL power to recover");
    }
}

static void axs_raw_sample(void *context, enum xiao147_raw_type type, uint8_t channels,
                           const int32_t *values, uint32_t elapsed_ms)
{
    (void)context;
    char line[156];
    size_t used = 0;
    for (uint8_t i = 0; i < channels; i++) {
        int written = snprintf(line + used, sizeof(line) - used, "%s%ld",
                               i ? "," : "", (long)values[i]);
        if (written < 0 || (size_t)written >= sizeof(line) - used) { return; }
        used += (size_t)written;
    }
    LOG_INF("AXS_SENSOR t=%u type=%u n=%u values=%s", elapsed_ms, type, channels, line);
}

static void axs_raw_command(struct axs_touch_data *data, enum xiao147_raw_command command)
{
    bool active = xiao147_raw_capture_active(&data->capture);
    if (command == XIAO147_RAW_COMMAND_STOP) {
        data->info_stage = 0;
        xiao147_raw_capture_stop(&data->capture, k_uptime_get_32());
        LOG_INF("AXS_RAW_STOP requested");
    } else if (command == XIAO147_RAW_COMMAND_INFO && !active && !data->info_stage) {
        cancel_contact(data);
        data->swallow_until_release = true;
        data->info_valid = false;
        data->info_stage = 1;
    } else if (command == XIAO147_RAW_COMMAND_START && !active && !data->info_stage &&
               data->info_valid && data->info_fw == data->control.expected_fw) {
        cancel_contact(data);
        data->swallow_until_release = true;
        data->info_valid = false; /* A new session always needs fresh INFO. */
        if (xiao147_raw_capture_start(&data->capture, k_uptime_get_32(),
                                     data->control.expected_fw)) {
            atomic_set(&capture_gate, 1);
            LOG_INF("AXS_RAW_START expected_fw=%04x; temporary sampling, no calibration/MTP", data->control.expected_fw);
        }
    } else if (command != XIAO147_RAW_COMMAND_NONE) {
        LOG_WRN("AXS_RAW command rejected: fresh INFO/version or idle state required");
    }
}

static bool axs_raw_poll(struct axs_touch_data *data)
{
    bool was_active = xiao147_raw_capture_active(&data->capture);
    uint32_t dtr = 0;
    bool connected = device_is_ready(capture_console) &&
        uart_line_ctrl_get(capture_console, UART_LINE_CTRL_DTR, &dtr) == 0 && dtr != 0;
    if (!connected) {
        data->control = (struct xiao147_raw_control){0};
        data->info_stage = 0;
        data->info_valid = false;
        if (was_active) { xiao147_raw_capture_stop(&data->capture, k_uptime_get_32()); }
    } else {
        /* Pinned legacy Zephyr CDC ACM queues USB OUT to its RX ring even with
         * RX IRQ disabled; poll_in drains that ring. Do not replace console
         * TX/IRQ callbacks. Kconfig excludes competing shell/input handlers. */
        for (unsigned i = 0; i < 32; i++) {
            uint8_t byte;
            int ret = uart_poll_in(capture_console, &byte);
            if (ret != 0) {
                if (ret != -1) { xiao147_raw_capture_stop(&data->capture, k_uptime_get_32()); }
                break;
            }
            axs_raw_command(data, xiao147_raw_control_byte(&data->control, byte));
        }
    }
    if (xiao147_raw_capture_active(&data->capture)) {
        /* Sole 0x63 owner: no ordinary touch reads while on the factory page,
         * including cleanup and sticky recovery faults. */
        xiao147_raw_capture_tick(&data->capture, k_uptime_get_32());
        bool active = xiao147_raw_capture_active(&data->capture);
        atomic_set(&capture_gate, active);
        if (!active) {
            data->swallow_until_release = true;
            LOG_INF("AXS_RAW_INPUT restored; waiting for a real touch release");
        }
        return true;
    }
    atomic_set(&capture_gate, 0);
    if (data->info_stage) {
        int ret;
        if (data->info_stage == 1) {
            ret = axs_raw_read(data, 0x08, data->info_id, 3);
            if (ret == 0) { data->info_stage = 2; }
            else { data->info_stage = 0; LOG_WRN("AXS_RAW_INFO ID read failed %d", ret); }
        } else {
            uint8_t version[2];
            ret = axs_raw_read(data, 0x05, version, 2);
            if (ret == 0) {
                data->info_fw = ((uint16_t)version[0] << 8) | version[1];
                data->info_valid = data->info_id[0] == 0x51 && data->info_id[1] == 0x06 &&
                                   data->info_id[2] == 0x01 && data->info_fw != 0 &&
                                   data->info_fw != UINT16_MAX;
                LOG_INF("AXS_RAW_INFO ID=%02x%02x%02x FW=%04x valid=%d", data->info_id[0],
                        data->info_id[1], data->info_id[2], data->info_fw, data->info_valid);
            } else { LOG_WRN("AXS_RAW_INFO FW read failed %d", ret); }
            data->info_stage = 0;
        }
        return true;
    }
    return was_active;
}
#endif

static void axs_poll(struct k_work *work)
{
    struct k_work_delayable *dwork = k_work_delayable_from_work(work);
    struct axs_touch_data *data = CONTAINER_OF(dwork, struct axs_touch_data, poll_work);
    const struct axs_touch_config *cfg = data->dev->config;
#ifdef CONFIG_LISM_XIAO147_RAW_CAPTURE
    if (axs_raw_poll(data)) { goto next; }
#endif
    if (!data->id_logged) {
        uint8_t id_reg = 0x08, id[3] = {0};
        int ret = axs_read_register(&cfg->bus, id_reg, id, sizeof(id));
        LOG_INF("Touch ID %02x %02x %02x, result %d", id[0], id[1], id[2], ret);
        data->id_logged = true;
    }
    uint8_t reg = AXS_TOUCH_REPORT_REG;
    uint8_t report[AXS_TOUCH_REPORT_LEN];
    /* Match Seeed's separate STOP-terminated register select and read. */
    int ret = i2c_write_dt(&cfg->bus, &reg, sizeof(reg));
    size_t report_len = data->legacy_only ? AXS_LEGACY_REPORT_LEN : sizeof(report);
    bool extended_read_attempted = ret == 0 && !data->legacy_only;
    if (ret == 0) {
        k_busy_wait(AXS_REGISTER_SETTLE_US);
        ret = i2c_read_dt(&cfg->bus, report, report_len);
    }
    if (ret < 0 && extended_read_attempted) {
        /* Do not repeatedly probe an unsupported length every 16ms. */
        data->legacy_only = true;
        data->extended_failures++;
        cancel_contact(data);
        LOG_WRN("Touch extended read failed %d; lock this boot to legacy 14-byte reads", ret);
        report_len = AXS_LEGACY_REPORT_LEN;
        ret = axs_read_register(&cfg->bus, reg, report, report_len);
    }
    if (ret < 0) {
        if (data->read_errors++ % 200 == 0) {
            LOG_WRN("Touch I2C error %d, failures %u", ret, data->read_errors);
        }
        cancel_contact(data);
        goto next;
    }

    uint8_t fingers = report[1];
    if (report_len == AXS_LEGACY_REPORT_LEN) { data->reads_legacy++; }
    else { data->reads_extended++; }
    if (fingers <= AXS_MAX_POINTS) { data->count_histogram[fingers]++; }
    uint8_t available = (report_len - 2) / 6;
    bool invalid = fingers > available;
    if (!invalid) {
        for (uint8_t i = 0; i < fingers; i++) {
            if (!point_valid(report, i)) { invalid = true; break; }
        }
    }
    /* Capture BEFORE rejection: diagnose count/ID/event nibbles and the full
     * second record even if our current format assumption rejects it. */
    bool first_report = data->reads_extended + data->reads_legacy == 1;
    if (first_report || ((invalid || fingers > 0 || fingers != data->reported_fingers) &&
        k_uptime_get_32() - data->last_report_trace_ms >= 1000)) {
        LOG_INF("Touch raw14 %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x, invalid %d",
                report[0], report[1], report[2], report[3], report[4], report[5], report[6],
                report[7], report[8], report[9], report[10], report[11], report[12], report[13], invalid);
        data->last_report_trace_ms = k_uptime_get_32();
        if (report_len > AXS_LEGACY_REPORT_LEN) {
            LOG_HEXDUMP_INF(report + AXS_LEGACY_REPORT_LEN,
                            report_len - AXS_LEGACY_REPORT_LEN, "Touch probe tail (points 3-5)");
        }
    }
    if (invalid) {
        if (data->bad_reports++ % 200 == 0) {
            LOG_WRN("Touch invalid count %u, xy bytes %02x %02x %02x %02x", fingers,
                    report[2], report[3], report[4], report[5]);
        }
        cancel_contact(data);
        /* A malformed nonzero report is not evidence of a new gesture or a
         * release. Even from idle, swallow its remainder until valid zero. */
        if (fingers != 0) { data->swallow_until_release = true; }
        goto next;
    }
    if (fingers != data->reported_fingers) {
        data->reported_fingers = fingers;
        /* Upper nibbles are retained for diagnosis rather than assuming an
         * undocumented event/ID convention. The official example masks them.
         * Only real multi-contact reports can establish device capability. */
        LOG_DBG("Touch contacts %u, page %d", fingers, xiao147_ui_page());
    }

    enum xiao147_page page = xiao147_ui_page();
    enum xiao147_orientation orientation = xiao147_rotation_get();
    if (page != data->last_page || orientation != data->last_orientation) {
        end_contact(data, false);
        data->last_page = page;
        data->last_orientation = orientation;
        data->swallow_until_release = fingers != 0;
    }

    if (fingers == 0) {
        if (page != XIAO147_PAGE_STATUS && data->fingers != 0 && !data->swallow_until_release) {
            xiao147_ui_touch(0, 0, 0, orientation);
        }
        end_contact(data, page == XIAO147_PAGE_STATUS);
        goto next;
    }
    if (fingers > 2) {
        /* Only one-/two-finger gestures are assigned. A third contact cancels
         * the whole gesture, even if it later becomes 2->1 before release. */
        cancel_contact(data);
        data->swallow_until_release = true;
        goto next;
    }

    struct point p = decode_point(report, 0, orientation);
    if (fingers == 2) {
        struct point q = decode_point(report, 1, orientation);
        p.x = (p.x + q.x) / 2;
        p.y = (p.y + q.y) / 2;
    }

    if (xiao147_ui_wake_on_touch()) { data->swallow_until_release = true; }
    if (data->swallow_until_release) { goto next; }
    if (page != XIAO147_PAGE_STATUS) {
        xiao147_ui_touch(fingers, p.x, p.y, orientation);
        data->fingers = fingers;
        goto next;
    }

    if (data->fingers == 0 || data->fingers != fingers) {
        if (data->fingers == 0) {
            data->touch_start_ms = k_uptime_get_32();
            data->moved = false;
        }
        data->fingers = fingers;
        if (fingers > data->max_fingers) { data->max_fingers = fingers; }
        data->last = p;
        data->start = p;
        data->scroll_x_remainder = 0;
        data->scroll_y_remainder = 0;
        goto next;
    }

    int16_t dx = p.x - data->last.x;
    int16_t dy = p.y - data->last.y;
    if (abs(dx) <= TOUCH_JUMP_LIMIT && abs(dy) <= TOUCH_JUMP_LIMIT) {
        if (abs(p.x - data->start.x) > TAP_MAX_MOVE ||
            abs(p.y - data->start.y) > TAP_MAX_MOVE) { data->moved = true; }
        if (fingers == 2) { report_scroll(data, dx, dy); }
        else if (data->max_fingers < 2) { report_pointer(data->dev, dx, dy); }
    }
    data->last = p;

next:
    if (k_uptime_get_32() - data->last_summary_ms >= 2000) {
        LOG_INF("Touch summary count0..5 %u %u %u %u %u %u, reads32 %u reads14 %u fallback %u invalid %u I2C %u wheel %u",
                data->count_histogram[0], data->count_histogram[1], data->count_histogram[2],
                data->count_histogram[3], data->count_histogram[4], data->count_histogram[5],
                data->reads_extended, data->reads_legacy, data->extended_failures,
                data->bad_reports, data->read_errors, data->wheel_events);
        data->last_summary_ms = k_uptime_get_32();
    }
    k_work_reschedule(&data->poll_work, K_MSEC(TOUCH_POLL_MS));
}

static int axs_touch_init(const struct device *dev)
{
    const struct axs_touch_config *cfg = dev->config;
    struct axs_touch_data *data = dev->data;
    if (!i2c_is_ready_dt(&cfg->bus)) { return -ENODEV; }
    data->dev = dev;
    data->last_page = XIAO147_PAGE_STATUS;
#ifdef CONFIG_LISM_XIAO147_RAW_CAPTURE
    BUILD_ASSERT(CONFIG_I2C_NRFX_TRANSFER_TIMEOUT > 0 && CONFIG_I2C_NRFX_TRANSFER_TIMEOUT <= 100,
                 "RAW diagnostic requires a bounded <=100ms I2C transfer timeout");
    static const struct xiao147_raw_ops ops = {
        .read = axs_raw_read, .write = axs_raw_write,
        .event = axs_raw_event, .sample = axs_raw_sample,
    };
    xiao147_raw_capture_init(&data->capture, &ops, data);
    LOG_INF("AXS_RAW_READY v1: INFO then START <FWhex>, STOP; 60s limit, failure requires power cycle");
#endif
    LOG_INF("Touch reader: reg01 extended32 probe, settle100us; single failure locks legacy14; no controller reprogramming");
    k_work_init_delayable(&data->poll_work, axs_poll);
    k_work_schedule(&data->poll_work, K_MSEC(1000));
    return 0;
}

#define AXS_DEFINE(inst) \
    static const struct axs_touch_config axs_cfg_##inst = {.bus = I2C_DT_SPEC_INST_GET(inst)}; \
    static struct axs_touch_data axs_data_##inst; \
    DEVICE_DT_INST_DEFINE(inst, axs_touch_init, NULL, &axs_data_##inst, &axs_cfg_##inst, \
                          POST_KERNEL, CONFIG_INPUT_INIT_PRIORITY, NULL);

DT_INST_FOREACH_STATUS_OKAY(AXS_DEFINE)
