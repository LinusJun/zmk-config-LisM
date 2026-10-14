#include "xiao147_raw_gestures.h"

#include <limits.h>
#include <stddef.h>
#include <string.h>

#define PROFILE_MIN_PEAK 80U
#define PROFILE_PEAK_PERCENT 35U
#define PROFILE_VALLEY_PERCENT 40U
#define BASELINE_MS 100U
#define TAP_MS 240U
#define TAP_MOVE 12
#define LIFT_TAIL_MS 60U
#define JUMP_LIMIT 80
#define SCROLL_STEP 8

static int magnitude(int value)
{
    return value < 0 ? -value : value;
}

static bool frame_valid(const int32_t *diff, uint8_t channels)
{
    if (diff == NULL || channels != XIAO147_PROFILE_CHANNELS) {
        return false;
    }
    for (unsigned i = 0; i < XIAO147_PROFILE_CHANNELS; i++) {
        if (diff[i] < INT16_MIN || diff[i] > INT16_MAX) {
            return false;
        }
    }
    return true;
}

bool xiao147_raw_profile_evaluate(const int32_t *diff, uint8_t channels,
                                 const int16_t *baseline,
                                 struct xiao147_raw_profile *profile)
{
    if (profile == NULL) {
        return false;
    }
    memset(profile, 0, sizeof(*profile));
    profile->hypothesis = XIAO147_RAW_UNKNOWN;
    if (!frame_valid(diff, channels)) {
        return false;
    }

    uint32_t positive[XIAO147_PROFILE_CHANNELS];
    uint32_t maximum = 0;
    uint64_t total = 0, weighted = 0, odd_column = 0;
    for (unsigned i = 0; i < XIAO147_PROFILE_CHANNELS; i++) {
        int32_t value = diff[i] - (baseline == NULL ? 0 : baseline[i]);
        /* Large negative electrode responses are unexplained by this positive
         * contact model, not a reliable release. Quiet negatives are noise.
         */
        if (value < -4 * XIAO147_PROFILE_FLOOR) {
            return true;
        }
        value -= XIAO147_PROFILE_FLOOR;
        positive[i] = value > 0 ? (uint32_t)value : 0;
        profile->stations[i / 2] += positive[i];
        total += positive[i];
        weighted += (uint64_t)positive[i] * (i / 2);
        if ((i & 1U) != 0) {
            odd_column += positive[i];
        }
    }
    for (unsigned i = 0; i < XIAO147_PROFILE_STATIONS; i++) {
        if (profile->stations[i] > maximum) {
            maximum = profile->stations[i];
        }
    }
    if (total == 0) {
        profile->hypothesis = XIAO147_RAW_NONE;
        return true;
    }
    if (maximum < PROFILE_MIN_PEAK) {
        return true; /* Weak/near-threshold contact: UNKNOWN, not release. */
    }
    profile->native_long_y = (uint16_t)((weighted * 319U) / (total * 5U));
    profile->native_short_x = (uint16_t)((odd_column * 171U) / total);

    uint8_t peaks[XIAO147_PROFILE_STATIONS];
    uint8_t widths[XIAO147_PROFILE_STATIONS];
    unsigned count = 0;
    for (unsigned left = 0; left < XIAO147_PROFILE_STATIONS;) {
        unsigned right = left;
        uint32_t height = profile->stations[left];
        while (right + 1U < XIAO147_PROFILE_STATIONS &&
               profile->stations[right + 1U] == height) {
            right++;
        }
        bool above_left = left == 0 || height > profile->stations[left - 1U];
        bool above_right = right + 1U == XIAO147_PROFILE_STATIONS ||
                           height > profile->stations[right + 1U];
        if (above_left && above_right && height >= PROFILE_MIN_PEAK &&
            (uint64_t)height * 100U >= (uint64_t)maximum * PROFILE_PEAK_PERCENT) {
            peaks[count] = (uint8_t)((left + right) / 2U);
            widths[count] = (uint8_t)(right - left + 1U);
            count++;
        }
        left = right + 1U;
    }
    profile->significant_peaks = (uint8_t)count;
    if (count == 1U && widths[0] <= 3U) {
        profile->hypothesis = XIAO147_RAW_ONE_LOBE;
        return true;
    }
    if (count != 2U || peaks[1] - peaks[0] < 2 || widths[0] > 2U ||
        widths[1] > 2U) {
        return true;
    }
    uint32_t a = profile->stations[peaks[0]], b = profile->stations[peaks[1]];
    uint32_t lesser = a < b ? a : b;
    uint32_t greater = a > b ? a : b;
    uint32_t valley = lesser;
    for (unsigned i = peaks[0] + 1U; i < peaks[1]; i++) {
        if (profile->stations[i] < valley) {
            valley = profile->stations[i];
        }
    }
    profile->valley_permille = (uint16_t)(((uint64_t)valley * 1000U) / lesser);
    profile->lesser_peak_permille = (uint16_t)(((uint64_t)lesser * 1000U) / greater);
    if ((uint64_t)valley * 100U <= (uint64_t)lesser * PROFILE_VALLEY_PERCENT) {
        profile->hypothesis = XIAO147_RAW_TWO_SEPARATED_LOBES;
    }
    return true;
}

