#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Same logical coordinates for both physical orientations in each aspect. */
static inline int xiao147_settings_control_at(bool portrait, uint16_t x, uint16_t y) {
    if (x < 8 || x >= (portrait ? 164 : 312)) { return -1; }
    if (portrait) {
        if (y >= 103 && y < 141) { return 0; }
        if (y >= 145 && y < 185) { return 1; }
        if (y >= 185 && y < 226) { return 2; }
        if (y >= 232 && y < 274) { return 3; }
        if (y >= 276 && y < 312) { return 4; }
    } else {
        if (y >= 65 && y < 98) { return 0; }
        if (x >= 154 && x < 162) { return -1; }
        if (y >= 99 && y < 133) { return x < 154 ? 1 : 2; }
        if (y >= 134 && y < 170) { return x < 154 ? 3 : 4; }
    }
    return -1;
}

struct xiao147_settings_gesture { int armed; uint16_t x, y; uint8_t fingers; bool blocked; };
static inline void xiao147_settings_cancel(struct xiao147_settings_gesture *g) {
    g->armed = -1;
    g->blocked = true;
}
static inline int xiao147_settings_touch(struct xiao147_settings_gesture *g,
        bool portrait, uint8_t fingers, uint16_t x, uint16_t y, bool slider) {
    int action = -1;
    if (g->blocked || slider || fingers > 1) {
        g->armed = -1;
        g->blocked = fingers != 0;
        g->fingers = fingers;
        return -1;
    }
    else if (fingers == 1 && !g->fingers) {
        g->armed = xiao147_settings_control_at(portrait, x, y);
        g->x = x; g->y = y;
    } else if (fingers == 1 && g->armed >= 0) {
        int dx = (int)x - g->x, dy = (int)y - g->y;
        if (xiao147_settings_control_at(portrait, x, y) != g->armed ||
            dx * dx + dy * dy > 144) { g->armed = -1; }
    } else if (!fingers) {
        if (g->fingers == 1) { action = g->armed; }
        g->armed = -1;
    }
    g->fingers = fingers;
    return action;
}
