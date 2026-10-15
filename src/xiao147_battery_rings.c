/*
 * Three compact peripheral battery rings for the 320x172 XIAO OPERATOR UI.
 * The upstream OPERATOR layout uses bars when PERIPHERAL_COUNT >= 3.
 * Keep its battery event plumbing, but hide that widget on this one shield
 * and draw three live rings in the extra landscape width.
 */
#include <lvgl.h>
#include <stdio.h>
#include <stdint.h>

#include <zephyr/init.h>
#include <zephyr/kernel.h>

#include <zmk/display.h>
#include "xiao147_display_test.h"
#include <zmk/event_manager.h>
#include <zmk/events/battery_state_changed.h>
#include <zmk/events/split_central_status_changed.h>

#include "xiao147_battery_rings.h"
#include "xiao147_battery_colors.h"
#include "xiao147_peripheral_roles.h"
#include "xiao147_rotation.h"
#include "xiao147_ui.h"
#include "lism_charge.h"

#define RING_COUNT 3
#define RING_SIZE 46
#define RING_STEP 49
#define RING_TOP 4

static lv_obj_t *ring_obj[RING_COUNT];
static lv_obj_t *ring_row;
static lv_obj_t *ring_badge[RING_COUNT];
static lv_obj_t *ring_badge_text[RING_COUNT];
static lv_obj_t *ring_charge[RING_COUNT];
static uint8_t battery_level[XIAO147_PERIPHERAL_COUNT];
static bool battery_seen[XIAO147_PERIPHERAL_COUNT];
static bool connected[XIAO147_PERIPHERAL_COUNT];
static struct k_work_delayable deferred_init_work;
static struct k_work display_init_work;

static void render_ring(uint8_t slot) {
    if (slot >= RING_COUNT || ring_obj[slot] == NULL) {
        return;
    }

    const uint8_t source_slot = slot < 2 ? slot : xiao147_active_joystick_slot(connected);
    const bool active = connected[source_slot];
    struct lism_charge_state charge = lism_charge_peripheral(source_slot);
    uint8_t level = 0;
    const bool known = active && lism_charge_level(charge, battery_seen[source_slot],
                                                   battery_level[source_slot], &level);
    const uint32_t ring_color = active ? 0x505050 : 0x282c30;
    const uint32_t fill_color = known ? xiao147_battery_color(level) : 0x383c42;

    lv_obj_set_style_arc_color(ring_obj[slot], lv_color_hex(ring_color), LV_PART_MAIN);
    lv_obj_set_style_arc_color(ring_obj[slot], lv_color_hex(fill_color), LV_PART_INDICATOR);
    lv_obj_set_style_arc_width(ring_obj[slot], active ? 5 : 2, LV_PART_MAIN);
    lv_obj_set_style_arc_width(ring_obj[slot], active ? 5 : 2, LV_PART_INDICATOR);
    lv_arc_set_value(ring_obj[slot], known ? level : 0);
    lv_obj_set_style_bg_color(ring_badge[slot], lv_color_hex(fill_color), LV_PART_MAIN);

    char text[5];
    if (known) {
        snprintf(text, sizeof(text), "%u", level);
    } else {
        snprintf(text, sizeof(text), "--");
    }
    lv_label_set_text(ring_badge_text[slot], text);
    lv_obj_set_style_text_color(ring_badge_text[slot],
                                lv_color_hex(known ? 0x000000 : 0x999999),
                                LV_PART_MAIN);
    bool charging = active && lism_charge_show_bolt(charge);
    if (charging) { lv_obj_clear_flag(ring_charge[slot], LV_OBJ_FLAG_HIDDEN); }
    else { lv_obj_add_flag(ring_charge[slot], LV_OBJ_FLAG_HIDDEN); }
}

void xiao147_battery_rings_refresh_charge(void) {
    /* Called only from the display queue, never a Bluetooth callback. */
    for (uint8_t slot = 0; slot < RING_COUNT; ++slot) { render_ring(slot); }
}

