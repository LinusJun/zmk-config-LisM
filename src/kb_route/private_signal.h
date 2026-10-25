/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "left_protocol.h"
/* Protected by the hub tx_lock. Samples and publications have separate clocks:
 * a publication may be held for 3 s only while fresh samples keep arriving. */
#define KB_PRIVATE_SIGNAL_FRESH_MS 1500U
#define KB_PRIVATE_SIGNAL_PUBLISH_MS 3000U
struct kb_private_signal_tuple {
    uint32_t authority,peer,epoch,receiver_boot;
    uint8_t phase,target,station;
};
struct kb_private_signal_cache {
    struct kb_private_signal_tuple tuple;
    uint32_t sampled_ms,published_ms;
    int8_t raw_dbm,published_dbm;
    bool valid;
};
static inline bool kb_private_signal_same(const struct kb_private_signal_tuple *a,
                                         const struct kb_private_signal_tuple *b){
    return a->authority==b->authority && a->peer==b->peer && a->epoch==b->epoch &&
        a->receiver_boot==b->receiver_boot && a->phase==b->phase &&
        a->target==b->target && a->station==b->station;
}
static inline bool kb_private_signal_stable(const struct kb_private_signal_tuple *t){
    return t->authority && t->peer && t->epoch && t->phase==KB_LEFT_COMMIT &&
        t->target==1 && t->station==KB_STATION_NONE && !t->receiver_boot;
}
static inline void kb_private_signal_invalidate(struct kb_private_signal_cache *s){s->valid=false;}
static inline bool kb_private_signal_read(struct kb_private_signal_cache *s,
                    const struct kb_private_signal_tuple *t,uint32_t now,int8_t *dbm){
    if(!s->valid || !kb_private_signal_stable(t) || !kb_private_signal_same(&s->tuple,t) ||
       (uint32_t)(now-s->sampled_ms)>=KB_PRIVATE_SIGNAL_FRESH_MS){s->valid=false;return false;}
    if(dbm)*dbm=s->published_dbm;
    return true;
}
static inline void kb_private_signal_sample(struct kb_private_signal_cache *s,
                    const struct kb_private_signal_tuple *t,const struct kb_left_control *c,
                    uint32_t peer,uint8_t magnitude,uint32_t sampled_ms,uint32_t now){
    /* Admission is independent of the app's queue acceptance. Never retain a
     * future/wrong tuple in anticipation of a later route transition. Held
     * keys/buttons remain legal once this session has reached APPLIED. */
    if(!kb_private_signal_stable(t) || !kb_left_packet_valid(&c->physical) ||
       c->prepared!=1 || c->phase!=KB_LEFT_APPLIED || c->target!=t->target ||
       c->physical.authority!=t->authority || c->physical.epoch!=t->epoch ||
       c->physical.station!=t->station || c->physical.receiver_boot!=t->receiver_boot ||
       peer!=t->peer || magnitude<1 || magnitude>126 ||
       (uint32_t)(now-sampled_ms)>=KB_PRIVATE_SIGNAL_FRESH_MS)return;
    bool fresh=s->valid && kb_private_signal_same(&s->tuple,t) &&
        (uint32_t)(now-s->sampled_ms)<KB_PRIVATE_SIGNAL_FRESH_MS;
    s->raw_dbm=-(int8_t)magnitude;
    if(!fresh || (uint32_t)(now-s->published_ms)>=KB_PRIVATE_SIGNAL_PUBLISH_MS){
        s->published_dbm=s->raw_dbm;s->published_ms=now;
    }
    s->tuple=*t;s->sampled_ms=sampled_ms;s->valid=true;
}
