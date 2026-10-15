"""Chronological actual-C replay of the complete 513f60d dynamic session.

All 2241 DIFF frames remain one in-sample session. Human-confirmed action order
is associated with signal-derived windows, not independent timed truth. This
test reports candidate changes, never touch-count accuracy/direction success.
No hardware access, firmware writes or HID calls are present.
"""
from pathlib import Path
import hashlib
import json
import re
import statistics
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
fixture_path = root / "tests/fixtures/xiao147_raw_dynamic_profiles.json"
fixture = json.loads(fixture_path.read_text())
source_sha = "5dcb4f2ab2482e7ff6b9ec3d548cbceb32058bf81b466360c2f79f83dbd9ca31"
rows_sha = "9bed5301065200033d6f28356c38c32396855a3b6e73093141f0191ae7d9998d"
assert fixture["schema_version"] == 1 and fixture["source_sha256"] == source_sha
assert fixture["firmware_commit"] == "513f60d34120be2d3de04a26df1b32578b44670f"
assert fixture["controller_id"] == "510601" and fixture["controller_firmware_hex"] == "0100"
assert fixture["channels"] == 12 and fixture["channel_layout"] == [6, 2, 0]
assert fixture["capture_mode"] == "FAST_DIFF_SHADOW_HID_DISABLED"
assert fixture["physical_orientation"] == "USB_LEFT"
assert fixture["frame_schema"] == ["t_ms"] + [f"diff_{i}" for i in range(12)]
frames = fixture["frames"]
assert len(frames) == fixture["source_frame_count"] == 2241
assert all(len(row) == 13 and all(type(value) is int for value in row) for row in frames)
assert all(0 <= row[0] <= 60000 and all(-32768 <= value <= 32767 for value in row[1:])
           for row in frames)
assert hashlib.sha256(json.dumps(frames, separators=(",", ":")).encode()).hexdigest() == rows_sha
assert fixture["frame_rows_sha256"] == rows_sha
intervals = [b[0] - a[0] for a, b in zip(frames, frames[1:])]
assert (frames[0][0], frames[-1][0]) == (56, 59989)
assert (min(intervals), statistics.median(intervals), max(intervals)) == (21, 26, 31)

# CI uses the pinned complete fixture; when the original local recording is
# available, independently ensure its full trace/rows still match provenance.
source_path = root.parent / fixture["source_log"]
if source_path.exists():
    source_bytes = source_path.read_bytes()
    assert hashlib.sha256(source_bytes).hexdigest() == source_sha
    report = re.compile(r"AXS_SENSOR t=(\d+) type=2 n=12 values=(-?\d+(?:,-?\d+){11})$")
    extracted = []
    for line in source_bytes.decode().splitlines():
        if "AXS_SENSOR" in line:
            match = report.search(line)
            assert match, "Incomplete sensor record in original source"
            extracted.append([int(match.group(1)), *map(int, match.group(2).split(","))])
    assert extracted == frames

integrity = fixture["source_integrity"]
assert integrity["rate_records"] == 30 and integrity["every_rate_counter_matches_received_frames"]
assert integrity["all_frames_complete_signed_diff12"] and integrity["strictly_increasing_timestamps"]
assert integrity["no_sampling_error_or_drop"] and integrity["normal_input_restored"]
protocol = fixture["operator_protocol"]
labels = ["two_finger_tap"] * 3 + ["two_finger_up"] * 2 + ["two_finger_down"] * 2
labels += ["two_finger_left"] * 2 + ["two_finger_right"] * 2
assert protocol["confirmed_action_count"] == 11 and protocol["requested_order"] == labels
assert protocol["all_actions_two_finger"] and protocol["complete_lift_between_actions"]
assert protocol["device_not_rotated"] and not protocol["timestamp_ground_truth"]
assert fixture["normal_lift_confirmed_by_firmware_start_gate"]

# Reproduce segmentation without using labels, centroids or a candidate result.
# The first five recorded quiet frames are the fresh baseline, not held-contact
# training. Integer averaging is deliberately C truncation toward zero.
baseline = [int(sum(row[channel] for row in frames[:5]) / 5) for channel in range(1, 13)]
assert baseline == fixture["activity_segmentation"]["quiet_baseline_values"] == [0, 2] + [0] * 10
active = [(index, row) for index, row in enumerate(frames)
          if any(abs(value - base) > 20 for value, base in zip(row[1:], baseline))]
bursts = []
for item in active:
    if not bursts or item[1][0] - bursts[-1][-1][1][0] > 100:
        bursts.append([])
    bursts[-1].append(item)
