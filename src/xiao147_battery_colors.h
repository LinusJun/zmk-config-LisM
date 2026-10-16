#pragma once

#include <stdint.h>
#include "xiao147_color_brightness.h"

/* One percentage palette for peripheral rings, device cards and dongle bars. */
static inline uint32_t xiao147_battery_color(uint8_t level)
{
    if (level >= 75) { return xiao147_original_color_brighten30(0x54806c); }
    if (level >= 50) { return xiao147_original_color_brighten30(0xffcc00); }
    if (level > 20) { return xiao147_original_color_brighten30(0xc08040); }
    return xiao147_original_color_brighten30(0xe46464);
}
