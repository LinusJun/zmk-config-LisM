#pragma once

/* XIAO-only original OPERATOR hues, with up to 30% uniform RGB value gain.
 * Neutral backgrounds and inactive/unknown states keep their original values.
 * The stock dongle continues to include its upstream display_colors.h. */
#include "xiao147_palette.h"

#define XIAO147_ACCENT_BLUE xiao147_palette_color(0x39bdec)
#define XIAO147_SLIDER_BLUE xiao147_palette_color(0x007aff)
#define XIAO147_SWITCH_GREEN xiao147_palette_color(0x34c759)
#define XIAO147_DEVICE_BORDER xiao147_palette_color(0x4a90e2)

#define DISPLAY_COLOR_MOD_ACTIVE       xiao147_palette_color(0xb1e5f0)
#define DISPLAY_COLOR_MOD_INACTIVE     xiao147_palette_color(0x3b527c)
#define DISPLAY_COLOR_MOD_SEPARATOR    0x606060
#define DISPLAY_COLOR_MOD_CAPS_WORD    xiao147_palette_color(0xffbf00)

/* Keep the exact upstream rose for WPM. Its 30% boosted variant looked red on
 * the user's panel; do not compensate by changing the global color pipeline. */
#define DISPLAY_COLOR_WPM_BAR_ACTIVE   0xc2526a
#define DISPLAY_COLOR_WPM_BAR_INACTIVE 0x242424
#define DISPLAY_COLOR_WPM_TEXT         0xc2526a

#define DISPLAY_COLOR_LAYER_DOT_ACTIVE   0xe0e0e0
#define DISPLAY_COLOR_LAYER_DOT_INACTIVE 0x575757
/* Default style matches the selected layer bar exactly, not Bright mode.
 * Keep the previously shipped Bright-mode layer text unchanged. */
#define DISPLAY_COLOR_LAYER_TEXT (xiao147_palette_bright() ? 0xb0b0b0 : DISPLAY_COLOR_LAYER_DOT_ACTIVE)

#define DISPLAY_COLOR_BATTERY_FILL       xiao147_palette_color(0x54806c)
#define DISPLAY_COLOR_BATTERY_RING       0x2a4036
#define DISPLAY_COLOR_BATTERY_BG         0x505050
#define DISPLAY_COLOR_BATTERY_LABEL      0xffffff
#define DISPLAY_COLOR_BATTERY_DISCONNECTED_FILL  0x383c42
#define DISPLAY_COLOR_BATTERY_DISCONNECTED_RING  0x282c30
#define DISPLAY_COLOR_BATTERY_DISCONNECTED_LABEL 0x000000
#define DISPLAY_COLOR_BATTERY_LOW_FILL   xiao147_palette_color(0xc08040)
#define DISPLAY_COLOR_BATTERY_LOW_RING   0x584028

#define DISPLAY_COLOR_USB_ACTIVE_BG        xiao147_palette_color(0xb9b9a7)
#define DISPLAY_COLOR_USB_INACTIVE_BG      0x4f4f40
#define DISPLAY_COLOR_BLE_ACTIVE_BG        xiao147_palette_color(0x569fa7)
#define DISPLAY_COLOR_BLE_INACTIVE_BG      0x353f40
#define DISPLAY_COLOR_OUTPUT_ACTIVE_TEXT   0x000000
#define DISPLAY_COLOR_OUTPUT_INACTIVE_TEXT 0x7b7d93

/* Keep the original low-saturation lavender hue of the selected profile. */
#define DISPLAY_COLOR_SLOT_ACTIVE_BG   xiao147_palette_color(0x7b7d93)
#define DISPLAY_COLOR_SLOT_INACTIVE_BG 0x353640
#define DISPLAY_COLOR_SLOT_TEXT        (xiao147_palette_bright() ? 0xffffff : 0x7b7d93)
#define DISPLAY_COLOR_SLOT_ACTIVE_TEXT (xiao147_palette_bright() ? 0xffffff : 0x000000)
