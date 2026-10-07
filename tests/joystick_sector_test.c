/* SPDX-License-Identifier: MIT */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include "../src/joystick_sector.h"

static uint8_t sector(int x, int y, uint8_t previous) {
    return joystick_sector8(x, y, previous, 90, 55);
}

/* Use properties extracted from the actual CirclePad overlay by CI. */
static uint8_t landscape(int raw_x, int raw_y, uint8_t previous) {
    int dx = raw_x, dy = raw_y;
    if (CIRCLE_SWAP_AXES) { int tmp = dx; dx = dy; dy = tmp; }
    if (CIRCLE_INVERT_X) dx = -dx;
    if (CIRCLE_INVERT_Y) dy = -dy;
    assert(CIRCLE_STRICT);
    return joystick_sector8_strict(dx, dy, previous, CIRCLE_ACTIVATION, CIRCLE_RELEASE);
}

int main(void) {
    /* Physical raw vectors labelled by the previous working orientation.
     * W,A,S,D,WA,AS,SD,DW must become D,W,A,S,WD,WA,AS,SD. */
    const int old_raw_x[] = {0, 1300, 0, -1300, 1300, 1300, -1300, -1300};
    const int old_raw_y[] = {-1300, 0, 1300, 0, -1300, 1300, 1300, -1300};
    const uint8_t new_masks[] = {8, 1, 4, 2, 9, 5, 6, 10};
    assert(CIRCLE_ACTIVATION == 300 && CIRCLE_RELEASE == 240);
    for (int i = 0; i < 8; ++i) {
        assert(landscape(old_raw_x[i], old_raw_y[i], 0) == new_masks[i]);
        assert(landscape(0, 0, new_masks[i]) == 0);
        assert(landscape(150, -150, new_masks[i]) == 0);
    }
    const uint8_t directions[] = {8, 10, 2, 6, 4, 5, 1, 9};
    const int vx[] = {400, 400, 0, -400, -400, -400, 0, 400};
    const int vy[] = {0, 400, 400, 400, 0, -400, -400, -400};
    for (int i = 0; i < 8; i++) {
        assert(sector(vx[i], vy[i], 0) == directions[i]);
        assert(sector(0, 0, directions[i]) == 0);
        assert(sector(vx[(i + 4) % 8], vy[(i + 4) % 8], directions[i]) ==
               directions[(i + 4) % 8]);
    }

    /* Small cross-axis offset must not turn a straight stroke into a diagonal. */
    assert(sector(400, 100, 0) == JOY_MASK_RIGHT);
    assert(sector(100, -400, 0) == JOY_MASK_UP);
    /* Enter/exit hysteresis and a genuinely radial diagonal dead zone. */
    assert(sector(90, 0, 0) == 0);
    assert(sector(91, 0, 0) == JOY_MASK_RIGHT);
    assert(sector(60, 0, JOY_MASK_RIGHT) == JOY_MASK_RIGHT);
    assert(sector(55, 0, JOY_MASK_RIGHT) == 0);
    assert(sector(70, 70, 0) == (JOY_MASK_RIGHT | JOY_MASK_DOWN));
    /* Same angle on either side of the boundary retains the prior sector. */
    assert(sector(1000, 450, 0) == (JOY_MASK_RIGHT | JOY_MASK_DOWN));
    assert(sector(1000, 450, JOY_MASK_RIGHT) == JOY_MASK_RIGHT);
    assert(sector(1000, 600, JOY_MASK_RIGHT) == (JOY_MASK_RIGHT | JOY_MASK_DOWN));
    assert(sector(1000, 350, JOY_MASK_RIGHT | JOY_MASK_DOWN) ==
           (JOY_MASK_RIGHT | JOY_MASK_DOWN));
    assert(sector(1000, 250, JOY_MASK_RIGHT | JOY_MASK_DOWN) == JOY_MASK_RIGHT);

    /* At every angle, no impossible/opposing key pair; magnitude-independent sectors. */
    for (int degree = 0; degree < 360; degree++) {
        double angle = degree * 3.14159265358979323846 / 180.0;
        int x = (int)lround(1000 * cos(angle));
        int y = (int)lround(1000 * sin(angle));
        uint8_t fresh = sector(x, y, 0);
        for (int p = 0; p < 8; p++) {
            uint8_t mask = sector(x, y, directions[p]);
            assert(mask != 0);
            assert((mask & 3) != 3 && (mask & 12) != 12);
            assert((mask & ~15) == 0);
            assert(sector(0, 0, mask) == 0);
        }
        assert(sector(3 * x, 3 * y, 0) == fresh);
    }

    /* Confirm the intended user orientation after flipping X only:
     * raw right used to mean A, raw up already means W. */
    assert(sector(-(-400), 0, 0) == JOY_MASK_RIGHT);
    assert(sector(-(400), 0, 0) == JOY_MASK_LEFT);
    assert(sector(-0, -400, 0) == JOY_MASK_UP);
    assert(sector(-0, 400, 0) == JOY_MASK_DOWN);
    /* CirclePad return-offset trial: 300 enter / 240 release.  Every old
     * cardinal/diagonal state must clear in all four neutral quadrants.
     * These observed residuals are test cases, not a proven hardware bound.
     */
    for (int p = 0; p < 8; p++) {
        for (int sx = -1; sx <= 1; sx += 2) {
            for (int sy = -1; sy <= 1; sy += 2) {
                assert(joystick_sector8(sx * 150, sy * 150, directions[p], 300, 240) == 0);
                assert(joystick_sector8(sx * 160, sy * 160, directions[p], 300, 240) == 0);
                assert(joystick_sector8(sx * 160, sy * 160, 0, 300, 240) == 0);
            }
        }
        assert(joystick_sector8(vx[p], vy[p], 0, 300, 240) == directions[p]);
        assert(joystick_sector8(vx[(p + 4) % 8], vy[(p + 4) % 8], directions[p], 300, 240) == directions[(p + 4) % 8]);
    }
    assert(joystick_sector8(300, 0, 0, 300, 240) == 0);
    assert(joystick_sector8(301, 0, 0, 300, 240) == JOY_MASK_RIGHT);
    assert(joystick_sector8(240, 0, JOY_MASK_RIGHT, 300, 240) == 0);
    assert(joystick_sector8(241, 0, JOY_MASK_RIGHT, 300, 240) == JOY_MASK_RIGHT);
    assert(joystick_sector8(280, 0, 0, 300, 240) == 0);
    for (int degree = 0; degree < 360; degree++) {
        double angle = degree * 3.14159265358979323846 / 180.0;
        int x = (int)lround(1300 * cos(angle));
        int y = (int)lround(1300 * sin(angle));
        assert(joystick_sector8(x, y, 0, 300, 240) == sector(x, y, 0));
    }
    /* Strict mode: compare to an independent atan2 reference at every degree
     * and on both sides of all eight boundaries, for every prior state. */
    for (int degree = 0; degree < 360; ++degree) {
        double angle = degree * 3.14159265358979323846 / 180.0;
        int x = (int)lround(1000000 * cos(angle));
        int y = (int)lround(1000000 * sin(angle));
        int reference = ((int)floor((atan2(y, x) * 180.0 / 3.14159265358979323846 + 382.5) / 45.0)) % 8;
        for (int p = -1; p < 8; ++p) {
            uint8_t prior = p < 0 ? 0 : directions[p];
            assert(joystick_sector8_strict(x, y, prior, 300, 240) == directions[reference]);
            assert(joystick_sector8_strict(0, 0, prior, 300, 240) == 0);
        }
    }
    for (int boundary = 0; boundary < 8; ++boundary) {
        for (int side = -1; side <= 1; side += 2) {
            double angle = (boundary * 45.0 + 22.5 + side * 0.1) * 3.14159265358979323846 / 180.0;
            int x = (int)lround(1000000 * cos(angle));
            int y = (int)lround(1000000 * sin(angle));
            uint8_t expected = directions[(boundary + (side > 0)) % 8];
            for (int p = 0; p < 8; ++p)
                assert(joystick_sector8_strict(x, y, directions[p], 300, 240) == expected);
        }
    }
    for (int sx = -1; sx <= 1; sx += 2) {
        for (int sy = -1; sy <= 1; sy += 2) {
            for (int p = 0; p < 8; ++p)
                assert(joystick_sector8_strict(sx * 160, sy * 160, directions[p], 300, 240) == 0);
            assert(joystick_sector8_strict(sx * 1000000, sy * 414214, 0, 300, 240) == (sx < 0 ? JOY_MASK_LEFT : JOY_MASK_RIGHT));
            assert(joystick_sector8_strict(sx * 414214, sy * 1000000, 0, 300, 240) == (sy < 0 ? JOY_MASK_UP : JOY_MASK_DOWN));
        }
    }
    assert(joystick_sector8_strict(300, 0, 0, 300, 240) == 0);
    assert(joystick_sector8_strict(301, 0, 0, 300, 240) == JOY_MASK_RIGHT);
    assert(joystick_sector8_strict(240, 0, JOY_MASK_RIGHT, 300, 240) == 0);
    assert(joystick_sector8_strict(241, 0, JOY_MASK_RIGHT, 300, 240) == JOY_MASK_RIGHT);
    puts("PASS: legacy sector hysteresis, overlay sideways mapping, strict 45-degree sectors, all eight boundaries and previous states, 300/240 radial release");
    return 0;
}
