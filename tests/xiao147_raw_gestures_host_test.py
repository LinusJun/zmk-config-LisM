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
           p.lesser_peak_permille >= 350);
    assert(p.native_short_x <= 171 && p.native_long_y <= 319);

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
    assert(p.hypothesis == XIAO147_RAW_UNKNOWN); /* Weak signal != release. */
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
                     100 + (axis == 0 ? sign * 16 : 0),
                     100 + (axis == 1 ? sign * 16 : 0), 0, 80);
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
        struct xiao147_xy b = xiao147_touch_to_logical(pose, 86, 166);
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
    profile_tests(); baseline_tests(); gesture_tests(); actual_scene_replay();
    puts("PASS: actual C signed DIFF morphology (4 in-sample scenes only), "
         "fresh-lift baseline gate, shadow right-tap/natural-scroll signs, "
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