void xiao147_raw_baseline_begin(struct xiao147_raw_baseline *baseline,
                              bool normal_lift_confirmed)
{
    if (baseline == NULL) {
        return;
    }
    memset(baseline, 0, sizeof(*baseline));
    baseline->normal_lift_confirmed = normal_lift_confirmed;
}

static void baseline_clear_window(struct xiao147_raw_baseline *baseline)
{
    memset(baseline->sum, 0, sizeof(baseline->sum));
    baseline->quiet_frames = 0;
}

bool xiao147_raw_baseline_observe(struct xiao147_raw_baseline *baseline,
                                const int32_t *diff, uint8_t channels,
                                bool valid, uint32_t now_ms)
{
    if (baseline == NULL) {
        return false;
    }
    if (!valid || !frame_valid(diff, channels)) {
        xiao147_raw_baseline_begin(baseline, false);
        return false;
    }
    if (!baseline->normal_lift_confirmed) {
        return false;
    }
    if (baseline->armed) {
        return true; /* Never adapt baseline with a finger present. */
    }
    if (baseline->have_time && now_ms - baseline->last_ms >
        XIAO147_SHADOW_MAX_GAP_MS) {
        baseline_clear_window(baseline);
    }
    baseline->last_ms = now_ms;
    baseline->have_time = true;
    for (unsigned i = 0; i < XIAO147_PROFILE_CHANNELS; i++) {
        if (magnitude((int)diff[i]) > XIAO147_PROFILE_FLOOR) {
            baseline_clear_window(baseline);
            return false;
        }
    }
    if (baseline->quiet_frames == 0) {
        baseline->first_ms = now_ms;
    }
    for (unsigned i = 0; i < XIAO147_PROFILE_CHANNELS; i++) {
        baseline->sum[i] += diff[i];
    }
    baseline->quiet_frames++;
    if (baseline->quiet_frames >= 3 && now_ms - baseline->first_ms >= BASELINE_MS) {
        for (unsigned i = 0; i < XIAO147_PROFILE_CHANNELS; i++) {
            baseline->values[i] = (int16_t)(baseline->sum[i] / baseline->quiet_frames);
        }
        baseline->armed = true;
    }
    /* At realistic sampling this arms long before overflow. Even callers
     * giving a frozen timestamp cannot overflow the accumulator or count.
     */
    if (!baseline->armed && baseline->quiet_frames >= 4096U) {
        baseline_clear_window(baseline);
    }
    return baseline->armed;
}

void xiao147_shadow_gesture_init(struct xiao147_shadow_gesture *gesture)
{
    if (gesture != NULL) {
        memset(gesture, 0, sizeof(*gesture));
        gesture->blocked = true;
    }
}

void xiao147_shadow_gesture_cancel(struct xiao147_shadow_gesture *gesture)
{
    if (gesture != NULL) {
        gesture->blocked = true;
        gesture->active = false;
        gesture->qualified_two = false;
        gesture->moving = false;
        gesture->tail_one = false;
        gesture->two_frames = 0;
        gesture->release_frames = 0;
        gesture->remainder_x = gesture->remainder_y = 0;
    }
}

static int16_t scroll_candidate(int *remainder)
{
    int ticks = *remainder / SCROLL_STEP;
    if (ticks > 127) { ticks = 127; }
    if (ticks < -127) { ticks = -127; }
    *remainder -= ticks * SCROLL_STEP;
    return (int16_t)-ticks;
}

