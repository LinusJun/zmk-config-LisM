#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include "xiao147_orientation.h"

/* Rate-based relative air mouse, not accelerometer position integration.
 * Raw counts at 245 dps; 6500 count*ms/HID-unit retains precision at low speed.
 * Faster motion ramps continuously to 4x with one shared vector gain.
 * HID units are not physical screen pixels; host acceleration/scaling applies.
 * Three native gyro axes are projected using trusted support acceleration,
 * giving one continuous world-yaw / projected-screen-right-pitch frame for
 * flat and upright grips. Filtering and integration use actual elapsed time. */
struct xiao147_air_motion {
    int32_t bias[3], sum[3];
    int16_t minimum[3], maximum[3];
    int32_t remainder[2];
    int32_t filtered[2]; /* Q8 corrected gyro counts */
    int16_t gravity[3];
    int32_t up[3]; /* Q14 low-pass support acceleration (world UP). */
    unsigned samples;
    uint32_t started, last_ms, gravity_ms, stable_since;
    uint32_t calibration_ms;
    bool calibrating, bias_valid, have_time, have_gravity, gravity_stable;
    bool have_calibration_time;
    /* Last zero-learning rejection: gravity=1, sample time=2, bias=4, spread=8. */
    uint8_t calibration_blocked;
    uint32_t up_at;
    bool up_valid, project_ready;
};

static inline void xiao147_air_clear(struct xiao147_air_motion *s)
{
    s->remainder[0] = s->remainder[1] = 0;
    s->filtered[0] = s->filtered[1] = 0;
    s->have_time = false;
}

static inline int32_t xiao147_air_gain_q8(int32_t speed_q8)
{
    const int32_t low = 500 * 256, high = 6000 * 256;
    if (speed_q8 <= low) { return 256; }
    if (speed_q8 >= high) { return 1024; }
    return 256 + (int32_t)((int64_t)(speed_q8 - low) * 768 / (high - low));
}

static inline void xiao147_air_begin(struct xiao147_air_motion *s, uint32_t now)
{
    xiao147_air_clear(s);
    s->samples = 0;
    s->started = now;
    s->calibrating = true; /* Keep the last valid bias until a stable replacement. */
    s->have_gravity = false;
    s->have_calibration_time = false;
    s->calibration_blocked = 0;
    s->up_valid = s->project_ready = false;
}

/* Restoring integer square root: bounded, deterministic and no libm/runtime
 * dependency on the embedded target. All squared vectors fit uint64_t. */
static inline uint32_t xiao147_air_sqrt(uint64_t value)
{
    uint64_t root = 0, bit = UINT64_C(1) << 62;
    while (bit > value) { bit >>= 2; }
    while (bit) {
        if (value >= root + bit) {
            value -= root + bit;
            root = (root >> 1) + bit;
        } else { root >>= 1; }
        bit >>= 2;
    }
    return (uint32_t)root;
}

static inline uint32_t xiao147_air_norm(const int32_t v[3])
{
    uint64_t norm = 0;
    for (int i = 0; i < 3; ++i) { norm += (int64_t)v[i] * v[i]; }
    return xiao147_air_sqrt(norm);
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
    /* Only approximately 1g samples may steer the reference. Brief invalid
     * acceleration holds the last trustworthy direction for at most 100 ms.
     * A bus failure separately clears have_gravity and fails closed at once. */
    if (norm >= 13000LL * 13000 && norm <= 19660LL * 19660) {
        uint32_t length = xiao147_air_sqrt((uint64_t)norm);
        uint32_t dt = now - s->up_at;
        int64_t alignment = 0;
        for (int i = 0; i < 3; ++i) { alignment += (int64_t)a[i] * s->up[i]; }
        if (s->up_valid && alignment < 0) {
            /* A discontinuous hemisphere flip is not an interpolation path.
             * Normalizing a short-step blend of antipodal unit vectors could
             * otherwise lock forever onto the OLD direction. Silence this
             * sample; a subsequent fresh trustworthy sample can re-anchor. */
            s->up_valid = s->project_ready = false;
            xiao147_air_clear(s);
            return;
        }
        int32_t next[3];
        for (int i = 0; i < 3; ++i) {
            int32_t unit = (int32_t)((int64_t)a[i] * 16384 / length);
            next[i] = !s->up_valid || dt > 100 ? unit : s->up[i] +
                (int32_t)((int64_t)(unit - s->up[i]) * dt / (80 + dt));
        }
        length = xiao147_air_norm(next);
        if (length) {
            for (int i = 0; i < 3; ++i) {
                s->up[i] = (int32_t)((int64_t)next[i] * 16384 / length);
            }
            s->up_at = now; s->up_valid = true;
        }
    }
}

/* One physical frame for both flat screen-up and upright grips. Native +X
 * points down and +Y left in USB-left front view; the front normal -Z follows
 * the project's right-handed mounting assumption, not a new hardware reading.
 * World yaw is about UP. Pitch is about the screen's projected RIGHT axis.
 * Roll about the remaining forward axis is intentionally not cursor motion.
 * Rates are already logical counts; do not apply another 2D pose rotation. */
