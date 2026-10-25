/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <zephyr/drivers/display.h>
void lism_lcd_trace_command(uint8_t cmd, const uint8_t *data, size_t len, int rc);
void lism_lcd_trace_write(uint16_t x, uint16_t y,
                          const struct display_buffer_descriptor *desc, const void *buf, int rc);
/* Called only by the LVGL display queue. No LVGL object crosses threads. */
void lism_lcd_trace_ui(uint32_t bg, const char *text, int16_t x1, int16_t y1,
                       int16_t x2, int16_t y2);
/* Resolved output style, copied on the display queue; never retain objects. */
void lism_lcd_trace_output_ui(uint32_t bg, uint8_t opa, uint16_t state,
    uint8_t visible, int8_t owner, uint8_t local_transport, uint8_t profile,
    int16_t x1, int16_t y1, int16_t x2, int16_t y2);
