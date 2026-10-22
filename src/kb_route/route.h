/* SPDX-License-Identifier: MIT */
#pragma once
#include <zephyr/kernel.h>
#include <zmk/split/transport/types.h>
#include <zmk/event_manager.h>
#include <zmk/events/position_state_changed.h>
#define KB_ROUTE_EVENT 126
#define KB_ROUTE_VERSION 3
#include "view.h"
#include "left_protocol.h"
struct kb_route_status { uint8_t version,mode,profile; uint32_t epoch,authority;uint8_t phase,target,station,owner_station;uint32_t receiver_boot; } __packed;
BUILD_ASSERT(sizeof(struct kb_left_control)<=sizeof(((struct zmk_split_transport_peripheral_event*)0)->data));
BUILD_ASSERT(sizeof(struct kb_route_status)<=sizeof(((struct zmk_split_transport_peripheral_event*)0)->data));
void kb_route_left_control(const struct kb_left_control *p);
bool kb_route_left_allowed(const struct kb_left_packet *p);
void kb_left_track(const struct zmk_split_transport_peripheral_event *e);
uint32_t kb_private_authority(void);
void kb_left_set_route(uint32_t epoch,uint8_t phase,uint8_t target,uint8_t station,uint32_t receiver_boot);
int kb_route_select_dongle(uint8_t station);
void kb_uplink_station_request(uint8_t station);
uint8_t kb_uplink_station_current(void);
uint32_t kb_receiver_boot(void);
uint32_t kb_receiver_peer_boot(unsigned source);
int kb_route_discovery_received(const struct kb_discovery *probe);
bool kb_route_discovery_release(const struct kb_discovery *probe);
bool kb_route_status_valid(const struct kb_route_status *status);
bool kb_route_probe_offer(uint8_t station,uint32_t cookie,uint32_t boot);
bool kb_uplink_is_probe(const uint8_t *frame,size_t len);
void kb_route_probe_transmitted(uint8_t station,uint32_t cookie);
bool kb_route_target_ready(void);
uint32_t kb_receiver_route_epoch(void);
void kb_route_flush_commands(void);
void kb_receiver_fence_commands(void);
void kb_route_left_telemetry(uint8_t *battery,uint8_t *online);
void kb_route_request(uint8_t mode);
int kb_route_select(uint8_t profile);
void kb_route_snapshot(struct kb_route_view *out);
void kb_route_status_received(const struct kb_route_status *status);
bool kb_route_capture(zmk_event_t *event);
bool kb_route_ble_enabled(void);
bool kb_route_output_enabled(void);
bool kb_route_input(unsigned source,const struct zmk_split_transport_peripheral_event *event);
int kb_route_relay(unsigned source,const struct zmk_split_transport_peripheral_event *event);
int kb_uplink_send(unsigned source,const struct zmk_split_transport_peripheral_event *event);
bool kb_uplink_command(uint8_t pipe,const uint8_t *frame,size_t length,uint32_t wire_epoch,uint32_t peer);
uint32_t kb_route_command_epoch(void);
uint32_t kb_uplink_peer_boot(void);
bool kb_route_receiver_output_allowed(uint16_t usage);
bool kb_uplink_valid(uint8_t pipe,const uint8_t *frame,size_t length);
void kb_route_ble_refresh(void);
void kb_route_replay(const struct zmk_position_state_changed *event);
bool kb_route_dongle_key_allowed(void);

void kb_uplink_reset(void);

void kb_route_left_alive(void);

bool kb_uplink_connected(void);

bool kb_route_accept_pointer(void);
void kb_route_pointer_reset(void);
void kb_route_listener_reset(void);
bool kb_route_queue_idle(void);
bool kb_route_holdtap_idle(void);
bool kb_route_combo_idle(void);

bool kb_route_mirror_enabled(void);