static inline bool xiao147_air_project(struct xiao147_air_motion *s, uint32_t now,
    enum xiao147_orientation pose, const int16_t raw[3], struct xiao147_xy *rates)
{
    *rates = (struct xiao147_xy){0, 0};
    if (!s->bias_valid || s->calibrating || !s->have_gravity || !s->up_valid ||
        now - s->up_at > 100 || now - s->gravity_ms > 100) {
        s->project_ready = false;
        xiao147_air_clear(s);
        return false;
    }
    int32_t right[3] = {0, 0, 0};
    switch (pose) {
    case XIAO147_USB_LEFT: right[1] = -16384; break;
    case XIAO147_BUTTONS_BOTTOM: right[0] = -16384; break;
    case XIAO147_USB_RIGHT: right[1] = 16384; break;
    case XIAO147_BUTTONS_TOP: right[0] = 16384; break;
    default:
        s->project_ready = false; xiao147_air_clear(s); return false;
    }
    int64_t dot = 0;
    for (int i = 0; i < 3; ++i) { dot += (int64_t)right[i] * s->up[i]; }
    dot /= 16384;
    int32_t horizontal[3];
    for (int i = 0; i < 3; ++i) {
        horizontal[i] = right[i] - (int32_t)(dot * s->up[i] / 16384);
    }
    uint32_t length = xiao147_air_norm(horizontal);
    /* Near screen-right parallel to gravity the intended pitch axis is
     * undefined. Silence with hysteresis, never switch axes or amplify noise. */
    if (length * 100 < (s->project_ready ? 35U : 50U) * 16384U) {
        s->project_ready = false; xiao147_air_clear(s); return false;
    }
    s->project_ready = true;
    int64_t yaw = 0, pitch = 0;
    for (int i = 0; i < 3; ++i) {
        int32_t omega = (int32_t)raw[i] - s->bias[i];
        int32_t h = (int32_t)((int64_t)horizontal[i] * 16384 / length);
        yaw -= (int64_t)omega * s->up[i];
        pitch -= (int64_t)omega * h;
    }
    rates->x = (int)(yaw / 16384);
    rates->y = (int)(pitch / 16384);
    return true;
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
            uint8_t blocked = s->have_gravity && s->gravity_stable &&
                              now - s->gravity_ms <= 40 ? 0 : 1;
            /* Duplicate/stalled polls cannot manufacture a stationary window. */
            if (s->have_calibration_time &&
                (now == s->calibration_ms || now - s->calibration_ms > 80)) {
                s->samples = 0; blocked |= 2;
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
                    blocked |= 4;
                }
                if (s->samples && (abs((int)raw[i] - s->minimum[i]) > 320 ||
                                   abs((int)raw[i] - s->maximum[i]) > 320)) {
                    blocked |= 8;
                }
            }
            s->calibration_blocked = blocked;
            if (blocked) { s->samples = 0; }
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
    struct xiao147_xy v;
    if (!xiao147_air_project(s, now, pose, raw, &v)) {
        return (struct xiao147_xy){0, 0};
    }
    uint32_t dt = now - s->last_ms;
    bool first = !s->have_time;
    s->last_ms = now;
    if (first || dt > 80) {
        xiao147_air_clear(s); s->have_time = true;
        return (struct xiao147_xy){0, 0};
    }
    if (!dt) { return (struct xiao147_xy){0, 0}; }
    int32_t values[2] = {v.x, v.y};
    for (int i = 0; i < 2; i++) {
        /* Soft deadband plus immediate rest cutoff: no filtered drift tail. */
        if (abs(values[i]) <= 80) {
            s->remainder[i] = s->filtered[i] = 0; continue;
        }
        values[i] += values[i] > 0 ? -80 : 80;
        int32_t target = values[i] * 256;
        s->filtered[i] += (int32_t)(((int64_t)target - s->filtered[i]) * dt / (30 + dt));
    }
    int32_t speed = abs(s->filtered[0]);
    if (abs(s->filtered[1]) > speed) { speed = abs(s->filtered[1]); }
    int32_t gain = xiao147_air_gain_q8(speed);
    int64_t velocity[2] = {(int64_t)s->filtered[0] * gain / 256,
                           (int64_t)s->filtered[1] * gain / 256};
    int64_t peak = llabs(velocity[0]);
    if (llabs(velocity[1]) > peak) { peak = llabs(velocity[1]); }
    const int64_t ceiling = 6500LL * 256 * 1500 / 1000;
    if (peak > ceiling) {
        /* Limit the vector before integration, not each axis independently:
         * fast diagonals retain direction and no excess becomes a backlog. */
        for (int i = 0; i < 2; i++) { velocity[i] = velocity[i] * ceiling / peak; }
    }
    int steps[2];
    for (int i = 0; i < 2; i++) {
        int64_t integrated = s->remainder[i] + velocity[i] * dt;
        steps[i] = integrated / (6500 * 256);
        s->remainder[i] = integrated % (6500 * 256);
    }
    return (struct xiao147_xy){steps[0], steps[1]};
}
