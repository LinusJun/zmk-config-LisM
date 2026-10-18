/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Fixed physical positions, independent of keymap/layer/dongle availability.
 * 39/40 are the real right-hand bottom-row F1/F2 positions, not optional keys.
 * With both held for 1200 ms, 5..9 choose dongle / direct BLE profiles 0..3. */
struct lism_mode_chord {
    bool down[2], forwarded[2], paired, armed, suppress[2];
    uint8_t first, selection;
    int64_t pressed[2], paired_at, armed_at;
    uint8_t selector_down;
    void *context;
    void (*emit)(void *, uint32_t, bool, int64_t);
    void (*request)(void *, uint8_t);
};
void lism_mode_chord_init(struct lism_mode_chord *c,
    void (*emit)(void *,uint32_t,bool,int64_t), void (*request)(void *,uint8_t), void *context);
void lism_mode_chord_event(struct lism_mode_chord *c,uint32_t position,bool pressed,int64_t now);
void lism_mode_chord_tick(struct lism_mode_chord *c,int64_t now);
int64_t lism_mode_chord_deadline(const struct lism_mode_chord *c);
