/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Read-only voltage-transition inference, NOT a battery-present pin or BMS.
 * A floating BAT node and a real cell can be observationally identical. USB
 * cold starts therefore remain unknown; a plateau is only a reference, never
 * proof of absence. Thresholds are UI heuristics, not cell safety limits.
 */
struct lism_battery_presence {
    bool source_seen, usb, have_sample, present, inferred;
    bool reference_valid, candidate, handoff, candidate_handoff;
    uint8_t stable_count, candidate_count;
    int32_t last_mv, reference_mv, stable_mv, candidate_mv, handoff_mv;
    uint32_t last_at, changed_at, candidate_at;
};

static inline int32_t lism_mv_delta(int32_t a, int32_t b) {
    return a > b ? a - b : b - a;
}

static inline void lism_presence_source(struct lism_battery_presence *p,
                                        bool usb, uint32_t now) {
    if (p->source_seen && p->usb == usb) { return; }
    /* A recent battery-powered observation only seeds a candidate. It does
     * not carry confirmation/percentage across VBUS: new USB samples needed. */
    bool handoff = p->source_seen && !p->usb && usb && p->present &&
                   p->have_sample && now - p->last_at <= 5000U;
    int32_t mv = p->last_mv;
    *p = (struct lism_battery_presence){.source_seen = true, .usb = usb,
        .changed_at = now, .handoff = handoff, .handoff_mv = mv};
}

static inline void lism_presence_invalidate(struct lism_battery_presence *p) {
    p->present = p->inferred = p->candidate = p->reference_valid = false;
    p->have_sample = p->handoff = p->candidate_handoff = false;
    p->stable_count = p->candidate_count = 0;
}

/* One call per COMPLETED, fresh ADC fetch, never once per UI/STAT refresh. */
static inline void lism_presence_sample(struct lism_battery_presence *p,
                                        uint32_t now, bool valid, int32_t mv) {
    if (!valid || mv < 2800 || mv > 4300) {
        lism_presence_invalidate(p);
        return;
    }
    if (now - p->changed_at < 1000U) { return; }
    /* Fast completions cannot add votes, but MUST still veto evidence. The
     * normal ZMK timer can race a telemetry request onto the same work item. */
    if (p->have_sample &&
        ((p->present && lism_mv_delta(mv, p->last_mv) >= 100) ||
         (p->candidate && (lism_mv_delta(mv, p->candidate_mv) > 30 ||
          (p->candidate_handoff && lism_mv_delta(mv, p->handoff_mv) > 80))))) {
        lism_presence_invalidate(p);
        return;
    }
    if (p->have_sample && now - p->last_at < 500U) {
        if (!p->present && lism_mv_delta(mv, p->last_mv) > 30) {
            lism_presence_invalidate(p);
        }
        return;
    }
    if (p->usb && p->have_sample && now - p->last_at > 3500U) {
        lism_presence_invalidate(p);
    }
    if (!p->usb) {
        p->present = true;
        p->inferred = false;
    } else if (p->present) {
        /* Invalidate immediately on a jump/noise, including CHG stuck LOW.
         * Smooth charging rise is allowed. No old percent/bolt is held. */
        if (lism_mv_delta(mv, p->last_mv) >= 100) {
            lism_presence_invalidate(p);
        }
    } else {
        if (p->handoff && now - p->changed_at > 7000U) { p->handoff = false; }
        bool handoff_ok = p->handoff && lism_mv_delta(mv, p->handoff_mv) <= 80;
        bool step_down = p->reference_valid && p->have_sample &&
            p->reference_mv - mv >= 150 && p->last_mv - mv >= 150;
        if (!p->candidate && (handoff_ok || step_down)) {
            p->candidate = true;
            p->candidate_handoff = handoff_ok;
            p->candidate_mv = mv;
            p->candidate_at = now;
            p->candidate_count = 0;
        }
        if (p->candidate) {
            if (lism_mv_delta(mv, p->candidate_mv) > 30 ||
                now - p->candidate_at > 6000U) {
                /* Do not repeatedly re-use a failed plateau to assert. */
                lism_presence_invalidate(p);
            } else {
                if (p->candidate_count < 4) { p->candidate_count++; }
                if (p->candidate_count == 4 && now - p->candidate_at >= 3000U) {
                    p->present = p->inferred = true;
                    p->candidate = p->handoff = p->candidate_handoff = false;
                }
            }
        } else {
            if (!p->stable_count || lism_mv_delta(mv, p->stable_mv) > 30) {
                p->stable_count = 1;
                p->stable_mv = mv;
            } else if (p->stable_count < 4) { p->stable_count++; }
            if (p->stable_count == 4) {
                p->reference_mv = p->stable_mv;
                p->reference_valid = true;
            }
        }
    }
    p->have_sample = true;
    p->last_mv = mv;
    p->last_at = now;
}
