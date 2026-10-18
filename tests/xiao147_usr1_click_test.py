"""Pure-C USR1 click timing regression, compiled with ASAN/UBSAN."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
test = r'''
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include "xiao147_usr1_click.h"
#define NONE XIAO147_USR1_NONE
#define MOVE XIAO147_USR1_MOVE
#define CAPTURE XIAO147_USR1_CAPTURE
#define ACTIVATE XIAO147_USR1_ACTIVATE
#define CANCEL XIAO147_USR1_CANCEL
static struct xiao147_usr1_click c;
static void init(void){xiao147_usr1_click_init(&c);}
static uint8_t edge(bool down,uint32_t t){return xiao147_usr1_click_edge(&c,down,t);}
static uint8_t timeout(uint32_t t){return xiao147_usr1_click_timeout(&c,t);}
static void deadline(uint32_t expected){uint32_t d=0;assert(xiao147_usr1_click_next_deadline(&c,&d)&&d==expected);}
static void no_deadline(void){uint32_t sentinel=42;assert(!xiao147_usr1_click_next_deadline(&c,&sentinel)&&sentinel==42);}
int main(void){
 init();no_deadline();assert(edge(false,5)==NONE);
 assert(edge(true,10)==CAPTURE);deadline(511);
 assert(edge(true,90)==NONE);deadline(511); /* duplicate press cannot restart hold */
 assert(edge(false,100)==NONE);deadline(400);
 assert(edge(false,150)==NONE);deadline(400); /* release without press ignored */
 assert(timeout(399)==NONE&&timeout(400)==MOVE);
 no_deadline();assert(timeout(1000)==NONE&&timeout(2000)==NONE);

 /* Double-click never moves focus before activating the original capture. */
 init();assert(edge(true,10)==CAPTURE);assert(edge(false,20)==NONE);
 assert(edge(true,200)==NONE);deadline(701);
 assert(timeout(320)==NONE&&timeout(700)==NONE); /* first single deadline suspended */
 assert(edge(false,700)==ACTIVATE);no_deadline();assert(timeout(900)==NONE);

 /* Exact double boundary is a new first click: old MOVE precedes new CAPTURE. */
 init();assert(edge(true,0)==CAPTURE);assert(edge(false,20)==NONE);
 assert(edge(true,319)==NONE);assert(edge(false,320)==ACTIVATE);
 init();assert(edge(true,0)==CAPTURE);assert(edge(false,20)==NONE);
 assert(edge(true,320)==(MOVE|CAPTURE));deadline(821);
 assert(edge(false,321)==NONE);deadline(621);assert(timeout(621)==MOVE);
 /* Timer and edge agree at the exact boundary, regardless of dispatch order. */
 init();assert(edge(true,0)==CAPTURE);assert(edge(false,20)==NONE);
 assert(timeout(320)==MOVE);assert(edge(true,320)==CAPTURE);

 /* <=500 ms releases are short. >500 ms is cancellation, not selection/move. */
 init();assert(edge(true,0)==CAPTURE);assert(timeout(500)==NONE);
 assert(edge(false,500)==NONE);deadline(800);assert(timeout(800)==MOVE);
 init();assert(edge(true,0)==CAPTURE);assert(edge(false,501)==CANCEL);
 no_deadline();assert(timeout(801)==NONE);
 init();assert(edge(true,0)==CAPTURE);assert(timeout(501)==CANCEL);
 no_deadline();assert(timeout(1000)==NONE&&edge(true,1001)==NONE);
 assert(edge(false,1002)==NONE);assert(edge(true,1100)==CAPTURE);
 assert(xiao147_usr1_click_cancel(&c)==CANCEL);no_deadline();
 assert(edge(false,1200)==NONE&&xiao147_usr1_click_cancel(&c)==NONE);

 /* A second held press may cross the first click deadline without moving.
  * If it becomes long, cancel the whole double and wait for physical release. */
 init();assert(edge(true,0)==CAPTURE);assert(edge(false,10)==NONE);
 assert(edge(true,309)==NONE);assert(timeout(310)==NONE);deadline(810);
 assert(timeout(809)==NONE&&timeout(810)==CANCEL);
 assert(edge(true,811)==NONE&&edge(false,900)==NONE);no_deadline();
 init();assert(edge(true,0)==CAPTURE);assert(edge(false,10)==NONE);
 assert(edge(true,309)==NONE);assert(edge(false,810)==CANCEL);no_deadline();

 /* Three clicks are one double-confirm followed by one independent single. */
 init();assert(edge(true,0)==CAPTURE);assert(edge(false,10)==NONE);
 assert(edge(true,20)==NONE);assert(edge(false,30)==ACTIVATE);
 assert(edge(true,40)==CAPTURE);assert(edge(false,50)==NONE);
 assert(timeout(349)==NONE&&timeout(350)==MOVE);assert(timeout(351)==NONE);

 /* Slow queued processing does not turn original-time short edges into holds.
  * The caller drains timestamped events before ticking the processing clock. */
 init();assert(edge(true,100)==CAPTURE);assert(edge(false,600)==NONE);
 assert(edge(true,899)==NONE);assert(edge(false,1399)==ACTIVATE);
 assert(timeout(1000000)==NONE); /* hypothetical much-later processing time */

 /* uint32_t rollover for both the double window and held-press deadline. */
 init();assert(edge(true,UINT32_MAX-100u)==CAPTURE);deadline(400);
 assert(edge(false,UINT32_MAX-50u)==NONE);deadline(249);
 assert(timeout(248)==NONE);assert(edge(true,248)==NONE);deadline(749);
 assert(timeout(500)==NONE);assert(edge(false,600)==ACTIVATE);
 init();assert(edge(true,UINT32_MAX-100u)==CAPTURE);
 assert(timeout(399)==NONE&&timeout(400)==CANCEL);assert(edge(false,401)==NONE);

 /* Every cancelable phase revokes once; invalid pointers stay harmless. */
 init();assert(xiao147_usr1_click_cancel(&c)==NONE);
 assert(edge(true,0)==CAPTURE);assert(xiao147_usr1_click_cancel(&c)==CANCEL);
 assert(edge(true,10)==CAPTURE);assert(edge(false,20)==NONE);
 assert(xiao147_usr1_click_cancel(&c)==CANCEL);
 assert(edge(true,30)==CAPTURE);assert(edge(false,40)==NONE);assert(edge(true,50)==NONE);
 assert(xiao147_usr1_click_cancel(&c)==CANCEL);
 xiao147_usr1_click_init(NULL);assert(xiao147_usr1_click_cancel(NULL)==NONE);
 assert(xiao147_usr1_click_edge(NULL,true,0)==NONE&&xiao147_usr1_click_timeout(NULL,0)==NONE);
 assert(!xiao147_usr1_click_next_deadline(NULL,NULL));
 assert(!xiao147_usr1_click_next_deadline(&c,NULL));

 /* Bounded randomized chronological edges/timeouts stress rollover arithmetic
  * and invariant action combinations without assuming a processing clock. */
 init();uint32_t random=0x147147u,now=UINT32_MAX-10000u;
 for(unsigned i=0;i<100000;i++){
  random=random*1664525u+1013904223u;now+=(random>>16)%700u;
  uint8_t action=(random&3u)==0?timeout(now):edge((random&4u)!=0,now);
  assert(!(action&ACTIVATE)||!(action&(MOVE|CAPTURE|CANCEL)));
  assert(!(action&CANCEL)||!(action&CAPTURE));
  uint32_t d=0;if(xiao147_usr1_click_next_deadline(&c,&d)){
   if((int32_t)(now-d)<0)now=d;
   uint8_t fired=timeout(now);assert(fired==MOVE||fired==CANCEL);
   assert(timeout(now)==NONE);
  }
 }
 puts("PASS: USR1 original-time single/double/triple clicks, no double MOVE, hold cancellation, exact boundaries, duplicate edges and uint32 rollover");
}
'''
with tempfile.TemporaryDirectory(prefix="xiao147-usr1-click-") as tmp:
    out = Path(tmp)
    (out / "test.c").write_text(test)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer", "-I", str(root / "src"),
                    str(root / "src/xiao147_usr1_click.c"), str(out / "test.c"),
                    "-o", str(out / "test")], check=True)
    subprocess.run([str(out / "test")], check=True)
