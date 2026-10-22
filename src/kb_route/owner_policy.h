/* SPDX-License-Identifier: MIT
 * One owner tuple per receiver boot. Retired NEVER returns to FREE; only a
 * hardware reboot may admit a different set/authority/right wire boot. This
 * is the queue fence, including reports already handed to USB/SDC DMA.
 */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "left_protocol.h"
#define KB_OWNER_LEASE_MS 2500
#define KB_OWNER_NONE 255
struct kb_owner_policy {
    uint32_t wire_boot,authority,epoch;
    int64_t seen;
    uint8_t set,phase,target;
    bool bound,retired;
};
static inline bool kb_owner_expire(struct kb_owner_policy *o,int64_t now) {
    if(o->bound && now-o->seen>=KB_OWNER_LEASE_MS)o->retired=true;
    return o->retired;
}
static inline bool kb_owner_same(const struct kb_owner_policy *o,unsigned set,uint32_t boot) {
    return o->bound && !o->retired && o->set==set && o->wire_boot==boot;
}
static inline bool kb_owner_probe_allowed(struct kb_owner_policy *o,unsigned set,uint32_t boot,int64_t now) {
    if(set>1 || !boot || kb_owner_expire(o,now))return false;
    /* A real keyboard search may retire the existing owner's BLE display.
     * Do this BEFORE offering a boot, so the searching right stays in HUNT
     * and can discover the fresh receiver boot instead of timing out a claim. */
    if(o->bound && o->target==1 && !kb_owner_same(o,set,boot)){o->retired=true;return false;}
    return !o->bound || kb_owner_same(o,set,boot);
}
static inline bool kb_owner_status(struct kb_owner_policy *o,unsigned set,uint32_t boot,
 uint32_t authority,uint32_t epoch,unsigned phase,unsigned target,unsigned mode,int64_t now) {
    if(set>1 || !boot || !authority || !epoch || phase>KB_LEFT_COMMIT || target>1 || mode>2 || kb_owner_expire(o,now))return false;
    if((mode==0 && (target!=0 || phase!=KB_LEFT_COMMIT)) || (mode==1 && (target!=1 || phase!=KB_LEFT_COMMIT)) || (mode==2 && phase!=KB_LEFT_FREEZE))return false;
    if(!o->bound){
        /* Presence and BLE mirror traffic cannot reserve a free receiver. */
        if(mode!=2 || target!=0 || phase!=KB_LEFT_FREEZE)return false;
        *o=(struct kb_owner_policy){.set=set,.wire_boot=boot,.authority=authority,.epoch=epoch,.phase=phase,.target=target,.bound=true,.seen=now};return true;
    }
    if(!kb_owner_same(o,set,boot) || authority!=o->authority || epoch<o->epoch)return false;
    if(phase==KB_LEFT_COMMIT && (epoch!=o->epoch || target!=o->target))return false;
    /* Once COMMIT was observed, a delayed same-epoch FREEZE cannot undo it. */
    if(epoch==o->epoch && o->phase==KB_LEFT_COMMIT && phase!=KB_LEFT_COMMIT)return false;
    o->epoch=epoch;o->phase=phase;o->target=target;o->seen=now;return true;
}
