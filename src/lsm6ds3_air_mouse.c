/* Gravity-referenced three-axis air mouse for the XIAO 1.47-inch LSM6DS3. */
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

#if defined(CONFIG_LISM_XIAO147_RAW_CAPTURE) || defined(CONFIG_LISM_XIAO147_AUTO_TOUCH)
extern bool xiao147_touch_capture_active(void);
#endif
#ifdef CONFIG_LISM_XIAO147_AUTO_TOUCH
extern bool xiao147_touch_blocks_air(void);
extern bool xiao147_touch_allows_air_wake(void);
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
    uint32_t input_errors;
    uint32_t accel_errors, gyro_errors, control_errors;
    bool output_allowed;
    int endpoint;
    uint32_t wake_since, wake_last;
    bool wake_tracking;
};

static int imu_write(const struct i2c_dt_spec *bus, uint8_t reg, uint8_t value)
{
    uint8_t data[2] = {reg, value};
    return i2c_write_dt(bus, data, sizeof(data));
}

/* Separate output ownership from calibration of native sensor readings. */
static unsigned imu_output_blockers(int endpoint)
{
    unsigned blocked = 0;
    if (endpoint < 0 || xiao147_pointer_endpoint() != endpoint) { blocked |= 1; }
    if (!xiao147_ui_imu_enabled()) { blocked |= 2; }
    if (xiao147_touch_button_pending()) { blocked |= 4; }
    if (xiao147_ui_is_sleeping()) { blocked |= 8; }
    if (xiao147_ui_page() != XIAO147_PAGE_STATUS) { blocked |= 16; }
#ifdef CONFIG_LISM_XIAO147_AUTO_TOUCH
    if (xiao147_touch_blocks_air()) { blocked |= 32; }
#elif defined(CONFIG_LISM_XIAO147_RAW_CAPTURE)
    if (xiao147_touch_capture_active()) { blocked |= 32; }
#endif
    return blocked;
}

static bool imu_output_permitted(int endpoint)
{
    return imu_output_blockers(endpoint) == 0;
}

/* Waking is not pointer ownership. In sleep the AUTO owner restores native
 * reports and deliberately revokes its RAW idle lease. Require a fresh native
 * no-contact observation instead; after wake RAW must independently re-arm. */
static bool imu_wake_motion(struct imu_data *data, int endpoint,
                            enum xiao147_orientation pose,
                            const int16_t raw[3], uint32_t now)
{
    bool permit = endpoint >= 0 && xiao147_pointer_endpoint() == endpoint &&
        xiao147_ui_imu_enabled() && xiao147_ui_is_sleeping() &&
        xiao147_ui_page() == XIAO147_PAGE_STATUS && !xiao147_touch_button_pending();
#ifdef CONFIG_LISM_XIAO147_AUTO_TOUCH
    permit = permit && xiao147_touch_allows_air_wake();
#elif defined(CONFIG_LISM_XIAO147_RAW_CAPTURE)
    permit = permit && !xiao147_touch_capture_active();
#endif
    struct xiao147_air_motion *s = &data->motion;
    struct xiao147_xy rates;
    bool projected = xiao147_air_project(s, now, pose, raw, &rates);
    bool moving = projected && (abs(rates.x) > 80 || abs(rates.y) > 80);
    if (!permit || !s->bias_valid || s->calibrating || !s->have_gravity ||
        now - s->gravity_ms > 40 || !moving) {
        data->wake_tracking = false;
        return false;
    }
    if (!data->wake_tracking || now == data->wake_last || now - data->wake_last > 40) {
        data->wake_tracking = true;
        data->wake_since = now;
    }
    data->wake_last = now;
    if (now - data->wake_since < 60) { return false; }
    data->wake_tracking = false;
    xiao147_air_clear(s);
    return xiao147_ui_wake_on_motion();
}

