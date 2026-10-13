#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include "xiao147_orientation.h"

/* Raw gyro counts at 104 Hz/245 dps. Output sampled at 20 ms. */
struct xiao147_air_motion {
    int32_t bias[3], sum[3];
    int16_t minimum[3], maximum[3];
    int32_t remainder[2];
    unsigned samples;
    uint32_t started;
    bool calibrating, bias_valid;
};

static inline void xiao147_air_clear(struct xiao147_air_motion *s)
{
    s->remainder[0] = s->remainder[1] = 0;
}

static inline void xiao147_air_begin(struct xiao147_air_motion *s, uint32_t now)
{
    xiao147_air_clear(s);
    s->samples = 0;
    s->started = now;
    s->calibrating = true; /* Keep the last valid bias until a stable replacement. */
}

static inline struct xiao147_xy xiao147_air_sample(struct xiao147_air_motion *s,
    uint32_t now, enum xiao147_orientation pose, const int16_t raw[3])
{
    if (s->calibrating) {
        if (now - s->started >= 3000) {
            s->calibrating = false; /* Never learn a moving/unknown zero. */
        } else {
            bool stable = true;
            for (int i = 0; i < 3; i++) {
                if (abs(raw[i]) > 500) { stable = false; }
                if (s->samples && (abs((int)raw[i] - s->minimum[i]) > 80 ||
                                   abs((int)raw[i] - s->maximum[i]) > 80)) {
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
                if (++s->samples >= 50) {
                    for (int i = 0; i < 3; i++) { s->bias[i] = s->sum[i] / (int)s->samples; }
                    s->bias_valid = true;
                    s->calibrating = false;
                }
            }
            xiao147_air_clear(s);
            return (struct xiao147_xy){0, 0};
        }
    }
    struct xiao147_xy v = xiao147_gyro_to_logical(pose, raw[0] - s->bias[0],
                                                raw[1] - s->bias[1]);
    int32_t values[2] = {v.x, v.y};
    int steps[2];
    for (int i = 0; i < 2; i++) {
        if (abs(values[i]) <= 80) { s->remainder[i] = 0; steps[i] = 0; continue; }
        s->remainder[i] += values[i];
        steps[i] = s->remainder[i] / 325; /* 2x old gain; retain subpixel motion. */
        s->remainder[i] %= 325;
        if (steps[i] > 5) { steps[i] = 5; }
        if (steps[i] < -5) { steps[i] = -5; }
    }
    return (struct xiao147_xy){steps[0], steps[1]};
}
