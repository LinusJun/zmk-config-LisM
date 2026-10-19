/* SPDX-License-Identifier: MIT */
#include <zephyr/sys/util.h>
#include <zmk/ble.h>
BUILD_ASSERT(ZMK_BLE_PROFILE_COUNT==5,"Keep the five numbered Prospector computer profiles");
BUILD_ASSERT(!IS_ENABLED(CONFIG_ZMK_SPLIT_BLE),"Keyboard halves must not use BLE commissioning");
BUILD_ASSERT(IS_ENABLED(CONFIG_ZMK_BLE),"Host BLE must remain available");
