#include "xiao147_usr1_click.h"

enum phase { IDLE, FIRST_HELD, WAIT_SECOND, SECOND_HELD, WAIT_RELEASE };

static bool reached(uint32_t now, uint32_t deadline) {
    return (int32_t)(now - deadline) >= 0;
}

void xiao147_usr1_click_init(struct xiao147_usr1_click *click) {
    if (click) { *click = (struct xiao147_usr1_click){0}; }
}

uint8_t xiao147_usr1_click_cancel(struct xiao147_usr1_click *click) {
    if (!click) { return XIAO147_USR1_NONE; }
    uint8_t action = click->phase == IDLE ? XIAO147_USR1_NONE : XIAO147_USR1_CANCEL;
    xiao147_usr1_click_init(click);
    return action;
}

static uint8_t first_press(struct xiao147_usr1_click *click, uint32_t time) {
    click->phase = FIRST_HELD;
    click->pressed_at = time;
    click->deadline = time + XIAO147_USR1_SHORT_MS + 1u;
    return XIAO147_USR1_CAPTURE;
}

uint8_t xiao147_usr1_click_edge(struct xiao147_usr1_click *click,
                                bool pressed, uint32_t event_time) {
    if (!click) { return XIAO147_USR1_NONE; }
    switch (click->phase) {
    case IDLE:
        return pressed ? first_press(click, event_time) : XIAO147_USR1_NONE;
    case WAIT_SECOND:
        if (!pressed) { return XIAO147_USR1_NONE; }
        if (reached(event_time, click->deadline)) {
            return XIAO147_USR1_MOVE | first_press(click, event_time);
        }
        click->phase = SECOND_HELD;
        click->pressed_at = event_time;
        click->deadline = event_time + XIAO147_USR1_SHORT_MS + 1u;
        return XIAO147_USR1_NONE;
    case FIRST_HELD:
    case SECOND_HELD:
        if (pressed) { return XIAO147_USR1_NONE; } /* repeated edge cannot extend hold */
        if (event_time - click->pressed_at > XIAO147_USR1_SHORT_MS) {
            click->phase = IDLE;
            return XIAO147_USR1_CANCEL;
        }
        if (click->phase == SECOND_HELD) {
            click->phase = IDLE;
            return XIAO147_USR1_ACTIVATE;
        }
        click->phase = WAIT_SECOND;
        click->deadline = event_time + XIAO147_USR1_DOUBLE_MS;
        return XIAO147_USR1_NONE;
    case WAIT_RELEASE:
        if (!pressed) { click->phase = IDLE; }
        return XIAO147_USR1_NONE;
    default:
        xiao147_usr1_click_init(click);
        return XIAO147_USR1_CANCEL;
    }
}

uint8_t xiao147_usr1_click_timeout(struct xiao147_usr1_click *click, uint32_t now) {
    if (!click || !reached(now, click->deadline)) { return XIAO147_USR1_NONE; }
    if (click->phase == WAIT_SECOND) {
        click->phase = IDLE;
        return XIAO147_USR1_MOVE;
    }
    if (click->phase == FIRST_HELD || click->phase == SECOND_HELD) {
        click->phase = WAIT_RELEASE;
        return XIAO147_USR1_CANCEL;
    }
    return XIAO147_USR1_NONE;
}

bool xiao147_usr1_click_next_deadline(const struct xiao147_usr1_click *click,
                                     uint32_t *deadline) {
    if (!click || !deadline || (click->phase != FIRST_HELD &&
        click->phase != SECOND_HELD && click->phase != WAIT_SECOND)) { return false; }
    *deadline = click->deadline;
    return true;
}
