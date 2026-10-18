/* SPDX-License-Identifier: MIT */
#pragma once
#include <zmk/split/transport/central.h>
#include <zephyr/bluetooth/conn.h>
extern struct zmk_split_transport_central lism_esb_central;
/* Hooks into the existing single split worker and its input cleanup fence. */
int lism_esb_queue_event(uint8_t source, uint32_t generation,
                         const struct zmk_split_transport_peripheral_event *event);
void lism_esb_queue_fault(uint8_t source);
void lism_esb_peer_ready(uint8_t source);
void lism_esb_control_start(void);
int lism_esb_control_command(uint8_t source, struct zmk_split_transport_central_command command);
int lism_esb_control_sources(uint8_t *sources);
