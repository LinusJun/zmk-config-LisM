/* SPDX-License-Identifier: MIT */
#pragma once
#include "protocol.h"
#include <esb.h>
struct lism_esb_radio_plan {
    uint8_t base0[4], base1[4], channel, count;
    struct esb_payload packets[6];
};
/* All four hooks run on the same cooperative thread, never in the MPSL ISR. */
void lism_esb_transport_poll(int64_t now);
bool lism_esb_transport_plan(struct lism_esb_radio_plan *plan, int64_t now);
void lism_esb_transport_receive(const struct esb_payload *payload, int64_t now);
void lism_esb_transport_error(int error);
