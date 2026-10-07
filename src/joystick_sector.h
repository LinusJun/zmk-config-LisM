/* SPDX-License-Identifier: MIT */
#ifndef LISM_JOYSTICK_SECTOR_H
#define LISM_JOYSTICK_SECTOR_H

#include <stdint.h>
#include <stdbool.h>

/* Matches kscan columns: W, S, A, D. No push input. */
#define JOY_MASK_UP (1u << 0)
#define JOY_MASK_DOWN (1u << 1)
#define JOY_MASK_LEFT (1u << 2)
#define JOY_MASK_RIGHT (1u << 3)

/* Sprint is a held modifier, separate from the four held direction keys.
 * The configured radius is an ADC travel estimate, not learned from a held
 * stick or a manual recenter. Only forward directions may sprint.
 */
static inline bool joystick_sprint(int32_t x, int32_t y, uint8_t directions,
                                   bool previous, uint16_t full_radius,
                                   uint8_t enter_percent, uint8_t exit_percent) {
    if (!full_radius || !(directions & JOY_MASK_UP) ||
        (directions & JOY_MASK_DOWN)) return false;
    int64_t radius100_squared = ((int64_t)x * x + (int64_t)y * y) * 10000;
    int64_t threshold = (int64_t)full_radius *
        (previous ? exit_percent : enter_percent);
    return radius100_squared > threshold * threshold;
}

/* Equal 45-degree sectors in ADC coordinates; no angular hysteresis.
 * tan(22.5deg) ~= 414214/1000000. Boundary ties belong to the cardinal.
 * Retain radial enter/release hysteresis; center release has precedence.
 * ADC axes are not endpoint-normalized.
 */
static inline uint8_t joystick_sector8_strict(int32_t x, int32_t y, uint8_t previous,
                                             int32_t activation, int32_t release) {
    int64_t ax = x < 0 ? -(int64_t)x : x;
    int64_t ay = y < 0 ? -(int64_t)y : y;
    int64_t threshold = previous ? release : activation;
    if (ax * ax + ay * ay <= threshold * threshold) return 0;
    uint8_t horizontal = x < 0 ? JOY_MASK_LEFT : JOY_MASK_RIGHT;
    uint8_t vertical = y < 0 ? JOY_MASK_UP : JOY_MASK_DOWN;
    if (ay * 1000000 <= ax * 414214) return horizontal;
    if (ax * 1000000 <= ay * 414214) return vertical;
    return horizontal | vertical;
}

/* Configurable hold band around nominal 45-degree sectors. Explicit zero
 * uses strict boundaries; 1..5 extends the prior sector by this many degrees.
 * Raw ADC magnitude and radial thresholds are unchanged. Only valid directions
 * in the current quadrant are held. Center release always takes precedence.
 */
static inline uint8_t joystick_sector8_hysteresis(int32_t x, int32_t y, uint8_t previous,
                                                int32_t activation, int32_t release,
                                                uint8_t degrees) {
    static const int32_t upper_tan[] = {414214, 434812, 455726, 476976, 498582, 520567};
    static const int32_t lower_tan[] = {414214, 393910, 373885, 354119, 334595, 315299};
    if (degrees == 0 || degrees > 5)
        return joystick_sector8_strict(x, y, previous, activation, release);
    int64_t ax = x < 0 ? -(int64_t)x : x;
    int64_t ay = y < 0 ? -(int64_t)y : y;
    int64_t threshold = previous ? release : activation;
    if (ax * ax + ay * ay <= threshold * threshold) return 0;
    uint8_t horizontal = x < 0 ? JOY_MASK_LEFT : JOY_MASK_RIGHT;
    uint8_t vertical = y < 0 ? JOY_MASK_UP : JOY_MASK_DOWN;
    if (previous == horizontal && ax > 0 && ay * 1000000 <= ax * upper_tan[degrees])
        return previous;
    if (previous == vertical && ay > 0 && ax * 1000000 <= ay * upper_tan[degrees])
        return previous;
    int64_t low = ax < ay ? ax : ay;
    int64_t high = ax > ay ? ax : ay;
    if (previous == (horizontal | vertical) && low * 1000000 >= high * lower_tan[degrees])
        return previous;
    return joystick_sector8_strict(x, y, previous, activation, release);
}

/* Eight equal 45-degree sectors. Hold the previous sector up to 5 degrees
 * beyond its boundary: tan(22.5)=.414, tan(27.5)=.521, tan(17.5)=.315.
 * Radial hysteresis takes precedence over angular hysteresis, so returning
 * to center always releases all keys. Raw ADC axes are not endpoint-normalized.
 */
static inline uint8_t joystick_sector8(int32_t x, int32_t y, uint8_t previous,
                                      int32_t activation, int32_t release) {
    int64_t ax = x < 0 ? -(int64_t)x : x;
    int64_t ay = y < 0 ? -(int64_t)y : y;
    int64_t threshold = previous ? release : activation;
    if (ax * ax + ay * ay <= threshold * threshold) {
        return 0;
    }

    uint8_t horizontal = x < 0 ? JOY_MASK_LEFT : JOY_MASK_RIGHT;
    uint8_t vertical = y < 0 ? JOY_MASK_UP : JOY_MASK_DOWN;
    if (previous == horizontal && ax > 0 && ay * 1000 <= ax * 521) {
        return previous;
    }
    if (previous == vertical && ay > 0 && ax * 1000 <= ay * 521) {
        return previous;
    }
    int64_t low = ax < ay ? ax : ay;
    int64_t high = ax > ay ? ax : ay;
    if (previous == (horizontal | vertical) && low * 1000 >= high * 315) {
        return previous;
    }
    if (ay * 1000 <= ax * 414) {
        return horizontal;
    }
    if (ax * 1000 <= ay * 414) {
        return vertical;
    }
    return horizontal | vertical;
}

#endif
