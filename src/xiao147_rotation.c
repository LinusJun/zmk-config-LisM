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
static atomic_t applied_orientation = ATOMIC_INIT(XIAO147_USB_LEFT);
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
    enum xiao147_orientation orientation = xiao147_rotation_get();
    bool portrait = xiao147_orientation_portrait(orientation);
    if (w <= 0 || h <= 0 || lv_display_get_color_format(display) != LV_COLOR_FORMAT_RGB565 ||
        area->x1 < 0 || area->y1 < 0 || area->x2 >= (portrait ? PHYSICAL_H : PHYSICAL_W) ||
        area->y2 >= (portrait ? PHYSICAL_W : PHYSICAL_H) ||
        stride < (uint32_t)w * 2 || stride % 2 != 0 ||
        !buffer || (size_t)stride * h > buffer->data_size) {
        xiao147_display_note_flush(false, -EINVAL);
        lv_display_flush_ready(display);
        return;
    }
    if (orientation == XIAO147_USB_LEFT) {
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
        /* Tile by logical rows, with no full-screen transform allocation. */
        const uint16_t *src = (const uint16_t *)pixels;
        const int max_rows = ROTATE_TILE_PIXELS / w;
        for (int row = 0; row < h; row += max_rows) {
            int rows = h - row;
            if (rows > max_rows) { rows = max_rows; }
            for (int y = 0; y < rows; y++) {
                for (int x = 0; x < w; x++) {
                    int index = orientation == XIAO147_BUTTONS_TOP ?
                                x * rows + rows - 1 - y :
                                orientation == XIAO147_BUTTONS_BOTTOM ?
                                (w - 1 - x) * rows + y :
                                (rows - 1 - y) * w + w - 1 - x;
                    rotate_tile[index] = src[(row + y) * (stride / 2) + x];
                }
            }
            struct display_buffer_descriptor desc = {
                .buf_size = (size_t)w * rows * 2,
                .width = portrait ? rows : w,
                .height = portrait ? w : rows,
                .pitch = portrait ? rows : w,
                .frame_incomplete = true,
            };
            if (row + rows == h) {
                desc.frame_incomplete = !lv_display_flush_is_last(display);
            }
            int px = orientation == XIAO147_BUTTONS_TOP ? PHYSICAL_W - (area->y1 + row + rows) :
                     orientation == XIAO147_BUTTONS_BOTTOM ? area->y1 + row :
                     PHYSICAL_W - (area->x1 + w);
            int py = orientation == XIAO147_BUTTONS_TOP ? area->x1 :
                     orientation == XIAO147_BUTTONS_BOTTOM ? PHYSICAL_H - (area->x1 + w) :
                     PHYSICAL_H - (area->y1 + row + rows);
            int ret = display_write(lcd, px, py, &desc, rotate_tile);
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

void xiao147_rotation_set(enum xiao147_orientation orientation)
{
    if (orientation < XIAO147_USB_LEFT || orientation > XIAO147_BUTTONS_TOP ||
        xiao147_rotation_get() == orientation) { return; }
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
    /* LVGL rotation updates resolution only; flush_cb performs the pixels. */
    static const lv_display_rotation_t rotations[] = {
        LV_DISPLAY_ROTATION_0, LV_DISPLAY_ROTATION_270,
        LV_DISPLAY_ROTATION_180, LV_DISPLAY_ROTATION_90,
    };
    lv_display_set_rotation(display, rotations[orientation]);
    atomic_set(&applied_orientation, orientation);
    lv_obj_invalidate(lv_scr_act());
}

bool xiao147_rotation_is_portrait(void)
{
    return xiao147_orientation_portrait(xiao147_rotation_get());
}

enum xiao147_orientation xiao147_rotation_get(void)
{
    return (enum xiao147_orientation)atomic_get(&applied_orientation);
}
