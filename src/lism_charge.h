/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Private, versioned telemetry. Never overload the standard BAS percentage. */
#define LISM_CHARGE_WIRE_SIZE 4
#define LISM_CHARGE_VERSION 2
#define LISM_CHARGE_USB 1
#define LISM_CHARGE_STAT_VALID 2
#define LISM_CHARGE_STAT_ACTIVE 4
#define LISM_CHARGE_LEVEL_VALID 8
#define LISM_CHARGE_BATTERY_CONFIRMED 16
#define LISM_CHARGE_BATTERY_INFERRED 32
/* Distinguishes a telemetry-capable peer reporting unknown from legacy BAS. */
#define LISM_CHARGE_OBSERVED 64
#define LISM_CHARGE_FLAGS_ALL 127
#define LISM_CHARGE_STALE_MS 10000U

struct lism_charge_state {
    uint8_t flags;
    uint8_t percent;
};

/* Prefer fresh private telemetry over BAS, which cannot encode 'unknown'. */
static inline bool lism_charge_level(struct lism_charge_state s, bool bas_seen,
                                     uint8_t bas_percent, uint8_t *level) {
    if (s.flags & LISM_CHARGE_OBSERVED) {
        *level = s.percent;
        return (s.flags & LISM_CHARGE_LEVEL_VALID) != 0;
    }
    *level = bas_percent;
    return bas_seen;
}

static inline bool lism_charge_usb_display(struct lism_charge_state s) {
    return (s.flags & LISM_CHARGE_USB) && !(s.flags & LISM_CHARGE_LEVEL_VALID);
}

static inline bool lism_charge_show_bolt(struct lism_charge_state s) {
    const uint8_t required = LISM_CHARGE_USB | LISM_CHARGE_STAT_VALID |
        LISM_CHARGE_STAT_ACTIVE | LISM_CHARGE_BATTERY_INFERRED | LISM_CHARGE_LEVEL_VALID;
    return (s.flags & required) == required;
}

/* data[3] is reserved and must be zero. Unknown versions fail closed. */
static inline bool lism_charge_decode(const uint8_t *data, unsigned length,
                                      struct lism_charge_state *out) {
    *out = (struct lism_charge_state){0};
    if (!data || length != LISM_CHARGE_WIRE_SIZE || data[0] != LISM_CHARGE_VERSION ||
        (data[1] & ~LISM_CHARGE_FLAGS_ALL) || data[2] > 100 || data[3] ||
        !(data[1] & LISM_CHARGE_OBSERVED) ||
        ((data[1] & LISM_CHARGE_BATTERY_CONFIRMED) && (data[1] & LISM_CHARGE_USB)) ||
        ((data[1] & LISM_CHARGE_BATTERY_INFERRED) && !(data[1] & LISM_CHARGE_USB)) ||
        ((data[1] & LISM_CHARGE_LEVEL_VALID) &&
         !(data[1] & (LISM_CHARGE_BATTERY_CONFIRMED | LISM_CHARGE_BATTERY_INFERRED))) ||
        ((data[1] & LISM_CHARGE_STAT_ACTIVE) &&
         (data[1] & (LISM_CHARGE_USB | LISM_CHARGE_STAT_VALID)) !=
         (LISM_CHARGE_USB | LISM_CHARGE_STAT_VALID))) {
        return false;
    }
    out->flags = data[1];
    out->percent = data[2];
    return true;
}

struct lism_charge_filter {
    uint8_t low_samples;
    uint32_t sampled_at;
    struct lism_charge_state state;
};

static inline void lism_charge_observe(struct lism_charge_filter *f, uint32_t now,
                                      bool usb, int stat, bool battery_evidence,
                                      bool level_valid, uint8_t percent) {
    /* Four fresh 250ms samples to assert, immediate removal on high/error/USB loss. */
    if (now - f->sampled_at > 750 || !usb || stat != 0) { f->low_samples = 0; }
    if (usb && stat == 0 && f->low_samples < 4) { f->low_samples++; }
    f->sampled_at = now;
    f->state = (struct lism_charge_state){.flags = LISM_CHARGE_OBSERVED};
    if (usb) { f->state.flags |= LISM_CHARGE_USB; }
    if (stat == 0 || stat == 1) { f->state.flags |= LISM_CHARGE_STAT_VALID; }
    if (f->low_samples == 4) { f->state.flags |= LISM_CHARGE_STAT_ACTIVE; }
    if (battery_evidence) {
        f->state.flags |= usb ? LISM_CHARGE_BATTERY_INFERRED : LISM_CHARGE_BATTERY_CONFIRMED;
    }
    if (level_valid && percent <= 100) {
        f->state.flags |= LISM_CHARGE_LEVEL_VALID;
        f->state.percent = percent;
    }
}

struct lism_charge_state lism_charge_local(void);
struct lism_charge_state lism_charge_peripheral(uint8_t slot);
