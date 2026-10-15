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
#define XIAO147_SHADOW_PENDING_MS 100U

enum xiao147_raw_hypothesis {
    XIAO147_RAW_NONE,
    /* ONE_LOBE is deliberately NOT a confirmed one-finger count. */
    XIAO147_RAW_ONE_LOBE,
    XIAO147_RAW_TWO_SEPARATED_LOBES,
    XIAO147_RAW_UNKNOWN,
    /* Weak, simple positive onset: not a contact count, release or motion. */
    XIAO147_RAW_PENDING,
};

struct xiao147_raw_lobe {
    uint32_t amplitude;
    uint16_t native_short_x;
    uint16_t native_long_y;
    uint8_t peak_station;
    uint8_t first_station;
    uint8_t last_station;
};

struct xiao147_raw_profile {
    enum xiao147_raw_hypothesis hypothesis;
    uint32_t stations[XIAO147_PROFILE_STATIONS];
    uint16_t valley_permille;
    uint16_t lesser_peak_permille;
    uint8_t significant_peaks;
    uint8_t valley_station;
    bool partition_valid;
    /* Two significant narrow peaks, but valley too shallow for TWO. Not a
     * contact count; only permits bounded scroll requalification, never tap. */
    bool two_peak_ambiguity;
    /* Ordered by long-axis position, not relative amplitude. The weakest
     * valley station is excluded from both support regions. Each significant
     * lobe is normalised independently, then the two coordinates are averaged
     * equally: unequal contact strength must not drag the pair centroid.
     */
    struct xiao147_raw_lobe lobes[2];
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
     * macOS natural-scroll convention. Callers must not emit these hypotheses
     * in default/daily firmware: count and both-axis coordinates are not
     * independently validated. Only a separate, explicit and bounded opt-in
     * experiment may emit them while preserving its fail-closed safeguards.
     * This portable module itself never calls input/HID interfaces.
     */
    int16_t candidate_wheel;
    int16_t candidate_hwheel;
};

enum xiao147_scroll_axis {
    XIAO147_SCROLL_NONE,
    XIAO147_SCROLL_HORIZONTAL,
    XIAO147_SCROLL_VERTICAL,
    XIAO147_SCROLL_BOTH,
};

struct xiao147_shadow_gesture {
    uint32_t last_ms;
    uint32_t started_ms;
    uint32_t first_release_ms;
    uint32_t one_since_ms;
    uint32_t pending_since_ms;
    uint32_t weak_release_since_ms;
    uint32_t reacquire_since_ms;
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
    /* moving permanently invalidates this gesture's tap when either net
     * coordinate exceeds 12 from the first credible TWO anchor. A scroll
     * activates then, even if the excess is too small for an 8-pixel tick.
     */
    bool moving;
    bool scroll_activated;
    bool scroll_x_started;
    bool scroll_y_started;
    bool scroll_x_emitted;
    bool scroll_y_emitted;
    enum xiao147_scroll_axis scroll_axis;
    /* Equal pair motion can hide opposing individual-lobe movement. The full
     * profile path separately disqualifies tap if either provisional native
     * lobe moves >12 from its fixed FIRST-TWO anchor, even if it returns.
     * This is not scroll activation and does not cancel valid net scrolling.
     */
    bool tap_disqualified;
    bool tap_anchor_valid;
    uint16_t tap_short_anchor[2];
    uint16_t tap_long_anchor[2];
    bool tail_one;
    bool pending;
    bool weak_release;
    bool reacquiring;
    bool association_valid;
    uint8_t previous_valley;
    struct xiao147_raw_lobe previous_lobes[2];
};

void xiao147_shadow_gesture_init(struct xiao147_shadow_gesture *gesture);
void xiao147_shadow_gesture_cancel(struct xiao147_shadow_gesture *gesture);
/* Caller must pass UNKNOWN/valid=false for UNARMED samples, bus errors,
 * stale frames or mapping uncertainty. x/y are caller-transformed logical
 * centroid features in 0..319. UNKNOWN, >100 ms gaps, pose changes or large
 * centroid jumps cancel the WHOLE gesture until two confirmed NONE frames.
 * Two consecutive TWO hypotheses qualify a shadow candidate. An intervening
 * ONE_LOBE before qualification cancels the whole gesture until full lift.
 * PENDING is allowed before any trusted contact, after two real NONE
 * frames already unblocked the tracker. It emits nothing, starts no tap clock
 * and is bounded to 100 ms. After qualified TWO it may wait up to 60 ms for
 * two NONE frames: no motion/release is inferred from PENDING, no tap clock
 * extension, and any renewed contact cancels. Unqualified contact cancels.
 * A right-tap candidate requires a <=240 ms stationary gesture and full lift.
 * Net displacement <=12 cannot accumulate scroll jitter. On first >12,
 * scroll activates permanently and only the excess beyond 12 is consumed.
 * A >=2:1 dominant axis locks H/V for this gesture; otherwise BOTH is fixed.
 * Before an axis emits its first tick, its remainder is recomputed from net
 * excess so a threshold excursion returning to origin cannot scroll backward.
 * After that first tick, incremental motion follows normally on that axis.
 * BOTH applies this independently to each axis.
 * Suppressed axes never retain hidden remainder; lift is required to reselect.
 * A short <=60 ms ONE_LOBE lift tail never becomes a left click; longer or
 * rejoined tails cancel. No left-click candidate exists in this interface.
 */
struct xiao147_shadow_result xiao147_shadow_gesture_observe(
    struct xiao147_shadow_gesture *gesture,
    enum xiao147_raw_hypothesis hypothesis, bool valid,
    int16_t logical_x, int16_t logical_y, uint8_t orientation, uint32_t now_ms);

/* Production shadow wrapper must use the full-profile API. It validates
 * disjoint lobe supports, ordered/strong peaks, valley geometry and bounded
 * per-frame association changes before the legacy shadow transition logic.
 * Invalid or abruptly swapped partitions cancel the whole gesture. A valid
 * two-peak shallow-valley ambiguity may restart scroll qualification within
 * 100 ms, discarding old motion/association and permanently forbidding tap
 * until full lift. Only motion between new consecutive trusted TWO counts.
 * Each first-TWO lobe coordinate is also a fixed tap-only anchor. A >12
 * cumulative native offset on either lobe permanently disqualifies tap,
 * without changing the net-motion scroll/axis policy. Native units are not
 * physically calibrated; pressure-shape variation may conservatively miss taps.
 * logical_x/y are the caller's four-pose transform of the equal-weight pair.
 * The old observe() remains for isolated synthetic state/sign tests only.
 */
struct xiao147_shadow_result xiao147_shadow_gesture_observe_profile(
    struct xiao147_shadow_gesture *gesture,
    const struct xiao147_raw_profile *profile, bool valid,
    int16_t logical_x, int16_t logical_y, uint8_t orientation, uint32_t now_ms);

#endif
