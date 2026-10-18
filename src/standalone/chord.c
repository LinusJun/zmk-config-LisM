/* SPDX-License-Identifier: MIT */
#include "chord.h"
#include <string.h>

static void flush(struct lism_mode_chord *c) {
    for (unsigned n=0;n<2;n++) {
        unsigned i=(c->first+n)%2;
        if (c->down[i] && !c->forwarded[i] && !c->suppress[i]) {
            c->forwarded[i]=true;c->emit(c->context,39+i,true,c->pressed[i]);
        }
    }
    c->paired=false;
}
static void finish(struct lism_mode_chord *c) {
    if (c->down[0] || c->down[1] || c->selector_down) return;
    if(c->armed && !c->selection)return; /* release chord, then select within 5 s */
    uint8_t selection=c->selection;
    c->paired=c->armed=false;c->selection=0;
    c->suppress[0]=c->suppress[1]=false;
    if (selection)c->request(c->context,selection-1);
}
void lism_mode_chord_init(struct lism_mode_chord *c,
    void (*emit)(void *,uint32_t,bool,int64_t),void (*request)(void *,uint8_t),void *context) {
    memset(c,0,sizeof(*c));c->emit=emit;c->request=request;c->context=context;
}
void lism_mode_chord_tick(struct lism_mode_chord *c,int64_t now) {
    if (c->paired && !c->armed && now-c->paired_at>=1200) {
        c->armed=true;c->armed_at=now;c->suppress[0]=c->suppress[1]=true;
    } else if (!c->paired && !c->armed) {
        for(unsigned i=0;i<2;i++)
            if(c->down[i] && !c->forwarded[i] && now-c->pressed[i]>=80) {flush(c);break;}
    }
    if(c->armed && !c->selection && now-c->armed_at>=5000) {
        c->armed=false;c->paired=false;
    }
    finish(c);
}
int64_t lism_mode_chord_deadline(const struct lism_mode_chord *c) {
    if(c->armed && !c->selection)return c->armed_at+5000;
    if(c->paired && !c->armed)return c->paired_at+1200;
    for(unsigned i=0;i<2;i++)
        if(c->down[i] && !c->forwarded[i] && !c->suppress[i])return c->pressed[i]+80;
    return -1;
}
void lism_mode_chord_event(struct lism_mode_chord *c,uint32_t p,bool pressed,int64_t now) {
    lism_mode_chord_tick(c,now);
    if(p>=39 && p<=40) {
        unsigned i=p-39,other=1-i;
        if(c->down[i]==pressed)return;
        if(pressed) {
            if(!c->down[other])c->first=i;
            c->down[i]=true;c->pressed[i]=now;
            if(c->down[other] && !c->forwarded[other] && !c->suppress[other] &&
               now-c->pressed[other]<80) {c->paired=true;c->paired_at=now;}
            else if(c->forwarded[other])flush(c);
        } else {
            if(!c->armed && !c->suppress[i])flush(c);
            c->down[i]=false;
            if(c->forwarded[i]) {c->emit(c->context,p,false,now);c->forwarded[i]=false;}
            finish(c);
        }
        return;
    }
    if(p>=5 && p<=9) {
        uint8_t bit=1u<<(p-5);
        if(!pressed && (c->selector_down&bit)) {c->selector_down&=~bit;finish(c);return;}
        if(pressed && c->armed) {
            if(!c->selection)c->selection=p-4;
            c->selector_down|=bit;return;
        }
    }
    if(c->armed && !c->selection) {c->armed=false;c->paired=false;}
    if(!c->armed)flush(c);
    c->emit(c->context,p,pressed,now);
}
