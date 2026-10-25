/* SPDX-License-Identifier: MIT */
#pragma once
#include <kb_route/stations.h>
/* Controlled two-hand experiment; not evidence of an RF fix. */
#define KB_ESB_DATA_CHANNEL 37
#define KB_ESB_PRIVATE_CHANNEL 76
static inline unsigned kb_radio_channel(unsigned net) {
    return net == KB_NET_PRIVATE ? KB_ESB_PRIVATE_CHANNEL : KB_ESB_DATA_CHANNEL;
}
