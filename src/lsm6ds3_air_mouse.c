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
#include "xiao147_rotation.h"
#include "xiao147_air_motion.h"

#ifdef CONFIG_LISM_XIAO147_RAW_CAPTURE
/* Only the explicit diagnostic may isolate this device's mouse reports. */
extern bool xiao147_touch_capture_active(void);
#endif

LOG_MODULE_REGISTER(lism_air_mouse, LOG_LEVEL_INF);

#define IMU_WHO_AM_I 0x0f
#define IMU_CTRL2_G 0x11
#define IMU_CTRL1_XL 0x10
#define IMU_CTRL3_C 0x12
#define IMU_OUTX_L_G 0x22
#define IMU_OUTX_L_XL 0x28
#define IMU_SAMPLE_MS 20
#define ORIENTATION_STABLE_MS 600

struct imu_config { struct i2c_dt_spec bus; };
struct imu_data {
    const struct device *dev;
    struct k_work_delayable poll_work;
    bool ready;
    bool gyro_on;
    bool auto_rotate_active;
    enum xiao147_orientation orientation_candidate;
    uint32_t orientation_since_ms;
    struct xiao147_air_motion motion;
    enum xiao147_orientation last_pose;
    uint32_t last_gyro_trace;
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
    bool enabled = xiao147_ui_imu_enabled();
#ifdef CONFIG_LISM_XIAO147_RAW_CAPTURE
    enabled = enabled && !xiao147_touch_capture_active();
#endif
    if (data->ready && enabled != data->gyro_on) {
        if (imu_write(&cfg->bus, IMU_CTRL2_G, enabled ? 0x40 : 0x00) == 0) {
            data->gyro_on = enabled;
            if (enabled) { xiao147_air_begin(&data->motion, k_uptime_get_32()); }
            else { xiao147_air_clear(&data->motion); }
        }
    }
    if (data->ready && xiao147_ui_auto_rotate_enabled()) {
        uint8_t reg = IMU_OUTX_L_XL;
        uint8_t axes[6];
        if (i2c_write_read_dt(&cfg->bus, &reg, 1, axes, sizeof(axes)) == 0) {
            int16_t ax = (int16_t)((uint16_t)axes[1] << 8 | axes[0]);
            int16_t ay = (int16_t)((uint16_t)axes[3] << 8 | axes[2]);
            enum xiao147_orientation candidate;
            if (xiao147_gravity_orientation(ax, ay, &candidate)) {
                if (!data->auto_rotate_active || candidate != data->orientation_candidate) {
                    data->auto_rotate_active = true;
                    data->orientation_candidate = candidate;
                    data->orientation_since_ms = k_uptime_get_32();
                    LOG_INF("Orientation candidate %d, gravity %d %d", candidate, ax, ay);
                } else if (k_uptime_get_32() - data->orientation_since_ms >=
                           ORIENTATION_STABLE_MS) {
                    xiao147_ui_set_auto_orientation(candidate);
                }
            } else {
                data->auto_rotate_active = false;
            }
        } else {
            data->auto_rotate_active = false;
        }
    } else {
        data->auto_rotate_active = false;
    }
    if (data->ready && data->gyro_on && enabled) {
        uint8_t reg = IMU_OUTX_L_G;
        uint8_t axes[6];
        if (i2c_write_read_dt(&cfg->bus, &reg, 1, axes, sizeof(axes)) == 0) {
            int16_t raw[3];
            for (int i = 0; i < 3; i++) { raw[i] = (int16_t)((uint16_t)axes[i*2+1] << 8 | axes[i*2]); }
            enum xiao147_orientation pose = xiao147_rotation_get();
            if (pose != data->last_pose) { xiao147_air_clear(&data->motion); data->last_pose = pose; }
            struct xiao147_xy motion = xiao147_air_sample(&data->motion, k_uptime_get_32(), pose, raw);
            int dx = motion.x, dy = motion.y;
            if (k_uptime_get_32() - data->last_gyro_trace >= 2000) {
                LOG_INF("IMU raw %d %d %d, bias %ld %ld, valid %d calibrating %d, mouse %d %d",
                        raw[0], raw[1], raw[2], (long)data->motion.bias[0],
                        (long)data->motion.bias[1], data->motion.bias_valid,
                        data->motion.calibrating, dx, dy);
                data->last_gyro_trace = k_uptime_get_32();
            }
            if (dx || dy) {
                bool woke = xiao147_ui_wake_on_motion();
                if (!woke && xiao147_ui_page() == XIAO147_PAGE_STATUS) {
                    if (dx) { input_report_rel(data->dev, INPUT_REL_X, dx, dy == 0, K_NO_WAIT); }
                    if (dy) { input_report_rel(data->dev, INPUT_REL_Y, dy, true, K_NO_WAIT); }
                }
                else { xiao147_air_clear(&data->motion); }
            }
        } else {
            xiao147_air_clear(&data->motion);
            data->motion.samples = 0;
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
    /* Board U2 is LSM6DS3TR-C, not the original LSM6DS3: ST's driver
     * lsm6ds3tr-c-pid 6735cd1 defines ID 0x6a with the same registers used here.
     */
    if (i2c_write_read_dt(&cfg->bus, &reg, 1, &who, 1) != 0 ||
        (who != 0x69 && who != 0x6a)) {
        LOG_ERR("LSM6DS3 not detected (WHO_AM_I=0x%02x)", who);
        return -ENODEV;
    }
    if (imu_write(&cfg->bus, IMU_CTRL3_C, 0x44) != 0 ||
        imu_write(&cfg->bus, IMU_CTRL1_XL, 0x40) != 0 ||
        imu_write(&cfg->bus, IMU_CTRL2_G, 0x00) != 0) {
        return -EIO;
    }
    data->dev = dev;
    data->ready = true;
    LOG_INF("IMU ready, WHO_AM_I=0x%02x", who);
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
