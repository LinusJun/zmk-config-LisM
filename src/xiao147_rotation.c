/* Software RGB565 rotation: Zephyr 4.1's ST7789 driver cannot rotate at runtime. */
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include <lvgl.h>
#include <zephyr/device.h>
#include <zephyr/drivers/display.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>

#include "xiao147_rotation.h"
#include "xiao147_display_test.h"

LOG_MODULE_REGISTER(xiao147_rotation, LOG_LEVEL_INF);

#define PHYSICAL_W 320
#define PHYSICAL_H 172
#define ROTATE_TILE_PIXELS 4096

static const struct device *lcd = DEVICE_DT_GET(DT_CHOSEN(zephyr_display));
static uint16_t rotate_tile[ROTATE_TILE_PIXELS];
static atomic_t portrait_mode = ATOMIC_INIT(0);
static uint32_t write_errors;
static bool first_write_logged;

static void report_write(int ret)
{
    if (ret < 0) {
        write_errors++;
        if (write_errors == 1 || write_errors % 100 == 0) {
            LOG_ERR("LCD flush failed: %d (%u failures)", ret, write_errors);
        }
    } else if (!first_write_logged) {
        first_write_logged = true;
        LOG_INF("First LVGL LCD flush succeeded");
    }
}

static void flush_cb(lv_display_t *display, const lv_area_t *area, uint8_t *pixels)
{
    xiao147_display_note_flush(true, 0);
    int result = 0;
    const int w = area->x2 - area->x1 + 1;
    const int h = area->y2 - area->y1 + 1;
    if (xiao147_display_hold_flush()) {
        xiao147_display_note_flush(false, 1); /* intentionally held raw test */
        lv_display_flush_ready(display);
        return;
    }
    const lv_draw_buf_t *buffer = lv_display_get_buf_active(display);
    uint32_t stride = buffer ? buffer->header.stride : 0;
    bool portrait = atomic_get(&portrait_mode) != 0;
    if (w <= 0 || h <= 0 || lv_display_get_color_format(display) != LV_COLOR_FORMAT_RGB565 ||
        area->x1 < 0 || area->y1 < 0 || area->x2 >= (portrait ? PHYSICAL_H : PHYSICAL_W) ||
        area->y2 >= (portrait ? PHYSICAL_W : PHYSICAL_H) ||
        stride < (uint32_t)w * 2 || stride % 2 != 0 ||
        !buffer || (size_t)stride * h > buffer->data_size) {
        xiao147_display_note_flush(false, -EINVAL);
        lv_display_flush_ready(display);
        return;
    }
    if (!portrait) {
        struct display_buffer_descriptor desc = {
            .buf_size = (size_t)stride * h,
            .width = w,
            .height = h,
            .pitch = stride / 2,
            .frame_incomplete = !lv_display_flush_is_last(display),
        };
        result = display_write(lcd, area->x1, area->y1, &desc, pixels);
        report_write(result);
    } else {
        /* Logical portrait (172x320) -> physical 320x172, clockwise.
         * Tile by logical rows so no full-screen framebuffer is required. */
        const uint16_t *src = (const uint16_t *)pixels;
        const int max_rows = ROTATE_TILE_PIXELS / w;
        for (int row = 0; row < h; row += max_rows) {
            int rows = h - row;
            if (rows > max_rows) { rows = max_rows; }
            for (int y = 0; y < rows; y++) {
                for (int x = 0; x < w; x++) {
                    rotate_tile[x * rows + (rows - 1 - y)] =
                        src[(row + y) * (stride / 2) + x];
                }
            }
            struct display_buffer_descriptor desc = {
                .buf_size = (size_t)w * rows * 2,
                .width = rows,
                .height = w,
                .pitch = rows,
                .frame_incomplete = true,
            };
            if (row + rows == h) {
                desc.frame_incomplete = !lv_display_flush_is_last(display);
            }
            int ret = display_write(lcd, PHYSICAL_W - (area->y1 + row + rows), area->x1,
                                    &desc, rotate_tile);
            report_write(ret);
            result = ret;
            if (ret < 0) { break; }
        }
    }
    xiao147_display_note_flush(false, result);
    lv_display_flush_ready(display);
}

void xiao147_rotation_install(void)
{
    lv_display_t *display = lv_display_get_default();
    if (display != NULL && device_is_ready(lcd)) {
        lv_display_set_flush_cb(display, flush_cb);
    } else {
        LOG_ERR("Display not ready for rotation");
    }
}

void xiao147_rotation_set(bool portrait)
{
    if ((atomic_get(&portrait_mode) != 0) == portrait) { return; }
    lv_display_t *display = lv_display_get_default();
    if (display == NULL) { return; }
    /* Invalidate the physical panel too: changed logical coordinates cannot
     * erase pixels left behind by the previous portrait/landscape mapping.
     * Reuse the rotation tile, never allocate a full-screen framebuffer.
     */
    memset(rotate_tile, 0, sizeof(rotate_tile));
    for (int y = 0; y < PHYSICAL_H; y += ROTATE_TILE_PIXELS / PHYSICAL_W) {
        int rows = PHYSICAL_H - y;
        if (rows > ROTATE_TILE_PIXELS / PHYSICAL_W) { rows = ROTATE_TILE_PIXELS / PHYSICAL_W; }
        struct display_buffer_descriptor desc = {
            .width = PHYSICAL_W, .height = rows, .pitch = PHYSICAL_W,
            .buf_size = PHYSICAL_W * rows * 2U,
            .frame_incomplete = y + rows < PHYSICAL_H,
        };
        report_write(display_write(lcd, 0, y, &desc, rotate_tile));
    }
    atomic_set(&portrait_mode, portrait);
    lv_display_set_rotation(display, portrait ? LV_DISPLAY_ROTATION_90 : LV_DISPLAY_ROTATION_0);
    lv_obj_invalidate(lv_scr_act());
}

bool xiao147_rotation_is_portrait(void)
{
    return atomic_get(&portrait_mode) != 0;
}
