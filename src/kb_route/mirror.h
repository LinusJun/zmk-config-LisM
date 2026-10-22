/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>
#define KB_MIRROR_EVENT 125
#define KB_MIRROR_VERSION 1
/* Fits the pinned transport's data union and the complete 64-byte ESB frame. */
struct kb_mirror {uint32_t epoch;uint8_t version,wpm,layer,mods,indicators,profile,host_connected,left_battery,left_online;} __attribute__((packed));
/* 0 = local dongle, 1 = fresh keyboard telemetry, -1 = remote/switching unknown. */
int kb_mirror_snapshot(struct kb_mirror *out);
void kb_mirror_received(const struct kb_mirror *state);
void kb_mirror_emit(void);
void kb_mirror_ui_start(void);
