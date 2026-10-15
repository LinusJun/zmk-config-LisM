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
#include <zephyr/sys/atomic.h>
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
#ifdef CONFIG_LISM_XIAO147_GESTURE_SHADOW
#include "xiao147_raw_gestures.h"
#endif
#endif

LOG_MODULE_REGISTER(lism_axs5106l, LOG_LEVEL_INF);

#define AXS_TOUCH_REPORT_REG 0x01
#define AXS_MAX_POINTS 5
#define AXS_LEGACY_REPORT_LEN 14
#define AXS_TOUCH_REPORT_LEN (2 + AXS_MAX_POINTS * 6)
#define AXS_REGISTER_SETTLE_US 100
#define TOUCH_POLL_MS 16
#define TOUCH_JUMP_LIMIT 80
#define TOUCH_MAX_GAP_MS 100
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
    uint32_t last_valid_frame_ms;
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
    uint16_t pending_button;
    uint32_t last_release_error_ms;
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
    bool normal_zero;
    uint32_t normal_zero_since_ms;
    uint32_t normal_frame_ms;
    bool experimental_uart_quarantine;
    bool experimental_uart_saw_low;
    bool experimental_uart_low_drained;
#ifdef CONFIG_LISM_XIAO147_GESTURE_SHADOW
    struct xiao147_raw_baseline baseline;
    struct xiao147_shadow_gesture shadow;
    bool shadow_active;
    bool shadow_abort_pending;
    bool shadow_armed_logged;
    uint32_t shadow_last_log_ms;
    enum xiao147_raw_hypothesis shadow_last_hypothesis;
    uint32_t shadow_histogram[5];
    uint32_t shadow_taps;
    uint32_t shadow_scrolls;
    uint32_t shadow_last_rate_ms;
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
    bool experimental_hid_active;
    bool experimental_button_down;
    uint32_t experimental_release_log_ms;
#endif
#endif
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

static atomic_t normal_button_gate;

bool xiao147_touch_button_pending(void)
{
    return atomic_get(&normal_button_gate) != 0;
}

static bool release_button(struct axs_touch_data *data)
{
    if (data->pending_button == 0) { return true; }
    int ret = input_report_key(data->dev, data->pending_button, 0, true, K_NO_WAIT);
    if (ret == 0) {
        data->pending_button = 0;
        atomic_set(&normal_button_gate, 0);
        return true;
    }
    data->input_errors++;
#ifdef CONFIG_LISM_XIAO147_RAW_CAPTURE
    if (!data->experimental_uart_quarantine) {
        data->experimental_uart_quarantine = true;
        data->experimental_uart_saw_low = false;
        data->experimental_uart_low_drained = false;
    }
#endif
    if (k_uptime_get_32() - data->last_release_error_ms >= 1000) {
        LOG_WRN("Touch button release retry failed %d", ret);
        data->last_release_error_ms = k_uptime_get_32();
    }
    return false;
}

static void report_button(struct axs_touch_data *data, uint16_t code)
{
    if (data->pending_button != 0) { return; }
    int ret = input_report_key(data->dev, code, 1, true, K_NO_WAIT);
    if (ret != 0) {
        data->input_errors++;
        return; /* Failed presses are never replayed as delayed clicks. */
    }
    data->pending_button = code;
    atomic_set(&normal_button_gate, 1);
    if (!release_button(data)) { data->swallow_until_release = true; }
}

static bool report_pointer(struct axs_touch_data *data, int16_t dx, int16_t dy)
{
    /* Each accepted axis is complete, even if the next queue submission fails. */
    int ret = dx == 0 ? 0 : input_report_rel(data->dev, INPUT_REL_X, dx, true, K_NO_WAIT);
    if (ret == 0 && dy != 0) { ret = input_report_rel(data->dev, INPUT_REL_Y, dy, true, K_NO_WAIT); }
    if (ret != 0) { data->input_errors++; }
    return ret == 0;
}

