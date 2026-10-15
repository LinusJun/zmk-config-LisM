"""Actual C spatial-hypothesis replay and fail-closed SHADOW gesture tests.

No device access and no HID emission. Each recorded hold is one scene. Frozen
rules were inspected against these same scenes: even whole-scene exclusion is
retrospective consistency, NOT independent hardware/contact-count validation.
Synthetic dynamics prove state transitions/signs only, not real touch mapping.
"""
from pathlib import Path
import json
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "tests/fixtures/xiao147_raw_profiles.json").read_text())
assert fixture["channels"] == 12 and fixture["channel_layout"] == [6, 2, 0]
scenes = fixture["scenes"]
assert {scene["id"] for scene in scenes} == {"left", "center", "right", "double"}
assert len(scenes) == 4 and sum(len(s["diff_frames"]) for s in scenes) == 516
arrays = []
for index, scene in enumerate(scenes):
    frames = scene["diff_frames"]
    assert len(frames) == 129 and not scene["runtime_baseline_captured_in_scene"]
    assert all(len(row) == 13 and all(-32768 <= x <= 32767 for x in row[1:])
               for row in frames)
    assert all(100 < b[0] - a[0] < 200 for a, b in zip(frames, frames[1:]))
    arrays.append(f"static const int32_t scene_{index}[129][13] = {{\n" +
                  ",\n".join("    {" + ",".join(str(x) for x in row) + "}"
                              for row in frames) + "\n};\n")
declarations = "\n".join(arrays)
declarations += "static const int32_t (*const scenes[4])[13] = {" + ",".join(
    f"scene_{i}" for i in range(4)) + "};\n"
declarations += "static const char *const scene_names[4] = {" + ",".join(
    json.dumps(scene["id"]) for scene in scenes) + "};\n"
declarations += "static const enum xiao147_raw_hypothesis labels[4] = {" + ",".join(
    "XIAO147_RAW_" + scene["operator_label"] for scene in scenes) + "};\n"