windows = fixture["action_windows"]
assert len(bursts) == len(windows) == len(labels) == 11
original = fixture["original_firmware_diagnostics"]
assert original["shape_histogram_none_one_two_unknown"] == [2134, 15, 79, 13]
assert sum(original["shape_histogram_none_one_two_unknown"]) == len(frames)
assert len(original["candidate_right_taps"]) == 1 and len(original["candidate_scroll_frames"]) == 13
assert original["no_hid_emission"] and len(original["sparse_shadow_records"]) == 108
for number, (burst, window, label) in enumerate(zip(bursts, windows, labels), 1):
    first, last = burst[0][1][0], burst[-1][1][0]
    assert window["id"] == f"action_{number:02d}" and window["operator_order_label"] == label
    assert (window["activity_start_t_ms"], window["activity_end_t_ms"]) == (first, last)
    assert window["activity_frame_indices"] == [index for index, _ in burst]
    assert window["evaluation_window_t_ms"] == [first - 100, last + 150]
    lo, hi = window["evaluation_window_t_ms"]
    assert window["original_candidate_right_taps"] == sum(
        lo <= record["t_ms"] <= hi for record in original["candidate_right_taps"])
    assert window["original_candidate_scroll_frames"] == sum(
        lo <= record["t_ms"] <= hi for record in original["candidate_scroll_frames"])
assert sum(window["original_candidate_right_taps"] for window in windows) == 1
assert sum(window["original_candidate_scroll_frames"] for window in windows) == 13
assert all(a["evaluation_window_t_ms"][1] < b["evaluation_window_t_ms"][0]
           for a, b in zip(windows, windows[1:]))

declarations = "static const int32_t frames[FRAME_COUNT][13] = {\n" + ",\n".join(
    "    {" + ",".join(map(str, row)) + "}" for row in frames) + "\n};\n"
declarations += "static const struct action_window actions[ACTION_COUNT] = {\n" + ",\n".join(
    "    {" + json.dumps(window["id"]) + "," + json.dumps(window["operator_order_label"]) + "," +
    ",".join(map(str, [*window["evaluation_window_t_ms"], window["activity_start_t_ms"],
                       window["activity_end_t_ms"], window["original_candidate_right_taps"],
                       window["original_candidate_scroll_frames"]])) + "}"
    for window in windows) + "\n};\n"
declarations += "static const int16_t expected_baseline[12] = {" + ",".join(map(str, baseline)) + "};\n"

harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "xiao147_raw_gestures.h"
#include "xiao147_orientation.h"

#define FRAME_COUNT 2241U
#define ACTION_COUNT 11U
struct action_window {
    const char *id;
    const char *label;
    uint32_t evaluation_start_ms, evaluation_end_ms;
    uint32_t activity_start_ms, activity_end_ms;
    unsigned original_taps, original_scroll_frames;
};
__ACTUAL_DATA__

struct action_counts {
    unsigned taps, scroll_frames, wheel_frames, pan_frames, cancelled_frames;
    int wheel_sum, pan_sum;
};
struct replay_counts {
    unsigned shape[5];
    unsigned taps, scroll_frames, cancellations, quiet_candidates;
    uint32_t arm_ms;
    struct action_counts actions[ACTION_COUNT];
};

static bool candidate(struct xiao147_shadow_result result)
{
    return result.candidate_right_tap || result.candidate_wheel != 0 ||
           result.candidate_hwheel != 0;
}
static void no_candidate(struct xiao147_shadow_result result)
{
    assert(!candidate(result));
}
static int action_index(uint32_t timestamp)
{
    for (unsigned index = 0; index < ACTION_COUNT; index++) {
        if (timestamp >= actions[index].evaluation_start_ms &&
            timestamp <= actions[index].evaluation_end_ms) { return (int)index; }
    }
    return -1;
}

