/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>
#define KB_ROUTE_DONGLE 0
#define KB_ROUTE_BLE 1
#define KB_ROUTE_SWITCHING 2
#define KB_ROUTE_UNKNOWN 3
struct kb_route_view { uint8_t mode,profile,last_mode; uint32_t epoch; int64_t seen; };
void kb_route_snapshot(struct kb_route_view *out);
