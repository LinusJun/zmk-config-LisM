/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdbool.h>
#include <stdint.h>
/* Probe only at boot; later RF loss never silently changes the output host. */
static inline int kb_boot_decision(int64_t now,int64_t deadline,bool dongle_confirmed) {
    if(dongle_confirmed)return 0;
    return now>=deadline?1:-1;
}
