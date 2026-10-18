#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "xiao147_color_brightness.h"

#if defined(CONFIG_LISM_XIAO147_TOUCH) && CONFIG_LISM_XIAO147_TOUCH
bool xiao147_palette_bright(void);
void xiao147_palette_set_bright(bool enabled);
#else
static inline bool xiao147_palette_bright(void) { return false; }
#endif

/* A UI palette choice, not a change to backlight PWM or battery policy. */
static inline uint32_t xiao147_palette_color(uint32_t original) {
    return xiao147_palette_bright() ? xiao147_original_color_brighten30(original) : original;
}
