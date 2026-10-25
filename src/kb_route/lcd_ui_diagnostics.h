/* SPDX-License-Identifier: MIT */
#pragma once
#include <lvgl.h>
#include <lism_lcd_diag.h>
#include <kb_route/mirror.h>
#include <zmk/endpoints.h>
static lv_obj_t *lism_lcd_ui_root, *lism_lcd_ui_number, *lism_lcd_ui_ble;
static void lism_lcd_ui_sample(lv_timer_t *timer)
{
    (void)timer;
    lv_area_t a;
    lv_obj_get_coords(lism_lcd_ui_number,&a);
    lism_lcd_trace_ui(lv_color_to_u32(lv_obj_get_style_bg_color(lism_lcd_ui_root,LV_PART_MAIN)) & 0xffffff,
        lv_label_get_text(lism_lcd_ui_number),a.x1,a.y1,a.x2,a.y2);
    struct kb_mirror mirror={0};
    int owner=kb_mirror_snapshot(&mirror);
    lv_obj_get_coords(lism_lcd_ui_ble,&a);
    lism_lcd_trace_output_ui(
        lv_color_to_u32(lv_obj_get_style_bg_color(lism_lcd_ui_ble,LV_PART_MAIN)) & 0xffffff,
        lv_obj_get_style_bg_opa(lism_lcd_ui_ble,LV_PART_MAIN),
        lv_obj_get_state(lism_lcd_ui_ble),lv_obj_is_visible(lism_lcd_ui_ble),owner,
        zmk_endpoint_get_selected().transport,mirror.profile,a.x1,a.y1,a.x2,a.y2);
}
static inline void lism_lcd_ui_diagnostics_start(lv_obj_t *root, lv_obj_t *number,
                                               lv_obj_t *ble)
{
    lism_lcd_ui_root=root; lism_lcd_ui_number=number; lism_lcd_ui_ble=ble;
    lism_lcd_ui_sample(NULL);
    lv_timer_create(lism_lcd_ui_sample,5000,NULL);
}