struct xiao147_shadow_result xiao147_shadow_gesture_observe(
    struct xiao147_shadow_gesture *gesture,
    enum xiao147_raw_hypothesis hypothesis, bool valid,
    int16_t logical_x, int16_t logical_y, uint8_t orientation, uint32_t now_ms)
{
    struct xiao147_shadow_result result = {0};
    if (gesture == NULL) {
        result.cancelled = true;
        return result;
    }
    bool time_or_pose_fault = gesture->have_time &&
        (now_ms == gesture->last_ms ||
         now_ms - gesture->last_ms > XIAO147_SHADOW_MAX_GAP_MS ||
         gesture->orientation != orientation);
    gesture->last_ms = now_ms;
    gesture->orientation = orientation;
    gesture->have_time = true;
    if (!valid || orientation > 3U ||
        (unsigned)hypothesis > (unsigned)XIAO147_RAW_UNKNOWN ||
        hypothesis == XIAO147_RAW_UNKNOWN ||
        time_or_pose_fault || (hypothesis != XIAO147_RAW_NONE &&
        (logical_x < 0 || logical_x > 319 || logical_y < 0 || logical_y > 319))) {
        xiao147_shadow_gesture_cancel(gesture);
        result.cancelled = true;
        /* Time/pose faults must not drop this frame and then create a click. */
        return result;
    }

    if (hypothesis == XIAO147_RAW_NONE) {
        if (gesture->release_frames == 0) {
            gesture->first_release_ms = now_ms;
        }
        if (gesture->release_frames < 2U) { gesture->release_frames++; }
        if (gesture->release_frames < 2U) { return result; }
        if (!gesture->blocked && gesture->active && gesture->qualified_two &&
            !gesture->moving && gesture->first_release_ms - gesture->started_ms <= TAP_MS &&
            (!gesture->tail_one ||
             gesture->first_release_ms - gesture->one_since_ms <= LIFT_TAIL_MS)) {
            result.candidate_right_tap = true;
        }
        gesture->blocked = false;
        gesture->active = false;
        gesture->qualified_two = false;
        gesture->moving = false;
        gesture->tail_one = false;
        gesture->two_frames = 0;
        gesture->remainder_x = gesture->remainder_y = 0;
        return result;
    }
    if (gesture->release_frames != 0 && gesture->active) {
        /* A contact reappearing before complete release is ambiguous bounce. */
        xiao147_shadow_gesture_cancel(gesture);
        result.cancelled = true;
        return result;
    }
    gesture->release_frames = 0;
    if (gesture->blocked) { return result; }
    if (!gesture->active) {
        gesture->active = true;
        gesture->started_ms = now_ms;
    }
    if (hypothesis == XIAO147_RAW_ONE_LOBE) {
        if (gesture->two_frames != 0 && !gesture->qualified_two) {
            /* The two confirmation frames must be consecutive. A one-lobe
             * interruption before qualification is uncertain contact count,
             * not a bridge joining two separate TWO hypotheses into a tap.
             */
            xiao147_shadow_gesture_cancel(gesture);
            result.cancelled = true;
            return result;
        }
        if (gesture->qualified_two && !gesture->tail_one) {
            gesture->tail_one = true;
            gesture->one_since_ms = now_ms;
        } else if (gesture->tail_one && now_ms - gesture->one_since_ms > LIFT_TAIL_MS) {
            xiao147_shadow_gesture_cancel(gesture);
            result.cancelled = true;
        }
        return result; /* Never infer a left tap from one lobe. */
    }
    if (gesture->tail_one) {
        xiao147_shadow_gesture_cancel(gesture);
        result.cancelled = true;
        return result;
    }
    if (gesture->two_frames == 0) {
        gesture->two_frames = 1;
        gesture->previous_x = gesture->start_x = logical_x;
        gesture->previous_y = gesture->start_y = logical_y;
        return result;
    }

    int dx = logical_x - gesture->previous_x;
    int dy = logical_y - gesture->previous_y;
    if (magnitude(dx) > JUMP_LIMIT || magnitude(dy) > JUMP_LIMIT) {
        xiao147_shadow_gesture_cancel(gesture);
        result.cancelled = true;
        return result;
    }
    if (magnitude(logical_x - gesture->start_x) > TAP_MOVE ||
        magnitude(logical_y - gesture->start_y) > TAP_MOVE) {
        gesture->moving = true;
    }
    gesture->previous_x = logical_x;
    gesture->previous_y = logical_y;
    if (!gesture->qualified_two) {
        gesture->two_frames = 2;
        gesture->qualified_two = true;
        return result; /* No onset jump sent as scroll. */
    }
    int rx = gesture->remainder_x + dx;
    int ry = gesture->remainder_y + dy;
    result.candidate_hwheel = scroll_candidate(&rx);
    result.candidate_wheel = scroll_candidate(&ry);
    gesture->remainder_x = (int16_t)rx;
    gesture->remainder_y = (int16_t)ry;
    if (result.candidate_hwheel != 0 || result.candidate_wheel != 0) {
        gesture->moving = true; /* Any scrolling cancels subsequent tap. */
    }
    return result;
}
