/* Experimental AXS5106L DIFF spatial hypotheses and SHADOW gesture tracking.
 * No function in this module emits input/HID events. The six adjacent pairs
 * and coordinate interpolation are hypotheses, not a documented touch map.
 * Four stationary scenes do not validate arbitrary-position finger count.
 */
#ifndef XIAO147_RAW_GESTURES_H
#define XIAO147_RAW_GESTURES_H

#include <stdbool.h>
#include <stdint.h>

#define XIAO147_PROFILE_CHANNELS 12U
#define XIAO147_PROFILE_STATIONS 6U
#define XIAO147_PROFILE_FLOOR 20
#define XIAO147_SHADOW_MAX_GAP_MS 100U

enum xiao147_raw_hypothesis {
    XIAO147_RAW_NONE,
    /* ONE_LOBE is deliberately NOT a confirmed one-finger count. */
    XIAO147_RAW_ONE_LOBE,
    XIAO147_RAW_TWO_SEPARATED_LOBES,
    XIAO147_RAW_UNKNOWN,
};

struct xiao147_raw_profile {
    enum xiao147_raw_hypothesis hypothesis;
    uint32_t stations[XIAO147_PROFILE_STATIONS];
    uint16_t valley_permille;
    uint16_t lesser_peak_permille;
    uint8_t significant_peaks;
    /* Provisional relative-motion features only. Native x=short axis,
     * y=long axis. Neither is a calibrated absolute physical coordinate.
     * Caller applies its existing native-to-logical four-pose transform.
     */
    uint16_t native_short_x;
    uint16_t native_long_y;
};

/* Complete signed-16 DIFF frame only; no RAW absolute baseline subtraction.
 * baseline may be NULL for an explicitly uncalibrated OFFLINE inspection.
 * Returns false and UNKNOWN on invalid layout, values or required pointers.
 * Experimental thresholds are morphology margins, not trained accuracy.
 */
bool xiao147_raw_profile_evaluate(const int32_t *diff, uint8_t channels,
                                 const int16_t *baseline,
                                 struct xiao147_raw_profile *profile);

struct xiao147_raw_baseline {
    int32_t sum[XIAO147_PROFILE_CHANNELS];
    int16_t values[XIAO147_PROFILE_CHANNELS];
    uint32_t first_ms;
    uint32_t last_ms;
    uint16_t quiet_frames;
    bool normal_lift_confirmed;
    bool have_time;
    bool armed;
};

/* normal_lift_confirmed means a genuine valid count-zero NORMAL report was
 * obtained immediately before the caller entered factory sampling. A held
 * contact may NEVER be treated as calibration or a learned baseline.
 */
void xiao147_raw_baseline_begin(struct xiao147_raw_baseline *baseline,
                              bool normal_lift_confirmed);
/* Arms only after >=100 ms and >=3 quiet complete DIFF frames, each channel
 * within +/-20, with gaps <=100 ms. Transport/layout errors revoke permission
 * and require begin() with a new real normal zero. Does not adapt while armed.
 */
bool xiao147_raw_baseline_observe(struct xiao147_raw_baseline *baseline,
                                const int32_t *diff, uint8_t channels,
                                bool valid, uint32_t now_ms);

struct xiao147_shadow_result {
    bool candidate_right_tap;
    bool cancelled;
    /* Candidate wheel values only. Negated finger delta is the existing
     * macOS natural-scroll convention; NEVER hand this struct to HID until
     * count, both-axis mapping and rate have independent hardware validation.
     */
    int16_t candidate_wheel;
    int16_t candidate_hwheel;
};

struct xiao147_shadow_gesture {
    uint32_t last_ms;
    uint32_t started_ms;
    uint32_t first_release_ms;
    uint32_t one_since_ms;
    int16_t previous_x;
    int16_t previous_y;
    int16_t start_x;
    int16_t start_y;
    int16_t remainder_x;
    int16_t remainder_y;
    uint8_t orientation;
    uint8_t two_frames;
    uint8_t release_frames;
    bool have_time;
    bool blocked;
    bool active;
    bool qualified_two;
    bool moving;
    bool tail_one;
};

void xiao147_shadow_gesture_init(struct xiao147_shadow_gesture *gesture);
void xiao147_shadow_gesture_cancel(struct xiao147_shadow_gesture *gesture);
/* Caller must pass UNKNOWN/valid=false for UNARMED samples, bus errors,
 * stale frames or mapping uncertainty. x/y are caller-transformed logical
 * centroid features in 0..319. UNKNOWN, >100 ms gaps, pose changes or large
 * centroid jumps cancel the WHOLE gesture until two confirmed NONE frames.
 * Two consecutive TWO hypotheses qualify a shadow candidate. An intervening
 * ONE_LOBE before qualification cancels the whole gesture until full lift.
 * A right-tap
 * candidate requires a <=240 ms stationary gesture and real full release.
 * A short <=60 ms ONE_LOBE lift tail never becomes a left click; longer or
 * rejoined tails cancel. No left-click candidate exists in this interface.
 */
struct xiao147_shadow_result xiao147_shadow_gesture_observe(
    struct xiao147_shadow_gesture *gesture,
    enum xiao147_raw_hypothesis hypothesis, bool valid,
    int16_t logical_x, int16_t logical_y, uint8_t orientation, uint32_t now_ms);

#endif
