/* Low-speed air mouse for the Seeed XIAO 1.47-inch board's LSM6DS3. */
#define DT_DRV_COMPAT lism_lsm6ds3_air_mouse

#include <stdint.h>

#include <zephyr/device.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/input/input.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/dt-bindings/input/input-event-codes.h>

#include "xiao147_ui.h"

LOG_MODULE_REGISTER(lism_air_mouse, LOG_LEVEL_INF);

#define IMU_WHO_AM_I 0x0f
#define IMU_CTRL2_G 0x11
#define IMU_CTRL3_C 0x12
#define IMU_OUTX_L_G 0x22
#define IMU_SAMPLE_MS 20
/* ±245 dps output is 114 LSB/dps. Keep the air mouse intentionally slow. */
#define IMU_DEADBAND 250
#define IMU_COUNTS_PER_CURSOR_STEP 650

struct imu_config { struct i2c_dt_spec bus; };
struct imu_data {
    const struct device *dev;
    struct k_work_delayable poll_work;
    bool ready;
};

static int imu_write(const struct i2c_dt_spec *bus, uint8_t reg, uint8_t value)
{
    uint8_t data[2] = {reg, value};
    return i2c_write_dt(bus, data, sizeof(data));
}

static void imu_poll(struct k_work *work)
{
    struct imu_data *data = CONTAINER_OF(k_work_delayable_from_work(work),
                                         struct imu_data, poll_work);
    const struct imu_config *cfg = data->dev->config;
    if (data->ready && xiao147_ui_imu_enabled()) {
        uint8_t reg = IMU_OUTX_L_G;
        uint8_t axes[6];
        if (i2c_write_read_dt(&cfg->bus, &reg, 1, axes, sizeof(axes)) == 0) {
            int16_t gx = (int16_t)((uint16_t)axes[1] << 8 | axes[0]);
            int16_t gy = (int16_t)((uint16_t)axes[3] << 8 | axes[2]);
            int dx = gy > IMU_DEADBAND || gy < -IMU_DEADBAND ?
                     gy / IMU_COUNTS_PER_CURSOR_STEP : 0;
            int dy = gx > IMU_DEADBAND || gx < -IMU_DEADBAND ?
                     gx / IMU_COUNTS_PER_CURSOR_STEP : 0;
            if (dx > 5) { dx = 5; } else if (dx < -5) { dx = -5; }
            if (dy > 5) { dy = 5; } else if (dy < -5) { dy = -5; }
            if (dx) { input_report_rel(data->dev, INPUT_REL_X, dx, dy == 0, K_NO_WAIT); }
            if (dy) { input_report_rel(data->dev, INPUT_REL_Y, dy, true, K_NO_WAIT); }
        }
    }
    k_work_reschedule(&data->poll_work, K_MSEC(IMU_SAMPLE_MS));
}

static int imu_init(const struct device *dev)
{
    const struct imu_config *cfg = dev->config;
    struct imu_data *data = dev->data;
    if (!i2c_is_ready_dt(&cfg->bus)) { return -ENODEV; }
    uint8_t reg = IMU_WHO_AM_I;
    uint8_t who = 0;
    if (i2c_write_read_dt(&cfg->bus, &reg, 1, &who, 1) != 0 || who != 0x69) {
        LOG_ERR("LSM6DS3 not detected (WHO_AM_I=0x%02x)", who);
        return -ENODEV;
    }
    if (imu_write(&cfg->bus, IMU_CTRL3_C, 0x44) != 0 ||
        imu_write(&cfg->bus, IMU_CTRL2_G, 0x40) != 0) {
        return -EIO;
    }
    data->dev = dev;
    data->ready = true;
    k_work_init_delayable(&data->poll_work, imu_poll);
    k_work_schedule(&data->poll_work, K_MSEC(IMU_SAMPLE_MS));
    return 0;
}

#define IMU_DEFINE(inst) \
    static const struct imu_config imu_cfg_##inst = {.bus = I2C_DT_SPEC_INST_GET(inst)}; \
    static struct imu_data imu_data_##inst; \
    DEVICE_DT_INST_DEFINE(inst, imu_init, NULL, &imu_data_##inst, &imu_cfg_##inst, \
                          POST_KERNEL, CONFIG_INPUT_INIT_PRIORITY, NULL);

DT_INST_FOREACH_STATUS_OKAY(IMU_DEFINE)
