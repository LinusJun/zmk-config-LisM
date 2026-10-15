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

static bool weak_simple_profile(const uint32_t *stations)
{
    unsigned peaks = 0;
    for (unsigned left = 0; left < XIAO147_PROFILE_STATIONS;) {
        unsigned right = left;
        uint32_t height = stations[left];
        while (right + 1U < XIAO147_PROFILE_STATIONS &&
               stations[right + 1U] == height) {
            right++;
        }
        bool above_left = left == 0 || height > stations[left - 1U];
        bool above_right = right + 1U == XIAO147_PROFILE_STATIONS ||
                           height > stations[right + 1U];
        if (height > 0 && above_left && above_right) {
            if (right - left + 1U > 3U || ++peaks > 2U) { return false; }
        }
        left = right + 1U;
    }
    return peaks > 0;
}

static bool calculate_lobe(const uint32_t *positive,
                           const uint32_t *stations,
                           uint8_t first, uint8_t last, uint8_t peak,
                           struct xiao147_raw_lobe *lobe)
{
    if (first > last || last >= XIAO147_PROFILE_STATIONS || peak < first ||
        peak > last || stations[peak] < PROFILE_MIN_PEAK) {
        return false; /* Never normalise an unqualified weak peak. */
    }
    uint64_t amplitude = 0, weighted = 0, odd_column = 0;
    for (unsigned i = first; i <= last; i++) {
        amplitude += stations[i];
        weighted += (uint64_t)stations[i] * i;
        odd_column += positive[2U * i + 1U];
    }
    if (amplitude < PROFILE_MIN_PEAK || amplitude > UINT32_MAX) { return false; }
    lobe->amplitude = (uint32_t)amplitude;
    lobe->native_long_y = (uint16_t)((weighted * 319U) / (amplitude * 5U));
    lobe->native_short_x = (uint16_t)((odd_column * 171U) / amplitude);
    lobe->peak_station = peak;
    lobe->first_station = first;
    lobe->last_station = last;
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

