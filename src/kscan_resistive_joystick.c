/* SPDX-License-Identifier: MIT */

#define DT_DRV_COMPAT linus_resistive_joystick

#include <errno.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/kscan.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(lism_joystick, CONFIG_ZMK_LOG_LEVEL);

/* 12-bit SAADC with 0.6 V reference and gain 1/6: approximately 3.6 V full scale.
 * These guards catch rails and disconnected/tied-high axes, not an unknown supply rating.
 */
#define JOY_CENTER_MIN 600
#define JOY_CENTER_MAX 3400
#define JOY_CALIBRATION_MAX_SPREAD 100
#define JOY_SAMPLE_MIN 80
#define JOY_SAMPLE_MAX 4000
#define JOY_LOG_INTERVAL_MS 500
#define JOY_ARM_HOLD_MS 1000

enum joystick_column {
    JOY_UP = 0,
    JOY_DOWN,
    JOY_LEFT,
    JOY_RIGHT,
    JOY_PRESS,
    JOY_COLUMN_COUNT,
};

struct joystick_config {
    const struct device *adc;
    uint8_t x_channel;
    uint8_t y_channel;
    struct gpio_dt_spec press;
    uint16_t poll_ms;
    int16_t activation;
    int16_t release;
    uint16_t calibration_samples;
};

struct joystick_data {
    const struct device *dev;
    kscan_callback_t callback;
    struct k_work_delayable work;
    bool enabled;
    bool state[JOY_COLUMN_COUNT];
    bool calibrated;
    bool armed;
    int64_t arm_start_ms;
    int64_t last_log_ms;
    int32_t x_center;
    int32_t y_center;
    int16_t x_min;
    int16_t x_max;
    int16_t y_min;
    int16_t y_max;
    uint16_t samples;
};

static int read_axis(const struct device *adc, uint8_t channel, int16_t *value) {
    struct adc_sequence sequence = {
        .channels = BIT(channel),
        .buffer = value,
        .buffer_size = sizeof(*value),
        .resolution = 12,
        .oversampling = 0,
    };
    return adc_read(adc, &sequence);
}

static int setup_axis(const struct device *adc, uint8_t channel_id) {
    struct adc_channel_cfg channel = {
        .gain = ADC_GAIN_1_6,
        .reference = ADC_REF_INTERNAL,
        .acquisition_time = ADC_ACQ_TIME_DEFAULT,
        .channel_id = channel_id,
#ifdef CONFIG_ADC_CONFIGURABLE_INPUTS
        .input_positive = SAADC_CH_PSELP_PSELP_AnalogInput0 + channel_id,
#endif
    };
    return adc_channel_setup(adc, &channel);
}

static void report(const struct device *dev, enum joystick_column column, bool pressed) {
    struct joystick_data *data = dev->data;
    if (data->state[column] == pressed) {
        return;
    }
    data->state[column] = pressed;
    if (data->callback) {
        data->callback(dev, 0, column, pressed);
    }
}

static void release_directions(const struct device *dev) {
    report(dev, JOY_UP, false);
    report(dev, JOY_DOWN, false);
    report(dev, JOY_LEFT, false);
    report(dev, JOY_RIGHT, false);
}

static bool direction_state(int32_t delta, bool old_state, bool positive,
                            const struct joystick_config *cfg) {
    int32_t signed_delta = positive ? delta : -delta;
    return old_state ? signed_delta > cfg->release : signed_delta > cfg->activation;
}

