/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#define KB_LEFT_DATA 123
#define KB_LEFT_CONTROL 122
#define KB_LEFT_FREEZE 0
#define KB_LEFT_COMMIT 1
#define KB_LEFT_APPLIED 2
/* Little-endian nRF wire ABI. No host-sized enums/pointers in these records. */
struct kb_left_packet {
    uint32_t authority,epoch;
    uint8_t keys[6],buttons,battery;
} __attribute__((packed));
struct kb_left_control {
    struct kb_left_packet physical;
    uint8_t phase,target;
} __attribute__((packed));
static inline uint64_t kb_left_keys(const struct kb_left_packet *p){
    uint64_t v=0;for(unsigned i=0;i<6;i++)v|=(uint64_t)p->keys[i]<<(i*8);return v;
}
static inline void kb_left_pack_keys(struct kb_left_packet *p,uint64_t keys){
    for(unsigned i=0;i<6;i++)p->keys[i]=keys>>(i*8);
}
static inline bool kb_left_packet_valid(const struct kb_left_packet *p){
    return !(p->keys[5]&0xc0) && !(p->buttons&~31) && p->battery<=100;
}
/* A cold left never adopts a live route until PREPARE and real all-up.
 * A new right boot is accepted only through the fixed private network. */
struct kb_left_policy {
    uint32_t authority,previous_authority,epoch;
    uint8_t phase,target;
    bool prepared;
};
static inline bool kb_left_policy_command(struct kb_left_policy *p,uint32_t authority,
                                  uint32_t epoch,unsigned phase,unsigned target,bool all_up){
    if(!authority || !epoch || phase>KB_LEFT_COMMIT || target>1)return false;
    if(authority!=p->authority){
        if(authority==p->previous_authority || phase!=KB_LEFT_FREEZE)return false;
        p->previous_authority=p->authority;p->authority=authority;p->epoch=0;p->prepared=false;
    }
    if(epoch<p->epoch || (epoch==p->epoch && p->target!=target))return false;
    if(phase==KB_LEFT_FREEZE){
        if(p->epoch!=epoch || p->target!=target){p->epoch=epoch;p->target=target;p->prepared=false;}
        /* Delayed duplicate PREPARE must not freeze an already applied tuple. */
        if(p->phase!=KB_LEFT_APPLIED || !p->prepared)p->phase=KB_LEFT_FREEZE;
        if(all_up)p->prepared=true;
        return true;
    }
    if(p->epoch!=epoch || p->target!=target || !p->prepared)return false;
    if(p->phase==KB_LEFT_APPLIED)return true;
    p->phase=all_up?KB_LEFT_APPLIED:KB_LEFT_COMMIT;return true;
}
static inline void kb_left_observe(struct kb_left_policy *p,bool all_up){
    if(!p->authority || !p->epoch || !all_up)return;
    p->prepared=true;if(p->phase==KB_LEFT_COMMIT)p->phase=KB_LEFT_APPLIED;
}
static inline bool kb_left_fault(struct kb_left_policy *p,bool current_generation,bool all_up){
    /* A fault can only quarantine an ALREADY authorized route. It cannot
     * promote a new PREPARE that has not received its COMMIT permission. */
    if(!current_generation || p->phase!=KB_LEFT_APPLIED)return false;
    p->phase=KB_LEFT_COMMIT;kb_left_observe(p,all_up);return true;
}
