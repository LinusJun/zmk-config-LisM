#pragma once

#include <stdint.h>

/* Scale the original RGB components together by 1.30, reducing that ONE gain
 * when the largest component would exceed 255. This preserves hue/saturation
 * up to 8-bit rounding; independently clipping channels would change hue.
 * Neutral gray is returned unchanged. This changes color values, not PWM. */
static inline uint32_t xiao147_original_color_brighten30(uint32_t color)
{
    uint32_t r = (color >> 16) & 255U;
    uint32_t g = (color >> 8) & 255U;
    uint32_t b = color & 255U;
    if (r == g && g == b) { return color; }
    uint32_t maximum = r > g ? r : g;
    if (b > maximum) { maximum = b; }
    uint32_t numerator = 13U, denominator = 10U;
    if (maximum * 13U > 2550U) { numerator = 255U; denominator = maximum; }
    r = (r * numerator + denominator / 2U) / denominator;
    g = (g * numerator + denominator / 2U) / denominator;
    b = (b * numerator + denominator / 2U) / denominator;
    return (r << 16) | (g << 8) | b;
}
