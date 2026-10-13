#pragma once
#include <stdbool.h>
#include <zephyr/sys/util.h>

#if IS_ENABLED(CONFIG_LISM_XIAO147_DISPLAY_SELFTEST)
bool xiao147_display_ui_ready(void);
bool xiao147_display_hold_flush(void);
#else
static inline bool xiao147_display_ui_ready(void) { return true; }
static inline bool xiao147_display_hold_flush(void) { return false; }
#endif
