/* AXS5106L report layout follows Seeed's xiao_nrf52840_147_touch example:
 * count at byte 1, two six-byte contact records beginning at bytes 2 and 8.
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

LOG_MODULE_REGISTER(lism_axs5106l, CONFIG_INPUT_LOG_LEVEL);

#define AXS_TOUCH_REPORT_REG 0x01
#define AXS_TOUCH_REPORT_LEN 14
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
    struct point last;
    struct point start;
    uint32_t touch_start_ms;
    int16_t scroll_x_remainder;
    int16_t scroll_y_remainder;
    bool moved;
    bool swallow_until_release;
};

static bool point_valid(const uint8_t report[AXS_TOUCH_REPORT_LEN], uint8_t index)
{
    const uint8_t off = 2 + index * 6;
    uint16_t x = ((uint16_t)(report[off] & 0x0f) << 8) | report[off + 1];
    uint16_t y = ((uint16_t)(report[off + 2] & 0x0f) << 8) | report[off + 3];
    return x < 172 && y < 320;
}

static struct point decode_point(const uint8_t report[AXS_TOUCH_REPORT_LEN], uint8_t index)
{
    const uint8_t off = 2 + index * 6;
    uint16_t raw_x = ((uint16_t)(report[off] & 0x0f) << 8) | report[off + 1];
    uint16_t raw_y = ((uint16_t)(report[off + 2] & 0x0f) << 8) | report[off + 3];
    /* Boot orientation: 320x172 landscape, USB at the left. */
    return (struct point){.x = raw_y, .y = 171 - raw_x};
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
    if (sx != 0) { input_report_rel(data->dev, INPUT_REL_HWHEEL, -sx, sy == 0, K_NO_WAIT); }
    if (sy != 0) { input_report_rel(data->dev, INPUT_REL_WHEEL, -sy, true, K_NO_WAIT); }
}

static void end_contact(struct axs_touch_data *data, bool allow_tap)
{
    if (allow_tap && data->fingers != 0 && !data->moved &&
        !data->swallow_until_release &&
        k_uptime_get_32() - data->touch_start_ms <= TAP_MAX_MS) {
        report_button(data->dev, data->max_fingers >= 2 ? INPUT_BTN_1 : INPUT_BTN_0);
    }
    data->fingers = 0;
    data->max_fingers = 0;
    data->moved = false;
    data->scroll_x_remainder = 0;
    data->scroll_y_remainder = 0;
    data->swallow_until_release = false;
}

static void axs_poll(struct k_work *work)
{
    struct k_work_delayable *dwork = k_work_delayable_from_work(work);
    struct axs_touch_data *data = CONTAINER_OF(dwork, struct axs_touch_data, poll_work);
    const struct axs_touch_config *cfg = data->dev->config;
    uint8_t reg = AXS_TOUCH_REPORT_REG;
    uint8_t report[AXS_TOUCH_REPORT_LEN];
    int ret = i2c_write_read_dt(&cfg->bus, &reg, sizeof(reg), report, sizeof(report));
    if (ret < 0) {
        end_contact(data, false);
        goto next;
    }

    uint8_t fingers = report[1];
    if (fingers > 2 || (fingers > 0 && !point_valid(report, 0)) ||
        (fingers == 2 && !point_valid(report, 1))) {
        end_contact(data, false);
        goto next;
    }

    enum xiao147_page page = xiao147_ui_page();
    if (page != data->last_page) {
        end_contact(data, false);
        data->last_page = page;
        data->swallow_until_release = fingers != 0;
    }

    if (fingers == 0) {
        if (page != XIAO147_PAGE_STATUS) { xiao147_ui_touch(0, 0, 0); }
        end_contact(data, page == XIAO147_PAGE_STATUS);
        goto next;
    }

    struct point p = decode_point(report, 0);
    if (fingers == 2) {
        struct point q = decode_point(report, 1);
        p.x = (p.x + q.x) / 2;
        p.y = (p.y + q.y) / 2;
    }

    if (xiao147_ui_wake_on_touch()) { data->swallow_until_release = true; }
    if (data->swallow_until_release) { goto next; }
    if (page != XIAO147_PAGE_STATUS) {
        xiao147_ui_touch(fingers, p.x, p.y);
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
        else { report_pointer(data->dev, dx, dy); }
    }
    data->last = p;

next:
    k_work_reschedule(&data->poll_work, K_MSEC(TOUCH_POLL_MS));
}

static int axs_touch_init(const struct device *dev)
{
    const struct axs_touch_config *cfg = dev->config;
    struct axs_touch_data *data = dev->data;
    if (!i2c_is_ready_dt(&cfg->bus)) { return -ENODEV; }
    data->dev = dev;
    data->last_page = XIAO147_PAGE_STATUS;
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
