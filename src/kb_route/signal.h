/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>
#include <stdbool.h>
bool kb_esb_signal(unsigned source,int8_t *dbm);
/* Right hub only: cached, validated left->right private-link RSSI. */
bool kb_private_left_signal(int8_t *dbm);
bool lism_input_host_signal(int8_t *dbm);
/* Cached per-slot RSSI only; this call never sends an HCI request. */
bool lism_input_host_slot_signal(unsigned slot,int8_t *dbm);
static inline unsigned kb_signal_bars(bool valid,int dbm){
    return !valid?0:dbm>=-55?4:dbm>=-67?3:dbm>=-78?2:1;
}
