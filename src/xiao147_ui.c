/* XIAO 1.47-inch dongle UI. LVGL operations run only in the ZMK display queue. */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include <lvgl.h>
#include <zephyr/device.h>
#include <zephyr/drivers/led.h>
#include <zephyr/input/input.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>

#include <zmk/battery.h>
#include <zmk/display.h>
#include "xiao147_display_test.h"
#include <zmk/event_manager.h>
#include <zmk/events/battery_state_changed.h>
#include <zmk/events/split_central_status_changed.h>
#include <zmk/usb.h>

#include "xiao147_ui.h"
#include "xiao147_rotation.h"
#include "xiao147_battery_rings.h"
#include "xiao147_rssi.h"
#include "xiao147_battery_colors.h"

LV_FONT_DECLARE(FG_Medium_20);
LV_FONT_DECLARE(FR_Medium_32);
LV_FONT_DECLARE(DINishExpanded_Light_36);
LOG_MODULE_REGISTER(xiao147_ui, LOG_LEVEL_INF);

#define SCREEN_W 320
#define SCREEN_H 172
#define COLOR_BG 0x10151e
#define COLOR_CARD 0x1a2432
#define COLOR_TEXT 0xe4ebf6
#define COLOR_MUTED 0x8295ae
#define COLOR_ACCENT 0x39bdec
#define COLOR_GREEN 0x78c79c
#define OFF_AFTER_MS 300000

#define DISP_BL DT_NODE_CHILD_IDX(DT_NODELABEL(disp_bl))

enum rotation_mode { ROTATION_AUTO, ROTATION_LANDSCAPE, ROTATION_PORTRAIT };
struct touch_sample { uint8_t fingers; uint16_t x; uint16_t y; };
struct peripheral_state {
    uint8_t battery[XIAO147_PERIPHERAL_COUNT];
    bool seen[XIAO147_PERIPHERAL_COUNT];
    bool connected[XIAO147_PERIPHERAL_COUNT];
};

K_MSGQ_DEFINE(touch_queue, sizeof(struct touch_sample), 32, 4);
K_MUTEX_DEFINE(peripheral_lock);
static struct peripheral_state peripheral;
static atomic_t page_atomic = ATOMIC_INIT(XIAO147_PAGE_STATUS);
static atomic_t sleeping = ATOMIC_INIT(0);
static atomic_t imu_mode = ATOMIC_INIT(0);
static atomic_t restore_landscape = ATOMIC_INIT(0);
static atomic_t auto_rotate_mode = ATOMIC_INIT(0);
static atomic_t auto_portrait = ATOMIC_INIT(0);
static uint32_t last_activity_ms;
static uint8_t brightness = 50;
static uint8_t applied_brightness = 255;
static bool usb_powered;
static enum rotation_mode rotation = ROTATION_LANDSCAPE;
static uint8_t previous_touch_fingers;
static struct touch_sample last_touch;

static lv_obj_t *panel;
static lv_obj_t *title_label;
static lv_obj_t *settings_group;
static lv_obj_t *settings_labels[5];
static lv_obj_t *settings_switches[4];
static lv_obj_t *settings_slider;
static lv_obj_t *brightness_value;
static lv_obj_t *devices_group;
static lv_obj_t *device_cards[4];
static lv_obj_t *device_names[4];
static lv_obj_t *device_signals[4];
static lv_obj_t *device_battery_frames[4];
static lv_obj_t *device_battery_fills[4];
static lv_obj_t *dongle_battery_bar;
static lv_obj_t *dongle_battery_label;
#define DONGLE_BATTERY_SEGMENTS 25
static lv_obj_t *dongle_battery_ticks;
static lv_obj_t *dongle_battery_segment[DONGLE_BATTERY_SEGMENTS];
static struct k_work init_work;
static struct k_work redraw_work;
static struct k_work touch_work;
static struct k_work_delayable init_retry_work;
static struct k_work_delayable periodic_work;

static void redraw(struct k_work *work);
static void apply_brightness(void);

static lv_obj_t *make_label(lv_obj_t *parent, int x, int y, int w, const char *text,
                            uint32_t color)
{
    lv_obj_t *obj = lv_label_create(parent);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_width(obj, w);
    lv_label_set_text(obj, text);
    lv_obj_set_style_text_color(obj, lv_color_hex(color), LV_PART_MAIN);
    lv_obj_set_style_text_font(obj, &lv_font_montserrat_14, LV_PART_MAIN);
    return obj;
}

static void set_switch(lv_obj_t *sw, bool enabled)
{
    if (enabled) { lv_obj_add_state(sw, LV_STATE_CHECKED); }
    else { lv_obj_remove_state(sw, LV_STATE_CHECKED); }
}

