/* SPDX-License-Identifier: MIT */
#include "chord.h"
#include <assert.h>
#include <stdio.h>
static struct {unsigned p;bool down;} events[128];
static unsigned count,requests,target;
static void emit(void *ctx,uint32_t p,bool down,int64_t t) {
    (void)ctx;(void)t;assert(count<128);events[count].p=p;events[count++].down=down;
}
static void request(void *ctx,uint8_t t) {(void)ctx;requests++;target=t;}
int main(void) {
    struct lism_mode_chord c;
    for(unsigned t=0;t<5;t++)for(unsigned order=0;order<2;order++) {
        count=requests=0;lism_mode_chord_init(&c,emit,request,NULL);
        lism_mode_chord_event(&c,39+order,true,0);
        lism_mode_chord_event(&c,40-order,true,40);
        lism_mode_chord_tick(&c,1239);assert(!c.armed && !count);
        lism_mode_chord_tick(&c,1240);assert(c.armed);
        lism_mode_chord_event(&c,5+t,true,1250);
        lism_mode_chord_event(&c,39,false,1260);assert(!requests);
        lism_mode_chord_event(&c,40,false,1270);assert(!requests);
        lism_mode_chord_event(&c,5+t,false,1280);
        assert(requests==1 && target==t && !count);
    }
    count=requests=0;lism_mode_chord_init(&c,emit,request,NULL);
    lism_mode_chord_event(&c,39,true,0);lism_mode_chord_event(&c,39,false,10);
    assert(count==2 && events[0].down && !events[1].down && !requests);
    count=0;lism_mode_chord_event(&c,40,true,100);lism_mode_chord_event(&c,6,true,110);
    assert(count==2 && events[0].p==40 && events[1].p==6); /* modifier precedes next key */
    lism_mode_chord_event(&c,40,false,120);lism_mode_chord_event(&c,6,false,130);
    count=0;lism_mode_chord_event(&c,39,true,200);lism_mode_chord_tick(&c,280);
    lism_mode_chord_event(&c,40,true,281);lism_mode_chord_tick(&c,2000);
    assert(count==2 && !c.armed); /* slow overlap is normal F1/F2 */
    lism_mode_chord_event(&c,39,false,2100);lism_mode_chord_event(&c,40,false,2200);
    count=0;lism_mode_chord_event(&c,39,true,3000);lism_mode_chord_event(&c,40,true,3010);
    lism_mode_chord_event(&c,5,true,3100);assert(count==3 && !requests);
    /* A two-stage chord can be selected after releasing both anchor keys. */
    count=requests=0;lism_mode_chord_init(&c,emit,request,NULL);
    lism_mode_chord_event(&c,39,true,0);lism_mode_chord_event(&c,40,true,20);
    lism_mode_chord_tick(&c,1220);lism_mode_chord_event(&c,39,false,1230);
    lism_mode_chord_event(&c,40,false,1240);assert(c.armed && !count && !requests);
    lism_mode_chord_event(&c,9,true,2000);lism_mode_chord_event(&c,9,false,2010);
    assert(requests==1 && target==4 && !count);
    count=requests=0;lism_mode_chord_init(&c,emit,request,NULL);
    lism_mode_chord_event(&c,39,true,0);lism_mode_chord_event(&c,40,true,20);
    lism_mode_chord_tick(&c,1220);lism_mode_chord_event(&c,39,false,1230);
    lism_mode_chord_event(&c,40,false,1240);lism_mode_chord_tick(&c,6220);
    lism_mode_chord_event(&c,5,true,6230);lism_mode_chord_event(&c,5,false,6240);
    assert(!c.armed && !requests && count==2);
    puts("PASS local mode chord: release-then-select, timeout, five targets, both orders, short tap, modifier order, slow overlap, normal overlapping chords");
}
