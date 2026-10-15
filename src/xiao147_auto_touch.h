/* Automatic owner of the verified volatile DIFF interface. Coordinates and
 * one/two-lobe classification remain heuristic, not native multi-touch. */
#pragma once
#include "xiao147_raw_capture.h"
#include "xiao147_raw_gestures.h"
#include "xiao147_orientation.h"

struct xiao147_auto_output {
    int16_t dx, dy, wheel, pan;
    bool left, right, activity;
};
struct xiao147_auto_ops {
    int (*read)(void *, uint8_t, uint8_t *, size_t);
    int (*write)(void *, uint8_t, const uint8_t *, size_t);
    /* False cancels input and restores the native interface; never retry a press. */
    bool (*output)(void *, struct xiao147_auto_output);
};
struct xiao147_auto_touch {
    struct xiao147_raw_capture capture;
    struct xiao147_raw_baseline baseline;
    struct xiao147_shadow_gesture pair;
    struct xiao147_auto_ops ops;
    void *context;
    uint32_t now, quiet_since, normal_at, cooldown_since, frame_at, one_since;
    uint32_t one_release_at;
    struct xiao147_xy one_anchor, one_previous;
    uint8_t orientation, one_frames, release_frames, failures;
    bool quiet, cooldown, disabled, have_frame, blocked, pair_seen;
    bool one_active, one_moved, one_releasing, allowed;
};
void xiao147_auto_init(struct xiao147_auto_touch *, const struct xiao147_auto_ops *, void *);
/* Driver submits every native read, including errors. Only a fresh stable
 * count-zero grants automatic entry. No DTR/serial/host activation involved. */
void xiao147_auto_normal(struct xiao147_auto_touch *, bool valid, uint8_t count, uint32_t now);
/* True means this tick belongs to the factory owner (including restoration).
 * allowed = status && awake && !IMU && !pending button release. */
bool xiao147_auto_tick(struct xiao147_auto_touch *, uint32_t now, bool allowed,
                       enum xiao147_orientation orientation);
