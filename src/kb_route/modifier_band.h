/* SPDX-License-Identifier: MIT */
#pragma once
#include <lvgl.h>

/* Prospector's initial four labels are later expanded to six lock/mod labels.
 * Cover the old glyph area too, while retaining the original text position. */
static inline void kb_modifier_band_layout(lv_obj_t *row) {
#ifndef CONFIG_LISM_XIAO147_TOUCH
    lv_obj_set_pos(row,10,0);
    /* The selected font has a 25 px line height. Theme flex gaps otherwise
     * overflow the six-label row and create unwanted AUTO scrollbars. */
    lv_obj_set_size(row,lv_obj_get_width(lv_obj_get_parent(row))-20,33);
    lv_obj_set_style_pad_top(row,8,LV_PART_MAIN);
    lv_obj_set_style_pad_bottom(row,0,LV_PART_MAIN);
    lv_obj_set_style_pad_column(row,0,LV_PART_MAIN);
    lv_obj_remove_flag(row,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(row,LV_SCROLLBAR_MODE_OFF);
    lv_obj_set_style_bg_color(row,lv_color_black(),LV_PART_MAIN);
    lv_obj_set_style_bg_opa(row,LV_OPA_COVER,LV_PART_MAIN);
    lv_obj_set_style_radius(row,0,LV_PART_MAIN);
    lv_obj_set_style_border_width(row,0,LV_PART_MAIN);
#else
    (void)row;
#endif
}
static inline void kb_modifier_band_refresh(lv_obj_t *row) {
#ifndef CONFIG_LISM_XIAO147_TOUCH
    lv_obj_invalidate(row);
#else
    (void)row;
#endif
}
