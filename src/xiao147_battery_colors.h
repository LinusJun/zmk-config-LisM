#pragma once

#include <stdint.h>

/* One percentage palette for peripheral rings, device cards and dongle bars. */
static inline uint32_t xiao147_battery_color(uint8_t level)
{
    if (level >= 75) { return 0x54806c; }
    if (level >= 50) { return 0xffcc00; }
    if (level > 20) { return 0xc08040; }
    return 0xe46464;
}
