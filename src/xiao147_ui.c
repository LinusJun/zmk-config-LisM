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

#define SCREEN_W 320
#define SCREEN_H 172
#define COLOR_BG 0x10151e
#define COLOR_CARD 0x202a38
#define COLOR_TEXT 0xe4ebf6
#define COLOR_MUTED 0x8295ae
#define COLOR_ACCENT 0x39bdec
#define COLOR_GREEN 0x78c79c
#define DIM_AFTER_MS 60000
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
static uint32_t last_activity_ms;
static uint8_t brightness = 50;
static uint8_t applied_brightness = 255;
static bool usb_powered;
static enum rotation_mode rotation = ROTATION_LANDSCAPE;
static uint8_t previous_touch_fingers;
static struct touch_sample last_touch;

static lv_obj_t *panel;
static lv_obj_t *title_label;
static lv_obj_t *row_labels[8];
static lv_obj_t *accent_bar;
static lv_obj_t *dongle_battery_label;
static struct k_work init_work;
static struct k_work redraw_work;
static struct k_work touch_work;
static struct k_work_delayable init_retry_work;
static struct k_work_delayable periodic_work;

static void redraw(struct k_work *work);

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

static void clear_rows(void)
{
    for (int i = 0; i < 8; i++) {
        lv_label_set_text(row_labels[i], "");
    }
    lv_obj_add_flag(accent_bar, LV_OBJ_FLAG_HIDDEN);
}

