/* SPDX-License-Identifier: MIT
 * Observe the failed boot without resetting a working display or radio.
 */
#include <zephyr/kernel.h>
#include <zephyr/init.h>
#include <zephyr/device.h>
#include <zephyr/drivers/display.h>
#include <hal/nrf_power.h>
#include <zmk/display.h>
#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(lism_startup, LOG_LEVEL_INF);

static int startup_marker(void) {
    LOG_INF("v4.4 application entered: raw_RESETREAS=%x LCD_ready=%d host_connections=%d",
            (unsigned)NRF_POWER->RESETREAS,
            device_is_ready(DEVICE_DT_GET(DT_CHOSEN(zephyr_display))), CONFIG_BT_MAX_CONN);
    return 0;
}
SYS_INIT(startup_marker, APPLICATION, 1);

static void startup_observer(void *a, void *b, void *c) {
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    for (unsigned sample = 0; sample < 3; sample++) {
        LOG_INF("v4.4 kernel alive: LCD_ready=%d UI_initialized=%d sample=%u",
                device_is_ready(DEVICE_DT_GET(DT_CHOSEN(zephyr_display))),
                zmk_display_is_initialized(), sample);
        k_sleep(K_SECONDS(2));
    }
}
K_THREAD_DEFINE(lism_startup_thread, 1024, startup_observer, NULL, NULL, NULL,
                K_LOWEST_APPLICATION_THREAD_PRIO, 0, 1000);
