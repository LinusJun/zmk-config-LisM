/* SPDX-License-Identifier: MIT */
#include "transition.h"
#include <assert.h>
#include <errno.h>
#include <stdio.h>

static const struct lism_mode usb = {LISM_OUTPUT_USB, 0};
static const struct lism_mode ble = {LISM_OUTPUT_BLE, 4};
static const struct lism_mode esb = {LISM_OUTPUT_ESB, 0};
static void ready(struct lism_switch *s, unsigned left) {
    assert(lism_switch_ready(s, left, s->epoch) == LISM_ACTION_NONE);
    assert(lism_switch_ready(s, left + 1, s->epoch) == LISM_ACTION_DRAIN);
    assert(lism_switch_drained(s, s->epoch) == LISM_ACTION_ACTIVATE);
    assert(lism_switch_activated(s, s->epoch, true) == LISM_ACTION_SYNC);
    assert(lism_switch_synced(s, left, s->epoch) == LISM_ACTION_NONE);
    assert(lism_switch_synced(s, left + 1, s->epoch) == LISM_ACTION_RESUME);
}
int main(void) {
    assert(lism_mode_link(usb) == LISM_LINK_ESB);
    assert(lism_mode_link(ble) == LISM_LINK_ESB);
    assert(lism_mode_link(esb) == LISM_LINK_ESB);
    struct lism_switch s;
    lism_switch_init(&s, ble, 10);
    assert(lism_switch_begin(&s, usb, 0x33, 0) == -EINVAL); /* A+B forbidden */
    assert(lism_switch_begin(&s, usb, 0x04, 0) == -EINVAL); /* non-half slot */
    assert(lism_switch_begin(&s, usb, 0x30, 0) == LISM_ACTION_PREPARE);
    assert(lism_switch_begin(&s, esb, 0x30, 0) == -EBUSY);
    assert(!lism_switch_accept_input(&s, 10));
    assert(lism_switch_ready(&s, 4, 10) == LISM_ACTION_NONE); /* old ACK */
    assert(lism_switch_ready(&s, 0, 11) == LISM_ACTION_NONE); /* other set */
    ready(&s, 4);
    assert(lism_switch_accept_input(&s, 11));
    assert(!lism_switch_accept_input(&s, 10));

    assert(lism_switch_begin(&s, esb, 3, 100) == LISM_ACTION_PREPARE);
    assert(s.required == 3);
    assert(lism_switch_ready(&s, 0, s.epoch) == LISM_ACTION_NONE);
    assert(lism_switch_ready(&s, 6, s.epoch) == LISM_ACTION_NONE);
    assert(lism_switch_ready(&s, 1, s.epoch) == LISM_ACTION_DRAIN);
    assert(lism_switch_drained(&s, s.epoch) == LISM_ACTION_ACTIVATE);
    assert(lism_switch_activated(&s, s.epoch, true) == LISM_ACTION_SYNC);
    assert(lism_switch_synced(&s, 0, s.epoch) == LISM_ACTION_NONE);
    assert(lism_switch_synced(&s, 1, s.epoch) == LISM_ACTION_NONE);
    assert(lism_switch_synced(&s, 6, s.epoch) == LISM_ACTION_RESUME);
    assert(s.current.output == LISM_OUTPUT_ESB);

    /* A missing release or peer never causes an unconfirmed mode commit. */
    assert(lism_switch_begin(&s, ble, 3, 1000) == LISM_ACTION_PREPARE);
    assert(lism_switch_tick(&s, 5999) == LISM_ACTION_NONE);
    assert(lism_switch_tick(&s, 6000) == LISM_ACTION_RECOVER);
    assert(!lism_switch_accept_input(&s, s.epoch));
    assert(lism_switch_recovered(&s, s.epoch, false) == LISM_ACTION_NONE);
    assert(lism_switch_recovered(&s, s.epoch - 1, true) == LISM_ACTION_NONE);
    assert(lism_switch_recovered(&s, s.epoch, true) == LISM_ACTION_RESUME);
    assert(s.current.output == LISM_OUTPUT_ESB);
    assert(lism_switch_accept_input(&s, s.epoch));

    /* Every ordered mode transition, each set, and no connected halves. */
    const struct lism_mode modes[] = {usb, ble, esb};
    const uint8_t sets[] = {0, 1, 2, 3, 0x10, 0x20, 0x30};
    for (unsigned a = 0; a < 3; ++a) for (unsigned b = 0; b < 3; ++b) {
        if (a == b) continue;
        for (unsigned k = 0; k < sizeof(sets); ++k) {
            lism_switch_init(&s, modes[a], 100);
            int action = lism_switch_begin(&s, modes[b], sets[k], 0);
            assert(action > 0);
            uint8_t peers = s.required;
            for (unsigned p = 0; p < 7; ++p)
                if (peers & (1u << p)) action = lism_switch_ready(&s, p, s.epoch);
            assert(action == LISM_ACTION_DRAIN);
            assert(lism_switch_drained(&s, s.epoch) == LISM_ACTION_ACTIVATE);
            action = lism_switch_activated(&s, s.epoch, true);
            peers = s.required;
            for (unsigned p = 0; p < 7; ++p)
                if (peers & (1u << p)) action = lism_switch_synced(&s, p, s.epoch);
            assert(action == LISM_ACTION_RESUME);
            assert(s.current.output == modes[b].output);
            assert(lism_switch_accept_input(&s, 101));
        }
    }
    lism_switch_init(&s, usb, UINT32_MAX);
    assert(lism_switch_begin(&s, ble, 3, 0) == -EOVERFLOW);
    puts("trimode transition policy: PASS (42 mode/set combinations plus faults)");
}
