/* SPDX-License-Identifier: MIT */
#pragma once
#include <zephyr/kernel.h>
#include <zmk/split/transport/types.h>
#define LISM_SNAPSHOT_TYPE 127
#define LISM_KEYS 46
#define LISM_LEASE_MS 1500
#define LISM_WIRE_HEADER 20
struct lism_snapshot { uint64_t keys; uint8_t buttons,battery; } __packed;
struct esb_event_envelope;
BUILD_ASSERT(sizeof(struct lism_snapshot) <= sizeof(((struct zmk_split_transport_peripheral_event *)0)->data));
void lism_tx_lock(void);
void lism_tx_unlock(void);
void lism_track_event(const struct zmk_split_transport_peripheral_event *event);
bool lism_valid_event(uint8_t pipe, const struct esb_event_envelope *env);
int lism_handle_event(uint8_t source, const struct zmk_split_transport_peripheral_event *event);
bool lism_connected(unsigned source);
void lism_ack_seen(void);
void lism_notify_status(void);
bool lism_radio_ready(void);
bool lism_wire_valid(uint8_t pipe,const uint8_t *frame,size_t length);
