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
    int32_t x_center;
    int32_t y_center;
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

    if (!data->enabled) {
        return;
    }

    if (read_axis(cfg->adc, cfg->x_channel, &x) ||
        read_axis(cfg->adc, cfg->y_channel, &y)) {
        LOG_WRN("ADC read failed");
        goto reschedule;
    }

    if (data->samples < cfg->calibration_samples) {
        data->x_center += x;
        data->y_center += y;
        data->samples++;
        if (data->samples == cfg->calibration_samples) {
            data->x_center /= cfg->calibration_samples;
            data->y_center /= cfg->calibration_samples;
            LOG_INF("center calibrated: x=%d y=%d", data->x_center, data->y_center);
        }
        goto reschedule;
    }

    const int32_t dx = x - data->x_center;
    const int32_t dy = y - data->y_center;

    report(dev, JOY_UP, direction_state(dy, data->state[JOY_UP], false, cfg));
    report(dev, JOY_DOWN, direction_state(dy, data->state[JOY_DOWN], true, cfg));
    report(dev, JOY_LEFT, direction_state(dx, data->state[JOY_LEFT], false, cfg));
    report(dev, JOY_RIGHT, direction_state(dx, data->state[JOY_RIGHT], true, cfg));
    report(dev, JOY_PRESS, gpio_pin_get_dt(&cfg->press) > 0);

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

#define JOYSTICK_DEFINE(inst)                                                                      \
    static struct joystick_data joystick_data_##inst;                                             \
    static const struct joystick_config joystick_config_##inst = {                                \
        .adc = DEVICE_DT_GET(DT_NODELABEL(adc)),                                                   \
        .x_channel = 2,                                                                            \
        .y_channel = 3,                                                                            \
        .press = GPIO_DT_SPEC_GET(DT_NODELABEL(joystick_kscan), press_gpios),                     \
        .poll_ms = DT_INST_PROP(inst, poll_period_ms),                                            \
        .activation = DT_INST_PROP(inst, activation_threshold),                                   \
        .release = DT_INST_PROP(inst, release_threshold),                                         \
        .calibration_samples = DT_INST_PROP(inst, calibration_samples),                           \
    };                                                                                            \
    DEVICE_DT_INST_DEFINE(inst, joystick_init, NULL, &joystick_data_##inst,                       \
                          &joystick_config_##inst, POST_KERNEL,                                   \
                          CONFIG_LISM_RESISTIVE_JOYSTICK_INIT_PRIORITY, &joystick_api);

DT_INST_FOREACH_STATUS_OKAY(JOYSTICK_DEFINE)
