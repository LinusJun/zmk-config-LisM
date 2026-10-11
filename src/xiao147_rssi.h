#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Index 0 is the selected BLE host; indices 1..3 are split source slots. */
void xiao147_rssi_set_active(bool active);
void xiao147_rssi_invalidate_slot(uint8_t slot);
void xiao147_rssi_snapshot(int8_t values[4], bool valid[4]);
