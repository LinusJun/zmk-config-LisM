#pragma once

#include <stdbool.h>
#include <stdint.h>

#define XIAO147_USR1_DOUBLE_MS 300u
#define XIAO147_USR1_SHORT_MS 500u

enum xiao147_usr1_action {
    XIAO147_USR1_NONE = 0,
    XIAO147_USR1_MOVE = 1u << 0,
    XIAO147_USR1_CAPTURE = 1u << 1,
    XIAO147_USR1_ACTIVATE = 1u << 2,
    XIAO147_USR1_CANCEL = 1u << 3,
};

/* Internal phase is public only to allow static storage: callers must not edit
 * fields. All timestamps must be original edge times in chronological order;
 * drain queued edges before calling timeout with the processing clock. Gaps
 * must be less than 2^31 ms, permitting normal uint32_t clock rollover. */
struct xiao147_usr1_click {
    uint32_t pressed_at;
    uint32_t deadline;
    uint8_t phase;
};

void xiao147_usr1_click_init(struct xiao147_usr1_click *click);
uint8_t xiao147_usr1_click_cancel(struct xiao147_usr1_click *click);
uint8_t xiao147_usr1_click_edge(struct xiao147_usr1_click *click,
                                bool pressed, uint32_t event_time);
uint8_t xiao147_usr1_click_timeout(struct xiao147_usr1_click *click, uint32_t now);
bool xiao147_usr1_click_next_deadline(const struct xiao147_usr1_click *click,
                                     uint32_t *deadline);

/* A second press must occur strictly before first release + 300 ms. A release
 * after <=500 ms is short; a still-held button becomes long at +501 ms.
 * MOVE consumes the previous capture; ACTIVATE consumes it without moving.
 * MOVE|CAPTURE means apply MOVE first, then capture the resulting focus.
 * CANCEL never accompanies CAPTURE. No function calls into UI or Zephyr. */