    uint32_t *positive = profile->positive;
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
        if (weak_simple_profile(profile->stations)) {
            profile->hypothesis = XIAO147_RAW_PENDING;
        }
        return true; /* Not NONE; no normalised coordinates for weak onset. */
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
    unsigned valley_first = peaks[0] + 1U, valley_last = valley_first;
    for (unsigned i = peaks[0] + 1U; i < peaks[1]; i++) {
        if (profile->stations[i] < valley) {
            valley = profile->stations[i];
            valley_first = valley_last = i;
        } else if (profile->stations[i] == valley) {
            valley_last = i;
        }
    }
    profile->valley_permille = (uint16_t)(((uint64_t)valley * 1000U) / lesser);
    profile->lesser_peak_permille = (uint16_t)(((uint64_t)lesser * 1000U) / greater);
    profile->two_peak_ambiguity =
        (uint64_t)valley * 100U > (uint64_t)lesser * PROFILE_VALLEY_PERCENT;
    if ((uint64_t)valley * 100U <= (uint64_t)lesser * PROFILE_VALLEY_PERCENT) {
        /* Select the middle of an equally deep valley plateau. Split supports
         * stay ordered by physical long axis, never by relative peak strength.
         */
        unsigned boundary = (valley_first + valley_last) / 2U;
        if (profile->stations[boundary] != valley || boundary == 0 ||
            boundary + 1U >= XIAO147_PROFILE_STATIONS) { return true; }
        profile->valley_station = (uint8_t)boundary;
        if (!calculate_lobe(positive, profile->stations, 0, (uint8_t)(boundary - 1U),
                            peaks[0], &profile->lobes[0]) ||
            !calculate_lobe(positive, profile->stations, (uint8_t)(boundary + 1U),
                            XIAO147_PROFILE_STATIONS - 1U, peaks[1], &profile->lobes[1])) {
            return true;
        }
        profile->partition_valid = true;
        profile->native_long_y = (uint16_t)((profile->lobes[0].native_long_y +
                                            profile->lobes[1].native_long_y) / 2U);
        profile->native_short_x = (uint16_t)((profile->lobes[0].native_short_x +
                                             profile->lobes[1].native_short_x) / 2U);
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
        gesture->scroll_activated = false;
        gesture->scroll_x_started = false;
        gesture->scroll_y_started = false;
        gesture->scroll_x_emitted = false;
        gesture->scroll_y_emitted = false;
        gesture->scroll_axis = XIAO147_SCROLL_NONE;
        gesture->tap_disqualified = false;
        gesture->tap_anchor_valid = false;
        gesture->tail_one = false;
        gesture->fading_tail = false;
        gesture->pending = false;
        gesture->weak_release = false;
        gesture->reacquiring = false;
        gesture->association_valid = false;
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

static int deadzone_excess(int net)
{
    if (net > TAP_MOVE) { return net - TAP_MOVE; }
    if (net < -TAP_MOVE) { return net + TAP_MOVE; }
    return 0;
}

static int activated_axis_remainder(bool *started, bool emitted,
                                    int net, int delta, int remainder)
{
    if (magnitude(net) > TAP_MOVE) { *started = true; }
    /* Until an actual tick, retain net excess, not an offset path that could
     * turn a 0 -> 13 -> 0 excursion into an unintended reverse tick. */
    return emitted ? remainder + delta : deadzone_excess(net);
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
        (unsigned)hypothesis > (unsigned)XIAO147_RAW_PENDING ||
        hypothesis == XIAO147_RAW_UNKNOWN ||
        time_or_pose_fault || (hypothesis != XIAO147_RAW_NONE &&
        hypothesis != XIAO147_RAW_PENDING &&
        (logical_x < 0 || logical_x > 319 || logical_y < 0 || logical_y > 319))) {
        xiao147_shadow_gesture_cancel(gesture);
        result.cancelled = true;
        /* Time/pose faults must not drop this frame and then create a click. */
        return result;
    }

    if (gesture->weak_release &&
        (now_ms - gesture->weak_release_since_ms > LIFT_TAIL_MS ||
         (hypothesis != XIAO147_RAW_PENDING && hypothesis != XIAO147_RAW_NONE))) {
        xiao147_shadow_gesture_cancel(gesture);
        result.cancelled = true;
        return result;
    }

    if (gesture->pending && now_ms - gesture->pending_since_ms >
        XIAO147_SHADOW_PENDING_MS) {
        xiao147_shadow_gesture_cancel(gesture);
        result.cancelled = true;
        return result;
    }
    if (hypothesis == XIAO147_RAW_PENDING) {
        if (!gesture->blocked && gesture->qualified_two && gesture->active &&
            gesture->release_frames == 0) {
            /* Weak signal is not release, and its coordinates are undefined.
             * Wait briefly for two real NONEs without advancing motion. */
            if (!gesture->weak_release) {
                gesture->weak_release = true;
                gesture->weak_release_since_ms = now_ms;
            }
            return result;
        }
        if (gesture->blocked || gesture->active || gesture->two_frames != 0) {
            xiao147_shadow_gesture_cancel(gesture);
            result.cancelled = true;
            return result;
        }
        if (!gesture->pending) {
            gesture->pending = true;
            gesture->pending_since_ms = now_ms;
        }
        gesture->release_frames = 0;
        return result; /* No contact count, release, tap clock or motion. */
    }

    if (hypothesis == XIAO147_RAW_NONE) {
        if (gesture->release_frames == 0) {
            gesture->first_release_ms = now_ms;
        }
        if (gesture->release_frames < 2U) { gesture->release_frames++; }
        if (gesture->release_frames < 2U) { return result; }
        if (!gesture->blocked && gesture->active && gesture->qualified_two &&
            !gesture->moving && !gesture->tap_disqualified &&
            gesture->first_release_ms - gesture->started_ms <= TAP_MS &&
            (!gesture->tail_one ||
             gesture->first_release_ms - gesture->one_since_ms <= LIFT_TAIL_MS)) {
            result.candidate_right_tap = true;
        }
        gesture->blocked = false;
        gesture->active = false;
        gesture->qualified_two = false;
        gesture->moving = false;
        gesture->scroll_activated = false;
        gesture->scroll_x_started = false;
        gesture->scroll_y_started = false;
        gesture->scroll_x_emitted = false;
        gesture->scroll_y_emitted = false;
        gesture->scroll_axis = XIAO147_SCROLL_NONE;
        gesture->tap_disqualified = false;
        gesture->tap_anchor_valid = false;
        gesture->tail_one = false;
        gesture->fading_tail = false;
        gesture->pending = false;
        gesture->weak_release = false;
        gesture->reacquiring = false;
        gesture->association_valid = false;
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
    gesture->pending = false;
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
    int net_x = logical_x - gesture->start_x;
    int net_y = logical_y - gesture->start_y;
    gesture->previous_x = logical_x;
    gesture->previous_y = logical_y;
    if (!gesture->qualified_two) {
        gesture->two_frames = 2;
        gesture->qualified_two = true;
    }

    if (!gesture->scroll_activated) {
        int nx = magnitude(net_x), ny = magnitude(net_y);
        if (nx <= TAP_MOVE && ny <= TAP_MOVE) {
            gesture->remainder_x = gesture->remainder_y = 0;
            return result; /* Net intent, not accumulated path/jitter length. */
        }
        gesture->moving = true; /* Permanent tap cancellation, even no tick. */
        gesture->scroll_activated = true;
        if (nx > TAP_MOVE && nx >= 2 * ny) {
            gesture->scroll_axis = XIAO147_SCROLL_HORIZONTAL;
        } else if (ny > TAP_MOVE && ny >= 2 * nx) {
            gesture->scroll_axis = XIAO147_SCROLL_VERTICAL;
        } else {
            gesture->scroll_axis = XIAO147_SCROLL_BOTH;
        }
    }
    bool allow_x = gesture->scroll_axis == XIAO147_SCROLL_HORIZONTAL ||
                   gesture->scroll_axis == XIAO147_SCROLL_BOTH;
    bool allow_y = gesture->scroll_axis == XIAO147_SCROLL_VERTICAL ||
                   gesture->scroll_axis == XIAO147_SCROLL_BOTH;
    int rx = 0, ry = 0;
    if (allow_x) {
        rx = activated_axis_remainder(&gesture->scroll_x_started, gesture->scroll_x_emitted,
                                      net_x, dx, gesture->remainder_x);
    } else {
        gesture->scroll_x_started = false;
    }
    if (allow_y) {
        ry = activated_axis_remainder(&gesture->scroll_y_started, gesture->scroll_y_emitted,
                                      net_y, dy, gesture->remainder_y);
    } else {
        gesture->scroll_y_started = false;
    }
    result.candidate_hwheel = scroll_candidate(&rx);
    result.candidate_wheel = scroll_candidate(&ry);
    if (result.candidate_hwheel != 0) { gesture->scroll_x_emitted = true; }
    if (result.candidate_wheel != 0) { gesture->scroll_y_emitted = true; }
    gesture->remainder_x = (int16_t)rx;
    gesture->remainder_y = (int16_t)ry;
    return result;
}

static bool partition_valid(const struct xiao147_raw_profile *profile)
{
    if (!profile->partition_valid || profile->significant_peaks != 2 ||
        profile->valley_station == 0 ||
        profile->valley_station + 1U >= XIAO147_PROFILE_STATIONS ||
        profile->valley_permille > 400 || profile->lesser_peak_permille < 350) {
        return false;
    }
    for (unsigned i = 0; i < 2; i++) {
        const struct xiao147_raw_lobe *lobe = &profile->lobes[i];
        if (lobe->first_station > lobe->last_station ||
            lobe->last_station >= XIAO147_PROFILE_STATIONS ||
            lobe->peak_station < lobe->first_station ||
            lobe->peak_station > lobe->last_station ||
            profile->stations[lobe->peak_station] < PROFILE_MIN_PEAK ||
            lobe->amplitude < PROFILE_MIN_PEAK || lobe->native_short_x > 171 ||
            lobe->native_long_y > 319) { return false; }
        uint64_t sum = 0;
        for (unsigned station = lobe->first_station; station <= lobe->last_station; station++) {
            sum += profile->stations[station];
        }
        if (sum != lobe->amplitude) { return false; }
    }
    return profile->lobes[0].first_station == 0 &&
           profile->lobes[0].last_station + 1U == profile->valley_station &&
           profile->lobes[1].first_station == profile->valley_station + 1U &&
           profile->lobes[1].last_station == XIAO147_PROFILE_STATIONS - 1U &&
           profile->lobes[0].peak_station < profile->valley_station &&
           profile->lobes[1].peak_station > profile->valley_station &&
           profile->lobes[0].native_long_y < profile->lobes[1].native_long_y;
}

static bool stationary_fading_tail(const struct xiao147_shadow_gesture *gesture,
                                   const struct xiao147_raw_profile *profile)
{
    if (!gesture->association_valid || gesture->moving || gesture->tap_disqualified ||
        gesture->reacquiring || gesture->weak_release ||
        (gesture->tail_one && !gesture->fading_tail)) { return false; }
    /* Rescue may begin only on the FIRST ONE after TWO. A preceding normal
     * ONE has no stored envelope, so never compare a later rebound to an old
     * larger TWO response and mistake it for monotonically fading contact. */
    uint64_t total = 0, previous = 0;
    for (unsigned i = 0; i < XIAO147_PROFILE_CHANNELS; i++) {
        /* No new/growing electrode response: a merge or renewed contact is
         * not rescued as release. All comparisons use floor-subtracted data. */
        if (profile->positive[i] > gesture->previous_positive[i]) { return false; }
        total += profile->positive[i];
        previous += gesture->previous_positive[i];
    }
    if (previous == 0 || total == 0 ||
        (!gesture->fading_tail && total * 2U > previous)) { return false; }
    unsigned credible = 0;
    for (unsigned i = 0; i < 2; i++) {
        const struct xiao147_raw_lobe *old = &gesture->previous_lobes[i];
        uint8_t peak = old->first_station;
        uint32_t amplitude = 0;
        for (unsigned j = old->first_station; j <= old->last_station; j++) {
            amplitude += profile->stations[j];
            if (profile->stations[j] > profile->stations[peak]) { peak = (uint8_t)j; }
        }
        if (amplitude < PROFILE_MIN_PEAK) { continue; } /* No weak normalisation. */
        struct xiao147_raw_lobe tail;
        if (!calculate_lobe(profile->positive, profile->stations, old->first_station,
                            old->last_station, peak, &tail) ||
            peak != old->peak_station ||
            magnitude((int)tail.native_short_x - gesture->tap_short_anchor[i]) > TAP_MOVE ||
            magnitude((int)tail.native_long_y - gesture->tap_long_anchor[i]) > TAP_MOVE) {
            return false;
        }
        credible++;
    }
    return credible > 0;
}

struct xiao147_shadow_result xiao147_shadow_gesture_observe_profile(
    struct xiao147_shadow_gesture *gesture,
    const struct xiao147_raw_profile *profile, bool valid,
    int16_t logical_x, int16_t logical_y, uint8_t orientation, uint32_t now_ms)
{
    enum xiao147_raw_hypothesis hypothesis = profile == NULL ?
        XIAO147_RAW_UNKNOWN : profile->hypothesis;
    bool associated = true;
    if (gesture != NULL) {
        bool timely = !gesture->have_time ||
            (now_ms != gesture->last_ms &&
             now_ms - gesture->last_ms <= XIAO147_SHADOW_MAX_GAP_MS &&
             orientation == gesture->orientation);
        bool in_window = !gesture->reacquiring ||
            now_ms - gesture->reacquire_since_ms <= XIAO147_SHADOW_PENDING_MS;
        if (valid && profile != NULL && timely && orientation <= 3U &&
            in_window && !gesture->blocked && !gesture->weak_release &&
            !gesture->tail_one && gesture->release_frames == 0 &&
            hypothesis == XIAO147_RAW_UNKNOWN && profile->two_peak_ambiguity) {
            uint32_t since = gesture->reacquiring ? gesture->reacquire_since_ms : now_ms;
            /* Not a TWO frame. Forget the entire motion segment; do not
             * bridge unknown coordinates, preserve a click, or reset timeout. */
            xiao147_shadow_gesture_cancel(gesture);
            gesture->blocked = false;
            gesture->reacquiring = true;
            gesture->reacquire_since_ms = since;
            gesture->tap_disqualified = true;
            gesture->last_ms = now_ms;
            gesture->have_time = true;
            gesture->orientation = orientation;
            return (struct xiao147_shadow_result){.cancelled = true};
        }
        if (gesture->reacquiring && (!in_window ||
            (hypothesis != XIAO147_RAW_TWO_SEPARATED_LOBES &&
             hypothesis != XIAO147_RAW_NONE))) {
            xiao147_shadow_gesture_cancel(gesture);
            return (struct xiao147_shadow_result){.cancelled = true};
        }
    }
    if (valid && hypothesis == XIAO147_RAW_ONE_LOBE && gesture != NULL &&
        !gesture->blocked && gesture->qualified_two && gesture->tap_anchor_valid) {
        /* A disappearing lobe can move the pair mean, so never scroll here.
         * However, ignoring the tail altogether would allow a displaced or
         * merged lobe to end in a right click. Require proximity to either
         * original lobe for tap eligibility; ONE still does NOT prove one
         * finger and this check does not establish physical calibration.
         */
        bool near_anchor = false;
        for (unsigned i = 0; i < 2; i++) {
            if (magnitude((int)profile->native_short_x - gesture->tap_short_anchor[i]) <= TAP_MOVE &&
                magnitude((int)profile->native_long_y - gesture->tap_long_anchor[i]) <= TAP_MOVE) {
                near_anchor = true;
            }
        }
        if (!near_anchor || gesture->fading_tail) {
            if (stationary_fading_tail(gesture, profile)) {
                gesture->fading_tail = true;
                memcpy(gesture->previous_positive, profile->positive,
                       sizeof(gesture->previous_positive));
            } else {
                gesture->tap_disqualified = true;
            }
        }
    }
    if (valid && hypothesis == XIAO147_RAW_TWO_SEPARATED_LOBES) {
        associated = partition_valid(profile);
        if (associated && gesture != NULL && gesture->association_valid) {
            if (magnitude((int)profile->valley_station - gesture->previous_valley) > 1) {
                associated = false;
            }
            for (unsigned i = 0; associated && i < 2; i++) {
                const struct xiao147_raw_lobe *lobe = &profile->lobes[i];
                const struct xiao147_raw_lobe *old = &gesture->previous_lobes[i];
                if (magnitude((int)lobe->peak_station - old->peak_station) > 1 ||
                    magnitude((int)lobe->native_short_x - old->native_short_x) > JUMP_LIMIT ||
                    magnitude((int)lobe->native_long_y - old->native_long_y) > JUMP_LIMIT) {
                    associated = false;
                }
            }
        }
        if (associated && gesture != NULL && !gesture->blocked &&
            gesture->tap_anchor_valid) {
            for (unsigned i = 0; i < 2; i++) {
                const struct xiao147_raw_lobe *lobe = &profile->lobes[i];
                if (magnitude((int)lobe->native_short_x - gesture->tap_short_anchor[i]) > TAP_MOVE ||
                    magnitude((int)lobe->native_long_y - gesture->tap_long_anchor[i]) > TAP_MOVE) {
                    /* Tap-only, cumulative guard: opposing lobe motion must
                     * not disappear in the equal pair mean. Do not change
                     * scroll_activated/moving or suppress valid net scrolling.
                     */
                    gesture->tap_disqualified = true;
                }
            }
        }
    }
    struct xiao147_shadow_result result = xiao147_shadow_gesture_observe(
        gesture, associated ? hypothesis : XIAO147_RAW_UNKNOWN,
        valid && profile != NULL && associated, logical_x, logical_y, orientation, now_ms);
    if (gesture != NULL && gesture->qualified_two) { gesture->reacquiring = false; }
    if (gesture != NULL && !gesture->blocked &&
        hypothesis == XIAO147_RAW_TWO_SEPARATED_LOBES && associated &&
        gesture->two_frames != 0) {
        gesture->association_valid = true;
        gesture->previous_valley = profile->valley_station;
        gesture->previous_lobes[0] = profile->lobes[0];
        gesture->previous_lobes[1] = profile->lobes[1];
        memcpy(gesture->previous_positive, profile->positive,
               sizeof(gesture->previous_positive));
        if (!gesture->tap_anchor_valid) {
            gesture->tap_anchor_valid = true;
            for (unsigned i = 0; i < 2; i++) {
                gesture->tap_short_anchor[i] = profile->lobes[i].native_short_x;
                gesture->tap_long_anchor[i] = profile->lobes[i].native_long_y;
            }
        }
    }
    return result;
}
