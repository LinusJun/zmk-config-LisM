#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Initial pairing order: left, right, then either joystick and the other. */
#define XIAO147_PERIPHERAL_COUNT 4
#define XIAO147_RSSI_COUNT (XIAO147_PERIPHERAL_COUNT + 1)
#define XIAO147_JOYSTICK_PRIMARY_SLOT 2
#define XIAO147_JOYSTICK_ALTERNATE_SLOT 3

/* Preserve three visible peripheral cards/rings. When both sticks are online,
 * the first-paired joystick wins; use one stick at a time. */
static inline uint8_t xiao147_active_joystick_slot(const bool connected[XIAO147_PERIPHERAL_COUNT])
{
    return !connected[XIAO147_JOYSTICK_PRIMARY_SLOT] && connected[XIAO147_JOYSTICK_ALTERNATE_SLOT] ?
           XIAO147_JOYSTICK_ALTERNATE_SLOT : XIAO147_JOYSTICK_PRIMARY_SLOT;
}
