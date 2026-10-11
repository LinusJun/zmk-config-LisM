/*
 * Minimal AXS5106L touch-to-relative-pointer input driver for the Seeed 1.47" dongle.
 * Seeed's reference report: register 0x01, 14 bytes, count in byte 1, first
 * point X/Y in bytes 2-5 (12-bit values). No tap-to-click: USR1/USR2 are the
 * dedicated left/right mouse buttons. A new contact establishes a baseline.
 */
#define DT_DRV_COMPAT lism_axs5106l_touchpad

#include <stdbool.h>
#include <stdint.h>

#include <zephyr/device.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/input/input.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/dt-bindings/input/input-event-codes.h>

LOG_MODULE_REGISTER(lism_axs5106l, CONFIG_INPUT_LOG_LEVEL);

#define AXS_TOUCH_REPORT_REG 0x01
#define AXS_TOUCH_REPORT_LEN 14
#define TOUCH_POLL_MS 16
#define TOUCH_JUMP_LIMIT 80

struct axs_touch_config {
    struct i2c_dt_spec bus;
};

struct axs_touch_data {
    struct k_work_delayable poll_work;
    const struct device *dev;
    bool touching;
    uint16_t last_x;
    uint16_t last_y;
};

static void axs_poll(struct k_work *work)
{
    struct k_work_delayable *dwork = k_work_delayable_from_work(work);
    struct axs_touch_data *data = CONTAINER_OF(dwork, struct axs_touch_data, poll_work);
    const struct axs_touch_config *cfg = data->dev->config;
    uint8_t reg = AXS_TOUCH_REPORT_REG;
    uint8_t report[AXS_TOUCH_REPORT_LEN];
    int ret = i2c_write_read_dt(&cfg->bus, &reg, sizeof(reg), report, sizeof(report));

    if (ret < 0 || report[1] == 0 || report[1] > 2) {
        data->touching = false;
        if (ret < 0) {
            LOG_DBG("AXS5106L read failed: %d", ret);
        }
        goto next;
    }

    uint16_t x = ((uint16_t)(report[2] & 0x0f) << 8) | report[3];
    uint16_t y = ((uint16_t)(report[4] & 0x0f) << 8) | report[5];

    /* The panel is 172x320 in portrait. Landscape rotates the touch axes. */
    if (x >= 172 || y >= 320) {
        data->touching = false;
        goto next;
    }

    if (data->touching) {
        int dx = (int)y - (int)data->last_y;
        int dy = (int)data->last_x - (int)x;

        /* Ignore a sudden coordinate jump from a changed finger or bad frame. */
        if (dx >= -TOUCH_JUMP_LIMIT && dx <= TOUCH_JUMP_LIMIT &&
            dy >= -TOUCH_JUMP_LIMIT && dy <= TOUCH_JUMP_LIMIT) {
            if (dx != 0) {
                input_report_rel(data->dev, INPUT_REL_X, dx, dy == 0, K_NO_WAIT);
            }
            if (dy != 0) {
                input_report_rel(data->dev, INPUT_REL_Y, dy, true, K_NO_WAIT);
            }
        }
    }

    data->last_x = x;
    data->last_y = y;
    data->touching = true;

next:
    k_work_reschedule(&data->poll_work, K_MSEC(TOUCH_POLL_MS));
}

static int axs_touch_init(const struct device *dev)
{
    const struct axs_touch_config *cfg = dev->config;
    struct axs_touch_data *data = dev->data;

    if (!i2c_is_ready_dt(&cfg->bus)) {
        return -ENODEV;
    }

    data->dev = dev;
    k_work_init_delayable(&data->poll_work, axs_poll);
    /* The LCD and touch controller share reset; wait for display start-up. */
    k_work_schedule(&data->poll_work, K_MSEC(1000));
    return 0;
}

#define AXS_DEFINE(inst) \
    static const struct axs_touch_config axs_cfg_##inst = { \
        .bus = I2C_DT_SPEC_INST_GET(inst), \
    }; \
    static struct axs_touch_data axs_data_##inst; \
    DEVICE_DT_INST_DEFINE(inst, axs_touch_init, NULL, &axs_data_##inst, \
                          &axs_cfg_##inst, POST_KERNEL, CONFIG_INPUT_INIT_PRIORITY, NULL);

DT_INST_FOREACH_STATUS_OKAY(AXS_DEFINE)
