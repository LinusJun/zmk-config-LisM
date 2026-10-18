/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Read-only voltage-transition inference, NOT a battery-present pin or BMS.
 * A floating BAT node and a real cell can be observationally identical. USB
 * cold-start plateaus remain unknown. The separate step-down path is also
 * inference, not hardware proof. Thresholds are UI heuristics, not safety limits.
 */
enum lism_presence_reason {
    LISM_PRESENCE_OK, LISM_PRESENCE_SAMPLE_ERROR, LISM_PRESENCE_RANGE,
    LISM_PRESENCE_JUMP, LISM_PRESENCE_GAP, LISM_PRESENCE_BASELINE,
    LISM_PRESENCE_NOISE, LISM_PRESENCE_TIMEOUT, LISM_PRESENCE_EPOCH,
    LISM_PRESENCE_NO_SEED,
};
struct lism_battery_presence {
    bool source_seen, usb, have_sample, present, inferred;
    bool reference_valid, candidate, handoff, candidate_handoff;
    uint8_t stable_count, candidate_count;
    uint8_t last_reason;
    int32_t last_mv, reference_mv, stable_mv, candidate_mv, handoff_mv;
    uint32_t last_at, changed_at, candidate_at, seed_at;
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
        .changed_at = now, .handoff = handoff, .handoff_mv = mv, .seed_at = now,
        .last_reason = usb && !handoff ? LISM_PRESENCE_NO_SEED : LISM_PRESENCE_OK};
}

static inline void lism_presence_invalidate(struct lism_battery_presence *p) {
    p->present = p->inferred = p->candidate = p->reference_valid = false;
    p->have_sample = p->handoff = p->candidate_handoff = false;
    p->stable_count = p->candidate_count = 0;
}

static inline void lism_presence_drop(struct lism_battery_presence *p,
                                      enum lism_presence_reason reason) {
    lism_presence_invalidate(p);
    p->last_reason = reason;
}

/* No stale display hold: revoke presence/votes now. A fresh, immutable seed
 * may only support a NEW four-sample confirmation within its original 7s.
 * Once revoked, repeated failures cannot create or extend another seed. */
static inline void lism_presence_retry(struct lism_battery_presence *p,
                                       uint32_t now, enum lism_presence_reason reason) {
    /* Repeated timer notices must not erase the actual preceding veto cause. */
    if (reason == LISM_PRESENCE_GAP && p->last_reason != LISM_PRESENCE_OK &&
        !p->present && !p->handoff && !p->candidate && !p->have_sample &&
        !p->reference_valid) { return; }
    if (!p->handoff && p->usb && p->present && p->have_sample &&
        now - p->last_at <= 3500U) {
        p->handoff = true;
        p->handoff_mv = p->last_mv;
        p->seed_at = p->last_at;
    }
    bool expired = p->handoff && now - p->seed_at > 7000U;
    bool retry = p->usb && p->handoff && !expired;
    lism_presence_invalidate(p);
    p->handoff = retry;
    p->last_reason = expired ? LISM_PRESENCE_TIMEOUT : reason;
}

/* One call per COMPLETED, fresh ADC fetch, never once per UI/STAT refresh. */
static inline void lism_presence_sample(struct lism_battery_presence *p,
                                        uint32_t now, bool valid, int32_t mv) {
    if (!valid) {
        lism_presence_retry(p, now, LISM_PRESENCE_SAMPLE_ERROR);
        return;
    }
    if (mv < 2800 || mv > 4300) {
        lism_presence_drop(p, LISM_PRESENCE_RANGE);
        return;
    }
    if (now - p->changed_at < 1000U) { return; }
    /* Fast completions cannot add votes, but MUST still veto evidence. The
     * normal ZMK timer can race a telemetry request onto the same work item. */
    if (p->handoff && now - p->seed_at > 7000U) {
        lism_presence_drop(p, LISM_PRESENCE_TIMEOUT);
        return;
    }
    if (p->handoff && lism_mv_delta(mv, p->handoff_mv) > 80) {
        lism_presence_drop(p, LISM_PRESENCE_BASELINE);
        return;
    }
    if (p->have_sample && (p->present || p->candidate) &&
        lism_mv_delta(mv, p->last_mv) >= 100) {
        lism_presence_drop(p, LISM_PRESENCE_JUMP);
        return;
    }
    if (p->candidate && lism_mv_delta(mv, p->candidate_mv) > 30) {
        if (p->candidate_handoff) { lism_presence_retry(p, now, LISM_PRESENCE_NOISE); }
        else { lism_presence_drop(p, LISM_PRESENCE_NOISE); }
        return;
    }
    if (p->have_sample && now - p->last_at < 500U) {
        if (!p->present && lism_mv_delta(mv, p->last_mv) > 30) {
            if (p->handoff) { lism_presence_retry(p, now, LISM_PRESENCE_NOISE); }
            else { lism_presence_drop(p, LISM_PRESENCE_NOISE); }
        }
        return;
    }
    if (p->usb && p->have_sample && now - p->last_at > 3500U) {
        lism_presence_retry(p, now, LISM_PRESENCE_GAP);
    }
    if (!p->usb) {
        p->present = true;
        p->inferred = false;
    } else if (p->present) {
        /* Hard jumps were already rejected above; smooth charging is allowed. */
    } else {
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
                lism_presence_drop(p, LISM_PRESENCE_NOISE);
            } else {
                if (p->candidate_count < 4) { p->candidate_count++; }
                if (p->candidate_count == 4 && now - p->candidate_at >= 3000U) {
                    /* Never let candidate's own window extend the seed deadline. */
                    if (p->candidate_handoff && now - p->seed_at > 7000U) {
                        lism_presence_drop(p, LISM_PRESENCE_TIMEOUT);
                        return;
                    }
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
