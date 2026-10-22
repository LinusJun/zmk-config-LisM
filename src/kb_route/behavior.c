/* SPDX-License-Identifier: MIT */
#define DT_DRV_COMPAT lism_behavior_kb_route
#include <zephyr/device.h>
#include <drivers/behavior.h>
#include <zmk/behavior.h>
#include "route.h"
static int press(struct zmk_behavior_binding *b,struct zmk_behavior_binding_event e) {
    ARG_UNUSED(e);if(b->param1<2)kb_route_request(b->param1);else if(b->param1>=10 && b->param1<15)kb_route_select(b->param1-10);
    return ZMK_BEHAVIOR_OPAQUE;
}
static int release(struct zmk_behavior_binding *b,struct zmk_behavior_binding_event e) {ARG_UNUSED(b);ARG_UNUSED(e);return ZMK_BEHAVIOR_OPAQUE;}
static const struct behavior_driver_api api={.binding_pressed=press,.binding_released=release};
BEHAVIOR_DT_INST_DEFINE(0,NULL,NULL,NULL,NULL,POST_KERNEL,CONFIG_KERNEL_INIT_PRIORITY_DEFAULT,&api);