static void draw_settings(void)
{
    clear_rows();
    const bool portrait = xiao147_rotation_is_portrait();
    lv_obj_set_pos(accent_bar, portrait ? 20 : 20, portrait ? 68 : 50);
    if (portrait) {
        const int row_x[8] = {10, 125, 10, 125, 10, 10, 10, 125};
        const int row_y[8] = {36, 36, 100, 100, 140, 177, 219, 219};
        for (int i = 0; i < 8; i++) {
            lv_obj_set_pos(row_labels[i], row_x[i], row_y[i]);
            lv_obj_set_width(row_labels[i], i == 1 || i == 3 || i == 7 ? 45 : 155);
        }
    } else {
    const int row_x[8] = {12, 266, 12, 270, 12, 173, 12, 270};
    const int row_y[8] = {27, 27, 63, 63, 95, 95, 133, 133};
    for (int i = 0; i < 8; i++) {
        lv_obj_set_pos(row_labels[i], row_x[i], row_y[i]);
    }
    }
    lv_label_set_text(title_label, "DISPLAY SETTINGS");
    char txt[48];
    uint8_t max_brightness = usb_powered ? 100 : 50;
    lv_label_set_text(row_labels[0], "Brightness");
    snprintf(txt, sizeof(txt), "%u%%", brightness);
    lv_label_set_text(row_labels[1], txt);
    lv_label_set_text(row_labels[2], "Auto rotate");
    lv_label_set_text(row_labels[3], rotation == ROTATION_AUTO ? "ON" : "OFF");
    lv_label_set_text(row_labels[4], rotation == ROTATION_LANDSCAPE ? "LANDSCAPE  [ON]" :
                      "LANDSCAPE");
    lv_label_set_text(row_labels[5], rotation == ROTATION_PORTRAIT ? "PORTRAIT  [ON]" :
                      "PORTRAIT");
    lv_label_set_text(row_labels[6], "6-axis mouse");
    lv_label_set_text(row_labels[7], xiao147_ui_imu_enabled() ? "ON" : "OFF");
    lv_obj_clear_flag(accent_bar, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_width(accent_bar, (int)brightness * (portrait ? 132 : 276) / max_brightness);
}

static void draw_devices(void)
{
    clear_rows();
    lv_label_set_text(title_label, "ACTIVE DEVICES");
    struct peripheral_state copy;
    k_mutex_lock(&peripheral_lock, K_FOREVER);
    copy = peripheral;
    k_mutex_unlock(&peripheral_lock);
    uint8_t dongle_battery = zmk_battery_state_of_charge();
    const char *names[] = {"DONGLE", "LISM LEFT", "LISM RIGHT", "JOYSTICK"};
    for (int row = 0; row < 4; row++) {
        char text[40];
        if (row == 0) {
            snprintf(text, sizeof(text), "%s   %u%%", names[row], dongle_battery);
        } else if (!copy.connected[row - 1]) {
            snprintf(text, sizeof(text), "%s   OFF", names[row]);
        } else if (!copy.seen[row - 1]) {
            snprintf(text, sizeof(text), "%s   --%%", names[row]);
        } else {
            snprintf(text, sizeof(text), "%s   %u%%", names[row], copy.battery[row - 1]);
        }
        const bool portrait = xiao147_rotation_is_portrait();
        lv_obj_set_pos(row_labels[row * 2], portrait ? 10 : 12,
                       portrait ? 37 + 67 * row : 30 + 34 * row);
        lv_obj_set_pos(row_labels[row * 2 + 1], portrait ? 10 : 250,
                       portrait ? 60 + 67 * row : 30 + 34 * row);
        lv_obj_set_width(row_labels[row * 2], portrait ? 152 : 230);
        lv_obj_set_width(row_labels[row * 2 + 1], portrait ? 152 : 70);
        lv_label_set_text(row_labels[row * 2], text);
        /* ZMK's public split-central status does not expose live RSSI. */
        lv_label_set_text(row_labels[row * 2 + 1], row == 0 ? "USB/BLE" : "-- dBm");
    }
}

static void layout_status(bool portrait)
{
    lv_obj_t *screen = lv_scr_act();
    /* The pinned OPERATOR layout creates these five children in this order. */
    if (lv_obj_get_child_cnt(screen) < 5) { return; }
    const int landscape_x[5] = {25, 10, 10, 11, 195};
    const int landscape_y[5] = {8, 34, 104, 170, 105};
    const int portrait_x[5] = {0, 0, 0, 11, 28};
    const int portrait_y[5] = {7, 45, 120, 170, 150};
    const int portrait_scale[5] = {185, 162, 160, 256, 256};
    const int landscape_scale[5] = {256, 175, 205, 256, 256};
    for (int i = 0; i < 5; i++) {
        lv_obj_t *obj = lv_obj_get_child(screen, i);
        lv_obj_set_pos(obj, portrait ? portrait_x[i] : landscape_x[i],
                       portrait ? portrait_y[i] : landscape_y[i]);
        lv_obj_set_style_transform_pivot_x(obj, 0, LV_PART_MAIN);
        lv_obj_set_style_transform_pivot_y(obj, 0, LV_PART_MAIN);
        lv_obj_set_style_transform_scale_x(obj,
                     portrait ? portrait_scale[i] : landscape_scale[i], LV_PART_MAIN);
        lv_obj_set_style_transform_scale_y(obj,
                     portrait ? portrait_scale[i] : landscape_scale[i], LV_PART_MAIN);
    }
    xiao147_battery_rings_set_portrait(portrait);
    lv_obj_set_pos(dongle_battery_label, portrait ? 50 : 242, portrait ? 218 : 91);
}

static void redraw(struct k_work *work)
{
    if (panel == NULL) { return; }
    /* The left button is the authoritative IMU-mode toggle. */
    if (xiao147_ui_imu_enabled()) { rotation = ROTATION_PORTRAIT; }
    else if (atomic_set(&restore_landscape, 0)) { rotation = ROTATION_LANDSCAPE; }
    const bool portrait = rotation == ROTATION_PORTRAIT;
    xiao147_rotation_set(portrait);
    lv_obj_set_size(panel, portrait ? SCREEN_H : SCREEN_W,
                    portrait ? SCREEN_W : SCREEN_H);
    layout_status(portrait);
    enum xiao147_page page = atomic_get(&page_atomic);
    if (page == XIAO147_PAGE_STATUS) {
        lv_obj_add_flag(panel, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_clear_flag(panel, LV_OBJ_FLAG_HIDDEN);
        if (page == XIAO147_PAGE_SETTINGS) { draw_settings(); }
        else { draw_devices(); }
    }
    char text[16];
    snprintf(text, sizeof(text), "D %u%%", zmk_battery_state_of_charge());
    lv_label_set_text(dongle_battery_label, text);
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
        if (sample.fingers == 1 && sample.y >= (portrait ? 35 : 24) &&
            sample.y <= (portrait ? 82 : 58)) {
            int x = sample.x;
            if (x < 20) { x = 20; }
            if (x > (portrait ? 152 : 296)) { x = portrait ? 152 : 296; }
            int value = (x - 20) * (usb_powered ? 100 : 50) / (portrait ? 132 : 276);
            brightness = value;
            apply_brightness();
            redraw(NULL);
        } else if (sample.fingers == 0 && previous_touch_fingers == 1) {
            uint16_t y = last_touch.y;
            uint16_t x = last_touch.x;
            if (y >= (portrait ? 94 : 58) && y < (portrait ? 132 : 86)) {
                if (!xiao147_ui_imu_enabled()) {
                    rotation = rotation == ROTATION_AUTO ? ROTATION_LANDSCAPE : ROTATION_AUTO;
                }
            } else if (y >= (portrait ? 135 : 88) && y < (portrait ? 212 : 125)) {
                if (!xiao147_ui_imu_enabled()) {
                    rotation = portrait ? (y < 172 ? ROTATION_LANDSCAPE : ROTATION_PORTRAIT) :
                              (x < SCREEN_W / 2 ? ROTATION_LANDSCAPE : ROTATION_PORTRAIT);
                }
            } else if (y >= (portrait ? 215 : 128)) {
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

    dongle_battery_label = make_label(screen, 242, 91, 74, "D --%", COLOR_GREEN);
    panel = lv_obj_create(screen);
    lv_obj_set_pos(panel, 0, 0);
    lv_obj_set_size(panel, SCREEN_W, SCREEN_H);
    lv_obj_set_style_bg_color(panel, lv_color_hex(COLOR_BG), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(panel, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(panel, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(panel, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(panel, 0, LV_PART_MAIN);
    lv_obj_clear_flag(panel, LV_OBJ_FLAG_SCROLLABLE);

    title_label = make_label(panel, 10, 4, 300, "", COLOR_TEXT);
    for (int i = 0; i < 8; i++) {
        int x, y;
        if (i == 0) { x = 12; y = 27; }
        else if (i == 1) { x = 266; y = 27; }
        else if (i == 2) { x = 12; y = 63; }
        else if (i == 3) { x = 270; y = 63; }
        else if (i == 4) { x = 12; y = 95; }
        else if (i == 5) { x = 173; y = 95; }
        else if (i == 6) { x = 12; y = 133; }
        else { x = 270; y = 133; }
        row_labels[i] = make_label(panel, x, y, i % 2 ? 50 : 200, "", i % 2 ? COLOR_ACCENT : COLOR_TEXT);
    }
    accent_bar = lv_obj_create(panel);
    lv_obj_set_pos(accent_bar, 20, 50);
    lv_obj_set_size(accent_bar, 276, 5);
    lv_obj_set_style_bg_color(accent_bar, lv_color_hex(COLOR_ACCENT), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(accent_bar, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(accent_bar, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(accent_bar, 2, LV_PART_MAIN);
    lv_obj_set_style_pad_all(accent_bar, 0, LV_PART_MAIN);
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

void xiao147_ui_toggle_imu(void)
{
    atomic_val_t old;
    do {
        old = atomic_get(&imu_mode);
    } while (!atomic_cas(&imu_mode, old, !old));
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
