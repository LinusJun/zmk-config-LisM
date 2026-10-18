/* SPDX-License-Identifier: MIT */
#pragma once
#include <zephyr/bluetooth/conn.h>
#include <zmk/event_manager.h>
#include <zmk/events/position_state_changed.h>
#include <zephyr/input/input.h>
#include <zmk/split/transport/peripheral.h>
#define LISM_COMPANION_UUID BT_UUID_128_ENCODE(0x9ddf4102,0xc125,0x484c,0x9832,0xb5261b4d7037)
bool lism_standalone_direct(void);
bool lism_standalone_running(void);
bool lism_standalone_host_conn(struct bt_conn *conn);
bool lism_standalone_control_conn(struct bt_conn *conn);
bool lism_standalone_left_addr(const bt_addr_le_t *addr);
bool lism_standalone_pairing_allowed(struct bt_conn *conn);
void lism_standalone_peer_poll(struct bt_conn *control);
void lism_standalone_link_wake(void);
void lism_standalone_left_connected(bool connected);
bool lism_standalone_event(zmk_event_t *event);
void lism_standalone_replay(const struct zmk_position_state_changed *event);
int lism_standalone_esb_report(const struct zmk_split_transport_peripheral_event *event);
void lism_standalone_esb_enable(bool enabled);
void lism_standalone_central_enable(bool enabled);
void lism_standalone_host_update(void);
void lism_standalone_hog_purge(void);

void lism_standalone_route_input(struct input_event *event);

int lism_standalone_profile_select(uint8_t index);
void lism_standalone_pointer_reset(void);
void lism_standalone_listener_reset(void);

bool lism_standalone_split_conn(struct bt_conn *conn);

void lism_standalone_note_activity(void);
void lism_standalone_host_adv_consumed(void);

bool lism_standalone_central_drained(void);

bool lism_standalone_companion_ready(void);
