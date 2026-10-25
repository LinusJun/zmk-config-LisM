/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#define KB_MIRROR_EVENT 125
#define KB_MIRROR_VERSION 1
/* Fits the pinned transport's data union and the complete 64-byte ESB frame. */
struct kb_mirror {uint32_t epoch;uint8_t version,wpm,layer,mods,indicators,profile,host_connected,left_battery,left_online;} __attribute__((packed));
/* Version 1 stays 13 bytes. Legacy senders use left_online=0/1 and legacy
 * receivers only test its truth value. Bit 0 remains online; bits 1..7 may
 * carry a measured RSSI magnitude (1..126). Offline is always the whole 0.
 */
_Static_assert(sizeof(struct kb_mirror)==13,"Keep the legacy mirror wire size");
static inline bool kb_mirror_left_status_valid(uint8_t status) {
    return status==0 || ((status&1) && (status>>1)<127);
}
static inline bool kb_mirror_left_online(uint8_t status) {
    return kb_mirror_left_status_valid(status) && (status&1);
}
static inline uint8_t kb_mirror_left_status(bool online, bool valid, int dbm) {
    if(!online)return 0;
    return valid && dbm<0 && dbm>=-126 ? 1 | ((unsigned)-dbm<<1) : 1;
}
static inline bool kb_mirror_left_rssi(uint8_t status, int8_t *dbm) {
    unsigned magnitude=status>>1;
    if(!dbm || !kb_mirror_left_online(status) || !magnitude)return false;
    *dbm=-(int)magnitude;return true;
}
/* 0 = local dongle, 1 = fresh keyboard telemetry, -1 = remote/switching unknown. */
int kb_mirror_snapshot(struct kb_mirror *out);
void kb_mirror_received(const struct kb_mirror *state);
void kb_mirror_emit(void);
void kb_mirror_ui_start(void);
