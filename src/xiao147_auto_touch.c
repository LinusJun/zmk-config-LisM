#include "xiao147_auto_touch.h"
#include <string.h>
#include <stdlib.h>

static void auto_cancel(struct xiao147_auto_touch *s)
{
    s->blocked = true;
    s->one_active = s->one_releasing = s->one_weak_tail = false;
    s->one_frames = s->release_frames = 0;
    s->pair_seen = false;
    s->air_quiet_frames = 0;
    xiao147_shadow_gesture_cancel(&s->pair);
}

/* A weak centroid is undefined. Preserve only an already qualified stationary
 * tap while every electrode is non-growing, then require two real NONEs.
 * Never bridge a renewed contact or learn motion from this release tail. */
static bool single_tail_fades(const struct xiao147_auto_touch *s,
                              const struct xiao147_raw_profile *p)
{
    uint64_t total = 0, previous = 0;
    for (unsigned i = 0; i < XIAO147_PROFILE_CHANNELS; i++) {
        if (p->positive[i] > s->one_positive[i]) { return false; }
        total += p->positive[i]; previous += s->one_positive[i];
    }
    return total > 0 && previous > 0 &&
           (s->one_weak_tail || total * 2U <= previous);
}

static int auto_read(void *ctx, uint8_t reg, uint8_t *buf, size_t len)
{
    struct xiao147_auto_touch *s = ctx;
    return s->ops.read(s->context, reg, buf, len);
}
static int auto_write(void *ctx, uint8_t reg, const uint8_t *buf, size_t len)
{
    struct xiao147_auto_touch *s = ctx;
    return s->ops.write(s->context, reg, buf, len);
}
static void auto_stop(struct xiao147_auto_touch *s)
{
    auto_cancel(s);
    s->quiet = false;
    xiao147_raw_capture_stop(&s->capture, s->now);
}
static void auto_event(void *ctx, enum xiao147_raw_event event, int detail)
{
    struct xiao147_auto_touch *s = ctx;
    if (event == XIAO147_RAW_CHANNELS &&
        (detail != 12 || s->capture.channel_layout[0] != 6 ||
         s->capture.channel_layout[1] != 2 || s->capture.channel_layout[2] != 0)) {
        s->disabled = true;
        auto_stop(s);
    }
    if (event == XIAO147_RAW_ERROR) {
        /* Exact chip/firmware/layout only. Recovery retries are bounded for
         * transient transport errors; mismatches never probe new writes. */
        if (detail == XIAO147_RAW_ID_MISMATCH || detail == XIAO147_RAW_VERSION_MISMATCH ||
            detail == XIAO147_RAW_BAD_CHANNELS || ++s->failures >= 3) { s->disabled = true; }
        auto_cancel(s);
        s->quiet = false;
        s->cooldown = true;
        s->cooldown_since = s->now;
    }
    if (event == XIAO147_RAW_RESTORING || event == XIAO147_RAW_RESTORED ||
        event == XIAO147_RAW_FAULT) {
        auto_cancel(s);
        s->quiet = false;
    }
}

