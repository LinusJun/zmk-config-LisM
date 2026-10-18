/* SPDX-License-Identifier: MIT
 * Transport-independent coordinator. Not a radio driver or flashable firmware.
 * All entrypoints must run on the same work queue, never from a radio ISR.
 */
#pragma once
#include <stdbool.h>
#include <stdint.h>

enum lism_output_mode { LISM_OUTPUT_USB, LISM_OUTPUT_BLE, LISM_OUTPUT_ESB };
enum lism_link_mode { LISM_LINK_BLE, LISM_LINK_ESB };
enum lism_switch_phase {
    LISM_SWITCH_IDLE, LISM_SWITCH_PREPARE, LISM_SWITCH_DRAIN,
    LISM_SWITCH_ACTIVATE, LISM_SWITCH_SYNC, LISM_SWITCH_RECOVER,
};
enum lism_switch_action {
    LISM_ACTION_NONE, LISM_ACTION_PREPARE, LISM_ACTION_DRAIN,
    LISM_ACTION_ACTIVATE, LISM_ACTION_SYNC, LISM_ACTION_RECOVER,
    LISM_ACTION_RESUME,
};
struct lism_mode { enum lism_output_mode output; uint8_t profile; };
struct lism_switch {
    struct lism_mode current, target;
    enum lism_switch_phase phase;
    uint32_t epoch;
    uint8_t halves, required, ready, synced;
    uint64_t deadline;
    bool input_frozen;
};
/* Physical slots 0/1 = LisM A, 4/5 = LisM B. Slots 2/3 are not LisM halves.
 * Wireless output receiver is bit 6. A missing but registered half is NOT
 * silently excluded: caller must resolve its reconnect policy before begin.
 */
#define LISM_SWITCH_RECEIVER (1u << 6)
enum lism_link_mode lism_mode_link(struct lism_mode mode);
void lism_switch_init(struct lism_switch *s, struct lism_mode current, uint32_t epoch);
int lism_switch_begin(struct lism_switch *s, struct lism_mode target,
                      uint8_t halves, uint64_t now);
enum lism_switch_action lism_switch_ready(struct lism_switch *s, uint8_t peer,
                                         uint32_t epoch);
enum lism_switch_action lism_switch_drained(struct lism_switch *s, uint32_t epoch);
enum lism_switch_action lism_switch_activated(struct lism_switch *s,
                                             uint32_t epoch, bool success);
enum lism_switch_action lism_switch_synced(struct lism_switch *s, uint8_t peer,
                                          uint32_t epoch);
enum lism_switch_action lism_switch_tick(struct lism_switch *s, uint64_t now);
enum lism_switch_action lism_switch_recovered(struct lism_switch *s,
                                             uint32_t epoch, bool success);
bool lism_switch_accept_input(const struct lism_switch *s, uint32_t epoch);