static bool report_scroll(struct axs_touch_data *data, int16_t dx, int16_t dy)
{
    data->scroll_x_remainder += dx;
    data->scroll_y_remainder += dy;
    int16_t sx = data->scroll_x_remainder / SCROLL_PIXELS_PER_TICK;
    int16_t sy = data->scroll_y_remainder / SCROLL_PIXELS_PER_TICK;
    data->scroll_x_remainder -= sx * SCROLL_PIXELS_PER_TICK;
    data->scroll_y_remainder -= sy * SCROLL_PIXELS_PER_TICK;
    /* HID wheel sign is inverted relative to content motion. */
    int horizontal = 0, vertical = 0;
    if (sx != 0) { horizontal = input_report_rel(data->dev, INPUT_REL_HWHEEL, -sx, true, K_NO_WAIT); }
    if (horizontal == 0 && sy != 0) { vertical = input_report_rel(data->dev, INPUT_REL_WHEEL, -sy, true, K_NO_WAIT); }
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
    return horizontal == 0 && vertical == 0;
}

static void end_contact(struct axs_touch_data *data, bool allow_tap)
{
    bool tap = allow_tap && data->fingers != 0 && !data->moved && !data->scrolled &&
        !data->swallow_until_release &&
        k_uptime_get_32() - data->touch_start_ms <= TAP_MAX_MS;
    uint16_t button = data->max_fingers >= 2 ? INPUT_BTN_1 : INPUT_BTN_0;
    data->fingers = 0;
    data->max_fingers = 0;
    data->moved = false;
    data->scrolled = false;
    data->scroll_x_remainder = 0;
    data->scroll_y_remainder = 0;
    data->swallow_until_release = false;
    if (tap) { report_button(data, button); }
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

#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
/* Only the separate, explicitly activated experiment reaches these helpers.
 * Queue acceptance is not an OS acknowledgment. A failed release is retried
 * once per work tick and keeps ordinary touch/air-mouse isolated until accepted.
 */
static bool axs_experimental_release(struct axs_touch_data *data)
{
    if (!data->experimental_button_down) { return true; }
    int ret = input_report_key(data->dev, INPUT_BTN_1, 0, true, K_NO_WAIT);
    if (ret == 0) {
        data->experimental_button_down = false;
        return true;
    }
    data->input_errors++;
    if (!data->experimental_uart_quarantine) {
        data->experimental_uart_quarantine = true;
        data->experimental_uart_saw_low = false;
        data->experimental_uart_low_drained = false;
    }
    data->control = (struct xiao147_raw_control){0};
    data->info_stage = 0;
    data->info_valid = false;
    if (k_uptime_get_32() - data->experimental_release_log_ms >= 1000) {
        LOG_ERR("AXS_GESTURE right-button release retry failed %d; normal input remains isolated", ret);
        data->experimental_release_log_ms = k_uptime_get_32();
    }
    return false;
}
#endif

static bool axs_experimental_discard_commands(struct axs_touch_data *data,
                                              bool known_low, bool known_high)
{
    if (!data->experimental_uart_quarantine) { return false; }
    /* An empty RX poll is not a session boundary: old USB OUT fragments may
     * arrive later. Keep discarding until a verified DTR-low/drained state
     * followed by DTR-high, an idle engine and an accepted button release.
     * Console/query errors cannot prove DTR-low or an empty receive queue.
     */
    data->control = (struct xiao147_raw_control){0};
    data->info_stage = 0;
    data->info_valid = false;
    if (known_low) { data->experimental_uart_saw_low = true; }
    bool empty = false;
    if (device_is_ready(capture_console)) {
        for (unsigned i = 0; i < 32; i++) {
            uint8_t ignored;
            int ret = uart_poll_in(capture_console, &ignored);
            if (ret != 0) {
                empty = ret == -1;
                break;
            }
        }
    }
    if (known_low) { data->experimental_uart_low_drained = empty; }
    bool button_down = data->pending_button != 0;
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
    button_down = button_down || data->experimental_button_down;
#endif
    if (known_high && data->experimental_uart_saw_low &&
        data->experimental_uart_low_drained && empty &&
        !button_down &&
        !xiao147_raw_capture_active(&data->capture)) {
        data->experimental_uart_quarantine = false;
        data->experimental_uart_saw_low = false;
        data->experimental_uart_low_drained = false;
        LOG_INF("AXS_GESTURE command quarantine cleared after DTR reconnect; fresh INFO required");
    }
    return true; /* Never parse bytes in the same tick that drains isolation. */
}

#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
static void axs_experimental_abort(struct axs_touch_data *data)
{
    data->experimental_hid_active = false;
    xiao147_shadow_gesture_cancel(&data->shadow);
    xiao147_raw_capture_stop(&data->capture, k_uptime_get_32());
}

static void axs_experimental_emit(struct axs_touch_data *data,
                                 struct xiao147_shadow_result result)
{
    if (!data->experimental_hid_active || result.cancelled || !data->baseline.armed ||
        data->capture.phase != XIAO147_RAW_READ_SAMPLE ||
        xiao147_ui_page() != XIAO147_PAGE_STATUS || xiao147_ui_imu_enabled() ||
        xiao147_ui_is_sleeping()) { return; }
    if (!axs_experimental_release(data) || result.candidate_wheel < -127 ||
        result.candidate_wheel > 127 || result.candidate_hwheel < -127 ||
        result.candidate_hwheel > 127) {
        axs_experimental_abort(data);
        return;
    }
    if (result.candidate_right_tap) {
        if (result.candidate_wheel || result.candidate_hwheel) {
            axs_experimental_abort(data);
            return;
        }
        int press = input_report_key(data->dev, INPUT_BTN_1, 1, true, K_NO_WAIT);
        if (press == 0) { data->experimental_button_down = true; }
        else { data->input_errors++; }
        bool released = axs_experimental_release(data);
        LOG_INF("AXS_GESTURE right tap queue_press=%d queue_release=%d", press, released);
        if (press != 0 || !released) { axs_experimental_abort(data); }
    } else {
        int horizontal = 0, vertical = 0;
        /* Each accepted axis is a complete sync event, never half a deferred
         * two-axis batch that could be flushed by a later unrelated action.
         * The profile tracker already negates logical finger deltas.
         */
        if (result.candidate_hwheel) {
            horizontal = input_report_rel(data->dev, INPUT_REL_HWHEEL,
                                           result.candidate_hwheel, true, K_NO_WAIT);
        }
        if (horizontal == 0 && result.candidate_wheel) {
            vertical = input_report_rel(data->dev, INPUT_REL_WHEEL,
                                         result.candidate_wheel, true, K_NO_WAIT);
        }
        if (result.candidate_wheel || result.candidate_hwheel) {
            data->wheel_events++;
            LOG_INF("AXS_GESTURE wheel=%d pan=%d queue_results=%d,%d",
                    result.candidate_wheel, result.candidate_hwheel, vertical, horizontal);
        }
        if (horizontal != 0 || vertical != 0) {
            data->input_errors++;
            axs_experimental_abort(data);
        }
    }
}
#endif

static void axs_raw_event(void *context, enum xiao147_raw_event event, int detail)
{
    struct axs_touch_data *data = context;
    LOG_INF("AXS_RAW_EVENT %d detail=%d", event, detail);
    if (event == XIAO147_RAW_CHANNELS) {
        LOG_INF("AXS_RAW_LAYOUT bytes=%02x,%02x,%02x n=%d",
                data->capture.channel_layout[0], data->capture.channel_layout[1],
                data->capture.channel_layout[2], detail);
#ifdef CONFIG_LISM_XIAO147_GESTURE_SHADOW
        if (data->shadow_active && (data->capture.channels != 12 ||
            data->capture.channel_layout[0] != 6 || data->capture.channel_layout[1] != 2 ||
            data->capture.channel_layout[2] != 0)) {
            data->shadow_abort_pending = true;
            LOG_ERR("AXS_SHADOW wrong layout; cancel without inferred mouse input");
        }
#endif
    } else if (event == XIAO147_RAW_FAULT) {
        LOG_ERR("AXS_RAW_FAULT: touch/air-mouse isolated; disconnect ALL power to recover");
    }
#ifdef CONFIG_LISM_XIAO147_GESTURE_SHADOW
    if (data->shadow_active && (event == XIAO147_RAW_ERROR ||
        event == XIAO147_RAW_RESTORING || event == XIAO147_RAW_FAULT)) {
        xiao147_shadow_gesture_cancel(&data->shadow);
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
        data->experimental_hid_active = false;
#endif
    }
#endif
}

static void axs_raw_sample(void *context, enum xiao147_raw_type type, uint8_t channels,
                           const int32_t *values, uint32_t elapsed_ms)
{
    struct axs_touch_data *data = context;
#ifndef CONFIG_LISM_XIAO147_GESTURE_SHADOW
    (void)data;
#endif
    char line[156];
    size_t used = 0;
    for (uint8_t i = 0; i < channels; i++) {
        int written = snprintf(line + used, sizeof(line) - used, "%s%ld",
                               i ? "," : "", (long)values[i]);
        if (written < 0 || (size_t)written >= sizeof(line) - used) { return; }
        used += (size_t)written;
    }
    LOG_INF("AXS_SENSOR t=%u type=%u n=%u values=%s", elapsed_ms, type, channels, line);
#ifdef CONFIG_LISM_XIAO147_GESTURE_SHADOW
    if (data->shadow_active) {
        uint32_t now = k_uptime_get_32();
        bool valid = type == XIAO147_RAW_DIFF && channels == 12;
        bool armed = xiao147_raw_baseline_observe(&data->baseline, values, channels, valid, now);
        struct xiao147_raw_profile profile = {.hypothesis = XIAO147_RAW_UNKNOWN};
        valid = valid && armed && xiao147_raw_profile_evaluate(values, channels,
                                                              data->baseline.values, &profile);
        if (armed && !data->shadow_armed_logged) {
            data->shadow_armed_logged = true;
            LOG_INF("AXS_SHADOW_ARMED quiet baseline verified; explicit mode controls experimental HID");
        }
        enum xiao147_orientation pose = xiao147_rotation_get();
        struct xiao147_xy p = xiao147_touch_to_logical(pose, profile.native_short_x,
                                                      profile.native_long_y);
        struct xiao147_shadow_result result = xiao147_shadow_gesture_observe_profile(&data->shadow,
            &profile, valid, p.x, p.y, pose, now);
        bool hid_enabled = false;
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
        hid_enabled = data->experimental_hid_active;
#endif
        data->shadow_histogram[profile.hypothesis]++;
        if (result.candidate_right_tap) { data->shadow_taps++; }
        if (result.candidate_wheel || result.candidate_hwheel) { data->shadow_scrolls++; }
        if (profile.hypothesis != data->shadow_last_hypothesis || result.candidate_right_tap ||
            result.candidate_wheel || result.candidate_hwheel ||
            now - data->shadow_last_log_ms >= 1000) {
            LOG_INF("AXS_SHADOW t=%u armed=%d shape=%u xy=%d,%d peaks=%u valley=%u ratio=%u tap=%d wheel=%d pan=%d cancel=%d HID=%d",
                    elapsed_ms, armed, profile.hypothesis, p.x, p.y, profile.significant_peaks,
                    profile.valley_permille, profile.lesser_peak_permille,
                    result.candidate_right_tap, result.candidate_wheel,
                    result.candidate_hwheel, result.cancelled, hid_enabled);
            data->shadow_last_hypothesis = profile.hypothesis;
            data->shadow_last_log_ms = now;
        }
        if (!data->baseline.normal_lift_confirmed || type != XIAO147_RAW_DIFF || channels != 12) {
            data->shadow_abort_pending = true;
        }
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
        axs_experimental_emit(data, result);
#endif
        /* SHADOW_START never emits. No one-lobe pointer/left click is inferred.
         * Separate GESTURE_START is an unvalidated, bounded live experiment,
         * not normal firmware and not automatically enabled at startup. */
    }
#endif
}

static void axs_raw_command(struct axs_touch_data *data, enum xiao147_raw_command command)
{
    bool active = xiao147_raw_capture_active(&data->capture);
#ifdef CONFIG_LISM_XIAO147_GESTURE_SHADOW
    bool profile_request = command == XIAO147_RAW_COMMAND_SHADOW_START;
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
    profile_request = profile_request || command == XIAO147_RAW_COMMAND_GESTURE_START;
#endif
#endif
    if (command == XIAO147_RAW_COMMAND_STOP) {
        data->info_stage = 0;
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
        data->experimental_hid_active = false;
        xiao147_shadow_gesture_cancel(&data->shadow);
#endif
        xiao147_raw_capture_stop(&data->capture, k_uptime_get_32());
        LOG_INF("AXS_RAW_STOP requested");
#ifdef CONFIG_LISM_XIAO147_GESTURE_SHADOW
    } else if (profile_request && !active && !data->info_stage &&
               data->info_valid && data->info_fw == data->control.expected_fw &&
               data->normal_zero && k_uptime_get_32() - data->normal_zero_since_ms >= 100 &&
               k_uptime_get_32() - data->normal_frame_ms <= 100 &&
               xiao147_ui_page() == XIAO147_PAGE_STATUS && !xiao147_ui_imu_enabled() &&
               !xiao147_ui_is_sleeping()) {
        cancel_contact(data);
        data->swallow_until_release = true;
        data->info_valid = false;
        xiao147_raw_baseline_begin(&data->baseline, true);
        xiao147_shadow_gesture_init(&data->shadow);
        memset(data->shadow_histogram, 0, sizeof(data->shadow_histogram));
        data->shadow_taps = data->shadow_scrolls = 0;
        data->shadow_active = true;
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
        data->experimental_hid_active = command == XIAO147_RAW_COMMAND_GESTURE_START;
#endif
        data->shadow_abort_pending = data->shadow_armed_logged = false;
        data->shadow_last_hypothesis = XIAO147_RAW_UNKNOWN;
        data->shadow_last_log_ms = data->shadow_last_rate_ms = k_uptime_get_32();
        if (xiao147_raw_capture_start_mode(&data->capture, k_uptime_get_32(),
                                         data->control.expected_fw, XIAO147_RAW_MODE_FAST_DIFF)) {
            atomic_set(&capture_gate, 1);
            bool hid = false;
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
            hid = data->experimental_hid_active;
#endif
            LOG_INF("%s_START expected_fw=%04x; FAST DIFF target20ms; HID=%d; ordinary pointer/left click paused",
                    hid ? "AXS_GESTURE" : "AXS_SHADOW", data->control.expected_fw, hid);
        } else {
            data->shadow_active = false;
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
            data->experimental_hid_active = false;
#endif
        }
#endif
    } else if (command == XIAO147_RAW_COMMAND_INFO && !active && !data->info_stage) {
        cancel_contact(data);
        data->swallow_until_release = true;
        data->info_valid = false;
        data->info_stage = 1;
    } else if (command == XIAO147_RAW_COMMAND_START && !active && !data->info_stage &&
               data->info_valid && data->info_fw == data->control.expected_fw &&
               !xiao147_ui_is_sleeping()) {
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
    int dtr_result = device_is_ready(capture_console) ?
        uart_line_ctrl_get(capture_console, UART_LINE_CTRL_DTR, &dtr) : -ENODEV;
    bool connected = dtr_result == 0 && dtr != 0;
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
    bool was_button_down = data->experimental_button_down;
    axs_experimental_release(data);
#endif
    bool discard_commands = axs_experimental_discard_commands(data,
        dtr_result == 0 && dtr == 0, connected);
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
    if (data->experimental_button_down && !was_active) {
        atomic_set(&capture_gate, 1);
        return true;
    }
    if (was_button_down && !data->experimental_button_down && !was_active) {
        LOG_INF("AXS_RAW_INPUT restored; right-button release accepted, waiting for real touch release");
    }
#endif
    if (!connected) {
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
        data->experimental_hid_active = false;
        xiao147_shadow_gesture_cancel(&data->shadow);
#endif
        data->control = (struct xiao147_raw_control){0};
        data->info_stage = 0;
        data->info_valid = false;
        if (was_active) { xiao147_raw_capture_stop(&data->capture, k_uptime_get_32()); }
    } else if (!discard_commands) {
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
        /* Factory samples do not pass through normal wake-on-touch. Restore
         * normal reports on sleep, rather than emitting invisible gestures or
         * leaving the screen unable to wake by touch. Never auto-rearm. */
        if (xiao147_ui_is_sleeping()) {
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
            data->experimental_hid_active = false;
#endif
#ifdef CONFIG_LISM_XIAO147_GESTURE_SHADOW
            xiao147_shadow_gesture_cancel(&data->shadow);
#endif
            data->info_valid = false;
            xiao147_raw_capture_stop(&data->capture, k_uptime_get_32());
        }
#ifdef CONFIG_LISM_XIAO147_GESTURE_SHADOW
        if (data->shadow_active && (xiao147_ui_page() != XIAO147_PAGE_STATUS ||
            xiao147_ui_imu_enabled() || data->shadow_abort_pending)) {
            xiao147_shadow_gesture_cancel(&data->shadow);
            xiao147_raw_capture_stop(&data->capture, k_uptime_get_32());
        }
#endif
        /* Sole 0x63 owner: no ordinary touch reads while on the factory page,
         * including cleanup and sticky recovery faults. */
        xiao147_raw_capture_tick(&data->capture, k_uptime_get_32());
#ifdef CONFIG_LISM_XIAO147_GESTURE_SHADOW
        if (data->shadow_abort_pending) {
            xiao147_raw_capture_stop(&data->capture, k_uptime_get_32());
        }
        if (data->shadow_active && k_uptime_get_32() - data->shadow_last_rate_ms >= 2000) {
            bool hid = false;
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
            hid = data->experimental_hid_active;
#endif
            LOG_INF("AXS_SHADOW_RATE samples=%u scans=%u interval=%u min=%u max=%u shapes=%u,%u,%u,%u,%u taps=%u scrolls=%u HID=%d",
                    data->capture.samples_received, data->capture.scan_requests,
                    data->capture.last_interval_ms, data->capture.min_interval_ms,
                    data->capture.max_interval_ms, data->shadow_histogram[0],
                    data->shadow_histogram[1], data->shadow_histogram[2], data->shadow_histogram[3],
                    data->shadow_histogram[4],
                    data->shadow_taps, data->shadow_scrolls, hid);
            data->shadow_last_rate_ms = k_uptime_get_32();
        }
#endif
        bool active = xiao147_raw_capture_active(&data->capture);
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
        atomic_set(&capture_gate, active || data->experimental_button_down);
#else
        atomic_set(&capture_gate, active);
#endif
        if (!active) {
            data->swallow_until_release = true;
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
            if (data->experimental_button_down) {
                LOG_WRN("AXS_RAW normal reports restored; input isolated until right-button release accepted");
            } else
#endif
            LOG_INF("AXS_RAW_INPUT restored; waiting for a real touch release");
#ifdef CONFIG_LISM_XIAO147_GESTURE_SHADOW
            data->shadow_active = false;
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
            data->experimental_hid_active = false;
#endif
#endif
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
                bool hid_supported = false;
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
                hid_supported = true;
#endif
                LOG_INF("AXS_RAW_INFO ID=%02x%02x%02x FW=%04x valid=%d HOST=4 EXPERIMENTAL_HID=%d", data->info_id[0],
                        data->info_id[1], data->info_id[2], data->info_fw, data->info_valid, hid_supported);
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
    if (data->pending_button != 0) {
        /* Retry once per work tick before bus access, page logic or RAW entry.
         * A held finger must lift after recovery before starting a new gesture. */
        release_button(data);
        cancel_contact(data);
        data->swallow_until_release = true;
#ifdef CONFIG_LISM_XIAO147_RAW_CAPTURE
        data->normal_zero = false;
        data->info_valid = false;
        data->info_stage = 0;
        data->control = (struct xiao147_raw_control){0};
        uint32_t dtr = 0;
        int status = device_is_ready(capture_console) ?
            uart_line_ctrl_get(capture_console, UART_LINE_CTRL_DTR, &dtr) : -ENODEV;
        axs_experimental_discard_commands(data, status == 0 && dtr == 0,
                                          status == 0 && dtr != 0);
#endif
        goto next;
    }
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
#ifdef CONFIG_LISM_XIAO147_RAW_CAPTURE
        data->normal_zero = false;
#endif
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
#ifdef CONFIG_LISM_XIAO147_RAW_CAPTURE
        data->normal_zero = false;
#endif
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
    /* Missing time is not evidence of a stationary finger. A delayed poll
     * cannot safely bridge an old contact to a new one (or a release/tap). */
    uint32_t frame_ms = k_uptime_get_32();
    if (data->fingers != 0 && frame_ms - data->last_valid_frame_ms > TOUCH_MAX_GAP_MS) {
        cancel_contact(data);
    }
    data->last_valid_frame_ms = frame_ms;
#ifdef CONFIG_LISM_XIAO147_RAW_CAPTURE
    data->normal_frame_ms = k_uptime_get_32();
    if (fingers == 0) {
        if (!data->normal_zero) { data->normal_zero_since_ms = data->normal_frame_ms; }
        data->normal_zero = true;
    } else { data->normal_zero = false; }
#endif
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
        /* Also cancel a queued settings drag in its original orientation. */
        cancel_contact(data);
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
        bool accepted = true;
        if (fingers == 2) { accepted = report_scroll(data, dx, dy); }
        else if (data->max_fingers < 2) { accepted = report_pointer(data, dx, dy); }
        if (!accepted) { cancel_contact(data); goto next; }
    } else {
        /* A discontinuity is not a stationary tap or a new movement anchor. */
        cancel_contact(data);
        goto next;
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
    uint32_t poll_ms = TOUCH_POLL_MS;
#ifdef CONFIG_LISM_XIAO147_GESTURE_SHADOW
    if (data->shadow_active && xiao147_raw_capture_active(&data->capture) &&
        data->capture.mode == XIAO147_RAW_MODE_FAST_DIFF &&
        data->capture.phase < XIAO147_RAW_RESTORE_WRITE) { poll_ms = 4; }
#endif
    k_work_reschedule(&data->poll_work, K_MSEC(poll_ms));
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
    LOG_INF("AXS_RAW_READY v4: INFO then START <FWhex>, STOP; 60s limit, failure requires power cycle");
#ifdef CONFIG_LISM_XIAO147_GESTURE_SHADOW
    LOG_INF("AXS_SHADOW_READY v3: SHADOW_START <FWhex>; net12 axis lock/lobe tap guards; HID=0");
#ifdef CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST
    LOG_WRN("AXS_GESTURE_READY experimental: only GESTURE_START <FWhex> enables <=60s right/wheel/pan; blank test page only, possible misclicks; default OFF");
#endif
#endif
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