static void auto_sample(void *ctx, enum xiao147_raw_type type, uint8_t channels,
                        const int32_t *values, uint32_t elapsed_ms)
{
    struct xiao147_auto_touch *s = ctx;
    (void)elapsed_ms;
    if (!s->allowed || type != XIAO147_RAW_DIFF) { auto_stop(s); return; }
    if (!xiao147_raw_baseline_observe(&s->baseline, values, channels, true, s->now)) {
        auto_cancel(s);
        return; /* A held finger can never become a learned baseline. */
    }
    struct xiao147_raw_profile profile;
    if (!xiao147_raw_profile_evaluate(values, channels, s->baseline.values, &profile)) {
        auto_stop(s); return;
    }
    if (s->have_frame && (s->now == s->frame_at || s->now - s->frame_at > 100)) {
        auto_cancel(s);
    }
    s->frame_at = s->now;
    s->have_frame = true;
    enum xiao147_raw_hypothesis h = profile.hypothesis;
    if (h == XIAO147_RAW_NONE) {
        if (s->air_quiet_frames < 2) { s->air_quiet_frames++; }
    } else { s->air_quiet_frames = 0; }
    struct xiao147_xy p = xiao147_touch_to_logical(s->orientation,
        profile.native_short_x, profile.native_long_y);
    struct xiao147_auto_output out = {0};
    out.activity = h == XIAO147_RAW_ONE_LOBE || h == XIAO147_RAW_TWO_SEPARATED_LOBES;

    if (s->blocked) {
        if (h == XIAO147_RAW_NONE) {
            if (++s->release_frames >= 2) {
                s->blocked = false; s->release_frames = 0;
                xiao147_shadow_gesture_init(&s->pair);
                /* Seed only with the two actual empty frames just observed. */
                xiao147_shadow_gesture_observe(&s->pair, XIAO147_RAW_NONE, true,
                                               0, 0, s->orientation, s->now - 1);
                xiao147_shadow_gesture_observe(&s->pair, XIAO147_RAW_NONE, true,
                                               0, 0, s->orientation, s->now);
            }
        } else { s->release_frames = 0; }
        goto emit;
    }

    if (s->one_weak_tail &&
        (s->now - s->one_weak_at > 60 ||
         (h != XIAO147_RAW_PENDING && h != XIAO147_RAW_NONE))) {
        auto_cancel(s); goto emit;
    }
    bool pair_started = h == XIAO147_RAW_TWO_SEPARATED_LOBES && !s->pair_seen;
    bool carry_single = pair_started && s->one_active;
    bool forbid_pair_tap = carry_single && (s->one_moved || s->one_releasing);
    uint32_t contact_since = carry_single ? s->one_since : s->now;
    if (pair_started) {
        /* No single tap survives a second lobe. Never bridge centroid deltas. */
        s->one_active = false; s->one_frames = 0; s->pair_seen = true;
        xiao147_shadow_gesture_init(&s->pair);
        s->pair.blocked = false; /* owner already witnessed genuine full lift */
    }
    if (s->pair_seen) {
        struct xiao147_shadow_result r = xiao147_shadow_gesture_observe_profile(
            &s->pair, &profile, true, p.x, p.y, s->orientation, s->now);
        if (pair_started) {
            /* Adding a finger cannot restart the tap clock or erase a drag.
             * The pair may still scroll after taking ownership. */
            s->pair.started_ms = contact_since;
            s->pair.tap_disqualified |= forbid_pair_tap;
        }
        out.right = r.candidate_right_tap;
        /* Empirical host correction: vertical only. Horizontal stays as tested. */
        out.wheel = -r.candidate_wheel; out.pan = r.candidate_hwheel;
        /* Entire TWO->ONE tail remains pair-owned, never pointer or left. */
        if (h == XIAO147_RAW_NONE) {
            if (++s->release_frames >= 2) {
                s->pair_seen = false; s->release_frames = 0;
                s->one_active = s->one_releasing = false;
            }
        } else { s->release_frames = 0; }
        goto emit;
    }
    if (h == XIAO147_RAW_UNKNOWN) { auto_cancel(s); goto emit; }
    if (h == XIAO147_RAW_PENDING) {
        if (s->one_active) {
            if (s->one_frames < 2 || s->one_moved || s->one_releasing ||
                !single_tail_fades(s, &profile)) {
                auto_cancel(s); goto emit;
            }
            if (!s->one_weak_tail) { s->one_weak_at = s->now; }
            s->one_weak_tail = true;
            memcpy(s->one_positive, profile.positive, sizeof(s->one_positive));
        }
        /* No output, confirmation frame, release or tap-clock extension. */
        goto emit;
    }
    if (h == XIAO147_RAW_NONE) {
        if (s->one_active) {
            if (!s->one_releasing) { s->one_release_at = s->now; s->one_releasing = true; }
            if (++s->release_frames >= 2) {
                out.left = s->one_frames >= 2 && !s->one_moved &&
                           s->one_release_at - s->one_since <= 300;
                s->one_active = s->one_releasing = s->one_weak_tail = false;
                s->one_frames = s->release_frames = 0;
            }
        }
        goto emit;
    }
    if (h == XIAO147_RAW_ONE_LOBE) {
        if (s->one_releasing) { auto_cancel(s); goto emit; }
        s->release_frames = 0;
        memcpy(s->one_positive, profile.positive, sizeof(s->one_positive));
        if (!s->one_active) {
            s->one_active = true; s->one_moved = false;
            s->one_frames = 1; s->one_since = s->now;
            s->one_anchor = s->one_previous = p;
            goto emit;
        }
        int dx = p.x - s->one_previous.x, dy = p.y - s->one_previous.y;
        if (abs(dx) > 60 || abs(dy) > 60) { auto_cancel(s); goto emit; }
        if (s->one_frames < 255) { s->one_frames++; }
        if (abs(p.x - s->one_anchor.x) > 12 || abs(p.y - s->one_anchor.y) > 12) {
            s->one_moved = true;
        }
        /* Require three credible frames before relative movement. The first
         * two establish an anchor, not a jump from the preceding gesture. */
        if (s->one_frames == 3) {
            /* Preserve confirmed onset displacement instead of discarding the
             * second frame. A second finger/unknown still cancels this anchor. */
            out.dx = p.x - s->one_anchor.x;
            out.dy = p.y - s->one_anchor.y;
        } else if (s->one_frames > 3) { out.dx = dx; out.dy = dy; }
        s->one_previous = p;
    }
emit:
    if ((out.activity || out.left || out.right || out.dx || out.dy || out.wheel || out.pan) &&
        s->ops.output && !s->ops.output(s->context, out)) {
        /* UI/HID error: cancel now, not at next frame; bounded native restore. */
        s->cooldown = true; s->cooldown_since = s->now;
        auto_stop(s);
    }
}

