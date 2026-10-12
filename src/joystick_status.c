/* SPDX-License-Identifier: MIT
 * Standard Seeed XIAO nRF52840 only: RGB pins and BQ25101 ISET/CHG wiring
 * verified against the Seeed v1.1 schematic. This does not replace a cell's
 * protection PCB and cannot measure cell temperature through two battery wires.
 */
#include <errno.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zmk/event_manager.h>
#include <zmk/events/battery_state_changed.h>
#include <zmk/events/usb_conn_state_changed.h>
#include <zmk/usb.h>

LOG_MODULE_REGISTER(lism_joystick_status, CONFIG_ZMK_LOG_LEVEL);

BUILD_ASSERT(!IS_ENABLED(CONFIG_CHARGE_INDICATOR) && !IS_ENABLED(CONFIG_RGBLED_WIDGET),
             "Joystick status must be the sole RGB owner");

static const struct device *const port = DEVICE_DT_GET(DT_NODELABEL(gpio0));
static struct gpio_callback charge_callback;
static int64_t boot_until;
static int64_t low_battery_flash_until;
static bool battery_known;
static bool status_ready;
static uint8_t battery_percent;

static void status_update(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(status_work, status_update);

static void rgb(bool red, bool green, bool blue) {
    gpio_pin_set_raw(port, 26, !red);
    gpio_pin_set_raw(port, 30, !green);
    gpio_pin_set_raw(port, 6, !blue);
}

static void status_update(struct k_work *work) {
    ARG_UNUSED(work);
    if (!status_ready) return;
    int64_t now = k_uptime_get();
    bool usb = zmk_usb_is_powered();
    int stat = gpio_pin_get_raw(port, 17);
    bool charging = usb && stat == 0;
    int32_t delay = usb ? 5000 : 60000;
    if (charging) {
        /* Same battery-level colors as the halves: <20 red, 20..79 yellow,
         * >=80 green. Green means high estimated charge, NOT charge complete.
         * Unknown battery readings never produce a green indication.
         */
        rgb(!battery_known || battery_percent < 80,
            battery_known && battery_percent >= 20, false);
    } else if (now < boot_until) {
        rgb(false, false, true);
        delay = (int32_t)(boot_until - now);
    } else if (!usb && now < low_battery_flash_until) {
        rgb(true, false, false);
        delay = (int32_t)(low_battery_flash_until - now);
    } else {
        rgb(false, false, false);
    }
    k_work_reschedule(&status_work, K_MSEC(delay));
}

static void charge_irq(const struct device *dev, struct gpio_callback *cb, uint32_t pins) {
    ARG_UNUSED(dev); ARG_UNUSED(cb); ARG_UNUSED(pins);
    /* GPIO callbacks may run in interrupt context: never sleep here. */
    if (status_ready) k_work_reschedule(&status_work, K_MSEC(10));
}

static int status_listener(const zmk_event_t *event) {
    const struct zmk_battery_state_changed *battery = as_zmk_battery_state_changed(event);
    if (battery) {
        battery_percent = battery->state_of_charge;
        battery_known = true;
        if (battery_percent <= 10) low_battery_flash_until = k_uptime_get() + 150;
    }
    if (status_ready) k_work_reschedule(&status_work, K_NO_WAIT);
    return ZMK_EV_EVENT_BUBBLE;
}
ZMK_LISTENER(lism_joystick_status, status_listener);
ZMK_SUBSCRIPTION(lism_joystick_status, zmk_battery_state_changed);
ZMK_SUBSCRIPTION(lism_joystick_status, zmk_usb_conn_state_changed);

static int status_init(void) {
    if (!device_is_ready(port)) return -ENODEV;
    /* ISET has its own resistor to GND. No pull/no drive -> about 50mA.
     * Driving LOW would parallel a second resistor and select about 100mA.
     * Never drive HIGH to stop charging. Hardware charging also operates
     * before application initialization and in bootloader mode.
     */
    int rc = gpio_pin_configure(port, 13, GPIO_INPUT);
    if (rc) return rc;
    for (int i = 0; i < 3; ++i) {
        static const uint8_t pins[] = {26, 30, 6};
        rc = gpio_pin_configure(port, pins[i], GPIO_OUTPUT_HIGH);
        if (rc) return rc;
    }
    /* Observe CHG as an input; the dedicated hardware charging LED remains
     * owned by the charger. RGB off does not disable that hardware LED.
     */
    rc = gpio_pin_configure(port, 17, GPIO_INPUT | GPIO_PULL_UP);
    if (rc) return rc;
    gpio_init_callback(&charge_callback, charge_irq, BIT(17));
    rc = gpio_add_callback(port, &charge_callback);
    if (rc) return rc;
    rc = gpio_pin_interrupt_configure(port, 17, GPIO_INT_EDGE_BOTH);
    if (rc) return rc;
    boot_until = k_uptime_get() + 250;
    status_ready = true;
    k_work_schedule(&status_work, K_NO_WAIT);
    return 0;
}
SYS_INIT(status_init, APPLICATION, 70);
