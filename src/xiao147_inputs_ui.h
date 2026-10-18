#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <lvgl.h>

/* Invoke only on the display queue. Coordinates use current screen rotation. */
lv_obj_t *xiao147_inputs_init(lv_obj_t *parent);
void xiao147_inputs_draw(bool portrait);
void xiao147_inputs_press(uint16_t x, uint16_t y);
void xiao147_inputs_release(uint16_t x, uint16_t y);
void xiao147_inputs_cancel(void);
/* Physical-button navigation. Capture at the first USR1 press; its nonzero
 * serial may be activated once by a short double-click. Any changed target,
 * touch, page context or auth request invalidates it. A timed-out single click
 * cancels the capture and advances focus. These functions do not select a
 * different device slot merely by moving the visible focus. */
uint32_t xiao147_inputs_focus_capture(void);
void xiao147_inputs_focus_activate(uint32_t serial);
void xiao147_inputs_focus_next(void);
void xiao147_inputs_focus_cancel_pending(void);
/* Callback runs on the display queue after a completed semantic redraw. It
 * must only invalidate external button state: do not redraw or re-enter UI. */
void xiao147_inputs_focus_set_context_callback(void (*changed)(void));
void xiao147_inputs_status_init(lv_obj_t *screen);
void xiao147_inputs_status_draw(bool portrait, bool visible);
