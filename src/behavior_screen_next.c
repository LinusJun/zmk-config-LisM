#define DT_DRV_COMPAT lism_behavior_screen_next
#include <zephyr/device.h>
#include <drivers/behavior.h>
#include <zmk/behavior.h>
#include "lism_screen.h"
static int pressed(struct zmk_behavior_binding *b, struct zmk_behavior_binding_event e) {
    ARG_UNUSED(b); ARG_UNUSED(e);
#if IS_ENABLED(CONFIG_LISM_BLE_INPUT_BRIDGE)
    lism_screen_request_next();
#endif
    return ZMK_BEHAVIOR_OPAQUE;
}
static int released(struct zmk_behavior_binding *b, struct zmk_behavior_binding_event e) {
    ARG_UNUSED(b); ARG_UNUSED(e); return ZMK_BEHAVIOR_OPAQUE;
}
static const struct behavior_driver_api api = {.binding_pressed=pressed,.binding_released=released};
BEHAVIOR_DT_INST_DEFINE(0,NULL,NULL,NULL,NULL,POST_KERNEL,CONFIG_KERNEL_INIT_PRIORITY_DEFAULT,&api);
