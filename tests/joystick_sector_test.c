/* SPDX-License-Identifier: MIT */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include "../src/joystick_sector.h"

static uint8_t sector(int x, int y, uint8_t previous) {
    return joystick_sector8(x, y, previous, 90, 55);
}

int main(void) {
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
    puts("PASS: eight sectors, radial/angle hysteresis, center release, reverse strokes, X-only flip");
    return 0;
}
