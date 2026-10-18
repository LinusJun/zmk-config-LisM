#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <zephyr/bluetooth/addr.h>

#define LISM_SPLIT_SET_SLOTS 6
#define LISM_SPLIT_SET_CANDIDATES 8
#define LISM_SPLIT_SET_POSITIONS 128
struct lism_split_set_candidate {
    uint32_t token;
    bt_addr_le_t address;
    char name[32];
    int8_t rssi;
};
/* Enrollment never overwrites an occupied slot or removes an existing bond. */
int lism_split_sets_register_begin(uint8_t slot);
void lism_split_sets_register_cancel(void);
size_t lism_split_sets_candidates(struct lism_split_set_candidate *out, size_t capacity);
int lism_split_sets_register_select(uint32_t token);
void lism_split_sets_get_status(char *out, size_t capacity);
int lism_split_sets_active_set(void); /* -1 none, 0 A, 1 B */
uint8_t lism_split_sets_logical_slot(uint8_t role); /* role 0 left / 1 right */
void lism_split_sets_connected_snapshot(bool out[LISM_SPLIT_SET_SLOTS]);

/* Sole scanner and transport hooks; no independent scanner. */
void lism_split_sets_observe(const bt_addr_le_t *addr, const char *name, int8_t rssi);
int lism_split_sets_reserve(uint8_t slot);
void lism_split_sets_connected(uint8_t slot);
void lism_split_sets_ready(uint8_t slot);
void lism_split_sets_released(uint8_t slot);
void lism_split_sets_fault(uint8_t slot);
bool lism_split_sets_take_fault(uint8_t slot);
uint32_t lism_split_sets_generation(uint8_t slot);
bool lism_split_sets_dispatch(uint8_t slot, uint32_t generation);
void lism_split_sets_applied(uint8_t slot, uint16_t position, bool pressed);
bool lism_split_sets_take_cleanup(uint8_t slot, uint8_t positions[16]);
void lism_split_sets_cleanup_done(uint8_t slot);
/* Marker is intercepted by the input thread, never exposed as user input. */
int lism_split_input_fence_enqueue(uint8_t slot, uint32_t generation);
void lism_split_sets_input_fence_done(uint8_t slot, uint32_t generation);

/* Generated BLE source accessors: known-first lookup and explicit empty-slot bind. */
int lism_split_sets_find_addr(const bt_addr_le_t *addr);
int lism_split_sets_bind_addr(uint8_t slot, const bt_addr_le_t *addr);
int lism_split_sets_unbind_addr(uint8_t slot, const bt_addr_le_t *expected);
bool lism_split_sets_slot_registered(uint8_t slot);
int lism_split_sets_read_addr(uint8_t slot, bt_addr_le_t *out);
