#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Semantic poses, relative to the physical 320x172 USB-left panel. */
enum xiao147_orientation {
    XIAO147_USB_LEFT = 0,
    XIAO147_BUTTONS_BOTTOM = 1,
    XIAO147_USB_RIGHT = 2,
    XIAO147_BUTTONS_TOP = 3,
};
struct xiao147_xy { int x, y; };
static inline bool xiao147_orientation_portrait(enum xiao147_orientation o)
{
    return (o & 1) != 0;
}
static inline struct xiao147_xy xiao147_panel_to_logical(enum xiao147_orientation o,
                                                        int x, int y)
{
    switch (o) {
    case XIAO147_BUTTONS_BOTTOM: return (struct xiao147_xy){171 - y, x};
    case XIAO147_USB_RIGHT: return (struct xiao147_xy){319 - x, 171 - y};
    case XIAO147_BUTTONS_TOP: return (struct xiao147_xy){y, 319 - x};
    default: return (struct xiao147_xy){x, y};
    }
}
static inline struct xiao147_xy xiao147_touch_to_logical(enum xiao147_orientation o,
                                                        int raw_x, int raw_y)
{
    /* Empirical mounting calibration: USB-left X=raw_y, Y=raw_x.
     * The previous 171-raw_x incorrectly mirrored vertical pointer motion. */
    return xiao147_panel_to_logical(o, raw_y, raw_x);
}
static inline struct xiao147_xy xiao147_vector_to_logical(enum xiao147_orientation o,
                                                         int x, int y)
{
    switch (o) {
    case XIAO147_BUTTONS_BOTTOM: return (struct xiao147_xy){-y, x};
    case XIAO147_USB_RIGHT: return (struct xiao147_xy){-x, -y};
    case XIAO147_BUTTONS_TOP: return (struct xiao147_xy){y, -x};
    default: return (struct xiao147_xy){x, y};
    }
}
static inline struct xiao147_xy xiao147_gyro_to_logical(enum xiao147_orientation o,
                                                      int gx, int gy)
{
    return xiao147_vector_to_logical(o, gx, -gy);
}
/* Flat/diagonal poses retain orientation; caller requires 600 ms stability.
 * Back-face PCB marks: +X right, +Y up. Viewed from the front USB-left:
 * +X down, +Y left. Static accelerometer reports support acceleration UP,
 * not gravitational acceleration down. Confirm signs with real-pose logs. */
static inline bool xiao147_gravity_orientation(int ax, int ay,
                                               enum xiao147_orientation *o)
{
    int x = ax < 0 ? -ax : ax, y = ay < 0 ? -ay : ay;
    if (x > 7500 && x - y > 2000) {
        *o = ax < 0 ? XIAO147_USB_LEFT : XIAO147_USB_RIGHT;
        return true;
    }
    if (y > 7500 && y - x > 2000) {
        *o = ay > 0 ? XIAO147_BUTTONS_BOTTOM : XIAO147_BUTTONS_TOP;
        return true;
    }
    return false;
}
