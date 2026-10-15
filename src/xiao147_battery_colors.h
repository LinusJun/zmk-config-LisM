#pragma once

#include <stdint.h>

/* One percentage palette for peripheral rings, device cards and dongle bars. */
static inline uint32_t xiao147_battery_color(uint8_t level)
{
    if (level >= 75) { return 0x00cc66; }
    if (level >= 50) { return 0xffcc00; }
    if (level > 20) { return 0xff8800; }
    return 0xff3333;
}
