/* SPDX-License-Identifier: MIT */
#include "codec.h"
#include <errno.h>
#include <string.h>

int lism_esb_encode_event(const struct zmk_split_transport_peripheral_event *e, uint8_t *p) {
    p[0] = e->type;
    switch (e->type) {
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT:
        if (e->data.key_position_event.position >= 128 || e->data.key_position_event.pressed > 1)
            return -EINVAL;
        p[1] = e->data.key_position_event.position; p[2] = e->data.key_position_event.pressed;
        return 3;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT:
        p[1] = e->data.input_event.reg; p[2] = e->data.input_event.sync;
        p[3] = e->data.input_event.type; p[4] = e->data.input_event.code;
        p[5] = e->data.input_event.code >> 8;
        lism_esb_put_u32(p + 6, e->data.input_event.value); return 10;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT:
        p[1] = e->data.battery_event.level; return p[1] <= 100 ? 2 : -EINVAL;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_SENSOR_EVENT:
        p[1] = e->data.sensor_event.sensor_index;
        lism_esb_put_u32(p + 2, e->data.sensor_event.channel_data.channel);
        lism_esb_put_u32(p + 6, e->data.sensor_event.channel_data.value.val1);
        lism_esb_put_u32(p + 10, e->data.sensor_event.channel_data.value.val2); return 14;
    default: return -ENOTSUP;
    }
}
int lism_esb_decode_event(const uint8_t *p, size_t n,
                          struct zmk_split_transport_peripheral_event *e) {
    if (!n) return -EINVAL;
    memset(e, 0, sizeof(*e)); e->type = p[0];
    switch (e->type) {
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT:
        if (n != 3 || p[1] >= 128 || p[2] > 1) return -EINVAL;
        e->data.key_position_event.position = p[1]; e->data.key_position_event.pressed = p[2];
        return 0;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT:
        if (n != 10 || p[2] > 1) return -EINVAL;
        e->data.input_event.reg = p[1]; e->data.input_event.sync = p[2];
        e->data.input_event.type = p[3]; e->data.input_event.code = p[4] | p[5] << 8;
        e->data.input_event.value = (int32_t)lism_esb_u32(p + 6); return 0;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT:
        if (n != 2 || p[1] > 100) return -EINVAL;
        e->data.battery_event.level = p[1]; return 0;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_SENSOR_EVENT:
        if (n != 14) return -EINVAL;
        e->data.sensor_event.sensor_index = p[1];
        e->data.sensor_event.channel_data.channel = lism_esb_u32(p + 2);
        e->data.sensor_event.channel_data.value.val1 = (int32_t)lism_esb_u32(p + 6);
        e->data.sensor_event.channel_data.value.val2 = (int32_t)lism_esb_u32(p + 10); return 0;
    default: return -ENOTSUP;
    }
}
int lism_esb_encode_command(const struct zmk_split_transport_central_command *c, uint8_t *p) {
    p[0] = c->type;
    switch (c->type) {
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_POLL_EVENTS: return 1;
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_PHYSICAL_LAYOUT:
        p[1] = c->data.set_physical_layout.layout_idx; return 2;
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_HID_INDICATORS:
        p[1] = c->data.set_hid_indicators.indicators; return 2;
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR:
        if (!memchr(c->data.invoke_behavior.behavior_dev, 0, 16)) return -EINVAL;
        memcpy(p + 1, c->data.invoke_behavior.behavior_dev, 16);
        lism_esb_put_u32(p + 17, c->data.invoke_behavior.param1);
        lism_esb_put_u32(p + 21, c->data.invoke_behavior.param2);
        lism_esb_put_u32(p + 25, c->data.invoke_behavior.position);
        p[29] = c->data.invoke_behavior.event_source; p[30] = c->data.invoke_behavior.state;
        return 31;
    default: return -ENOTSUP;
    }
}
int lism_esb_decode_command(const uint8_t *p, size_t n,
                            struct zmk_split_transport_central_command *c) {
    if (!n) return -EINVAL;
    memset(c, 0, sizeof(*c)); c->type = p[0];
    switch (c->type) {
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_POLL_EVENTS: return n == 1 ? 0 : -EINVAL;
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_PHYSICAL_LAYOUT:
        if (n != 2) return -EINVAL;
        c->data.set_physical_layout.layout_idx = p[1]; return 0;
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_HID_INDICATORS:
        if (n != 2) return -EINVAL;
        c->data.set_hid_indicators.indicators = p[1]; return 0;
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR:
        if (n != 31 || !memchr(p + 1, 0, 16) || p[30] > 1) return -EINVAL;
        memcpy(c->data.invoke_behavior.behavior_dev, p + 1, 16);
        c->data.invoke_behavior.param1 = lism_esb_u32(p + 17);
        c->data.invoke_behavior.param2 = lism_esb_u32(p + 21);
        c->data.invoke_behavior.position = lism_esb_u32(p + 25);
        c->data.invoke_behavior.event_source = p[29]; c->data.invoke_behavior.state = p[30];
        return 0;
    default: return -ENOTSUP;
    }
}
