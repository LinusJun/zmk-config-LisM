#pragma once

#include <stdint.h>
#include "xiao147_palette.h"

/* One percentage palette for peripheral rings, device cards and dongle bars. */
static inline uint32_t xiao147_battery_color(uint8_t level)
{
    if (level >= 75) { return xiao147_palette_color(0x54806c); }
    /* Upstream has green/orange only. Added yellow/red match its muted style. */
    if (level >= 50) { return xiao147_palette_color(0xb4a264); }
    if (level > 20) { return xiao147_palette_color(0xc08040); }
    return xiao147_palette_color(0xb77979);
}
