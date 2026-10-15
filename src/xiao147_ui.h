#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "xiao147_orientation.h"
#define XIAO147_TOUCH_CANCEL UINT8_MAX

enum xiao147_page {
    XIAO147_PAGE_STATUS = 0,
    XIAO147_PAGE_SETTINGS = 1,
    XIAO147_PAGE_DEVICES = 2,
};

/* Safe to call from input and touch polling work queues. */
enum xiao147_page xiao147_ui_page(void);
void xiao147_ui_next_page(void);
void xiao147_ui_toggle_imu(void);
bool xiao147_ui_imu_enabled(void);
bool xiao147_ui_portrait(void);
bool xiao147_ui_auto_rotate_enabled(void);
void xiao147_ui_set_auto_orientation(enum xiao147_orientation orientation);
bool xiao147_ui_wake_on_motion(void);
void xiao147_ui_touch(uint8_t fingers, uint16_t x, uint16_t y,
                       enum xiao147_orientation orientation);
bool xiao147_ui_wake_on_touch(void);
/* Read-only: safety checks must not wake or extend the inactivity timer. */
bool xiao147_ui_is_sleeping(void);
/* A queued touch press still needs its matching release; suppress air motion. */
bool xiao147_touch_button_pending(void);
/* Connected HID destination identity, or -1. A change cancels pointer anchors. */
int xiao147_pointer_endpoint(void);