static void style_switch(lv_obj_t *sw)
{
    lv_obj_set_size(sw, 43, 23);
    lv_obj_set_style_radius(sw, 12, LV_PART_MAIN);
    lv_obj_set_style_bg_color(sw, lv_color_hex(0x3a3a3c), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(sw, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(sw, 12, LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(sw, lv_color_hex(0x34c759), LV_PART_INDICATOR | LV_STATE_CHECKED);
    lv_obj_set_style_bg_opa(sw, LV_OPA_COVER, LV_PART_INDICATOR);
    lv_obj_set_style_radius(sw, LV_RADIUS_CIRCLE, LV_PART_KNOB);
    lv_obj_set_style_bg_color(sw, lv_color_hex(0xffffff), LV_PART_KNOB);
    lv_obj_set_style_bg_opa(sw, LV_OPA_COVER, LV_PART_KNOB);
    lv_obj_set_style_pad_all(sw, -2, LV_PART_KNOB);
    lv_obj_set_style_border_width(sw, 0, LV_PART_MAIN);
    lv_obj_clear_flag(sw, LV_OBJ_FLAG_CLICKABLE);
}

static void layout_settings(bool portrait)
{
    lv_obj_set_size(settings_group, portrait ? SCREEN_H : SCREEN_W,
                    portrait ? SCREEN_W : SCREEN_H);
    lv_obj_set_pos(settings_labels[0], 12, portrait ? 38 : 25);
    lv_obj_set_pos(brightness_value, portrait ? 126 : 266, portrait ? 38 : 25);
    lv_obj_set_pos(settings_slider, 14, portrait ? 77 : 52);
    lv_obj_set_size(settings_slider, portrait ? 142 : 287, 6);
    if (portrait) {
        const int row_y[4] = {110, 151, 192, 238};
        for (int i = 0; i < 4; i++) {
            lv_obj_set_pos(settings_labels[i + 1], 12, row_y[i] + 3);
            lv_obj_set_pos(settings_switches[i], 116, row_y[i]);
        }
    } else {
        const int label_x[4] = {12, 12, 166, 12};
        const int label_y[4] = {72, 108, 108, 140};
        const int switch_x[4] = {264, 107, 264, 264};
        const int switch_y[4] = {68, 104, 104, 136};
        for (int i = 0; i < 4; i++) {
            lv_obj_set_pos(settings_labels[i + 1], label_x[i], label_y[i]);
            lv_obj_set_pos(settings_switches[i], switch_x[i], switch_y[i]);
        }
    }
}

static void draw_settings(void)
{
    const bool portrait = xiao147_rotation_is_portrait();
    layout_settings(portrait);
    lv_label_set_text(title_label, "Display Settings");
    char text[8];
    uint8_t limit = usb_powered ? 100 : 50;
    lv_slider_set_range(settings_slider, 1, limit);
    lv_slider_set_value(settings_slider, brightness, LV_ANIM_OFF);
    snprintf(text, sizeof(text), "%u%%", brightness);
    lv_label_set_text(brightness_value, text);
    set_switch(settings_switches[0], rotation == ROTATION_AUTO);
    set_switch(settings_switches[1], rotation == ROTATION_LANDSCAPE && !xiao147_ui_imu_enabled());
    set_switch(settings_switches[2], rotation == ROTATION_PORTRAIT);
    set_switch(settings_switches[3], xiao147_ui_imu_enabled());
    /* Six-axis mode owns portrait orientation, so its lock controls are read-only. */
    for (int i = 0; i < 3; i++) {
        lv_obj_set_style_opa(settings_switches[i], xiao147_ui_imu_enabled() ?
                             LV_OPA_50 : LV_OPA_COVER, LV_PART_MAIN);
    }
}

static void layout_devices(bool portrait)
{
    lv_obj_set_size(devices_group, portrait ? SCREEN_H : SCREEN_W,
                    portrait ? SCREEN_W : SCREEN_H);
    for (int row = 0; row < 4; row++) {
        lv_obj_set_pos(device_cards[row], portrait ? 9 : 10,
                       portrait ? 44 + row * 58 : 25 + row * 35);
        lv_obj_set_size(device_cards[row], portrait ? 154 : 300, portrait ? 36 : 31);
        lv_obj_set_pos(device_battery_frames[row], portrait ? 6 : 8,
                       portrait ? 12 : 10);
        lv_obj_set_pos(device_signals[row], portrait ? 38 : 49,
                       portrait ? 9 : 7);
        lv_obj_set_width(device_signals[row], portrait ? 52 : 65);
        lv_obj_set_pos(device_names[row], portrait ? 94 : 125,
                       portrait ? 9 : 7);
        lv_obj_set_width(device_names[row], portrait ? 54 : 165);
        lv_obj_set_style_text_font(device_signals[row], &lv_font_montserrat_12,
                                   LV_PART_MAIN);
        lv_obj_set_style_text_letter_space(device_signals[row], -1, LV_PART_MAIN);
        lv_obj_set_style_text_font(device_names[row],
                                   portrait ? &lv_font_montserrat_12 :
                                              &lv_font_montserrat_14, LV_PART_MAIN);
    }
}

static uint32_t rssi_color(int8_t value)
{
    /* t-ogura zmk-config-prospector main pins module v2.2.3. Its
     * custom_status_screen.c ks_get_rssi_color colors the signal bar.
     * Apply that palette to dBm text here, with the requested four bands:
     * merge the two green levels; valid weak readings stay red, unknown gray. */
    if (value >= -60) { return 0x00cc66; }
    if (value >= -70) { return 0xffcc00; }
    if (value >= -80) { return 0xff8800; }
    return 0xff3333;
}

static void draw_devices(void)
{
    layout_devices(xiao147_rotation_is_portrait());
    lv_label_set_text(title_label, "Active Devices");
    struct peripheral_state copy;
    k_mutex_lock(&peripheral_lock, K_FOREVER);
    copy = peripheral;
    k_mutex_unlock(&peripheral_lock);
    int8_t signal_values[XIAO147_RSSI_COUNT];
    bool signal_valid[XIAO147_RSSI_COUNT];
    xiao147_rssi_snapshot(signal_values, signal_valid);
    static const char *const names[] = {"DONGLE", "LISM LEFT", "LISM RIGHT", "JOYSTICK"};
    static const char *const short_names[] = {"DONGLE", "LEFT", "RIGHT", "JOY"};
    bool portrait = xiao147_rotation_is_portrait();
    uint8_t joystick_slot = xiao147_active_joystick_slot(copy.connected);
    for (int row = 0; row < 4; row++) {
        uint8_t source_slot = row == 0 ? 0 : row == 3 ? joystick_slot : row - 1;
        uint8_t signal_index = row == 0 ? 0 : source_slot + 1;
        bool active = row == 0 || copy.connected[source_slot];
        uint8_t level = 0;
        bool known = false;
        if (row == 0) {
            level = zmk_battery_state_of_charge();
            known = true;
        } else if (active && copy.seen[source_slot]) {
            level = copy.battery[source_slot];
            known = true;
        }
        lv_label_set_text(device_names[row], portrait ? short_names[row] : names[row]);
        char signal_text[12];
        bool has_signal = signal_valid[signal_index] && active;
        if (has_signal) {
            snprintf(signal_text, sizeof(signal_text), "%ddBm", signal_values[signal_index]);
        } else {
            snprintf(signal_text, sizeof(signal_text), "--dBm");
        }
        lv_label_set_text(device_signals[row], signal_text);
        lv_obj_set_style_text_color(device_signals[row],
                                    lv_color_hex(has_signal ? rssi_color(signal_values[signal_index]) :
                                                 0x606060), LV_PART_MAIN);
        lv_color_t battery_color = lv_color_hex(known ? xiao147_battery_color(level) :
                                                        0x485362);
        lv_obj_set_style_bg_color(device_battery_fills[row], battery_color, LV_PART_MAIN);
        lv_obj_set_width(device_battery_fills[row], known ? 2 + level * 18 / 100 : 0);
        lv_obj_set_style_border_color(device_battery_frames[row],
                                      known ? battery_color : lv_color_hex(0x485362),
                                      LV_PART_MAIN);
        lv_obj_set_style_text_color(device_names[row],
                                    active ? lv_color_hex(COLOR_TEXT) :
                                             lv_color_hex(COLOR_MUTED), LV_PART_MAIN);
    }
}

static void layout_status(bool portrait)
{
    lv_obj_t *screen = lv_scr_act();
    /* The pinned OPERATOR layout creates these five children in this order. */
    if (lv_obj_get_child_cnt(screen) < 5) { return; }
    lv_obj_t *modifiers = lv_obj_get_child(screen, 0);
    lv_obj_set_pos(modifiers, portrait ? 6 : 10, portrait ? 7 : 2);
    lv_obj_set_size(modifiers, portrait ? 160 : 300, portrait ? 18 : 24);
    for (uint32_t i = 0; i < lv_obj_get_child_cnt(modifiers); i++) {
        lv_obj_t *child = lv_obj_get_child(modifiers, i);
        if (i % 2 == 0) {
            lv_obj_set_style_text_font(child,
                   portrait ? &lv_font_montserrat_14 : &FG_Medium_20, LV_PART_MAIN);
        } else {
            lv_obj_set_height(child, portrait ? 16 : 24);
        }
    }

    lv_obj_t *wpm = lv_obj_get_child(screen, 1);
    lv_obj_set_pos(wpm, portrait ? 4 : 10, portrait ? 33 : 28);
    /* Native geometry avoids the ARGB transform layer (260*65*4 = 67600
     * bytes before widgets, already larger than our entire 64 KiB LVGL heap).
     */
    int meter_width = portrait ? 164 : 300;
    int meter_height = portrait ? 75 : 65;
    lv_obj_set_size(wpm, meter_width, meter_height);
    uint32_t wpm_children = lv_obj_get_child_cnt(wpm);
    if (wpm_children > 3) {
        uint32_t bars = wpm_children - 3; /* bars, peak, WPM label, layer label */
        for (uint32_t i = 0; i < bars; i++) {
            int gap = portrait ? 1 : 2;
            int x = i * (meter_width + gap) / bars;
            int end = (i + 1) * (meter_width + gap) / bars - gap;
            lv_obj_t *bar = lv_obj_get_child(wpm, i);
            lv_obj_set_pos(bar, x, 0);
            lv_obj_set_size(bar, end - x, meter_height);
        }
        lv_obj_set_height(lv_obj_get_child(wpm, bars), meter_height);
        lv_obj_t *number = lv_obj_get_child(wpm, wpm_children - 2);
        lv_obj_t *name = lv_obj_get_child(wpm, wpm_children - 1);
        lv_obj_set_style_text_font(number, portrait ? &FG_Medium_20 : &FR_Medium_32, LV_PART_MAIN);
        lv_obj_set_style_text_font(name, portrait ? &FG_Medium_20 : &DINishExpanded_Light_36, LV_PART_MAIN);
        lv_obj_align(number, LV_ALIGN_TOP_LEFT, 0, 0);
        lv_obj_align(name, LV_ALIGN_BOTTOM_RIGHT, 0, 0);
    }

    lv_obj_t *layers = lv_obj_get_child(screen, 2);
    lv_obj_set_pos(layers, portrait ? 7 : 10, portrait ? 121 : 98);
    int layer_width = portrait ? 158 : 300;
    lv_obj_set_size(layers, layer_width, 6);
    uint32_t layer_count = lv_obj_get_child_cnt(layers);
    for (uint32_t i = 0; i < layer_count; i++) {
        int x = i * (layer_width + 3) / layer_count;
        int end = (i + 1) * (layer_width + 3) / layer_count - 3;
        lv_obj_t *dot = lv_obj_get_child(layers, i);
        lv_obj_set_pos(dot, x, 0);
        lv_obj_set_size(dot, end - x, 6);
    }

    /* The three-peripheral stock widget uses vertical bars; our matching
     * compact arcs live in their own status-only object. */
    lv_obj_add_flag(lv_obj_get_child(screen, 3), LV_OBJ_FLAG_HIDDEN);
    lv_obj_t *output = lv_obj_get_child(screen, 4);
    lv_obj_set_pos(output, portrait ? 8 : 202, portrait ? 144 : 109);
    lv_obj_set_size(output, portrait ? 156 : 116, portrait ? 65 : 62);
    uint32_t output_children = lv_obj_get_child_cnt(output);
    if (output_children >= 3) {
        for (uint32_t i = 0; i < 2; i++) {
            lv_obj_t *button = lv_obj_get_child(output, i);
            lv_obj_set_pos(button, portrait ? (int)i * 79 : (int)i * 58, 0);
            lv_obj_set_size(button, portrait ? 77 : 56, 29);
        }
        uint32_t profile_count = output_children - 2;
        int width = (portrait ? 156 : 116) - ((int)profile_count - 1) * 2;
        width /= profile_count;
        int start_x = portrait ? ((156 - ((int)profile_count * width +
                                         ((int)profile_count - 1) * 2)) / 2) : 0;
        for (uint32_t i = 0; i < profile_count; i++) {
            lv_obj_t *slot = lv_obj_get_child(output, i + 2);
            lv_obj_set_pos(slot, start_x + (int)i * (width + 2), portrait ? 36 : 33);
            lv_obj_set_size(slot, width, 29);
        }
    }
    lv_obj_set_style_transform_pivot_x(output, 0, LV_PART_MAIN);
    lv_obj_set_style_transform_pivot_y(output, 0, LV_PART_MAIN);
    lv_obj_set_style_transform_scale_x(output, 256, LV_PART_MAIN);
    lv_obj_set_style_transform_scale_y(output, 256, LV_PART_MAIN);

    xiao147_battery_rings_set_portrait(portrait);
    lv_obj_set_pos(dongle_battery_bar, 10, 160);
    lv_obj_set_size(dongle_battery_bar, 140, 6);
    lv_obj_set_pos(dongle_battery_label, portrait ? 123 : 166,
                   portrait ? 288 : 150);
    lv_obj_set_width(dongle_battery_label, portrait ? 42 : 35);
    lv_obj_set_style_text_align(dongle_battery_label,
                                portrait ? LV_TEXT_ALIGN_RIGHT : LV_TEXT_ALIGN_LEFT,
                                LV_PART_MAIN);
}

static void redraw(struct k_work *work)
{
    if (panel == NULL) { return; }
    /* The left button is the authoritative IMU-mode toggle. */
    if (xiao147_ui_imu_enabled()) { rotation = ROTATION_PORTRAIT; }
    else if (atomic_set(&restore_landscape, 0)) { rotation = ROTATION_LANDSCAPE; }
    const bool portrait = rotation == ROTATION_PORTRAIT ||
                          (rotation == ROTATION_AUTO && atomic_get(&auto_portrait));
    enum xiao147_page page = atomic_get(&page_atomic);
    static enum xiao147_page drawn_page = XIAO147_PAGE_STATUS;
    static bool drawn_portrait;
    if (page != drawn_page || portrait != drawn_portrait) {
        k_msgq_purge(&touch_queue);
        previous_touch_fingers = 0;
        last_touch = (struct touch_sample){0};
        drawn_page = page;
        drawn_portrait = portrait;
        LOG_INF("UI page %d, portrait %d, IMU %d", page, portrait, xiao147_ui_imu_enabled());
    }
    xiao147_rotation_set(portrait);
    lv_obj_set_size(panel, portrait ? SCREEN_H : SCREEN_W,
                    portrait ? SCREEN_W : SCREEN_H);
    lv_obj_set_pos(title_label, 0, portrait ? 8 : 3);
    lv_obj_set_width(title_label, portrait ? SCREEN_H : SCREEN_W);
    lv_obj_set_style_text_align(title_label, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    layout_status(portrait);
    xiao147_rssi_set_active(page == XIAO147_PAGE_DEVICES && !atomic_get(&sleeping));
    if (page == XIAO147_PAGE_STATUS) {
        lv_obj_add_flag(panel, LV_OBJ_FLAG_HIDDEN);
        if (portrait) {
            lv_obj_add_flag(dongle_battery_bar, LV_OBJ_FLAG_HIDDEN);
            lv_obj_clear_flag(dongle_battery_ticks, LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_clear_flag(dongle_battery_bar, LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(dongle_battery_ticks, LV_OBJ_FLAG_HIDDEN);
        }
        lv_obj_clear_flag(dongle_battery_label, LV_OBJ_FLAG_HIDDEN);
        xiao147_battery_rings_set_visible(true);
    } else {
        lv_obj_add_flag(dongle_battery_bar, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(dongle_battery_ticks, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(dongle_battery_label, LV_OBJ_FLAG_HIDDEN);
        xiao147_battery_rings_set_visible(false);
        lv_obj_clear_flag(panel, LV_OBJ_FLAG_HIDDEN);
        if (page == XIAO147_PAGE_SETTINGS) {
            lv_obj_clear_flag(settings_group, LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(devices_group, LV_OBJ_FLAG_HIDDEN);
            draw_settings();
        } else {
            lv_obj_add_flag(settings_group, LV_OBJ_FLAG_HIDDEN);
            lv_obj_clear_flag(devices_group, LV_OBJ_FLAG_HIDDEN);
            draw_devices();
        }
    }
    uint8_t dongle_level = zmk_battery_state_of_charge();
    lv_bar_set_value(dongle_battery_bar, dongle_level, LV_ANIM_OFF);
    uint32_t dongle_color = xiao147_battery_color(dongle_level);
    lv_obj_set_style_bg_color(dongle_battery_bar, lv_color_hex(dongle_color),
                              LV_PART_INDICATOR);
    uint8_t active_segments = (dongle_level * DONGLE_BATTERY_SEGMENTS + 99) / 100;
    for (uint8_t i = 0; i < DONGLE_BATTERY_SEGMENTS; i++) {
        lv_obj_set_style_bg_color(dongle_battery_segment[i],
                                  lv_color_hex(i < active_segments ? dongle_color : 0x282c30),
                                  LV_PART_MAIN);
    }
    lv_obj_set_style_text_color(dongle_battery_label, lv_color_hex(dongle_color),
                                LV_PART_MAIN);
    char text[8];
    snprintf(text, sizeof(text), "%u%%", dongle_level);
    lv_label_set_text(dongle_battery_label, text);
    apply_brightness();
}

static void apply_brightness(void)
{
    const struct device *pwm_leds = DEVICE_DT_GET_ONE(pwm_leds);
    uint8_t target = atomic_get(&sleeping) ? 0 : brightness;
    if (target == applied_brightness || !device_is_ready(pwm_leds)) { return; }
    int ret = led_set_brightness(pwm_leds, DISP_BL, target);
    LOG_INF("Backlight USB %d, target %u%%, PWM result %d", usb_powered, target, ret);
    if (ret == 0) {
        applied_brightness = target;
    }
}

static void periodic(struct k_work *work)
{
    bool powered = zmk_usb_is_powered();
    if (powered != usb_powered) {
        usb_powered = powered;
        brightness = powered ? 100 : 50;
        applied_brightness = 255;
        if (powered) { atomic_set(&sleeping, 0); }
    }
    if (!powered && k_uptime_get_32() - last_activity_ms >= OFF_AFTER_MS) {
        atomic_set(&sleeping, 1);
    }
    apply_brightness();
    redraw(NULL);
    k_work_reschedule_for_queue(zmk_display_work_q(), &periodic_work, K_SECONDS(1));
}

static void touch_handler(struct k_work *work)
{
    struct touch_sample sample;
    while (k_msgq_get(&touch_queue, &sample, K_NO_WAIT) == 0) {
        if (atomic_get(&page_atomic) != XIAO147_PAGE_SETTINGS) {
            previous_touch_fingers = sample.fingers;
            continue;
        }
        bool portrait = xiao147_rotation_is_portrait();
        if (sample.fingers == 1 && sample.y >= (portrait ? 64 : 42) &&
            sample.y <= (portrait ? 94 : 66)) {
            int x = sample.x;
            if (x < 14) { x = 14; }
            if (x > (portrait ? 156 : 301)) { x = portrait ? 156 : 301; }
            int value = (x - 14) * (usb_powered ? 100 : 50) / (portrait ? 142 : 287);
            brightness = value < 1 ? 1 : value;
            apply_brightness();
            redraw(NULL);
        } else if (sample.fingers == 0 && previous_touch_fingers == 1) {
            uint16_t y = last_touch.y;
            uint16_t x = last_touch.x;
            if (y >= (portrait ? 103 : 65) && y < (portrait ? 141 : 98)) {
                if (!xiao147_ui_imu_enabled()) {
                    rotation = rotation == ROTATION_AUTO ? ROTATION_LANDSCAPE : ROTATION_AUTO;
                    atomic_set(&auto_rotate_mode, rotation == ROTATION_AUTO);
                }
            } else if (y >= (portrait ? 145 : 99) && y < (portrait ? 226 : 133)) {
                if (!xiao147_ui_imu_enabled()) {
                    rotation = portrait ? (y < 185 ? ROTATION_LANDSCAPE : ROTATION_PORTRAIT) :
                              (x < SCREEN_W / 2 ? ROTATION_LANDSCAPE : ROTATION_PORTRAIT);
                    atomic_set(&auto_rotate_mode, 0);
                }
            } else if (y >= (portrait ? 232 : 134) && y < (portrait ? 274 : 170)) {
                xiao147_ui_toggle_imu();
            }
            redraw(NULL);
        }
        if (sample.fingers != 0) { last_touch = sample; }
        previous_touch_fingers = sample.fingers;
    }
}

static void create_ui(struct k_work *work)
{
    lv_obj_t *screen = lv_scr_act();
    if (screen == NULL) { return; }
    xiao147_rotation_install();

    dongle_battery_bar = lv_bar_create(screen);
    lv_bar_set_range(dongle_battery_bar, 0, 100);
    lv_obj_set_pos(dongle_battery_bar, 10, 158);
    lv_obj_set_size(dongle_battery_bar, 140, 6);
    lv_obj_set_style_radius(dongle_battery_bar, 3, LV_PART_MAIN);
    lv_obj_set_style_radius(dongle_battery_bar, 3, LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(dongle_battery_bar, lv_color_hex(0x282c30), LV_PART_MAIN);
    lv_obj_set_style_bg_color(dongle_battery_bar, lv_color_hex(0x54806c), LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(dongle_battery_bar, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(dongle_battery_bar, LV_OPA_COVER, LV_PART_INDICATOR);
    dongle_battery_label = make_label(screen, 166, 150, 35, "--%", COLOR_GREEN);
    lv_obj_set_style_text_font(dongle_battery_label, &lv_font_montserrat_12,
                               LV_PART_MAIN);
    lv_obj_set_style_text_letter_space(dongle_battery_label, -1, LV_PART_MAIN);
    dongle_battery_ticks = lv_obj_create(screen);
    lv_obj_set_pos(dongle_battery_ticks, 10, 288);
    lv_obj_set_size(dongle_battery_ticks, 99, 16);
    lv_obj_set_style_bg_opa(dongle_battery_ticks, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(dongle_battery_ticks, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(dongle_battery_ticks, 0, LV_PART_MAIN);
    for (uint8_t i = 0; i < DONGLE_BATTERY_SEGMENTS; i++) {
        lv_obj_t *segment = lv_obj_create(dongle_battery_ticks);
        dongle_battery_segment[i] = segment;
        lv_obj_set_pos(segment, i * 4, 0);
        lv_obj_set_size(segment, 3, 16);
        lv_obj_set_style_bg_opa(segment, LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_bg_color(segment, lv_color_hex(0x282c30), LV_PART_MAIN);
        lv_obj_set_style_border_width(segment, 0, LV_PART_MAIN);
        lv_obj_set_style_radius(segment, 0, LV_PART_MAIN);
        lv_obj_set_style_pad_all(segment, 0, LV_PART_MAIN);
    }
    lv_obj_add_flag(dongle_battery_ticks, LV_OBJ_FLAG_HIDDEN);

    panel = lv_obj_create(screen);
    lv_obj_set_pos(panel, 0, 0);
    lv_obj_set_size(panel, SCREEN_W, SCREEN_H);
    lv_obj_set_style_bg_color(panel, lv_color_hex(COLOR_BG), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(panel, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(panel, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(panel, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(panel, 0, LV_PART_MAIN);
    lv_obj_clear_flag(panel, LV_OBJ_FLAG_SCROLLABLE);

    title_label = make_label(panel, 10, 5, 300, "", COLOR_TEXT);
    settings_group = lv_obj_create(panel);
    devices_group = lv_obj_create(panel);
    lv_obj_t *groups[] = {settings_group, devices_group};
    for (int i = 0; i < 2; i++) {
        lv_obj_set_pos(groups[i], 0, 0);
        lv_obj_set_size(groups[i], SCREEN_W, SCREEN_H);
        lv_obj_set_style_bg_opa(groups[i], LV_OPA_TRANSP, LV_PART_MAIN);
        lv_obj_set_style_border_width(groups[i], 0, LV_PART_MAIN);
        lv_obj_set_style_pad_all(groups[i], 0, LV_PART_MAIN);
        lv_obj_clear_flag(groups[i], LV_OBJ_FLAG_SCROLLABLE);
    }
    const char *const setting_text[] = {
        "Brightness", "Auto rotate", "Landscape", "Portrait", "6-axis mouse"
    };
    for (int i = 0; i < 5; i++) {
        settings_labels[i] = make_label(settings_group, 12, 25 + i * 28, 115,
                                        setting_text[i], COLOR_TEXT);
    }
    brightness_value = make_label(settings_group, 266, 25, 45, "50%", COLOR_ACCENT);
    settings_slider = lv_slider_create(settings_group);
    lv_slider_set_range(settings_slider, 1, 100);
    lv_obj_set_pos(settings_slider, 14, 52);
    lv_obj_set_size(settings_slider, 287, 6);
    lv_obj_set_style_bg_color(settings_slider, lv_color_hex(0x3a3a3c), LV_PART_MAIN);
    lv_obj_set_style_bg_color(settings_slider, lv_color_hex(0x007aff), LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(settings_slider, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(settings_slider, LV_OPA_COVER, LV_PART_INDICATOR);
    lv_obj_set_style_radius(settings_slider, 3, LV_PART_MAIN);
    lv_obj_set_style_radius(settings_slider, 3, LV_PART_INDICATOR);
    lv_obj_set_style_radius(settings_slider, LV_RADIUS_CIRCLE, LV_PART_KNOB);
    lv_obj_set_style_bg_color(settings_slider, lv_color_hex(0xffffff), LV_PART_KNOB);
    lv_obj_set_style_bg_opa(settings_slider, LV_OPA_COVER, LV_PART_KNOB);
    lv_obj_set_style_pad_all(settings_slider, 8, LV_PART_KNOB);
    lv_obj_clear_flag(settings_slider, LV_OBJ_FLAG_CLICKABLE);
    for (int i = 0; i < 4; i++) {
        settings_switches[i] = lv_switch_create(settings_group);
        style_switch(settings_switches[i]);
    }
    for (int i = 0; i < 4; i++) {
        device_cards[i] = lv_obj_create(devices_group);
        lv_obj_set_style_bg_color(device_cards[i],
                     lv_color_hex(i == 0 ? 0x284361 : COLOR_CARD), LV_PART_MAIN);
        lv_obj_set_style_bg_opa(device_cards[i], LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_border_width(device_cards[i], 1, LV_PART_MAIN);
        lv_obj_set_style_border_color(device_cards[i],
                     lv_color_hex(i == 0 ? 0x4a90e2 : 0x303c4b), LV_PART_MAIN);
        lv_obj_set_style_radius(device_cards[i], 6, LV_PART_MAIN);
        lv_obj_set_style_pad_all(device_cards[i], 0, LV_PART_MAIN);
        lv_obj_clear_flag(device_cards[i], LV_OBJ_FLAG_SCROLLABLE);
        device_battery_frames[i] = lv_obj_create(device_cards[i]);
        lv_obj_set_size(device_battery_frames[i], 27, 12);
        lv_obj_set_style_bg_opa(device_battery_frames[i], LV_OPA_TRANSP, LV_PART_MAIN);
        lv_obj_set_style_border_width(device_battery_frames[i], 1, LV_PART_MAIN);
        lv_obj_set_style_radius(device_battery_frames[i], 2, LV_PART_MAIN);
        lv_obj_set_style_pad_all(device_battery_frames[i], 0, LV_PART_MAIN);
        lv_obj_clear_flag(device_battery_frames[i], LV_OBJ_FLAG_SCROLLABLE);
        device_battery_fills[i] = lv_obj_create(device_battery_frames[i]);
        lv_obj_set_pos(device_battery_fills[i], 2, 2);
        lv_obj_set_size(device_battery_fills[i], 0, 6);
        lv_obj_set_style_border_width(device_battery_fills[i], 0, LV_PART_MAIN);
        lv_obj_set_style_radius(device_battery_fills[i], 1, LV_PART_MAIN);
        lv_obj_set_style_bg_opa(device_battery_fills[i], LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_pad_all(device_battery_fills[i], 0, LV_PART_MAIN);
        device_signals[i] = make_label(device_cards[i], 49, 7, 65, "--dBm", COLOR_MUTED);
        device_names[i] = make_label(device_cards[i], 125, 7, 165, "", COLOR_TEXT);
    }
    last_activity_ms = k_uptime_get_32();
    usb_powered = zmk_usb_is_powered();
    brightness = usb_powered ? 100 : 50;
    apply_brightness();
    redraw(NULL);
    k_work_reschedule_for_queue(zmk_display_work_q(), &periodic_work, K_SECONDS(1));
}

static void init_retry(struct k_work *work)
{
    if (!zmk_display_is_initialized() || !xiao147_display_ui_ready()) {
        k_work_reschedule(&init_retry_work, K_MSEC(250));
        return;
    }
    k_work_submit_to_queue(zmk_display_work_q(), &init_work);
}

enum xiao147_page xiao147_ui_page(void)
{
    return (enum xiao147_page)atomic_get(&page_atomic);
}

bool xiao147_ui_portrait(void)
{
    return xiao147_rotation_is_portrait();
}

bool xiao147_ui_imu_enabled(void)
{
    return atomic_get(&imu_mode) != 0;
}

bool xiao147_ui_auto_rotate_enabled(void)
{
    return atomic_get(&auto_rotate_mode) != 0;
}

void xiao147_ui_set_auto_orientation(bool portrait)
{
    if (atomic_set(&auto_portrait, portrait) != portrait &&
        zmk_display_is_initialized()) {
        k_work_submit_to_queue(zmk_display_work_q(), &redraw_work);
    }
}

void xiao147_ui_toggle_imu(void)
{
    atomic_val_t old;
    do {
        old = atomic_get(&imu_mode);
    } while (!atomic_cas(&imu_mode, old, !old));
    atomic_set(&auto_rotate_mode, 0);
    if (old != 0) { atomic_set(&restore_landscape, 1); }
    last_activity_ms = k_uptime_get_32();
    atomic_set(&sleeping, 0);
    if (zmk_display_is_initialized()) {
        k_work_submit_to_queue(zmk_display_work_q(), &redraw_work);
    }
}

void xiao147_ui_next_page(void)
{
    last_activity_ms = k_uptime_get_32();
    if (atomic_set(&sleeping, 0)) {
        if (zmk_display_is_initialized()) { k_work_submit_to_queue(zmk_display_work_q(), &redraw_work); }
        return;
    }
    atomic_val_t current, next;
    do {
        current = atomic_get(&page_atomic);
        next = (current + 1) % 3;
    } while (!atomic_cas(&page_atomic, current, next));
    if (zmk_display_is_initialized()) { k_work_submit_to_queue(zmk_display_work_q(), &redraw_work); }
}

bool xiao147_ui_wake_on_touch(void)
{
    last_activity_ms = k_uptime_get_32();
    if (atomic_set(&sleeping, 0)) {
        if (zmk_display_is_initialized()) { k_work_submit_to_queue(zmk_display_work_q(), &redraw_work); }
        return true;
    }
    return false;
}

bool xiao147_ui_wake_on_motion(void)
{
    last_activity_ms = k_uptime_get_32();
    if (atomic_set(&sleeping, 0)) {
        if (zmk_display_is_initialized()) {
            k_work_submit_to_queue(zmk_display_work_q(), &redraw_work);
        }
        return true;
    }
    return false;
}

void xiao147_ui_touch(uint8_t fingers, uint16_t x, uint16_t y)
{
    struct touch_sample sample = {.fingers = fingers, .x = x, .y = y};
    if (k_msgq_put(&touch_queue, &sample, K_NO_WAIT) != 0) {
        struct touch_sample discarded;
        k_msgq_get(&touch_queue, &discarded, K_NO_WAIT);
        k_msgq_put(&touch_queue, &sample, K_NO_WAIT);
    }
    if (zmk_display_is_initialized()) { k_work_submit_to_queue(zmk_display_work_q(), &touch_work); }
}

static void menu_button_input(struct input_event *evt, void *user_data)
{
    LOG_INF("USR2 type %u code %u value %d", evt->type, evt->code, evt->value);
    if (evt->type == INPUT_EV_KEY && evt->code == INPUT_BTN_1 && evt->value == 1) {
        xiao147_ui_next_page();
    }
}
INPUT_CALLBACK_DEFINE(DEVICE_DT_GET(DT_NODELABEL(xiao_touch_menu_button)), menu_button_input, NULL);

static void imu_button_input(struct input_event *evt, void *user_data)
{
    LOG_INF("USR1 type %u code %u value %d", evt->type, evt->code, evt->value);
    if (evt->type == INPUT_EV_KEY && evt->code == INPUT_BTN_0 && evt->value == 1) {
        xiao147_ui_toggle_imu();
    }
}
INPUT_CALLBACK_DEFINE(DEVICE_DT_GET(DT_NODELABEL(xiao_touch_left_button)), imu_button_input, NULL);

static int peripheral_event_listener(const zmk_event_t *eh)
{
    const struct zmk_peripheral_battery_state_changed *battery =
        as_zmk_peripheral_battery_state_changed(eh);
    const struct zmk_split_central_status_changed *connection =
        as_zmk_split_central_status_changed(eh);
    k_mutex_lock(&peripheral_lock, K_FOREVER);
    if (battery && battery->source < XIAO147_PERIPHERAL_COUNT) {
        peripheral.battery[battery->source] = battery->state_of_charge;
        peripheral.seen[battery->source] = true;
    }
    if (connection && connection->slot < XIAO147_PERIPHERAL_COUNT) {
        peripheral.connected[connection->slot] = connection->connected;
        if (!connection->connected) { peripheral.seen[connection->slot] = false; }
    }
    k_mutex_unlock(&peripheral_lock);
    if (connection && !connection->connected) {
        xiao147_rssi_invalidate_slot(connection->slot);
    }
    if (zmk_display_is_initialized()) { k_work_submit_to_queue(zmk_display_work_q(), &redraw_work); }
    return ZMK_EV_EVENT_BUBBLE;
}
ZMK_LISTENER(xiao147_peripheral_state, peripheral_event_listener);
ZMK_SUBSCRIPTION(xiao147_peripheral_state, zmk_peripheral_battery_state_changed);
ZMK_SUBSCRIPTION(xiao147_peripheral_state, zmk_split_central_status_changed);

static int xiao147_ui_init(void)
{
    k_work_init(&init_work, create_ui);
    k_work_init(&redraw_work, redraw);
    k_work_init(&touch_work, touch_handler);
    k_work_init_delayable(&init_retry_work, init_retry);
    k_work_init_delayable(&periodic_work, periodic);
    k_work_schedule(&init_retry_work, K_SECONDS(1));
    return 0;
}
SYS_INIT(xiao147_ui_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