static bool create_ring_row(void) {
    lv_obj_t *screen = lv_scr_act();
    if (screen == NULL || lv_obj_get_child_cnt(screen) < 5) {
        return false;
    }

    /* Root child 3 is Prospector's original battery widget. */
    lv_obj_add_flag(lv_obj_get_child(screen, 3), LV_OBJ_FLAG_HIDDEN);

    lv_obj_t *row = lv_obj_create(screen);
    ring_row = row;
    lv_obj_set_pos(row, 10, 105);
    lv_obj_set_size(row, 152, 52);
    lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(row, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(row, 0, LV_PART_MAIN);

    for (uint8_t slot = 0; slot < RING_COUNT; slot++) {
        lv_obj_t *ring = lv_arc_create(row);
        ring_obj[slot] = ring;
        lv_obj_set_pos(ring, slot * RING_STEP + 7, RING_TOP);
        lv_obj_set_size(ring, RING_SIZE, RING_SIZE);
        lv_arc_set_range(ring, 0, 100);
        lv_arc_set_bg_angles(ring, 270, 180);
        lv_arc_set_rotation(ring, 0);
        /* Flat arc ends preserve the small gap beside the battery badge. */
        lv_obj_set_style_arc_rounded(ring, false, LV_PART_MAIN);
        lv_obj_set_style_arc_rounded(ring, false, LV_PART_INDICATOR);
        lv_obj_remove_style(ring, NULL, LV_PART_KNOB);
        lv_obj_clear_flag(ring, LV_OBJ_FLAG_CLICKABLE);

        /* The original two-peripheral OPERATOR puts battery % in a separate
         * top-left rectangle, not a peripheral index inside the ring. */
        lv_obj_t *badge = lv_obj_create(row);
        ring_badge[slot] = badge;
        lv_obj_set_pos(badge, slot * RING_STEP + 7, RING_TOP);
        lv_obj_set_size(badge, 20, 20);
        lv_obj_set_style_bg_opa(badge, LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_border_width(badge, 0, LV_PART_MAIN);
        lv_obj_set_style_radius(badge, 2, LV_PART_MAIN);
        lv_obj_set_style_pad_all(badge, 0, LV_PART_MAIN);
        lv_obj_clear_flag(badge, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_t *value = lv_label_create(badge);
        ring_badge_text[slot] = value;
        lv_label_set_text(value, "--");
        lv_obj_set_style_text_font(value, &lv_font_montserrat_12, LV_PART_MAIN);
        /* Keep three-digit 100 inside the compact badge as well. */
        lv_obj_set_style_text_letter_space(value, -2, LV_PART_MAIN);
        lv_obj_align(value, LV_ALIGN_CENTER, 0, 0);
        ring_charge[slot] = lv_label_create(ring);
        lv_label_set_text(ring_charge[slot], LV_SYMBOL_CHARGE);
        lv_obj_set_style_text_font(ring_charge[slot], &lv_font_montserrat_14, LV_PART_MAIN);
        lv_obj_set_style_text_color(ring_charge[slot], lv_color_hex(0xffd54f), LV_PART_MAIN);
        lv_obj_align(ring_charge[slot], LV_ALIGN_CENTER, 0, 0);
        render_ring(slot);
    }

    xiao147_battery_rings_set_portrait(xiao147_rotation_is_portrait());
    xiao147_battery_rings_set_visible(xiao147_ui_page() == XIAO147_PAGE_STATUS);
    return true;
}

void xiao147_battery_rings_set_portrait(bool portrait) {
    if (ring_row != NULL) {
        lv_obj_set_pos(ring_row, portrait ? 11 : 10, portrait ? 224 : 105);
        lv_obj_set_size(ring_row, portrait ? 150 : 152, 52);
        for (uint8_t slot = 0; slot < RING_COUNT; slot++) {
            const int x = portrait ? slot * 52 : slot * RING_STEP + 7;
            lv_obj_set_pos(ring_obj[slot], x, RING_TOP);
            lv_obj_set_pos(ring_badge[slot], x, RING_TOP);
        }
    }
}

void xiao147_battery_rings_set_visible(bool visible) {
    if (ring_row == NULL) { return; }
    if (visible) { lv_obj_clear_flag(ring_row, LV_OBJ_FLAG_HIDDEN); }
    else { lv_obj_add_flag(ring_row, LV_OBJ_FLAG_HIDDEN); }
}

/*
 * Each listener transports all three slots as one state snapshot. This
 * prevents the display work queue from losing a quick second slot event and
 * prevents a NULL initial read from overwriting an earlier connection.
 */
struct battery_ring_state {
    uint8_t level[XIAO147_PERIPHERAL_COUNT];
    bool seen[XIAO147_PERIPHERAL_COUNT];
};

static struct battery_ring_state latest_battery;

static void battery_update_cb(struct battery_ring_state state) {
    for (uint8_t slot = 0; slot < XIAO147_PERIPHERAL_COUNT; slot++) {
        battery_level[slot] = state.level[slot];
        battery_seen[slot] = state.seen[slot];
    }
    for (uint8_t slot = 0; slot < RING_COUNT; slot++) { render_ring(slot); }
}

static struct battery_ring_state battery_get_state(const zmk_event_t *eh) {
    if (eh != NULL) {
        const struct zmk_peripheral_battery_state_changed *event =
            as_zmk_peripheral_battery_state_changed(eh);
        if (event != NULL && event->source < XIAO147_PERIPHERAL_COUNT) {
            latest_battery.level[event->source] = event->state_of_charge;
            latest_battery.seen[event->source] = true;
        }
    }
    return latest_battery;
}

struct connection_ring_state {
    bool connected[XIAO147_PERIPHERAL_COUNT];
};

static struct connection_ring_state latest_connection;

static void connection_update_cb(struct connection_ring_state state) {
    for (uint8_t slot = 0; slot < XIAO147_PERIPHERAL_COUNT; slot++) {
        connected[slot] = state.connected[slot];
        if (!connected[slot]) { battery_seen[slot] = false; }
    }
    for (uint8_t slot = 0; slot < RING_COUNT; slot++) { render_ring(slot); }
}

static struct connection_ring_state connection_get_state(const zmk_event_t *eh) {
    if (eh != NULL) {
        const struct zmk_split_central_status_changed *event =
            as_zmk_split_central_status_changed(eh);
        if (event != NULL && event->slot < XIAO147_PERIPHERAL_COUNT) {
            latest_connection.connected[event->slot] = event->connected;
            if (!event->connected) {
                latest_battery.seen[event->slot] = false;
            }
        }
    }
    return latest_connection;
}

ZMK_DISPLAY_WIDGET_LISTENER(widget_lism_three_ring_battery, struct battery_ring_state,
                            battery_update_cb, battery_get_state)
ZMK_SUBSCRIPTION(widget_lism_three_ring_battery, zmk_peripheral_battery_state_changed);

ZMK_DISPLAY_WIDGET_LISTENER(widget_lism_three_ring_connection, struct connection_ring_state,
                            connection_update_cb, connection_get_state)
ZMK_SUBSCRIPTION(widget_lism_three_ring_connection, zmk_split_central_status_changed);

static void display_init_work_handler(struct k_work *work) {
    if (!create_ring_row()) {
        k_work_reschedule(&deferred_init_work, K_MSEC(250));
        return;
    }
    widget_lism_three_ring_battery_init();
    widget_lism_three_ring_connection_init();
}

static void deferred_init_work_handler(struct k_work *work) {
    if (!zmk_display_is_initialized() || !xiao147_display_ui_ready()) {
        k_work_reschedule(&deferred_init_work, K_MSEC(250));
        return;
    }
    k_work_submit_to_queue(zmk_display_work_q(), &display_init_work);
}

static int lism_three_ring_init(void) {
    k_work_init(&display_init_work, display_init_work_handler);
    k_work_init_delayable(&deferred_init_work, deferred_init_work_handler);
    k_work_schedule(&deferred_init_work, K_SECONDS(1));
    return 0;
}

SYS_INIT(lism_three_ring_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