void xiao147_auto_init(struct xiao147_auto_touch *s, const struct xiao147_auto_ops *ops, void *ctx)
{
    memset(s, 0, sizeof(*s));
    s->ops = *ops; s->context = ctx;
    const struct xiao147_raw_ops raw = {
        .read = auto_read, .write = auto_write, .event = auto_event, .sample = auto_sample,
    };
    xiao147_raw_capture_init(&s->capture, &raw, s);
    auto_cancel(s);
}

bool xiao147_auto_air_idle(const struct xiao147_auto_touch *s, uint32_t now)
{
    return s->allowed && !s->disabled && !s->cooldown && !s->blocked &&
           s->baseline.armed && s->have_frame && now - s->frame_at <= 100 &&
           s->air_quiet_frames >= 2 && !s->one_active && !s->pair_seen &&
           s->capture.phase >= XIAO147_RAW_SELECT_TYPE &&
           s->capture.phase <= XIAO147_RAW_PAIR_WAIT;
}

void xiao147_auto_normal(struct xiao147_auto_touch *s, bool valid, uint8_t count, uint32_t now)
{
    if (!valid || count || now - s->normal_at > 100) { s->quiet = false; }
    s->normal_at = now;
    if (valid && count == 0) {
        if (!s->quiet) { s->quiet_since = now; }
        s->quiet = true;
    }
}

bool xiao147_auto_tick(struct xiao147_auto_touch *s, uint32_t now, bool allowed,
                       enum xiao147_orientation orientation)
{
    s->now = now; s->allowed = allowed;
    if (s->orientation != (uint8_t)orientation) {
        auto_cancel(s); s->orientation = orientation;
    }
    if (xiao147_raw_capture_active(&s->capture)) {
        if (!allowed) { auto_stop(s); }
        xiao147_raw_capture_tick(&s->capture, now);
        return true; /* no native read in the same tick as restoration */
    }
    if (!allowed) { s->quiet = false; return false; }
    if (s->disabled) { return false; }
    if (s->cooldown && now - s->cooldown_since < 5000) { return false; }
    s->cooldown = false;
    if (s->quiet && now - s->quiet_since >= 100 && now - s->normal_at <= 100) {
        xiao147_raw_baseline_begin(&s->baseline, true);
        auto_cancel(s); s->have_frame = false; s->quiet = false;
        return xiao147_raw_capture_start_mode(&s->capture, now, 0x0100,
                                              XIAO147_RAW_MODE_AUTO_DIFF);
    }
    return false;
}
