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
static struct k_work_delayable heartbeat_work;
static atomic_t progress;
static atomic_t progress_time;
static atomic_t flush_entered;
static atomic_t flush_exited;
static atomic_t last_flush_result;
static atomic_t transmitted;
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

static void raw_refresh_gate(lv_event_t *event)
{
    /* Actual LVGL f1db87ee registers its resume callback at display creation.
     * Event callbacks run in registration order, so this later gate pauses it
     * again during raw mode, including ZMK's subsequent lv_screen_load().
     */
    if (xiao147_display_hold_flush()) {
        lv_timer_t *timer = lv_display_get_refr_timer(lv_event_get_target(event));
        if (timer != NULL) { lv_timer_pause(timer); }
    }
}

static void checkpoint(int point)
{
    atomic_set(&progress_time, k_uptime_get_32());
    atomic_set(&progress, point);
    LOG_INF("Display checkpoint %d, stage %ld", point, (long)atomic_get(&stage));
}

void xiao147_display_note_flush(bool entering, int result)
{
    if (entering) {
        if (atomic_inc(&flush_entered) == 0) {
            LOG_INF("First LVGL flush entered (stage %ld)", (long)atomic_get(&stage));
        }
        return;
    }
    atomic_inc(&flush_exited);
    atomic_set(&last_flush_result, result);
    if (result != 1 && atomic_cas(&transmitted, 0, 1)) {
        LOG_INF("First non-held LVGL flush returned %d", result);
    }
}

static void heartbeat(struct k_work *work)
{
    /* No LVGL, heap statistics or SPI here: survives a blocked display queue. */
    LOG_INF("Heartbeat stage %ld checkpoint %ld age %u ms flush %ld/%ld result %ld",
            (long)atomic_get(&stage), (long)atomic_get(&progress),
            k_uptime_get_32() - (uint32_t)atomic_get(&progress_time),
            (long)atomic_get(&flush_entered), (long)atomic_get(&flush_exited),
            (long)atomic_get(&last_flush_result));
    k_work_reschedule(&heartbeat_work, K_SECONDS(2));
}

static void raw_stage_marker(int number)
{
    /* Visible even if LVGL stalls before its first write: a raw white 2 or 3.
     * Only the display queue touches the LCD and the shared pattern buffer.
     */
    static const uint8_t glyphs[2][7] = {
        {14, 17, 1, 2, 4, 8, 31}, {30, 1, 1, 14, 1, 1, 30}
    };
    for (int y = 0; y < 20; y++) {
        for (int x = 0; x < 20; x++) {
            int gx = (x - 5) / 2, gy = (y - 3) / 2;
            bool ink = x >= 5 && x < 15 && y >= 3 && y < 17 &&
                       (glyphs[number - 2][gy] & (1U << (4 - gx)));
            pattern[y * 20 + x] = ink ? 0xffff : 0x0000;
        }
    }
    struct display_buffer_descriptor desc = {
        .width = 20, .height = 20, .pitch = 20, .buf_size = 20 * 20 * 2,
        .frame_incomplete = false,
    };
    int ret = display_write(lcd, 0, 0, &desc, pattern);
    LOG_INF("Raw stage %d marker write returned %d", number, ret);
}

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
        checkpoint(10);
        /* Continue holding LVGL flush until the five-second color test ends. */
        pattern_y = 172;
        k_work_reschedule_for_queue(zmk_display_work_q(), &phase_work, K_SECONDS(5));
        return;
    }
    checkpoint(30);
    raw_stage_marker(3);
    checkpoint(31);
    lv_obj_t *screen = xiao147_stock_status_screen();
    checkpoint(32);
    if (screen == NULL) {
        LOG_ERR("Stage 3 failed to allocate status screen");
        return;
    }
    lv_screen_load(screen);
    checkpoint(33);
    lv_obj_delete(test_screen);
    test_screen = NULL;
    report_memory("Stage 3/3 stock OPERATOR");
    checkpoint(34);
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
        checkpoint(40);
        report_memory("Stage 3/3 complete menus and rings");
        checkpoint(41);
        return;
    }
    if (atomic_get(&stage) == 0 && pattern_y == 172) {
        checkpoint(20);
        raw_stage_marker(2);
        atomic_set(&stage, 1);
        lv_timer_t *timer = lv_display_get_refr_timer(lv_display_get_default());
        if (timer != NULL) { lv_timer_resume(timer); }
        checkpoint(21);
        lv_obj_invalidate(test_screen);
        checkpoint(22);
        report_memory("Stage 2/3 minimal LVGL");
        checkpoint(23);
        k_work_reschedule_for_queue(zmk_display_work_q(), &phase_work, K_SECONDS(5));
        LOG_INF("Stage 2/3 minimal LVGL render requested");
        /* Explicit handoff on the same queue, not a second LVGL thread. */
        checkpoint(24);
        lv_refr_now(NULL);
        checkpoint(25);
        LOG_INF("Stage 2/3 synchronous LVGL render returned");
        return;
    }
    phase_handler(work);
}

lv_obj_t *zmk_display_status_screen(void)
{
    xiao147_rotation_install();
    lv_display_t *display = lv_display_get_default();
    if (display == NULL) { return NULL; }
    lv_display_add_event_cb(display, raw_refresh_gate, LV_EVENT_REFR_REQUEST, NULL);
    lv_timer_t *timer = lv_display_get_refr_timer(display);
    if (timer != NULL) { lv_timer_pause(timer); }
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
    k_work_init_delayable(&heartbeat_work, heartbeat);
    checkpoint(1);
    report_memory("Stage 1/3 before stock UI allocation");
    checkpoint(2);
    k_work_schedule(&heartbeat_work, K_SECONDS(2));
    k_work_schedule_for_queue(zmk_display_work_q(), &phase_work, K_MSEC(100));
    return test_screen;
}
