#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include "xiao147_orientation.h"

/* Rate-based relative air mouse, not accelerometer position integration.
 * Raw counts at 245 dps; 6500 count*ms/pixel retains the old 20ms/325 gain.
 * Filtering and integration both use actual elapsed time. */
struct xiao147_air_motion {
    int32_t bias[3], sum[3];
    int16_t minimum[3], maximum[3];
    int32_t remainder[2];
    int32_t filtered[2]; /* Q8 corrected gyro counts */
    int16_t gravity[3];
    unsigned samples;
    uint32_t started, last_ms, gravity_ms, stable_since;
    uint32_t calibration_ms;
    bool calibrating, bias_valid, have_time, have_gravity, gravity_stable;
    bool have_calibration_time;
};

static inline void xiao147_air_clear(struct xiao147_air_motion *s)
{
    s->remainder[0] = s->remainder[1] = 0;
    s->filtered[0] = s->filtered[1] = 0;
    s->have_time = false;
}

static inline void xiao147_air_begin(struct xiao147_air_motion *s, uint32_t now)
{
    xiao147_air_clear(s);
    s->samples = 0;
    s->started = now;
    s->calibrating = true; /* Keep the last valid bias until a stable replacement. */
    s->have_gravity = false;
    s->have_calibration_time = false;
}

/* Fresh accelerometer support is required for zero learning, never as a
 * substitute for gyro velocity. Constant rotation about gravity can still
 * fool stationary detection; admission bounds deliberately remain small. */
static inline void xiao147_air_gravity(struct xiao147_air_motion *s,
                                      uint32_t now, const int16_t a[3])
{
    int64_t norm = 0;
    bool stable = s->have_gravity && now - s->gravity_ms <= 80;
    for (int i = 0; i < 3; ++i) {
        norm += (int64_t)a[i] * a[i];
        if (abs((int)a[i] - s->gravity[i]) > 300) { stable = false; }
        s->gravity[i] = a[i];
    }
    s->gravity_stable = stable && norm >= 13000LL * 13000 && norm <= 19660LL * 19660;
    s->have_gravity = true; s->gravity_ms = now;
}

static inline struct xiao147_xy xiao147_air_sample(struct xiao147_air_motion *s,
    uint32_t now, enum xiao147_orientation pose, const int16_t raw[3])
{
    if (s->calibrating || !s->bias_valid) {
        if (now - s->started >= 3000) {
            /* Retry automatically; never emit uncalibrated zero-bias motion. */
            s->samples = 0;
            s->started = now;
            s->calibrating = !s->bias_valid;
        }
        if (s->calibrating || !s->bias_valid) {
            bool stable = s->have_gravity && s->gravity_stable && now - s->gravity_ms <= 40;
            /* Duplicate/stalled polls cannot manufacture a stationary window. */
            if (s->have_calibration_time &&
                (now == s->calibration_ms || now - s->calibration_ms > 80)) {
                s->samples = 0; stable = false;
            }
            s->have_calibration_time = true; s->calibration_ms = now;
            for (int i = 0; i < 3; i++) {
                /* Calibration assumes the user holds the device still on
                 * enabling. Gyro alone cannot distinguish true bias from
                 * perfectly constant rotation. Use a conservative admission
                 * bound and never silently move an established zero far. */
                /* This panel's observed stationary bias reaches -524 counts.
                 * 160 rejected every sample forever. 800 is an empirical
                 * admission bound, not an IC specification or motion proof. */
                if (abs(raw[i]) > 800 ||
                    (s->bias_valid && abs((int)raw[i] - s->bias[i]) > 256)) {
                    stable = false;
                }
                if (s->samples && (abs((int)raw[i] - s->minimum[i]) > 320 ||
                                   abs((int)raw[i] - s->maximum[i]) > 320)) {
                    stable = false;
                }
            }
            if (!stable) { s->samples = 0; }
            else {
                for (int i = 0; i < 3; i++) {
                    if (!s->samples) { s->sum[i] = 0; s->minimum[i] = s->maximum[i] = raw[i]; }
                    if (raw[i] < s->minimum[i]) { s->minimum[i] = raw[i]; }
                    if (raw[i] > s->maximum[i]) { s->maximum[i] = raw[i]; }
                    s->sum[i] += raw[i];
                }
                if (!s->samples) { s->stable_since = now; }
                if (++s->samples >= 80 && now - s->stable_since >= 1980) {
                    for (int i = 0; i < 3; i++) { s->bias[i] = s->sum[i] / (int)s->samples; }
                    s->bias_valid = true;
                    s->calibrating = false;
                }
            }
            xiao147_air_clear(s);
            return (struct xiao147_xy){0, 0};
        }
    }
    if (!s->bias_valid) { xiao147_air_clear(s); return (struct xiao147_xy){0, 0}; }
    uint32_t dt = now - s->last_ms;
    bool first = !s->have_time;
    s->last_ms = now;
    if (first || dt > 80) {
        xiao147_air_clear(s); s->have_time = true;
        return (struct xiao147_xy){0, 0};
    }
    if (!dt) { return (struct xiao147_xy){0, 0}; }
    struct xiao147_xy v = xiao147_gyro_to_logical(pose, raw[0] - s->bias[0],
                                                raw[1] - s->bias[1]);
    int32_t values[2] = {v.x, v.y};
    int steps[2];
    for (int i = 0; i < 2; i++) {
        /* Soft deadband plus immediate rest cutoff: no filtered drift tail. */
        if (abs(values[i]) <= 80) {
            s->remainder[i] = s->filtered[i] = 0; steps[i] = 0; continue;
        }
        values[i] += values[i] > 0 ? -80 : 80;
        int32_t target = values[i] * 256;
        s->filtered[i] += (int32_t)(((int64_t)target - s->filtered[i]) * dt / (30 + dt));
        int64_t integrated = s->remainder[i] + (int64_t)s->filtered[i] * dt;
        steps[i] = integrated / (6500 * 256);
        s->remainder[i] = integrated % (6500 * 256);
        /* Time-proportional velocity ceiling, no backlog after fast gestures. */
        int limit = (int)(250 * dt / 1000); if (limit < 1) { limit = 1; }
        if (steps[i] > limit) { steps[i] = limit; s->remainder[i] = 0; }
        if (steps[i] < -limit) { steps[i] = -limit; s->remainder[i] = 0; }
    }
    return (struct xiao147_xy){steps[0], steps[1]};
}