static void imu_poll(struct k_work *work)
{
    struct imu_data *data = CONTAINER_OF(k_work_delayable_from_work(work),
                                         struct imu_data, poll_work);
    const struct imu_config *cfg = data->dev->config;
    /* Touch ownership gates output, not sensor power or calibration. */
    bool enabled = xiao147_ui_imu_enabled();
    if (!enabled || !xiao147_ui_is_sleeping()) { data->wake_tracking = false; }
#if defined(CONFIG_LISM_XIAO147_RAW_CAPTURE) && !defined(CONFIG_LISM_XIAO147_AUTO_TOUCH)
    enabled = enabled && !xiao147_touch_capture_active();
#endif
    int endpoint = xiao147_pointer_endpoint();
    bool output_allowed = enabled && imu_output_permitted(endpoint);
    if (data->output_allowed != output_allowed || data->endpoint != endpoint) {
        /* A touch/page/endpoint transition must discard queued displacement,
         * not an otherwise continuous stationary SENSOR calibration window. */
        xiao147_air_clear(&data->motion);
        if (data->endpoint != endpoint) { data->wake_tracking = false; }
        data->output_allowed = output_allowed;
        data->endpoint = endpoint;
    }
    if (data->ready && enabled != data->gyro_on) {
        if (imu_write(&cfg->bus, IMU_CTRL2_G, enabled ? 0x40 : 0x00) == 0) {
            data->gyro_on = enabled;
            if (enabled) { xiao147_air_begin(&data->motion, k_uptime_get_32()); }
            else { xiao147_air_clear(&data->motion); }
        } else { data->control_errors++; }
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
        uint8_t accel_reg = IMU_OUTX_L_XL;
        uint8_t accel_bytes[6];
        if (i2c_write_read_dt(&cfg->bus, &accel_reg, 1, accel_bytes, sizeof(accel_bytes)) == 0) {
            int16_t accel[3];
            for (int i = 0; i < 3; ++i) { accel[i] = (int16_t)((uint16_t)accel_bytes[2*i+1] << 8 | accel_bytes[2*i]); }
            xiao147_air_gravity(&data->motion, k_uptime_get_32(), accel);
        } else {
            data->accel_errors++;
            data->motion.have_gravity = false;
            data->motion.samples = 0;
            xiao147_air_clear(&data->motion);
        }
        uint8_t reg = IMU_OUTX_L_G;
        uint8_t axes[6];
        if (i2c_write_read_dt(&cfg->bus, &reg, 1, axes, sizeof(axes)) == 0) {
            int16_t raw[3];
            for (int i = 0; i < 3; i++) { raw[i] = (int16_t)((uint16_t)axes[i*2+1] << 8 | axes[i*2]); }
            enum xiao147_orientation pose = xiao147_rotation_get();
            if (pose != data->last_pose) {
                xiao147_air_clear(&data->motion);
                /* Bias is learned in native sensor axes, independently of the
                 * logical screen pose. Real movement still fails its checks. */
                data->last_pose = pose;
            }
            struct xiao147_xy motion = xiao147_air_sample(&data->motion, k_uptime_get_32(), pose, raw);
            int dx = motion.x, dy = motion.y;
            bool woke = imu_wake_motion(data, endpoint, pose, raw, k_uptime_get_32());
            if (woke) { output_allowed = false; }
            if (!output_allowed) { xiao147_air_clear(&data->motion); dx = dy = 0; }
            if (k_uptime_get_32() - data->last_gyro_trace >= 2000) {
                LOG_INF("IMU raw %d %d %d, bias %ld %ld %ld, valid %d calibrating %d, mouse %d %d",
                        raw[0], raw[1], raw[2], (long)data->motion.bias[0],
                        (long)data->motion.bias[1], (long)data->motion.bias[2], data->motion.bias_valid,
                        data->motion.calibrating, dx, dy);
                LOG_INF("IMU calibration samples %u reject=0x%x, gravity %d %d %d stable=%d; candidate %d %d gate=0x%x errors accel=%u gyro=%u control=%u queue=%u",
                        data->motion.samples, data->motion.calibration_blocked,
                        data->motion.gravity[0], data->motion.gravity[1], data->motion.gravity[2],
                        data->motion.gravity_stable, motion.x, motion.y,
                        imu_output_blockers(endpoint), data->accel_errors, data->gyro_errors,
                        data->control_errors, data->input_errors);
                struct xiao147_xy rates;
                bool projected = xiao147_air_project(&data->motion, k_uptime_get_32(), pose, raw, &rates);
                LOG_INF("IMU projection trusted=%d pose=%d rates=%d %d up=%ld %ld %ld age=%u",
                        projected, pose, rates.x, rates.y,
                        (long)data->motion.up[0], (long)data->motion.up[1],
                        (long)data->motion.up[2],
                        data->motion.up_valid ? k_uptime_get_32() - data->motion.up_at : UINT32_MAX);
                data->last_gyro_trace = k_uptime_get_32();
            }
            if (dx || dy) {
                /* Active movement refreshes idle time; sleeping motion used
                 * the separate, guarded wake path above and emits no HID. */
                woke = xiao147_ui_wake_on_motion();
                if (!woke && output_allowed && imu_output_permitted(endpoint) &&
                    xiao147_rotation_get() == pose) {
                    int ret = dx == 0 ? 0 : input_report_rel(data->dev, INPUT_REL_X, dx, true, K_NO_WAIT);
                    if (ret == 0 && dy) {
                        if (imu_output_permitted(endpoint) && xiao147_rotation_get() == pose) {
                            ret = input_report_rel(data->dev, INPUT_REL_Y, dy, true, K_NO_WAIT);
                        } else {
                            xiao147_air_clear(&data->motion);
                            data->motion.samples = 0;
                        }
                    }
                    if (ret != 0) {
                        data->input_errors++;
                        xiao147_air_clear(&data->motion);
                        data->motion.samples = 0;
                        if (data->input_errors % 100 == 1) {
                            LOG_WRN("IMU mouse queue error %d, failures %u", ret, data->input_errors);
                        }
                    }
                }
                else { xiao147_air_clear(&data->motion); data->motion.samples = 0; }
            }
        } else {
            data->gyro_errors++;
            data->wake_tracking = false;
            xiao147_air_clear(&data->motion);
            data->motion.samples = 0;
            if (k_uptime_get_32() - data->last_gyro_trace >= 2000) {
                LOG_WRN("IMU gyro read failed, errors accel=%u gyro=%u control=%u gate=0x%x",
                        data->accel_errors, data->gyro_errors, data->control_errors,
                        imu_output_blockers(endpoint));
                data->last_gyro_trace = k_uptime_get_32();
            }
        }
    }
    if (enabled && data->ready && !data->gyro_on &&
        k_uptime_get_32() - data->last_gyro_trace >= 2000) {
        LOG_WRN("IMU gyro enable failed, control errors %u", data->control_errors);
        data->last_gyro_trace = k_uptime_get_32();
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
