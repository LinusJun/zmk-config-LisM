/* SPDX-License-Identifier: MIT */
#pragma once
#include "protocol.h"
#include <zmk/split/transport/types.h>
int lism_esb_encode_event(const struct zmk_split_transport_peripheral_event *ev, uint8_t *out);
int lism_esb_decode_event(const uint8_t *data, size_t len,
                          struct zmk_split_transport_peripheral_event *ev);
int lism_esb_encode_command(const struct zmk_split_transport_central_command *cmd, uint8_t *out);
int lism_esb_decode_command(const uint8_t *data, size_t len,
                            struct zmk_split_transport_central_command *cmd);
