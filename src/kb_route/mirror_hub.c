/* SPDX-License-Identifier: MIT */
#include "route.h"
#include "mirror.h"
#include <zmk/hid.h>
#include <zmk/hid_indicators.h>
#include <zmk/keymap.h>
#include <zmk/wpm.h>
#include <zmk/ble.h>
#include <zmk/events/modifiers_state_changed.h>
#include <zmk/events/layer_state_changed.h>
#include <zmk/events/hid_indicators_changed.h>
#include <zmk/events/wpm_state_changed.h>
void kb_mirror_emit(void) {
    if(!kb_route_mirror_enabled())return;
    struct kb_route_view v;kb_route_snapshot(&v);
    /* Read the final single-byte HID modifier mask, including implicit LS/LG
     * and masked modifiers, rather than only the explicit key presses. */
    uint8_t mods=zmk_hid_get_keyboard_report()->body.modifiers;
    struct kb_mirror m={.epoch=v.epoch,.version=KB_MIRROR_VERSION,.wpm=zmk_wpm_get_state(),.layer=zmk_keymap_highest_layer_active(),.mods=mods,.indicators=zmk_hid_indicators_get_current_profile(),.profile=zmk_ble_active_profile_index(),.host_connected=zmk_ble_active_profile_is_connected()};
    kb_route_left_telemetry(&m.left_battery,&m.left_online);
    struct zmk_split_transport_peripheral_event e={.type=KB_MIRROR_EVENT};
    BUILD_ASSERT(sizeof(m)<=sizeof(e.data));BUILD_ASSERT(20+5+1+sizeof(e.type)+sizeof(m)+4<=64);
    memcpy(&e.data,&m,sizeof(m));kb_uplink_send(1,&e);
}
static int changed(const zmk_event_t *e) {ARG_UNUSED(e);kb_mirror_emit();return ZMK_EV_EVENT_BUBBLE;}
ZMK_LISTENER(kb_mirror,changed);
ZMK_SUBSCRIPTION(kb_mirror,zmk_modifiers_state_changed);
ZMK_SUBSCRIPTION(kb_mirror,zmk_layer_state_changed);
ZMK_SUBSCRIPTION(kb_mirror,zmk_hid_indicators_changed);
ZMK_SUBSCRIPTION(kb_mirror,zmk_wpm_state_changed);
