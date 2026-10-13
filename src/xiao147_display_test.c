/* One test UF2, three observable stages, all work on the display queue.
 * Keep the BLE/system queue responsive. No writes to persistent settings.
 */
#include <lvgl.h>
#include <zephyr/device.h>
#include <zephyr/drivers/display.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/mem_stats.h>
#include <zmk/display.h>
#include "xiao147_rotation.h"
#include "xiao147_display_test.h"

LOG_MODULE_REGISTER(xiao147_display_test, LOG_LEVEL_INF);

static const struct device *lcd = DEVICE_DT_GET(DT_CHOSEN(zephyr_display));
static atomic_t stage = ATOMIC_INIT(0);
static struct k_work_delayable phase_work;
static lv_obj_t *test_screen;
static uint16_t pattern[320 * 4];
static uint16_t pattern_y;
static uint32_t failed_writes;
lv_obj_t *xiao147_stock_status_screen(void);
/* Zephyr 4.1 modules/lvgl/lvgl_mem.c: measure the real private sys_heap,
 * including peak use, rather than LVGL's custom-allocator monitor stub.
 */
void lvgl_heap_stats(struct sys_memory_stats *stats);

bool xiao147_display_ui_ready(void) { return atomic_get(&stage) == 2; }
bool xiao147_display_hold_flush(void) { return atomic_get(&stage) == 0; }

static void report_memory(const char *name)
{
    struct sys_memory_stats mem = {0};
    lvgl_heap_stats(&mem);
    LOG_INF("%s: LVGL allocated %zu, free %zu, peak %zu",
            name, mem.allocated_bytes, mem.free_bytes, mem.max_allocated_bytes);
}

static void phase_handler(struct k_work *work)
{
    if (atomic_get(&stage) == 0) {
        /* Raw device writes, bypassing LVGL rendering/rotation completely.
         * Five bands L->R: red, green, blue, white, black; white corner marks.
         * Native RGB565 values are converted exactly once by the panel driver.
         */
        static const uint16_t colors[] = {0xf800, 0x07e0, 0x001f, 0xffff, 0x0000};
        for (int row = 0; row < 4; row++) {
            for (int x = 0; x < 320; x++) {
                bool corner = (x < 6 || x >= 314) &&
                              (pattern_y + row < 6 || pattern_y + row >= 166);
                pattern[row * 320 + x] = corner ? 0xffff : colors[x / 64];
            }
        }
        struct display_buffer_descriptor desc = {
            .width = 320, .height = 4, .pitch = 320, .buf_size = sizeof(pattern),
            .frame_incomplete = pattern_y + 4 < 172,
        };
        int ret = display_write(lcd, 0, pattern_y, &desc, pattern);
        if (ret < 0 && failed_writes++ == 0) {
            LOG_ERR("Stage 1 raw write failed: %d", ret);
        }
        pattern_y += 4;
        if (pattern_y < 172) {
            k_work_reschedule_for_queue(zmk_display_work_q(), &phase_work, K_MSEC(1));
            return;
        }
        LOG_INF("Stage 1/3 RGB bars drawn, failed writes %u; hold 5 seconds", failed_writes);
        /* Continue holding LVGL flush until the five-second color test ends. */
        pattern_y = 172;
        k_work_reschedule_for_queue(zmk_display_work_q(), &phase_work, K_SECONDS(5));
        return;
    }
    lv_obj_t *screen = xiao147_stock_status_screen();
    if (screen == NULL) {
        LOG_ERR("Stage 3 failed to allocate status screen");
        return;
    }
    lv_screen_load(screen);
    lv_obj_delete(test_screen);
    test_screen = NULL;
    report_memory("Stage 3/3 stock OPERATOR");
    atomic_set(&stage, 2);
    lv_obj_invalidate(screen);
    LOG_INF("Stage 3/3 full UI enabled; deferred menu/ring initialization follows");
    k_work_reschedule_for_queue(zmk_display_work_q(), &phase_work, K_SECONDS(2));
}

/* Separate transition wrapper: raw drawing never overruns y=172 and the raw
 * frame remains on screen while the display tick continues to acknowledge LVGL.
 */
static void diagnostic_work(struct k_work *work)
{
    if (atomic_get(&stage) == 2) {
        report_memory("Stage 3/3 complete menus and rings");
        return;
    }
    if (atomic_get(&stage) == 0 && pattern_y == 172) {
        atomic_set(&stage, 1);
        lv_obj_invalidate(test_screen);
        report_memory("Stage 2/3 minimal LVGL");
        LOG_INF("Stage 2/3 LVGL OK screen; hold 5 seconds");
        k_work_reschedule_for_queue(zmk_display_work_q(), &phase_work, K_SECONDS(5));
        return;
    }
    phase_handler(work);
}

lv_obj_t *zmk_display_status_screen(void)
{
    xiao147_rotation_install();
    test_screen = lv_obj_create(NULL);
    if (test_screen == NULL) { return NULL; }
    lv_obj_set_style_bg_color(test_screen, lv_color_hex(0x000000), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(test_screen, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_t *label = lv_label_create(test_screen);
    if (label == NULL) { return NULL; }
    lv_label_set_text(label, "2/3 LVGL OK");
    lv_obj_set_style_text_color(label, lv_color_hex(0xffffff), LV_PART_MAIN);
    lv_obj_set_style_text_font(label, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_center(label);
    k_work_init_delayable(&phase_work, diagnostic_work);
    report_memory("Stage 1/3 before stock UI allocation");
    k_work_schedule_for_queue(zmk_display_work_q(), &phase_work, K_MSEC(100));
    return test_screen;
}
