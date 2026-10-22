#pragma once
#include <lvgl.h>
#include <zephyr/kernel.h>
struct zmk_widget_output {
    sys_snode_t node;
    lv_obj_t *obj;
};
int zmk_widget_output_init(struct zmk_widget_output *widget, lv_obj_t *parent);
lv_obj_t *zmk_widget_output_obj(struct zmk_widget_output *widget);