harness = r'''
#include <assert.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xiao147_raw_gestures.h"
#include "xiao147_orientation.h"

/* All recording arrays are generated from the anonymised committed fixture,
 * not copied medians or random within-hold train/test splits. */
__ACTUAL_SCENES__

static const int32_t quiet[12] = {0, 2, -3, 4, -5, 6, -7, 8, -2, 1, 0, -1};
static const int32_t one_lobe[12] = {0,0, 100,100, 150,150, 50,50, 0,0, 0,0};
static const int32_t two_lobes[12] = {0,0, 200,200, 25,25, 25,25, 180,180, 30,30};

static void profile_tests(void)
{
    struct xiao147_raw_profile p;
    assert(xiao147_raw_profile_evaluate(quiet, 12, NULL, &p));
    assert(p.hypothesis == XIAO147_RAW_NONE);
    assert(xiao147_raw_profile_evaluate(one_lobe, 12, NULL, &p));
    assert(p.hypothesis == XIAO147_RAW_ONE_LOBE && p.significant_peaks == 1);
    assert(xiao147_raw_profile_evaluate(two_lobes, 12, NULL, &p));
    assert(p.hypothesis == XIAO147_RAW_TWO_SEPARATED_LOBES &&
           p.significant_peaks == 2 && p.valley_permille <= 400 &&
           p.lesser_peak_permille >= 350 && p.partition_valid);
    assert(p.native_short_x <= 171 && p.native_long_y <= 319);
    assert(p.lobes[0].peak_station < p.valley_station &&
           p.lobes[1].peak_station > p.valley_station &&
           p.lobes[0].last_station < p.lobes[1].first_station);

    int32_t data[12];
    for (unsigned i = 0; i < 12; i++) { data[i] = 100; }
    assert(xiao147_raw_profile_evaluate(data, 12, NULL, &p));
    assert(p.hypothesis == XIAO147_RAW_UNKNOWN); /* Unresolved broad plateau. */
    for (unsigned i = 0; i < 12; i++) { data[i] = 0; }
    data[0] = data[1] = data[4] = data[5] = data[8] = data[9] = 200;
    assert(xiao147_raw_profile_evaluate(data, 12, NULL, &p));
    assert(p.hypothesis == XIAO147_RAW_UNKNOWN && p.significant_peaks == 3);
    memcpy(data, one_lobe, sizeof(data)); data[11] = -81;
    assert(xiao147_raw_profile_evaluate(data, 12, NULL, &p));
    assert(p.hypothesis == XIAO147_RAW_UNKNOWN); /* Negative anomaly != release. */
    memset(data, 0, sizeof(data)); data[3] = 21;
    assert(xiao147_raw_profile_evaluate(data, 12, NULL, &p));
    assert(p.hypothesis == XIAO147_RAW_PENDING && !p.partition_valid &&
           p.native_short_x == 0 && p.native_long_y == 0); /* Weak != release. */
    memset(data, 0, sizeof(data));
    data[0] = data[4] = data[8] = 21;
    assert(xiao147_raw_profile_evaluate(data, 12, NULL, &p));
    assert(p.hypothesis == XIAO147_RAW_UNKNOWN); /* Weak complex three peaks. */
    for (unsigned i = 0; i < 12; i++) { data[i] = 21; }
    assert(xiao147_raw_profile_evaluate(data, 12, NULL, &p));
    assert(p.hypothesis == XIAO147_RAW_UNKNOWN); /* Weak full-width plateau. */
    memset(data, 0, sizeof(data)); data[3] = 21; data[11] = -81;
    assert(xiao147_raw_profile_evaluate(data, 12, NULL, &p));
    assert(p.hypothesis == XIAO147_RAW_UNKNOWN); /* Negative anomaly != PENDING. */
    data[3] = INT32_MAX;
    assert(!xiao147_raw_profile_evaluate(data, 12, NULL, &p) &&
           p.hypothesis == XIAO147_RAW_UNKNOWN);
    data[3] = INT16_MIN - 1;
    assert(!xiao147_raw_profile_evaluate(data, 12, NULL, &p));
    assert(!xiao147_raw_profile_evaluate(NULL, 12, NULL, &p));
    assert(!xiao147_raw_profile_evaluate(one_lobe, 11, NULL, &p));
    assert(!xiao147_raw_profile_evaluate(one_lobe, 13, NULL, &p));
    assert(!xiao147_raw_profile_evaluate(one_lobe, 12, NULL, NULL));
    /* Worst signed subtraction, positive sums and ratio multiplication must
     * be defined with no signed overflow and all provisional bounds held. */
    int16_t baseline[12];
    for (unsigned i = 0; i < 12; i++) { data[i] = INT16_MAX; baseline[i] = INT16_MIN; }
    assert(xiao147_raw_profile_evaluate(data, 12, baseline, &p));
    assert(p.hypothesis == XIAO147_RAW_UNKNOWN && p.native_short_x <= 171 &&
           p.native_long_y <= 319);
}

static void baseline_tests(void)
{
    struct xiao147_raw_baseline b;
    xiao147_raw_baseline_begin(&b, false);
    for (unsigned t = 0; t <= 500; t += 20) {
        assert(!xiao147_raw_baseline_observe(&b, quiet, 12, true, t));
    }
    assert(!b.armed && b.quiet_frames == 0); /* No genuine normal zero. */
    xiao147_raw_baseline_begin(&b, true);
    for (unsigned t = 0; t <= 80; t += 20) {
        assert(!xiao147_raw_baseline_observe(&b, quiet, 12, true, t));
    }
    assert(xiao147_raw_baseline_observe(&b, quiet, 12, true, 100));
    for (unsigned i = 0; i < 12; i++) { assert(b.values[i] == quiet[i]); }
    assert(xiao147_raw_baseline_observe(&b, two_lobes, 12, true, 120));
    for (unsigned i = 0; i < 12; i++) { assert(b.values[i] == quiet[i]); }
    assert(!xiao147_raw_baseline_observe(&b, quiet, 12, false, 140));
    assert(!b.armed && !b.normal_lift_confirmed);
    xiao147_raw_baseline_begin(&b, true);
    for (unsigned t = 0; t < 2000; t += 20) {
        assert(!xiao147_raw_baseline_observe(&b, one_lobe, 12, true, t));
    }
    assert(b.quiet_frames == 0 && !b.armed); /* Never learn held contact. */
    xiao147_raw_baseline_begin(&b, true);
    for (unsigned t = 0; t < 3000; t += 154) {
        assert(!xiao147_raw_baseline_observe(&b, quiet, 12, true, t));
    }
    assert(!b.armed); /* Old diagnostic cadence cannot qualify fresh timing. */
    xiao147_raw_baseline_begin(&b, true);
    uint32_t start = UINT32_MAX - 70U;
    for (unsigned dt = 0; dt < 100; dt += 20) {
        assert(!xiao147_raw_baseline_observe(&b, quiet, 12, true, start + dt));
    }
    assert(xiao147_raw_baseline_observe(&b, quiet, 12, true, start + 100U));
    assert(!xiao147_raw_baseline_observe(&b, quiet, 11, true, 140));
    /* Even a frozen caller clock cannot overflow the accumulation count. */
    xiao147_raw_baseline_begin(&b, true);
    for (unsigned n = 0; n < 10000; n++) {
        assert(!xiao147_raw_baseline_observe(&b, quiet, 12, true, 10));
    }
    assert(b.quiet_frames < 4096);
    xiao147_raw_baseline_begin(NULL, true);
    assert(!xiao147_raw_baseline_observe(NULL, quiet, 12, true, 100));
}

static struct xiao147_shadow_result step(struct xiao147_shadow_gesture *g,
    enum xiao147_raw_hypothesis h, int x, int y, uint8_t pose, uint32_t t)
{
    return xiao147_shadow_gesture_observe(g, h, true, (int16_t)x, (int16_t)y, pose, t);
}

static void no_output(struct xiao147_shadow_result r)
{
    assert(!r.candidate_right_tap && r.candidate_wheel == 0 && r.candidate_hwheel == 0);
}

static void release(struct xiao147_shadow_gesture *g, uint32_t t)
{
    no_output(step(g, XIAO147_RAW_NONE, 0, 0, 0, t));
    no_output(step(g, XIAO147_RAW_NONE, 0, 0, 0, t + 20));
    assert(!g->blocked);
}

static void qualify(struct xiao147_shadow_gesture *g, uint32_t t)
{
    no_output(step(g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, t));
    no_output(step(g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, t + 20));
    assert(g->qualified_two && !g->blocked);
}

static void pending_tests(void)
{
    struct xiao147_shadow_gesture g;
    struct xiao147_shadow_result r;
    xiao147_shadow_gesture_init(&g);
    r = step(&g, XIAO147_RAW_PENDING, 0, 0, 0, 0);
    assert(r.cancelled && g.blocked && !g.pending);
    release(&g, 20);
    no_output(step(&g, XIAO147_RAW_PENDING, 0, 0, 0, 60));
    no_output(step(&g, XIAO147_RAW_PENDING, 0, 0, 0, 80));
    assert(g.pending && !g.active && !g.qualified_two && g.started_ms == 0);
    qualify(&g, 100);
    assert(!g.pending && g.started_ms == 100); /* No clock during weak onset. */
    no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 140));
    assert(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 160).candidate_right_tap);

    xiao147_shadow_gesture_init(&g); release(&g, 0);
    for (unsigned t = 40; t <= 140; t += 20) {
        no_output(step(&g, XIAO147_RAW_PENDING, 0, 0, 0, t));
        assert(g.pending && !g.active && !g.qualified_two);
    }
    r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, 141);
    assert(r.cancelled && g.blocked && !g.pending); release(&g, 160);

    /* PENDING cannot count as a NONE frame or finish an incomplete release. */
    xiao147_shadow_gesture_init(&g); release(&g, 0);
    no_output(step(&g, XIAO147_RAW_PENDING, 0, 0, 0, 40));
    no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 60));
    assert(g.release_frames == 1 && g.pending);
    no_output(step(&g, XIAO147_RAW_PENDING, 0, 0, 0, 80));
    assert(g.release_frames == 0 && g.pending && !g.active);
    release(&g, 100); assert(!g.pending);

    xiao147_shadow_gesture_init(&g); release(&g, 0);
    no_output(step(&g, XIAO147_RAW_ONE_LOBE, 100, 100, 0, 40));
    assert(step(&g, XIAO147_RAW_PENDING, 0, 0, 0, 60).cancelled);
    release(&g, 80);
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, 120));
    assert(step(&g, XIAO147_RAW_PENDING, 0, 0, 0, 140).cancelled);
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, 160));
    assert(g.blocked && !g.qualified_two); release(&g, 180);

    no_output(step(&g, XIAO147_RAW_PENDING, 0, 0, 0, 220));
    assert(step(&g, XIAO147_RAW_UNKNOWN, 0, 0, 0, 240).cancelled);
    release(&g, 260);
    no_output(step(&g, XIAO147_RAW_PENDING, 0, 0, 0, 300));
    assert(step(&g, XIAO147_RAW_PENDING, 0, 0, 1, 320).cancelled);
    xiao147_shadow_gesture_init(&g); release(&g, 0);
    no_output(step(&g, XIAO147_RAW_PENDING, 0, 0, 0, 40));
    assert(step(&g, XIAO147_RAW_PENDING, 0, 0, 0, 141).cancelled);
    xiao147_shadow_gesture_init(&g); release(&g, 0);
    no_output(step(&g, XIAO147_RAW_PENDING, 0, 0, 0, 40));
    r = xiao147_shadow_gesture_observe(&g, XIAO147_RAW_PENDING, false, 0, 0, 0, 60);
    assert(r.cancelled); release(&g, 80);
}

static struct xiao147_shadow_result profile_step(struct xiao147_shadow_gesture *g,
    const struct xiao147_raw_profile *p, uint32_t t)
{
    struct xiao147_xy xy = xiao147_touch_to_logical(XIAO147_USB_LEFT,
                                                 p->native_short_x, p->native_long_y);
    return xiao147_shadow_gesture_observe_profile(g, p, true, xy.x, xy.y,
                                                XIAO147_USB_LEFT, t);
}

static void independent_lobe_tests(void)
{
    /* Same two isolated footprints, pressure/gain changed independently.
     * Positive channels are 140/280 in each lobe, then 70/140 vs 168/336.
     * Their within-lobe ratios and positions are unchanged. A global weighted
     * centroid would move ~40 long-axis pixels, but equal lobe weighting must
     * remain stationary. This is a synthetic mechanism test, not calibration.
     */
    const int32_t balanced[12] = {20,20,160,300,20,20,20,20,160,300,20,20};
    const int32_t unequal[12] = {20,20,90,160,20,20,20,20,188,356,20,20};
    struct xiao147_raw_profile a, b, lifted;
    assert(xiao147_raw_profile_evaluate(balanced, 12, NULL, &a));
    assert(xiao147_raw_profile_evaluate(unequal, 12, NULL, &b));
    assert(xiao147_raw_profile_evaluate(quiet, 12, NULL, &lifted));
    assert(a.hypothesis == XIAO147_RAW_TWO_SEPARATED_LOBES &&
           b.hypothesis == XIAO147_RAW_TWO_SEPARATED_LOBES);
    assert(a.lobes[0].amplitude == 420 && a.lobes[1].amplitude == 420);
    assert(b.lobes[0].amplitude == 210 && b.lobes[1].amplitude == 504);
    assert(a.native_long_y == b.native_long_y && a.native_short_x == b.native_short_x);
    for (unsigned i = 0; i < 2; i++) {
        assert(a.lobes[i].native_long_y == b.lobes[i].native_long_y &&
               a.lobes[i].native_short_x == b.lobes[i].native_short_x);
    }
    struct xiao147_shadow_gesture g;
    xiao147_shadow_gesture_init(&g);
    no_output(profile_step(&g, &lifted, 0)); no_output(profile_step(&g, &lifted, 20));
    no_output(profile_step(&g, &a, 40)); no_output(profile_step(&g, &a, 60));
    assert(g.qualified_two && g.association_valid);
    no_output(profile_step(&g, &b, 80)); no_output(profile_step(&g, &a, 100));
    assert(!g.moving);
    no_output(profile_step(&g, &lifted, 120));
    assert(profile_step(&g, &lifted, 140).candidate_right_tap);
    assert(!g.association_valid);

    /* Weak second peak is ONE morphology, not forcibly normalised as two. */
    int32_t weak[12]; memcpy(weak, balanced, sizeof(weak)); weak[2] = 48; weak[3] = 76;
    assert(xiao147_raw_profile_evaluate(weak, 12, NULL, &b));
    assert(b.hypothesis == XIAO147_RAW_ONE_LOBE && !b.partition_valid);

    /* Opaque support/ordering validation rejects forged or stale metadata. */
    for (unsigned corruption = 0; corruption < 5; corruption++) {
        xiao147_shadow_gesture_init(&g);
        no_output(profile_step(&g, &lifted, 0)); no_output(profile_step(&g, &lifted, 20));
        no_output(profile_step(&g, &a, 40)); no_output(profile_step(&g, &a, 60));
        b = a;
        if (corruption == 0) { b.partition_valid = false; }
        if (corruption == 1) { b.lobes[0].amplitude++; }
        if (corruption == 2) { b.lobes[1].first_station = b.lobes[0].last_station; }
        if (corruption == 3) { b.lobes[0].native_long_y = b.lobes[1].native_long_y; }
        if (corruption == 4) { b.stations[b.lobes[0].peak_station] = 79; }
        struct xiao147_shadow_result r = profile_step(&g, &b, 80);
        no_output(r); assert(r.cancelled && g.blocked && !g.association_valid);
        no_output(profile_step(&g, &a, 100)); assert(g.blocked);
        no_output(profile_step(&g, &lifted, 120)); no_output(profile_step(&g, &lifted, 140));
    }

    /* Even unchanged aggregate x/y cannot hide a sudden lobe/valley swap. */
    const int32_t far_a[12] = {220,220,20,20,20,20,220,220,20,20,20,20};
    const int32_t far_b[12] = {20,20,20,20,220,220,20,20,20,20,220,220};
    assert(xiao147_raw_profile_evaluate(far_a, 12, NULL, &a));
    assert(xiao147_raw_profile_evaluate(far_b, 12, NULL, &b));
    assert(a.partition_valid && b.partition_valid && b.valley_station - a.valley_station == 2);
    xiao147_shadow_gesture_init(&g); release(&g, 0);
    no_output(xiao147_shadow_gesture_observe_profile(&g, &a, true, 100, 100, 0, 40));
    assert(xiao147_shadow_gesture_observe_profile(&g, &b, true, 100, 100, 0, 60).cancelled);
    release(&g, 80);
    assert(xiao147_shadow_gesture_observe_profile(&g, NULL, true, 100, 100, 0, 120).cancelled);
}

static void activation_and_axis_tests(void)
{
    struct xiao147_shadow_gesture g;
    struct xiao147_shadow_result r;
    for (int net = 11; net <= 13; net++) {
        xiao147_shadow_gesture_init(&g); release(&g, 0); qualify(&g, 40);
        no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100 + net, 100, 0, 80));
        assert(g.moving == (net > 12) && g.scroll_activated == (net > 12));
        assert(g.scroll_axis == (net > 12 ? XIAO147_SCROLL_HORIZONTAL : XIAO147_SCROLL_NONE));
        assert(g.remainder_x == (net > 12 ? 1 : 0) && g.remainder_y == 0);
        no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 100));
        r = step(&g, XIAO147_RAW_NONE, 0, 0, 0, 120);
        assert(r.candidate_right_tap == (net <= 12));
        assert(!g.scroll_activated && g.scroll_axis == XIAO147_SCROLL_NONE &&
               !g.scroll_x_started && !g.scroll_y_started && !g.remainder_x && !g.remainder_y);
    }
    /* Repeated travel inside the deadzone must not accumulate path distance
     * or jitter remainders. Net offset stays <=7; stationary tap survives. */
    xiao147_shadow_gesture_init(&g); release(&g, 0); qualify(&g, 40);
    for (unsigned t = 80; t <= 180; t += 20) {
        int x = ((t / 20) & 1U) ? 100 : 107;
        no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, x, 100, 0, t));
        assert(!g.scroll_activated && !g.moving && !g.remainder_x && !g.remainder_y);
    }
    no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 200));
    assert(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 220).candidate_right_tap);

    /* Crossing on the qualification frame cancels tap permanently, even if
     * the excess is less than a tick and the pointer returns to the anchor. */
    xiao147_shadow_gesture_init(&g); release(&g, 0);
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, 40));
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 113, 100, 0, 60));
    assert(g.qualified_two && g.moving && g.scroll_activated && g.remainder_x == 1);
    r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, 80);
    no_output(r);
    assert(!r.candidate_right_tap && g.moving && g.scroll_activated);
    assert(!g.remainder_x && !g.scroll_x_emitted);
    no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 100));
    no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 120));

    /* Before the first tick, excursions beyond the activation threshold must
     * not build an artificial reverse offset. Verify both signs and axes,
     * repeated return-to-origin and crossing to the opposite side. */
    for (unsigned axis = 0; axis < 2; axis++) for (int sign = -1; sign <= 1; sign += 2) {
        xiao147_shadow_gesture_init(&g); release(&g, 0); qualify(&g, 40);
        const int offsets[] = {13, 0, 19, 0, -13, 0, -19, 0};
        for (unsigned i = 0; i < sizeof(offsets) / sizeof(offsets[0]); i++) {
            int net = sign * offsets[i];
            no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES,
                100 + (axis == 0 ? net : 0), 100 + (axis == 1 ? net : 0), 0, 80 + i * 20));
            assert(!g.scroll_x_emitted && !g.scroll_y_emitted);
            if (net == 0) { assert(!g.remainder_x && !g.remainder_y); }
        }
        r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES,
            100 + (axis == 0 ? sign * 20 : 0), 100 + (axis == 1 ? sign * 20 : 0), 0, 240);
        assert((axis == 0 ? r.candidate_hwheel : r.candidate_wheel) == -sign);
        assert((axis == 0 ? g.scroll_x_emitted : g.scroll_y_emitted));
        r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES,
            100 + (axis == 0 ? sign * 12 : 0), 100 + (axis == 1 ? sign * 12 : 0), 0, 260);
        assert((axis == 0 ? r.candidate_hwheel : r.candidate_wheel) == sign);
        release(&g, 280);
        assert(!g.scroll_x_emitted && !g.scroll_y_emitted);
    }

    /* BOTH axes independently remain anchored until each emits a real tick. */
    xiao147_shadow_gesture_init(&g); release(&g, 0); qualify(&g, 40);
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 113, 113, 0, 80));
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, 100));
    assert(!g.remainder_x && !g.remainder_y);
    r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 120, 113, 0, 120);
    assert(r.candidate_hwheel == -1 && !r.candidate_wheel);
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 120, 100, 0, 140));
    assert(g.scroll_x_emitted && !g.scroll_y_emitted && !g.remainder_y);

    /* First 32-pixel displacement sends only 32-12=20, not a retroactive
     * four-tick onset jump. Axis stays locked through a later right-angle turn.
     */
    xiao147_shadow_gesture_init(&g); release(&g, 0); qualify(&g, 40);
    r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 132, 100, 0, 80);
    assert(r.candidate_hwheel == -2 && !r.candidate_wheel && g.remainder_x == 4);
    assert(g.scroll_axis == XIAO147_SCROLL_HORIZONTAL && g.scroll_x_started && !g.scroll_y_started);
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 132, 132, 0, 100));
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 132, 100, 0, 120));
    assert(g.remainder_y == 0 && !g.scroll_y_started && g.scroll_axis == XIAO147_SCROLL_HORIZONTAL);
    r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 148, 140, 0, 140);
    assert(r.candidate_hwheel == -2 && !r.candidate_wheel && g.remainder_y == 0);
    release(&g, 160); assert(g.scroll_axis == XIAO147_SCROLL_NONE);
    qualify(&g, 200);
    r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 132, 0, 240);
    assert(r.candidate_wheel == -2 && !r.candidate_hwheel && g.scroll_axis == XIAO147_SCROLL_VERTICAL);
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 132, 132, 0, 260));
    assert(g.remainder_x == 0 && !g.scroll_x_started);
    assert(step(&g, XIAO147_RAW_UNKNOWN, 0, 0, 0, 280).cancelled);
    assert(g.scroll_axis == XIAO147_SCROLL_NONE && !g.scroll_activated &&
           !g.remainder_x && !g.remainder_y && !g.scroll_x_started && !g.scroll_y_started);

    const int vectors[][3] = {
        {26,13,XIAO147_SCROLL_HORIZONTAL}, {13,26,XIAO147_SCROLL_VERTICAL},
        {25,13,XIAO147_SCROLL_BOTH}, {13,25,XIAO147_SCROLL_BOTH},
        {28,28,XIAO147_SCROLL_BOTH}, {-26,13,XIAO147_SCROLL_HORIZONTAL},
        {13,-26,XIAO147_SCROLL_VERTICAL}, {-28,-28,XIAO147_SCROLL_BOTH},
    };
    for (unsigned i = 0; i < sizeof(vectors) / sizeof(vectors[0]); i++) {
        xiao147_shadow_gesture_init(&g); release(&g, 0); qualify(&g, 40);
        r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100 + vectors[i][0],
                 100 + vectors[i][1], 0, 80);
        assert(g.scroll_axis == (enum xiao147_scroll_axis)vectors[i][2] &&
               g.scroll_activated && g.moving);
        if (g.scroll_axis == XIAO147_SCROLL_HORIZONTAL) {
            assert(!r.candidate_wheel && !g.remainder_y && !g.scroll_y_started);
        } else if (g.scroll_axis == XIAO147_SCROLL_VERTICAL) {
            assert(!r.candidate_hwheel && !g.remainder_x && !g.scroll_x_started);
        } else if (vectors[i][0] == vectors[i][1]) {
            assert(r.candidate_hwheel == (vectors[i][0] > 0 ? -2 : 2));
            assert(r.candidate_wheel == r.candidate_hwheel);
        }
    }
    /* BOTH does not accumulate its second axis before that axis crosses 12.
     * Each axis consumes the one-time deadzone independently, then follows
     * normal increments without reapplying the deadzone on every sample. */
    xiao147_shadow_gesture_init(&g); release(&g, 0); qualify(&g, 40);
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 113, 111, 0, 80));
    assert(g.scroll_axis == XIAO147_SCROLL_BOTH && g.scroll_x_started &&
           !g.scroll_y_started && g.remainder_x == 1 && g.remainder_y == 0);
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 114, 112, 0, 100));
    assert(!g.scroll_y_started && g.remainder_x == 2 && g.remainder_y == 0);
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 115, 113, 0, 120));
    assert(g.scroll_y_started && g.remainder_x == 3 && g.remainder_y == 1);
    r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 120, 120, 0, 140);
    assert(r.candidate_hwheel == -1 && r.candidate_wheel == -1 &&
           !g.remainder_x && !g.remainder_y && g.scroll_axis == XIAO147_SCROLL_BOTH);
}

static void individual_lobe_tap_tests(void)
{
    const int32_t a_values[12] = {0,0,280,280,0,0,0,0,280,280,0,0};
    const int32_t b_values[12] = {160,160,280,280,0,0,0,0,280,280,160,160};
    struct xiao147_raw_profile a, b, lifted;
    assert(xiao147_raw_profile_evaluate(a_values, 12, NULL, &a));
    assert(xiao147_raw_profile_evaluate(b_values, 12, NULL, &b));
    assert(xiao147_raw_profile_evaluate(quiet, 12, NULL, &lifted));
    assert(a.partition_valid && b.partition_valid &&
           a.native_short_x == b.native_short_x && a.native_long_y == b.native_long_y);
    assert(a.lobes[0].native_long_y == 63 && b.lobes[0].native_long_y == 41 &&
           a.lobes[1].native_long_y == 255 && b.lobes[1].native_long_y == 277);
    struct xiao147_shadow_gesture g;
    xiao147_shadow_gesture_init(&g);
    no_output(profile_step(&g, &lifted, 0)); no_output(profile_step(&g, &lifted, 20));
    no_output(profile_step(&g, &a, 40)); no_output(profile_step(&g, &a, 60));
    assert(g.tap_anchor_valid && !g.tap_disqualified);
    no_output(profile_step(&g, &b, 80));
    assert(g.tap_disqualified && !g.blocked && !g.scroll_activated && !g.moving);
    no_output(profile_step(&g, &a, 100));
    assert(g.tap_disqualified); /* Returning both lobes never restores tap. */
    no_output(profile_step(&g, &lifted, 120)); no_output(profile_step(&g, &lifted, 140));
    assert(!g.tap_anchor_valid && !g.tap_disqualified);

    /* Exact per-lobe 12/13 native-unit boundary, with unchanged pair mean.
     * Added outer support 85 -> +/-12, 91 -> +/-13 from the FIRST anchor. */
    for (unsigned outer = 85; outer <= 91; outer += 6) {
        int32_t v[12]; memcpy(v, a_values, sizeof(v));
        v[0] = v[1] = v[10] = v[11] = (int32_t)outer;
        assert(xiao147_raw_profile_evaluate(v, 12, NULL, &b));
        int offset = (int)a.lobes[0].native_long_y - b.lobes[0].native_long_y;
        assert(offset == (outer == 85 ? 12 : 13));
        assert((int)b.lobes[1].native_long_y - a.lobes[1].native_long_y == offset);
        xiao147_shadow_gesture_init(&g);
        no_output(profile_step(&g, &lifted, 0)); no_output(profile_step(&g, &lifted, 20));
        no_output(profile_step(&g, &a, 40)); no_output(profile_step(&g, &a, 60));
        no_output(profile_step(&g, &b, 80));
        assert(g.tap_disqualified == (offset > 12) && !g.scroll_activated && !g.blocked);
        no_output(profile_step(&g, &lifted, 100));
        assert(profile_step(&g, &lifted, 120).candidate_right_tap == (offset <= 12));
    }

    /* Slow opposing increments also disqualify from a FIXED anchor. No
     * individual frame moves >4, so an accidentally updated anchor would fail. */
    const unsigned outer_steps[] = {41, 60, 79, 91};
    xiao147_shadow_gesture_init(&g);
    no_output(profile_step(&g, &lifted, 0)); no_output(profile_step(&g, &lifted, 20));
    no_output(profile_step(&g, &a, 40)); no_output(profile_step(&g, &a, 60));
    uint16_t previous_left = a.lobes[0].native_long_y;
    uint16_t previous_right = a.lobes[1].native_long_y;
    for (unsigned step_index = 0; step_index < 4; step_index++) {
        int32_t v[12]; memcpy(v, a_values, sizeof(v));
        v[0] = v[1] = v[10] = v[11] = (int32_t)outer_steps[step_index];
        assert(xiao147_raw_profile_evaluate(v, 12, NULL, &b));
        assert(previous_left - b.lobes[0].native_long_y <= 4 &&
               b.lobes[1].native_long_y - previous_right <= 4);
        no_output(profile_step(&g, &b, 80 + 20U * step_index));
        assert(!g.blocked && !g.scroll_activated && !g.moving);
        assert(g.tap_long_anchor[0] == a.lobes[0].native_long_y &&
               g.tap_long_anchor[1] == a.lobes[1].native_long_y);
        previous_left = b.lobes[0].native_long_y;
        previous_right = b.lobes[1].native_long_y;
    }
    assert(g.tap_disqualified);
    no_output(profile_step(&g, &a, 160));
    no_output(profile_step(&g, &lifted, 180)); no_output(profile_step(&g, &lifted, 200));

    /* Tap-only disqualification must not block subsequent coherent net
     * scrolling or accidentally set moving before the net deadzone crosses. */
    const int32_t shifted_values[12] = {0,0,0,0,280,280,0,0,0,0,280,280};
    struct xiao147_raw_profile shifted;
    assert(xiao147_raw_profile_evaluate(shifted_values, 12, NULL, &shifted));
    xiao147_shadow_gesture_init(&g);
    no_output(profile_step(&g, &lifted, 0)); no_output(profile_step(&g, &lifted, 20));
    no_output(profile_step(&g, &a, 40)); no_output(profile_step(&g, &a, 60));
    no_output(profile_step(&g, &b, 80)); /* b is the slow sequence's last +/-13. */
    assert(g.tap_disqualified && !g.moving && !g.scroll_activated);
    struct xiao147_shadow_result r = profile_step(&g, &shifted, 100);
    assert(!r.cancelled && !g.blocked && g.tap_disqualified && g.moving &&
           g.scroll_activated && g.scroll_axis == XIAO147_SCROLL_HORIZONTAL &&
           r.candidate_hwheel == -6 && !r.candidate_wheel);
    no_output(profile_step(&g, &lifted, 120)); no_output(profile_step(&g, &lifted, 140));
}

static void moving_lift_tail_tests(void)
{
    const int32_t pair_values[12] = {0,0,280,280,0,0,0,0,280,280,0,0};
    struct xiao147_raw_profile pair, lifted, one;
    assert(xiao147_raw_profile_evaluate(pair_values,12,NULL,&pair));
    assert(xiao147_raw_profile_evaluate(quiet,12,NULL,&lifted));
    for (unsigned lobe=0;lobe<2;lobe++) {
        for (unsigned outer=0;outer<=2;outer++) {
            int32_t tail[12]={0};
            unsigned peak=lobe==0?1:4,edge=lobe==0?0:5;
            tail[2*peak]=tail[2*peak+1]=280;
            tail[2*edge]=tail[2*edge+1]=outer==0?0:outer==1?85:91;
            assert(xiao147_raw_profile_evaluate(tail,12,NULL,&one));
            assert(one.hypothesis==XIAO147_RAW_ONE_LOBE);
            assert(abs((int)one.native_long_y-pair.lobes[lobe].native_long_y)==
                   (outer==0?0:outer==1?12:13));
            struct xiao147_shadow_gesture g;
            xiao147_shadow_gesture_init(&g);
            no_output(profile_step(&g,&lifted,0));no_output(profile_step(&g,&lifted,20));
            no_output(profile_step(&g,&pair,40));no_output(profile_step(&g,&pair,60));
            no_output(profile_step(&g,&one,80));
            assert(g.tap_disqualified==(outer==2)&&g.tail_one);
            /* Returning the remaining lobe cannot undo a disqualification. */
            tail[2*edge]=tail[2*edge+1]=0;
            assert(xiao147_raw_profile_evaluate(tail,12,NULL,&one));
            no_output(profile_step(&g,&one,100));
            no_output(profile_step(&g,&lifted,120));
            struct xiao147_shadow_result r=profile_step(&g,&lifted,140);
            assert(r.candidate_right_tap==(outer!=2));
            assert(!r.candidate_wheel&&!r.candidate_hwheel);
        }
    }
    /* A merged central lobe is not evidence of a stationary lift tail. */
    const int32_t merged[12]={0,0,0,0,280,280,280,280,0,0,0,0};
    assert(xiao147_raw_profile_evaluate(merged,12,NULL,&one));
    assert(one.hypothesis==XIAO147_RAW_ONE_LOBE);
    struct xiao147_shadow_gesture g;
    xiao147_shadow_gesture_init(&g);
    no_output(profile_step(&g,&lifted,0));no_output(profile_step(&g,&lifted,20));
    no_output(profile_step(&g,&pair,40));no_output(profile_step(&g,&pair,60));
    no_output(profile_step(&g,&one,80));assert(g.tap_disqualified);
    no_output(profile_step(&g,&lifted,100));no_output(profile_step(&g,&lifted,120));
    puts("PASS: synthetic RAW lift-tail movement/merging suppresses false right tap; no claim of independent physical negatives");
}

static void gesture_tests(void)
{
    struct xiao147_shadow_gesture g;
    struct xiao147_shadow_result r;
    xiao147_shadow_gesture_init(&g);
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, 0));
    assert(g.blocked); /* No inferred click without an initial real lift. */
    release(&g, 20); qualify(&g, 60);
    no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 100));
    r = step(&g, XIAO147_RAW_NONE, 0, 0, 0, 120);
    assert(r.candidate_right_tap && r.candidate_wheel == 0 && r.candidate_hwheel == 0);
    no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 140)); /* Never replay click. */

    /* TWO,ONE,TWO is not two consecutive confirmations. No right candidate
     * may appear when this interrupted unqualified sequence finally lifts.
     */
    xiao147_shadow_gesture_init(&g); release(&g, 0);
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, 40));
    assert(g.two_frames == 1 && !g.qualified_two);
    r = step(&g, XIAO147_RAW_ONE_LOBE, 100, 100, 0, 60);
    no_output(r); assert(r.cancelled && g.blocked && g.two_frames == 0);
    no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, 80));
    assert(g.blocked && !g.qualified_two);
    release(&g, 100);

    qualify(&g, 160);
    no_output(step(&g, XIAO147_RAW_ONE_LOBE, 10, 10, 0, 200));
    no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 220));
    assert(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 240).candidate_right_tap);
    /* Tail centroid changed to remaining finger; no scroll jump or left tap. */
    qualify(&g, 260);
    no_output(step(&g, XIAO147_RAW_ONE_LOBE, 10, 10, 0, 300));
    r = step(&g, XIAO147_RAW_ONE_LOBE, 10, 10, 0, 380);
    assert(r.cancelled); release(&g, 400);
    qualify(&g, 440);
    no_output(step(&g, XIAO147_RAW_ONE_LOBE, 10, 10, 0, 480));
    r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, 500);
    assert(r.cancelled); release(&g, 520);

    /* Natural-scroll candidate is -logical finger delta on both axes. */
    for (int axis = 0; axis < 2; axis++) {
        for (int sign = -1; sign <= 1; sign += 2) {
            xiao147_shadow_gesture_init(&g); release(&g, 0); qualify(&g, 40);
            r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES,
                     100 + (axis == 0 ? sign * 28 : 0),
                     100 + (axis == 1 ? sign * 28 : 0), 0, 80);
            assert(r.candidate_hwheel == (axis == 0 ? -2 * sign : 0));
            assert(r.candidate_wheel == (axis == 1 ? -2 * sign : 0));
            assert(!r.candidate_right_tap);
            no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 100));
            no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 120));
        }
    }
    /* Root's actual four-pose transform is composed once before tracking;
     * synthetic vector checks cannot claim physical short-axis calibration. */
    for (uint8_t pose = 0; pose < 4; pose++) {
        struct xiao147_xy a = xiao147_touch_to_logical(pose, 70, 150);
        struct xiao147_xy b = xiao147_touch_to_logical(pose, 98, 178);
        /* Each raw axis moves 28; the initial one-time 12 deadzone leaves 16. */
        struct xiao147_xy expected = xiao147_vector_to_logical(pose, 16, 16);
        xiao147_shadow_gesture_init(&g);
        no_output(step(&g, XIAO147_RAW_NONE, 0, 0, pose, 0));
        no_output(step(&g, XIAO147_RAW_NONE, 0, 0, pose, 20));
        no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, a.x, a.y, pose, 40));
        no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, a.x, a.y, pose, 60));
        r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, b.x, b.y, pose, 80);
        assert(r.candidate_hwheel == -expected.x / 8);
        assert(r.candidate_wheel == -expected.y / 8);
    }

    xiao147_shadow_gesture_init(&g); release(&g, 0); qualify(&g, 40);
    r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, 214);
    assert(r.cancelled); release(&g, 230); /* Missing frame cancels tap. */
    qualify(&g, 270);
    r = step(&g, XIAO147_RAW_UNKNOWN, 100, 100, 0, 310);
    assert(r.cancelled); release(&g, 330);
    qualify(&g, 370);
    r = xiao147_shadow_gesture_observe(&g, XIAO147_RAW_NONE, false, 0, 0, 0, 410);
    assert(r.cancelled); release(&g, 430); /* Error is not a release. */
    qualify(&g, 470);
    r = step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 1, 510);
    assert(r.cancelled);
    no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 1, 530));
    no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 1, 550));
    assert(!g.blocked);

    xiao147_shadow_gesture_init(&g); release(&g, 0); qualify(&g, 40);
    assert(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 181, 100, 0, 80).cancelled);
    release(&g, 100); qualify(&g, 140);
    no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 0, 180));
    assert(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, 200).cancelled);
    release(&g, 220); /* Contact return before confirmed lift is ambiguous. */
    no_output(step(&g, XIAO147_RAW_ONE_LOBE, 100, 100, 0, 260));
    release(&g, 280); /* No left-click API, even for confirmed ONE morphology. */

    xiao147_shadow_gesture_init(&g); release(&g, 0); qualify(&g, 40);
    for (unsigned t = 80; t <= 320; t += 20) {
        no_output(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, t));
    }
    release(&g, 340); /* Long hold does not right click on lift. */
    xiao147_shadow_gesture_init(&g);
    uint32_t start = UINT32_MAX - 90U;
    release(&g, start); qualify(&g, start + 40U);
    no_output(step(&g, XIAO147_RAW_NONE, 0, 0, 0, start + 80U));
    assert(step(&g, XIAO147_RAW_NONE, 0, 0, 0, start + 100U).candidate_right_tap);
    xiao147_shadow_gesture_init(&g); release(&g, 0); qualify(&g, 40);
    assert(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, 100, 100, 0, 60).cancelled);
    release(&g, 80); /* Duplicate frame time cannot qualify a touch twice. */
    assert(step(&g, (enum xiao147_raw_hypothesis)-1, 100, 100, 0, 120).cancelled);
    release(&g, 140);
    assert(step(&g, XIAO147_RAW_TWO_SEPARATED_LOBES, -1, 100, 0, 180).cancelled);
    r = xiao147_shadow_gesture_observe(NULL, XIAO147_RAW_NONE, true, 0, 0, 0, 0);
    assert(r.cancelled); xiao147_shadow_gesture_init(NULL); xiao147_shadow_gesture_cancel(NULL);
}

static void actual_scene_replay(void)
{
    unsigned total_matches = 0;
    for (unsigned held_scene = 0; held_scene < 4; held_scene++) {
        /* Whole-scene exclusion keeps every frame from this hold together.
         * No threshold is refit on the other three scenes. It is still a
         * retrospective frozen-rule check because rules were inspected on
         * these same four recordings; do NOT call this unbiased validation.
         */
        unsigned fitting_frames = 0, fitting_two_scenes = 0;
        for (unsigned other = 0; other < 4; other++) {
            if (other == held_scene) { continue; }
            fitting_frames += 129;
            fitting_two_scenes += labels[other] == XIAO147_RAW_TWO_SEPARATED_LOBES;
        }
        assert(fitting_frames == 387);
        if (labels[held_scene] == XIAO147_RAW_TWO_SEPARATED_LOBES) {
            assert(fitting_two_scenes == 0); /* No independent second two scene! */
        }
        struct xiao147_raw_baseline baseline;
        struct xiao147_shadow_gesture gesture;
        xiao147_raw_baseline_begin(&baseline, true);
        xiao147_shadow_gesture_init(&gesture);
        unsigned matches = 0;
        for (unsigned frame = 0; frame < 129; frame++) {
            const int32_t *row = scenes[held_scene][frame];
            struct xiao147_raw_profile profile;
            assert(xiao147_raw_profile_evaluate(row + 1, 12, NULL, &profile));
            matches += profile.hypothesis == labels[held_scene];
            assert(profile.native_short_x <= 171 && profile.native_long_y <= 319);
            assert(!xiao147_raw_baseline_observe(&baseline, row + 1, 12, true,
                                                (uint32_t)row[0]));
            /* Each contact was already held before acquisition. With no
             * freshly verified baseline none of these scenes is runtime
             * gesture ground truth, even if morphology happens to match. */
            struct xiao147_shadow_result r = xiao147_shadow_gesture_observe(
                &gesture, XIAO147_RAW_UNKNOWN, false, 0, 0, 0, (uint32_t)row[0]);
            no_output(r);
        }
        assert(matches == 129 && !baseline.armed);
        total_matches += matches;
        printf("Retrospective WHOLE-scene exclusion %s: %u/129 morphology matches; "
               "other-scene frames=387, other two-contact scenes=%u; no runtime HID\n",
               scene_names[held_scene], matches, fitting_two_scenes);
    }
    assert(total_matches == 516);
}

int main(void)
{
    profile_tests(); baseline_tests(); pending_tests(); independent_lobe_tests();
    activation_and_axis_tests(); individual_lobe_tap_tests();
    moving_lift_tail_tests();
    gesture_tests(); actual_scene_replay();
    puts("PASS: actual C signed DIFF morphology (4 in-sample scenes only), "
         "fresh-lift baseline gate, independently normalised/equal lobes, "
         "bounded weak onset PENDING, partition association guards, "
         "net12 activation/no jitter backlog, fixed2:1 axes/true diagonal, "
         "fixed individual-lobe tap-only guard/opposed-motion cancellation, "
         "shadow right-tap/natural-scroll signs, "
         "two-to-one release tail, uncertainty/gap/pose cancellation, "
         "no left-click API, no HID calls, integer bounds and uptime wrap. "
         "NOT validated arbitrary finger count or physical both-axis mapping.");
}
'''.replace("__ACTUAL_SCENES__", declarations)

with tempfile.TemporaryDirectory(prefix="xiao147-raw-gestures-") as directory:
    folder = Path(directory)
    source = folder / "test.c"
    source.write_text(harness)
    binary = folder / "test"
    subprocess.run([
        "cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
        "-I", str(root / "src"), str(root / "src/xiao147_raw_gestures.c"),
        str(source), "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], check=True)
    # Prove the new counterexamples fail with the previous ignored-tail rule.
    driver = (root / "src/xiao147_raw_gestures.c").read_text()
    guard = "if (!near_anchor) { gesture->tap_disqualified = true; }"
    assert driver.count(guard) == 1
    mutant = folder / "mutant.c"
    mutant.write_text(driver.replace(guard,
        "if (false && !near_anchor) { gesture->tap_disqualified = true; }"))
    subprocess.run([
        "cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
        "-I", str(root / "src"), str(mutant), str(source), "-o", str(binary),
    ], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.returncode != 0 and "g.tap_disqualified==(outer==2)" in result.stderr, result.stderr
    print("PASS: removing RAW tail guard reproduces the moving-lift-tail regression")
