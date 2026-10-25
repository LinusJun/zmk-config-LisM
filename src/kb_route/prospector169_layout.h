/* SPDX-License-Identifier: MIT */
#pragma once
#include <lvgl.h>

/* Keep the complete WPM graph above its separate status/icon rows. */
static inline void kb_prospector169_wpm_layout(lv_obj_t *meter, lv_obj_t **bars,
                                              unsigned bar_count, lv_obj_t *peak) {
#ifndef CONFIG_LISM_XIAO147_TOUCH
    lv_obj_set_pos(meter,10,36);
    lv_obj_set_size(meter,260,84);
    lv_obj_remove_flag(meter,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(meter,LV_SCROLLBAR_MODE_OFF);
    for(unsigned i=0;i<bar_count;i++) {
        lv_obj_set_height(bars[i],84);
        lv_obj_set_style_pad_all(bars[i],0,LV_PART_MAIN);
        lv_obj_remove_flag(bars[i],LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_scrollbar_mode(bars[i],LV_SCROLLBAR_MODE_OFF);
    }
    lv_obj_set_height(peak,84);
    lv_obj_set_style_pad_all(peak,0,LV_PART_MAIN);
    lv_obj_remove_flag(peak,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(peak,LV_SCROLLBAR_MODE_OFF);
#else
    (void)meter;(void)bars;(void)bar_count;(void)peak;
#endif
}

static inline void kb_prospector169_layer_row_layout(lv_obj_t *row) {
#ifndef CONFIG_LISM_XIAO147_TOUCH
    lv_obj_set_pos(row,10,130);
    lv_obj_set_size(row,260,6);
#else
    (void)row;
#endif
}