static struct replay_counts replay(enum xiao147_orientation pose,
                                  struct xiao147_shadow_result *all_results,
                                  bool normal_lift_confirmed)
{
    struct xiao147_raw_baseline baseline;
    struct xiao147_shadow_gesture gesture;
    struct replay_counts counts = {0};
    xiao147_raw_baseline_begin(&baseline, normal_lift_confirmed);
    xiao147_shadow_gesture_init(&gesture);
    for (unsigned index = 0; index < FRAME_COUNT; index++) {
        const int32_t *row = frames[index];
        uint32_t timestamp = (uint32_t)row[0];
        bool armed_before = baseline.armed;
        bool armed = xiao147_raw_baseline_observe(&baseline, row + 1, 12, true, timestamp);
        if (armed && !armed_before) {
            counts.arm_ms = timestamp;
            assert(index == 4 && timestamp == 157 && baseline.quiet_frames == 5);
            assert(baseline.last_ms - baseline.first_ms == 101);
        }
        if (armed) {
            for (unsigned channel = 0; channel < 12; channel++) {
                assert(baseline.values[channel] == expected_baseline[channel]);
            }
        }
        struct xiao147_raw_profile profile = {.hypothesis = XIAO147_RAW_UNKNOWN};
        bool valid = armed && xiao147_raw_profile_evaluate(row + 1, 12, baseline.values, &profile);
        assert((unsigned)profile.hypothesis <= (unsigned)XIAO147_RAW_PENDING);
        assert(profile.native_short_x <= 171 && profile.native_long_y <= 319);
        if (profile.hypothesis == XIAO147_RAW_TWO_SEPARATED_LOBES) {
            assert(profile.partition_valid && profile.significant_peaks == 2);
            assert(profile.lobes[0].last_station < profile.valley_station &&
                   profile.valley_station < profile.lobes[1].first_station);
            assert(profile.lobes[0].native_long_y < profile.lobes[1].native_long_y);
        }
        struct xiao147_xy logical = xiao147_touch_to_logical(pose,
            profile.native_short_x, profile.native_long_y);
        bool was_active = gesture.active;
        struct xiao147_shadow_result result = xiao147_shadow_gesture_observe_profile(
            &gesture, &profile, valid, (int16_t)logical.x, (int16_t)logical.y, pose, timestamp);
        assert(result.candidate_wheel >= -127 && result.candidate_wheel <= 127);
        assert(result.candidate_hwheel >= -127 && result.candidate_hwheel <= 127);
        if (result.cancelled) { no_candidate(result); }
        if (result.cancelled || (profile.hypothesis == XIAO147_RAW_NONE &&
                                 gesture.release_frames == 2)) {
            assert(gesture.scroll_axis == XIAO147_SCROLL_NONE);
            assert(!gesture.scroll_activated && !gesture.scroll_x_started &&
                   !gesture.scroll_y_started);
            assert(!gesture.scroll_x_emitted && !gesture.scroll_y_emitted);
            assert(!gesture.remainder_x && !gesture.remainder_y);
        }
        if (gesture.scroll_axis == XIAO147_SCROLL_HORIZONTAL) {
            assert(!result.candidate_wheel && !gesture.remainder_y &&
                   !gesture.scroll_y_started);
        }
        if (gesture.scroll_axis == XIAO147_SCROLL_VERTICAL) {
            assert(!result.candidate_hwheel && !gesture.remainder_x &&
                   !gesture.scroll_x_started);
        }
        if (!armed || !valid || profile.hypothesis == XIAO147_RAW_UNKNOWN ||
            profile.hypothesis == XIAO147_RAW_PENDING) { no_candidate(result); }
        if (was_active && (profile.hypothesis == XIAO147_RAW_UNKNOWN ||
                           profile.hypothesis == XIAO147_RAW_PENDING)) {
            assert(result.cancelled && gesture.blocked && !gesture.active);
        }
        if (gesture.pending) {
            assert(!gesture.active && !gesture.blocked && !gesture.qualified_two);
            assert(timestamp - gesture.pending_since_ms <= XIAO147_SHADOW_PENDING_MS);
        }
        if (result.candidate_right_tap) {
            assert(armed && valid && profile.hypothesis == XIAO147_RAW_NONE);
            assert(!gesture.active && gesture.release_frames == 2);
            assert(!result.candidate_wheel && !result.candidate_hwheel);
        }
        if (result.candidate_wheel || result.candidate_hwheel) {
            assert(armed && valid && profile.hypothesis == XIAO147_RAW_TWO_SEPARATED_LOBES);
            assert(gesture.qualified_two && gesture.association_valid && gesture.moving);
            assert(gesture.scroll_activated && gesture.scroll_axis != XIAO147_SCROLL_NONE);
            if (result.candidate_wheel) { assert(gesture.scroll_y_started); }
            if (result.candidate_hwheel) { assert(gesture.scroll_x_started); }
        }
        counts.shape[profile.hypothesis]++;
        counts.taps += result.candidate_right_tap;
        counts.scroll_frames += result.candidate_wheel != 0 || result.candidate_hwheel != 0;
        counts.cancellations += result.cancelled;
        int action = action_index(timestamp);
        if (action < 0) {
            counts.quiet_candidates += candidate(result);
            no_candidate(result); /* Long initial/tail quiet and lift gaps. */
        } else {
            struct action_counts *a = &counts.actions[(unsigned)action];
            a->taps += result.candidate_right_tap;
            a->scroll_frames += result.candidate_wheel != 0 || result.candidate_hwheel != 0;
            a->wheel_frames += result.candidate_wheel != 0;
            a->pan_frames += result.candidate_hwheel != 0;
            a->wheel_sum += result.candidate_wheel;
            a->pan_sum += result.candidate_hwheel;
            a->cancelled_frames += result.cancelled;
        }
        if (all_results != NULL) { all_results[index] = result; }
    }
    unsigned total = 0;
    for (unsigned hypothesis = 0; hypothesis < 5; hypothesis++) { total += counts.shape[hypothesis]; }
    assert(total == FRAME_COUNT && counts.quiet_candidates == 0);
    if (normal_lift_confirmed) {
        assert(baseline.armed && counts.arm_ms == 157 && !gesture.active && !gesture.pending);
        assert(!gesture.blocked); /* Real final quiet fully releases tracker. */
    } else {
        assert(!baseline.armed && counts.arm_ms == 0 && counts.taps == 0 && counts.scroll_frames == 0);
        assert(gesture.blocked);
    }
    return counts;
}

