#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "xiao147_peripheral_roles.h"

/* Index 0 is the selected BLE host; indices 1..4 are split source slots. */
void xiao147_rssi_set_active(bool active);
void xiao147_rssi_invalidate_slot(uint8_t slot);
void xiao147_rssi_snapshot(int8_t values[XIAO147_RSSI_COUNT], bool valid[XIAO147_RSSI_COUNT]);
