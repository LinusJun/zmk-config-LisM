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
static const uint8_t kb_station_base0[2][4]={{0xb7,0x63,0xa9,0xd5},{0xb7,0x63,0xa9,0xb3}};
static const uint8_t kb_station_base1[2][4]={{0xc9,0x57,0x8d,0xab},{0xc9,0x57,0x8d,0xc5}};
static inline unsigned kb_left_network(unsigned mode,unsigned station){return mode==1?KB_NET_PRIVATE:station==0?KB_NET_DONGLE_A:KB_NET_DONGLE_B;}
static inline bool kb_station_tuple_valid(unsigned mode,unsigned station,uint32_t boot){return mode<=1 && (mode==1?(station==KB_STATION_NONE && !boot):(station<2 && boot));}
struct kb_discovery {uint32_t cookie,authority;uint8_t version,station;} __attribute__((packed));
