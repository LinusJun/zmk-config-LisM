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

LOG_MODULE_REGISTER(lism_axs5106l, LOG_LEVEL_INF);

#define AXS_TOUCH_REPORT_REG 0x01
#define AXS_MAX_POINTS 5
#define AXS_LEGACY_REPORT_LEN 14
#define AXS_TOUCH_REPORT_LEN (2 + AXS_MAX_POINTS * 6)
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

static void axs_poll(struct k_work *work)
{
    struct k_work_delayable *dwork = k_work_delayable_from_work(work);
    struct axs_touch_data *data = CONTAINER_OF(dwork, struct axs_touch_data, poll_work);
    const struct axs_touch_config *cfg = data->dev->config;
    if (!data->id_logged) {
        uint8_t id_reg = 0x08, id[3] = {0};
        int ret = i2c_write_dt(&cfg->bus, &id_reg, 1);
        if (ret == 0) { ret = i2c_read_dt(&cfg->bus, id, sizeof(id)); }
        LOG_INF("Touch ID %02x %02x %02x, result %d", id[0], id[1], id[2], ret);
        data->id_logged = true;
    }
    uint8_t reg = AXS_TOUCH_REPORT_REG;
    uint8_t report[AXS_TOUCH_REPORT_LEN];
    /* Match Seeed's separate STOP-terminated register select and read. */
    int ret = i2c_write_dt(&cfg->bus, &reg, sizeof(reg));
    size_t report_len = data->legacy_only ? AXS_LEGACY_REPORT_LEN : sizeof(report);
    bool extended_read_attempted = ret == 0 && !data->legacy_only;
    if (ret == 0) { ret = i2c_read_dt(&cfg->bus, report, report_len); }
    if (ret < 0 && extended_read_attempted) {
        /* Do not repeatedly probe an unsupported length every 16ms. */
        data->legacy_only = true;
        data->extended_failures++;
        cancel_contact(data);
        LOG_WRN("Touch extended read failed %d; lock this boot to legacy 14-byte reads", ret);
        report_len = AXS_LEGACY_REPORT_LEN;
        ret = i2c_write_dt(&cfg->bus, &reg, sizeof(reg));
        if (ret == 0) { ret = i2c_read_dt(&cfg->bus, report, report_len); }
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
    if ((invalid || fingers >= 2 || fingers != data->reported_fingers) &&
        k_uptime_get_32() - data->last_report_trace_ms >= 1000) {
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
    LOG_INF("Touch reader: reg01 extended32 probe, single failure locks legacy14; no controller reprogramming");
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
