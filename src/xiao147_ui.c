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
#include <zephyr/sys/atomic.h>

#include <zmk/battery.h>
#include <zmk/display.h>
#include <zmk/event_manager.h>
#include <zmk/events/battery_state_changed.h>
#include <zmk/events/split_central_status_changed.h>
#include <zmk/usb.h>

#include "xiao147_ui.h"
#include "xiao147_rotation.h"
#include "xiao147_battery_rings.h"

LV_FONT_DECLARE(FG_Medium_20);

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
    uint8_t battery[3];
    bool seen[3];
    bool connected[3];
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
        lv_obj_set_style_text_font(device_names[row],
                                   portrait ? &lv_font_montserrat_12 :
                                              &lv_font_montserrat_14, LV_PART_MAIN);
    }
}

static void draw_devices(void)
{
    layout_devices(xiao147_rotation_is_portrait());
    lv_label_set_text(title_label, "Active Devices");
    struct peripheral_state copy;
    k_mutex_lock(&peripheral_lock, K_FOREVER);
    copy = peripheral;
    k_mutex_unlock(&peripheral_lock);
    static const char *const names[] = {"DONGLE", "LISM LEFT", "LISM RIGHT", "JOYSTICK"};
    static const char *const short_names[] = {"DONGLE", "LEFT", "RIGHT", "JOY"};
    bool portrait = xiao147_rotation_is_portrait();
    for (int row = 0; row < 4; row++) {
        bool active = row == 0 || copy.connected[row - 1];
        uint8_t level = 0;
        bool known = false;
        if (row == 0) {
            level = zmk_battery_state_of_charge();
            known = true;
        } else if (active && copy.seen[row - 1]) {
            level = copy.battery[row - 1];
            known = true;
        }
        lv_label_set_text(device_names[row], portrait ? short_names[row] : names[row]);
        /* Split-central status lacks RSSI, unlike the independent scanner reference. */
        lv_label_set_text(device_signals[row], "--dBm");
        lv_color_t battery_color = lv_color_hex(!known ? 0x485362 :
                                               level <= 20 ? 0xe46464 :
                                               level <= 50 ? 0xf5be57 : 0x63cf70);
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
    lv_obj_set_pos(wpm, portrait ? 4 : 30, portrait ? 39 : 28);
    lv_obj_set_size(wpm, 260, portrait ? 90 : 55);
    lv_obj_set_style_transform_pivot_x(wpm, 0, LV_PART_MAIN);
    lv_obj_set_style_transform_pivot_y(wpm, 0, LV_PART_MAIN);
    lv_obj_set_style_transform_scale_x(wpm, portrait ? 159 : 256, LV_PART_MAIN);
    lv_obj_set_style_transform_scale_y(wpm, portrait ? 159 : 256, LV_PART_MAIN);

    lv_obj_t *layers = lv_obj_get_child(screen, 2);
    lv_obj_set_pos(layers, portrait ? 7 : 30, portrait ? 110 : 93);
    lv_obj_set_style_transform_pivot_x(layers, 0, LV_PART_MAIN);
    lv_obj_set_style_transform_pivot_y(layers, 0, LV_PART_MAIN);
    lv_obj_set_style_transform_scale_x(layers, portrait ? 155 : 256, LV_PART_MAIN);
    lv_obj_set_style_transform_scale_y(layers, portrait ? 155 : 256, LV_PART_MAIN);

    /* The three-peripheral stock widget uses vertical bars; our matching
     * compact arcs live in their own status-only object. */
    lv_obj_add_flag(lv_obj_get_child(screen, 3), LV_OBJ_FLAG_HIDDEN);
    lv_obj_t *output = lv_obj_get_child(screen, 4);
    lv_obj_set_pos(output, portrait ? 28 : 202, portrait ? 139 : 105);
    lv_obj_set_style_transform_pivot_x(output, 0, LV_PART_MAIN);
    lv_obj_set_style_transform_pivot_y(output, 0, LV_PART_MAIN);
    lv_obj_set_style_transform_scale_x(output, 256, LV_PART_MAIN);
    lv_obj_set_style_transform_scale_y(output, 256, LV_PART_MAIN);

    xiao147_battery_rings_set_portrait(portrait);
    lv_obj_set_pos(dongle_battery_bar, portrait ? 16 : 10, portrait ? 285 : 158);
    lv_obj_set_size(dongle_battery_bar, portrait ? 106 : 140, 6);
    lv_obj_set_pos(dongle_battery_label, portrait ? 125 : 154, portrait ? 278 : 150);
    lv_obj_set_width(dongle_battery_label, portrait ? 42 : 45);
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
    }
    xiao147_rotation_set(portrait);
    lv_obj_set_size(panel, portrait ? SCREEN_H : SCREEN_W,
                    portrait ? SCREEN_W : SCREEN_H);
    lv_obj_set_pos(title_label, 0, portrait ? 8 : 3);
    lv_obj_set_width(title_label, portrait ? SCREEN_H : SCREEN_W);
    lv_obj_set_style_text_align(title_label, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    layout_status(portrait);
    if (page == XIAO147_PAGE_STATUS) {
        lv_obj_add_flag(panel, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(dongle_battery_bar, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(dongle_battery_label, LV_OBJ_FLAG_HIDDEN);
        xiao147_battery_rings_set_visible(true);
    } else {
        lv_obj_add_flag(dongle_battery_bar, LV_OBJ_FLAG_HIDDEN);
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
    if (led_set_brightness(pwm_leds, DISP_BL, target) == 0) {
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
    dongle_battery_label = make_label(screen, 154, 150, 45, "--%", COLOR_GREEN);

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
    if (!zmk_display_is_initialized()) {
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
    if (evt->type == INPUT_EV_KEY && evt->code == INPUT_BTN_1 && evt->value == 1) {
        xiao147_ui_next_page();
    }
}
INPUT_CALLBACK_DEFINE(DEVICE_DT_GET(DT_NODELABEL(xiao_touch_menu_button)), menu_button_input, NULL);

static void imu_button_input(struct input_event *evt, void *user_data)
{
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
    if (battery && battery->source < 3) {
        peripheral.battery[battery->source] = battery->state_of_charge;
        peripheral.seen[battery->source] = true;
    }
    if (connection && connection->slot < 3) {
        peripheral.connected[connection->slot] = connection->connected;
        if (!connection->connected) { peripheral.seen[connection->slot] = false; }
    }
    k_mutex_unlock(&peripheral_lock);
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
