/*
 * LisM OPERATOR status-screen extension.
 *
 * The Prospector OPERATOR screen dedicates the full top row to four textual
 * modifier names. Compacting those names to symbols leaves enough room for
 * host-controlled Caps Lock and Num Lock indicators without reducing the WPM
 * meter, layer display, battery circles, or output status areas.
 */

#include <lvgl.h>
#include <string.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>

#include <zmk/display.h>
#include <zmk/event_manager.h>
#include <zmk/events/hid_indicators_changed.h>
#include <zmk/hid_indicators.h>

LV_FONT_DECLARE(Symbols_Semibold_32);
LV_FONT_DECLARE(FG_Medium_20);

#define SYMBOL_COMMAND "\xF4\x80\x86\x94"
#define SYMBOL_OPTION "\xF4\x80\x86\x95"
#define SYMBOL_CONTROL "\xF4\x80\x86\x8D"
#define SYMBOL_SHIFT "\xF4\x80\x86\x9D"

#define HID_INDICATOR_NUM_LOCK 0x01
#define HID_INDICATOR_CAPS_LOCK 0x02

#define COLOR_INACTIVE 0x3b527c
#define COLOR_CAPS_ACTIVE 0xffbf00
#define COLOR_NUM_ACTIVE 0xb1e5f0
#define COLOR_SEPARATOR 0x606060

struct lock_indicator_state {
    uint8_t indicators;
};

static lv_obj_t *caps_label;
static lv_obj_t *num_label;
static struct k_work_delayable deferred_init_work;
static struct k_work display_init_work;

static const char *compact_modifier_symbol(const char *text) {
    if (text == NULL) {
        return NULL;
    }

    if (!strcmp(text, "SHFT")) {
        return SYMBOL_SHIFT;
    }
    if (!strcmp(text, "CTRL")) {
        return SYMBOL_CONTROL;
    }
    if (!strcmp(text, "ALT") || !strcmp(text, "OPT")) {
        return SYMBOL_OPTION;
    }
    if (!strcmp(text, "GUI") || !strcmp(text, "CMD") || !strcmp(text, "WIN")) {
        return SYMBOL_COMMAND;
    }

    return NULL;
}

static bool compact_operator_modifier_row(lv_obj_t *screen) {
    if (lv_obj_get_child_cnt(screen) < 5) {
        return false;
    }

    /* OPERATOR creates its modifier container first. Its children alternate
     * label/separator, giving label indices 0, 2, 4, and 6. */
    lv_obj_t *modifiers = lv_obj_get_child(screen, 0);
    if (lv_obj_get_child_cnt(modifiers) < 7) {
        return false;
    }

    lv_obj_set_pos(modifiers, 10, 4);
    lv_obj_set_size(modifiers, 160, 32);

    for (int index = 0; index < 7; index += 2) {
        lv_obj_t *label = lv_obj_get_child(modifiers, index);
        const char *symbol = compact_modifier_symbol(lv_label_get_text(label));
        if (symbol != NULL) {
            lv_label_set_text(label, symbol);
            lv_obj_set_style_text_font(label, &Symbols_Semibold_32, LV_PART_MAIN);
        }
    }

    return true;
}

static lv_obj_t *create_lock_label(lv_obj_t *parent, const char *text) {
    lv_obj_t *label = lv_label_create(parent);
    lv_label_set_text(label, text);
    lv_obj_set_style_text_font(label, &FG_Medium_20, LV_PART_MAIN);
    lv_obj_set_style_text_color(label, lv_color_hex(COLOR_INACTIVE), LV_PART_MAIN);
    return label;
}

static bool create_lock_indicator_row(void) {
    lv_obj_t *screen = lv_scr_act();
    if (screen == NULL || !compact_operator_modifier_row(screen)) {
        return false;
    }

    lv_obj_t *container = lv_obj_create(screen);
    lv_obj_set_pos(container, 180, 8);
    lv_obj_set_size(container, 90, 24);
    lv_obj_set_style_bg_opa(container, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(container, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(container, 0, LV_PART_MAIN);
    lv_obj_set_flex_flow(container, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(container, LV_FLEX_ALIGN_SPACE_BETWEEN, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER);

    caps_label = create_lock_label(container, "CAP");

    lv_obj_t *separator = lv_obj_create(container);
    lv_obj_set_size(separator, 2, 24);
    lv_obj_set_style_bg_color(separator, lv_color_hex(COLOR_SEPARATOR), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(separator, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(separator, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(separator, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(separator, 0, LV_PART_MAIN);

    num_label = create_lock_label(container, "NUM");
    return true;
}

static void lock_indicator_update_cb(struct lock_indicator_state state) {
    if (caps_label == NULL || num_label == NULL) {
        if (!create_lock_indicator_row()) {
            k_work_reschedule(&deferred_init_work, K_MSEC(250));
            return;
        }
    }

    const bool caps_active = (state.indicators & HID_INDICATOR_CAPS_LOCK) != 0;
    const bool num_active = (state.indicators & HID_INDICATOR_NUM_LOCK) != 0;

    lv_obj_set_style_text_color(
        caps_label, lv_color_hex(caps_active ? COLOR_CAPS_ACTIVE : COLOR_INACTIVE), LV_PART_MAIN);
    lv_obj_set_style_text_color(
        num_label, lv_color_hex(num_active ? COLOR_NUM_ACTIVE : COLOR_INACTIVE), LV_PART_MAIN);
}

static struct lock_indicator_state lock_indicator_get_state(const zmk_event_t *eh) {
    const struct zmk_hid_indicators_changed *event =
        eh == NULL ? NULL : as_zmk_hid_indicators_changed(eh);

    return (struct lock_indicator_state){
        .indicators =
            event == NULL ? zmk_hid_indicators_get_current_profile() : event->indicators,
    };
}

ZMK_DISPLAY_WIDGET_LISTENER(widget_lism_lock_indicators, struct lock_indicator_state,
                            lock_indicator_update_cb, lock_indicator_get_state)
ZMK_SUBSCRIPTION(widget_lism_lock_indicators, zmk_hid_indicators_changed);

static void display_init_work_handler(struct k_work *work) {
    widget_lism_lock_indicators_init();
}

static void deferred_init_work_handler(struct k_work *work) {
    if (!zmk_display_is_initialized()) {
        k_work_reschedule(&deferred_init_work, K_MSEC(250));
        return;
    }

    k_work_submit_to_queue(zmk_display_work_q(), &display_init_work);
}

static int lism_lock_indicators_init(void) {
    k_work_init(&display_init_work, display_init_work_handler);
    k_work_init_delayable(&deferred_init_work, deferred_init_work_handler);
    k_work_schedule(&deferred_init_work, K_SECONDS(1));
    return 0;
}

SYS_INIT(lism_lock_indicators_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
