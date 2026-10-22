/* USB-only diagnostic: keep OPERATOR layout space, expose no BLE profiles. */
#include "output.h"
#include <fonts.h>
int zmk_widget_output_init(struct zmk_widget_output *widget, lv_obj_t *parent) {
    widget->obj = lv_obj_create(parent);
    lv_obj_set_size(widget->obj, 116, 62);
    lv_obj_set_style_bg_opa(widget->obj, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(widget->obj, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(widget->obj, 0, LV_PART_MAIN);
    lv_obj_t *label = lv_label_create(widget->obj);
    lv_label_set_text(label, "USB");
    lv_obj_set_style_text_font(label, &FG_Medium_20, LV_PART_MAIN);
    lv_obj_set_style_text_color(label, lv_color_hex(0xb1e5f0), LV_PART_MAIN);
    lv_obj_align(label, LV_ALIGN_TOP_MID, 0, 2);
    label = lv_label_create(widget->obj);
    lv_label_set_text(label, "ESB v3.3");
    lv_obj_set_style_text_font(label, &FG_Medium_20, LV_PART_MAIN);
    lv_obj_align(label, LV_ALIGN_BOTTOM_MID, 0, -2);
    return 0;
}
lv_obj_t *zmk_widget_output_obj(struct zmk_widget_output *widget) { return widget->obj; }