int main(void)
{
    struct xiao147_shadow_result reference[FRAME_COUNT];
    struct xiao147_shadow_result rotated[FRAME_COUNT];
    struct replay_counts counts = replay(XIAO147_USB_LEFT, reference, true);
    printf("IN-SAMPLE full-session replay: frames=%u arm=%u ms quiet_candidates=%u "
           "shape NONE/ONE/TWO/UNKNOWN/PENDING=%u/%u/%u/%u/%u; "
           "original taps/scroll_frames=1/13; revised=%u/%u; HID=0\n",
           FRAME_COUNT, counts.arm_ms, counts.quiet_candidates,
           counts.shape[0], counts.shape[1], counts.shape[2], counts.shape[3], counts.shape[4],
           counts.taps, counts.scroll_frames);
    for (unsigned index = 0; index < ACTION_COUNT; index++) {
        const struct action_counts *a = &counts.actions[index];
        printf("IN-SAMPLE order-associated %s %s [%u..%u ms] "
               "original taps/scroll_frames=%u/%u -> revised=%u/%u "
               "wheel/pan frames=%u/%u signed sums=%d/%d cancelled=%u\n",
               actions[index].id, actions[index].label,
               actions[index].activity_start_ms, actions[index].activity_end_ms,
               actions[index].original_taps, actions[index].original_scroll_frames,
               a->taps, a->scroll_frames, a->wheel_frames, a->pan_frames,
               a->wheel_sum, a->pan_sum, a->cancelled_frames);
    }
    /* Same real USB-left recording, transformed mathematically only. This is
     * a four-pose algebra/safety regression, not three new hardware sessions. */
    for (unsigned pose = 1; pose < 4; pose++) {
        struct replay_counts other = replay((enum xiao147_orientation)pose, rotated, true);
        assert(other.taps == counts.taps && other.scroll_frames == counts.scroll_frames);
        assert(other.cancellations == counts.cancellations);
        assert(memcmp(other.shape, counts.shape, sizeof(counts.shape)) == 0);
        for (unsigned index = 0; index < FRAME_COUNT; index++) {
            struct xiao147_xy expected = xiao147_vector_to_logical((enum xiao147_orientation)pose,
                reference[index].candidate_hwheel, reference[index].candidate_wheel);
            assert(rotated[index].candidate_right_tap == reference[index].candidate_right_tap);
            assert(rotated[index].cancelled == reference[index].cancelled);
            assert(rotated[index].candidate_hwheel == expected.x);
            assert(rotated[index].candidate_wheel == expected.y);
        }
    }
    /* Reusing the same data with missing normal-zero permission is a formal
     * safety test only, not an independently collected negative recording. */
    struct replay_counts denied = replay(XIAO147_USB_LEFT, NULL, false);
    assert(denied.taps == 0 && denied.scroll_frames == 0);
    puts("PASS: complete real dynamic C replay, fresh immutable baseline, quiet/invalid/PENDING/association safety, "
         "four-pose algebra; diagnostic candidates only, no accuracy/direction/HID success claim");
    return 0;
}
'''
harness = harness.replace("__ACTUAL_DATA__", declarations)

with tempfile.TemporaryDirectory(prefix="xiao147-raw-dynamic-") as directory:
    c = Path(directory) / "test.c"
    c.write_text(harness)
    binary = Path(directory) / "test"
    # A portable module with no Zephyr/OS input dependency is a requirement,
    # not a stubbed HID implementation that silently swallows real events.
    preprocessed = subprocess.run(
        ["cc", "-std=c11", "-E", "-P", "-I", str(root / "src"),
         str(root / "src/xiao147_raw_gestures.c")], check=True, capture_output=True, text=True,
    ).stdout
    for forbidden in ("input_report_key", "input_report_rel", "usb_hid", "zmk_hid"):
        assert forbidden not in preprocessed, f"HID dependency in shadow module: {forbidden}"
    subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
         "-I", str(root / "src"), str(c), str(root / "src/xiao147_raw_gestures.c"),
         "-o", str(binary)], check=True,
    )
    subprocess.run([str(binary)], check=True)