static void joystick_work(struct k_work *work) {
    struct k_work_delayable *dwork = k_work_delayable_from_work(work);
    struct joystick_data *data = CONTAINER_OF(dwork, struct joystick_data, work);
    const struct device *dev = data->dev;
    const struct joystick_config *cfg = dev->config;
    int16_t x = 0;
    int16_t y = 0;
    int button = gpio_pin_get_dt(&cfg->press);
    bool pressed = button > 0;
    int64_t now = k_uptime_get();

    if (!data->enabled) {
        return;
    }

    /* Push is a separate digital input. ADC calibration and directional arming
     * must never suppress Space, including when an axis is disconnected.
     */
    report(dev, JOY_PRESS, button >= 0 && pressed);

    if (button < 0 || read_axis(cfg->adc, cfg->x_channel, &x) ||
        read_axis(cfg->adc, cfg->y_channel, &y)) {
        release_directions(dev);
        data->calibrated = false;
        data->armed = false;
        data->samples = 0;
        if (now - data->last_log_ms >= JOY_LOG_INTERVAL_MS) {
            LOG_WRN("ADC/GPIO read failed; push=%d gpio_result=%d", pressed, button);
            data->last_log_ms = now;
        }
        goto reschedule;
    }

    if (now - data->last_log_ms >= JOY_LOG_INTERVAL_MS) {
        LOG_INF("ADC x=%d y=%d center=%d,%d cal=%d armed=%d push=%d",
                x, y, data->x_center, data->y_center,
                data->calibrated, data->armed, pressed);
        data->last_log_ms = now;
    }

    if (!data->calibrated) {
        if (data->samples == 0) {
            data->x_center = 0;
            data->y_center = 0;
            data->x_min = data->x_max = x;
            data->y_min = data->y_max = y;
        }
        data->x_center += x;
        data->y_center += y;
        if (x < data->x_min) data->x_min = x;
        if (x > data->x_max) data->x_max = x;
        if (y < data->y_min) data->y_min = y;
        if (y > data->y_max) data->y_max = y;
        data->samples++;
        if (data->samples >= cfg->calibration_samples) {
            data->x_center /= cfg->calibration_samples;
            data->y_center /= cfg->calibration_samples;
            if (data->x_center >= JOY_CENTER_MIN &&
                data->x_center <= JOY_CENTER_MAX &&
                data->y_center >= JOY_CENTER_MIN &&
                data->y_center <= JOY_CENTER_MAX &&
                data->x_max - data->x_min <= JOY_CALIBRATION_MAX_SPREAD &&
                data->y_max - data->y_min <= JOY_CALIBRATION_MAX_SPREAD) {
                data->calibrated = true;
                LOG_INF("neutral accepted: x=%d y=%d", data->x_center, data->y_center);
            } else {
                LOG_WRN("neutral rejected: x=%d [%d,%d] y=%d [%d,%d]",
                        data->x_center, data->x_min, data->x_max,
                        data->y_center, data->y_min, data->y_max);
            }
            data->samples = 0;
        }
    }

    if (IS_ENABLED(CONFIG_LISM_JOYSTICK_DIAG_ONLY)) {
        release_directions(dev);
        goto reschedule;
    }

    if (!data->calibrated) {
        release_directions(dev);
        data->arm_start_ms = 0;
        goto reschedule;
    }

    if (x < JOY_SAMPLE_MIN || x > JOY_SAMPLE_MAX ||
        y < JOY_SAMPLE_MIN || y > JOY_SAMPLE_MAX) {
        release_directions(dev);
        data->calibrated = false;
        data->armed = false;
        data->samples = 0;
        LOG_WRN("ADC rail value; disarmed");
        goto reschedule;
    }

    if (IS_ENABLED(CONFIG_LISM_JOYSTICK_REQUIRE_ARM) && !data->armed) {
        release_directions(dev);
        if (!pressed) {
            data->arm_start_ms = 0;
        } else if (data->arm_start_ms == 0) {
            data->arm_start_ms = now;
        } else if (now - data->arm_start_ms >= JOY_ARM_HOLD_MS) {
            data->armed = true;
            LOG_INF("joystick armed; release push before testing");
        }
        goto reschedule;
    }

    const int32_t dx = x - data->x_center;
    const int32_t dy = y - data->y_center;
    report(dev, JOY_UP, direction_state(dy, data->state[JOY_UP], false, cfg));
    report(dev, JOY_DOWN, direction_state(dy, data->state[JOY_DOWN], true, cfg));
    report(dev, JOY_LEFT, direction_state(dx, data->state[JOY_LEFT], false, cfg));
    report(dev, JOY_RIGHT, direction_state(dx, data->state[JOY_RIGHT], true, cfg));

reschedule:
    k_work_schedule(&data->work, K_MSEC(cfg->poll_ms));
}

static int joystick_configure(const struct device *dev, kscan_callback_t callback) {
    struct joystick_data *data = dev->data;
    if (!callback) {
        return -EINVAL;
    }
    data->callback = callback;
    return 0;
}

static int joystick_enable(const struct device *dev) {
    struct joystick_data *data = dev->data;
    data->enabled = true;
    k_work_schedule(&data->work, K_NO_WAIT);
    return 0;
}

static int joystick_disable(const struct device *dev) {
    struct joystick_data *data = dev->data;
    data->enabled = false;
    k_work_cancel_delayable(&data->work);
    release_directions(dev);
    report(dev, JOY_PRESS, false);
    return 0;
}

static int joystick_init(const struct device *dev) {
    const struct joystick_config *cfg = dev->config;
    struct joystick_data *data = dev->data;
    if (!device_is_ready(cfg->adc) || !gpio_is_ready_dt(&cfg->press)) {
        return -ENODEV;
    }
    if (setup_axis(cfg->adc, cfg->x_channel) ||
        setup_axis(cfg->adc, cfg->y_channel)) {
        return -EIO;
    }
    if (gpio_pin_configure_dt(&cfg->press, GPIO_INPUT)) {
        return -EIO;
    }
    data->dev = dev;
    k_work_init_delayable(&data->work, joystick_work);
    return 0;
}

static const struct kscan_driver_api joystick_api = {
    .config = joystick_configure,
    .enable_callback = joystick_enable,
    .disable_callback = joystick_disable,
};

#define JOYSTICK_NODE DT_NODELABEL(joystick_kscan)

static struct joystick_data joystick_data;

static const struct joystick_config joystick_config = {
    .adc = DEVICE_DT_GET(DT_NODELABEL(adc)),
    .x_channel = 2,
    .y_channel = 3,
    .press = {
        .port = DEVICE_DT_GET(DT_NODELABEL(gpio1)),
        .pin = 11,
        .dt_flags = GPIO_ACTIVE_LOW | GPIO_PULL_UP,
    },
    .poll_ms = DT_PROP(JOYSTICK_NODE, poll_period_ms),
    .activation = DT_PROP(JOYSTICK_NODE, activation_threshold),
    .release = DT_PROP(JOYSTICK_NODE, release_threshold),
    .calibration_samples = DT_PROP(JOYSTICK_NODE, calibration_samples),
};

DEVICE_DT_DEFINE(JOYSTICK_NODE, joystick_init, NULL, &joystick_data, &joystick_config,
                 POST_KERNEL, CONFIG_LISM_RESISTIVE_JOYSTICK_INIT_PRIORITY, &joystick_api);
