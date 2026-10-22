/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#define KB_STATION_COUNT 2
#define KB_STATION_NONE 255
#define KB_NET_DONGLE_A 0
#define KB_NET_PRIVATE 1
#define KB_NET_DONGLE_B 2
#define KB_NET_COUNT 3
#define KB_PROBE_EVENT 121
#define KB_PROTOCOL_VERSION 3
#define KB_LEFT_CONTROL_LEASE_MS 1500
static const uint8_t kb_station_base0[2][4]={{0xb7,0x63,0xa9,0x51},{0xb7,0x63,0xa9,0x52}};
/* Nordic pipe 0 uses base0; pipes 1..7 use base1. Equal bases let a
 * half's local pipe 0/1 address the receiver's physical set*2+hand pipe. */
static const uint8_t kb_station_base1[2][4]={{0xb7,0x63,0xa9,0x51},{0xb7,0x63,0xa9,0x52}};
static const uint8_t kb_set_prefixes[2][2]={{0xa7,0xd3},{0x69,0xb5}};
#define KB_SET_MAGIC (CONFIG_LISM_KEYBOARD_SET==0 ? "Ls51" : "Ls52")
static inline const char *kb_set_magic(unsigned set){return set==0?"Ls51":"Ls52";}

static inline unsigned kb_left_network(unsigned mode,unsigned station){return mode==1?KB_NET_PRIVATE:station==0?KB_NET_DONGLE_A:KB_NET_DONGLE_B;}
static inline bool kb_station_tuple_valid(unsigned mode,unsigned station,uint32_t boot){return mode<=1 && (mode==1?(station==KB_STATION_NONE && !boot):(station<2 && boot));}
struct kb_discovery {uint32_t cookie,authority;uint8_t version,station;} __attribute__((packed));
