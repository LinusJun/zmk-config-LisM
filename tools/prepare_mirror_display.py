"""Compose read-only telemetry at every OPERATOR widget drawing entry point."""
from pathlib import Path
from prepare_upstream_esb import replace
from prepare_wpm_display import prepare as prepare_wpm

def prepare(operator):
    operator=Path(operator)
    f=operator/'wpm_meter.c';s='#include <kb_route/mirror.h>\n#include <kb_route/layer_label.h>\n'+f.read_text()
    s=replace(s,'static void wpm_meter_update_cb(struct wpm_meter_state state) {','static void wpm_meter_update_cb(struct wpm_meter_state state) {\n    struct kb_mirror m;if(kb_mirror_snapshot(&m))state.wpm=m.wpm;')
    s=replace(s,'static void layer_update_cb(struct layer_state state) {','static void layer_update_cb(struct layer_state state) {\n    struct kb_mirror m;if(kb_mirror_snapshot(&m))state.index=m.layer;')
    s=replace(s,'const char *layer_name = zmk_keymap_layer_name(zmk_keymap_layer_index_to_id(state.index));','const char *layer_name = state.index==255?"--":zmk_keymap_layer_name(zmk_keymap_layer_index_to_id(state.index));')
    s=replace(s,'        lv_label_set_text(widget->layer_label, display_name);','        kb_layer_label_update(widget->layer_label, display_name);')
    s+='\nvoid kb_mirror_wpm_refresh(void) {wpm_meter_update_cb(wpm_meter_get_state(NULL));layer_update_cb(layer_get_state(NULL));}\n';f.write_text(s);prepare_wpm(f)
    f=operator/'layer_display.c';s='#include <kb_route/mirror.h>\n'+f.read_text();s=replace(s,'static void layer_display_update_cb(struct layer_display_state state) {','static void layer_display_update_cb(struct layer_display_state state) {\n    struct kb_mirror m;if(kb_mirror_snapshot(&m))state.index=m.layer;');s+='\nvoid kb_mirror_layer_refresh(void) {layer_display_update_cb(layer_display_get_state(NULL));}\n';f.write_text(s)
    f=operator/'modifier_indicator.c';s='#include <kb_route/mirror.h>\n'+f.read_text();s=replace(s,'static void modifier_indicator_update_cb(struct modifier_indicator_state state) {','''static void modifier_indicator_update_cb(struct modifier_indicator_state state) {
    struct kb_mirror m;if(kb_mirror_snapshot(&m)) {
        state.mods[MOD_TYPE_GUI]=(m.mods&0x88)!=0;state.mods[MOD_TYPE_ALT]=(m.mods&0x44)!=0;
        state.mods[MOD_TYPE_CTRL]=(m.mods&0x11)!=0;state.mods[MOD_TYPE_SHIFT]=(m.mods&0x22)!=0;
#ifdef CONFIG_DT_HAS_ZMK_BEHAVIOR_CAPS_WORD_ENABLED
        state.caps_word=false;
#endif
    }''');s+='\nvoid kb_mirror_mods_refresh(void) {modifier_indicator_update_cb(modifier_indicator_get_state(NULL));}\n';f.write_text(s)
