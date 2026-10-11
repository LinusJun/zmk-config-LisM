#pragma once

#include <stdbool.h>
#include <stdint.h>

enum xiao147_page {
    XIAO147_PAGE_STATUS = 0,
    XIAO147_PAGE_SETTINGS = 1,
    XIAO147_PAGE_DEVICES = 2,
};

/* Safe to call from input and touch polling work queues. */
enum xiao147_page xiao147_ui_page(void);
void xiao147_ui_next_page(void);
void xiao147_ui_touch(uint8_t fingers, uint16_t x, uint16_t y);
bool xiao147_ui_wake_on_touch(void);
