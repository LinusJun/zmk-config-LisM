"""Bounded generic HID parser; synthetic descriptors, no hardware or BLE writes."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = r'''
#include "lism_input_parser.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static struct lism_input_descriptor d;
static struct lism_input_state s;
static struct lism_input_event e;
static const uint8_t keyboard[]={
 5,1,9,6,0xa1,1,0x85,1,5,7,0x19,0xe0,0x29,0xe7,0x15,0,0x25,1,
 0x75,1,0x95,8,0x81,2,0x75,8,0x95,1,0x81,1,
 0x19,0,0x29,0xff,0x15,0,0x26,0xff,0,0x75,8,0x95,6,0x81,0,0xc0
};
static const uint8_t nkro[]={
 5,1,9,6,0xa1,1,0x85,2,5,7,0x19,0,0x29,0xff,0x15,0,0x25,1,
 0x75,1,0x96,0,1,0x81,2,0xc0
};
static const uint8_t mouse[]={
 5,1,9,2,0xa1,1,0x85,3,5,9,0x19,1,0x29,8,0x15,0,0x25,1,
 0x75,1,0x95,8,0x81,2,5,1,9,0x30,9,0x31,9,0x38,
 0x15,0x81,0x25,0x7f,0x75,8,0x95,3,0x81,6,
 5,12,0x0a,0x38,2,0x95,1,0x81,6,0xc0
};
static const uint8_t consumer[]={
 5,12,9,1,0xa1,1,0x85,4,0x19,0,0x2a,0xff,3,
 0x15,0,0x26,0xff,3,0x75,16,0x95,2,0x81,0,0xc0
};
static const uint8_t vendor[]={
 6,0,0xff,9,1,0xa1,1,0x85,5,0x15,0,0x26,0xff,0,
 0x75,8,0x95,4,0x81,2,0xc0
};
static void decode(unsigned id,const uint8_t *p,size_t n) {
 enum lism_input_error err=lism_input_decode(&d,&s,id,p,n,&e);
 if(err)fprintf(stderr,"decode id %u: %s\n",id,lism_input_error_string(err));
 assert(!err);
}
static void bad_decode(unsigned id,const uint8_t *p,size_t n,enum lism_input_error expected) {
 struct lism_input_state before=s;struct lism_input_event event=e;
 assert(lism_input_decode(&d,&s,id,p,n,&e)==expected);
 assert(!memcmp(&before,&s,sizeof(s)));assert(!memcmp(&event,&e,sizeof(e)));
}
static void composite(void) {
 uint8_t map[1024];size_t n=0;
 #define APPEND(a) memcpy(map+n,a,sizeof(a));n+=sizeof(a)
 APPEND(keyboard);APPEND(nkro);APPEND(mouse);APPEND(consumer);APPEND(vendor);
 assert(!lism_input_parse(map,n,&d));assert(d.report_count==5);assert(d.run_count==7);
 assert(d.capabilities==15);assert(sizeof(d)<4096);assert(sizeof(s)<1200);
 uint8_t k[]={2,0,4,5,6,7,8,9};decode(1,k,sizeof(k));
 assert(e.modifiers==2 && e.keys[0]==0xf0 && e.keys[1]==3 && !e.keyboard_rollover);
 uint8_t bitmap[32]={0};bitmap[2]=0xff;bitmap[28]=1;decode(2,bitmap,sizeof(bitmap));
 assert(e.keys[2]==0xff && e.modifiers==3 && !e.keyboard_rollover);
 /* More than 6 keys is legitimate input NKRO; output merger chooses 6KRO rollover. */
 uint8_t m[]={3,0xff,2,0xfe,3};decode(3,m,sizeof(m));
 assert(e.buttons==3 && e.dx==-1 && e.dy==2 && e.wheel==-2 && e.pan==3);
 assert(e.keys[2]==0xff && e.modifiers==3 && e.updated==LISM_INPUT_UPDATED_MOUSE);
 uint8_t c[]={0xe9,0,0xea,0};decode(4,c,sizeof(c));
 assert(e.consumer_count==2 && e.consumer[0]==0xe9 && e.consumer[1]==0xea && e.buttons==3 && !e.dx);
 memset(k,0,sizeof(k));decode(1,k,sizeof(k));assert(!e.keys[0] && e.keys[2]==0xff && e.modifiers==1);
 memset(bitmap,0,sizeof(bitmap));decode(2,bitmap,sizeof(bitmap));assert(!e.modifiers && !e.keys[2]);
 k[2]=4;decode(1,k,sizeof(k));k[2]=1;decode(1,k,sizeof(k));
 assert(e.keyboard_rollover && (e.keys[0]&(1U<<4)) && !(e.keys[0]&2));
 k[2]=0;decode(1,k,sizeof(k));assert(!e.keyboard_rollover && !e.keys[0]);
 bad_decode(1,k,7,LISM_INPUT_ERR_LENGTH);bad_decode(20,k,8,LISM_INPUT_ERR_REPORT_ID);
 uint8_t v[4]={0};bad_decode(5,v,4,LISM_INPUT_ERR_UNSUPPORTED);
 m[1]=0x80;bad_decode(3,m,5,LISM_INPUT_ERR_RANGE);
}
static void malformed_and_fuzz(void) {
 uint8_t bytes[80];
 /* Every truncated prefix is bounded; valid complete maps need not be rejected. */
 for(size_t n=0;n<sizeof(keyboard);n++) {
  enum lism_input_error r=lism_input_parse(keyboard,n,&d);
  if(r)assert(!d.run_count && !d.report_count);
 }
 uint32_t seed=0x12345678;
 for(unsigned n=0;n<20000;n++) {
  for(unsigned j=0;j<sizeof(bytes);j++){seed=seed*1664525+1013904223;bytes[j]=seed>>24;}
  enum lism_input_error r=lism_input_parse(bytes,n%sizeof(bytes)+1,&d);
  if(r)assert(!d.run_count && !d.report_count);
 }
 uint8_t badrange[]={5,1,9,6,0xa1,1,5,7,0x19,8,0x29,4,0x75,1,0x95,8,0x81,2,0xc0};
 assert(lism_input_parse(badrange,sizeof(badrange),&d)==LISM_INPUT_ERR_MALFORMED);
 uint8_t badid[]={5,1,9,6,0xa1,1,0x85,0,0xc0};
 assert(lism_input_parse(badid,sizeof(badid),&d)==LISM_INPUT_ERR_REPORT_ID);
 assert(!lism_input_parse(mouse,sizeof(mouse),&d));
 memset(&s,0,sizeof(s));memset(&e,0,sizeof(e));
 d.runs[0].usage_offset=LISM_INPUT_MAX_USAGES;
 uint8_t m[5]={0};bad_decode(3,m,5,LISM_INPUT_ERR_MALFORMED);
}
static void consumer_bounds_and_metadata(void) {
 /* Explicit selector list differs from a usage range; map by logical index. */
 const uint8_t media[]={5,1,9,6,0xa1,1,5,12,9,0,9,0xe9,9,0xea,
  0x15,0,0x25,2,0x75,8,0x95,1,0x81,0,0xc0};
 assert(!lism_input_parse(media,sizeof(media),&d));memset(&s,0,sizeof(s));
 uint8_t b=1;decode(0,&b,1);assert(e.consumer_count==1 && e.consumer[0]==0xe9);
 b=2;decode(0,&b,1);assert(e.consumer_count==1 && e.consumer[0]==0xea);
 b=0;decode(0,&b,1);assert(!e.consumer_count);
 /* Output/feature do not advance the input offset. */
 const uint8_t outputs[]={5,1,9,6,0xa1,1,5,7,0x19,4,0x29,11,
  0x15,0,0x25,1,0x75,1,0x95,8,0x91,2,0xb1,2,0x19,4,0x29,11,0x81,2,0xc0};
 assert(!lism_input_parse(outputs,sizeof(outputs),&d));memset(&s,0,sizeof(s));
 b=1;decode(0,&b,1);assert(e.keys[0]==0x10);
 /* Overflow in consumers cannot partially commit state, including union
  * overflow accumulated over distinct report IDs. */
 uint8_t map[128];size_t n=0;
 const uint8_t cc[]={5,12,9,1,0xa1,1,0x85,1,0x19,0,0x29,30,
  0x15,0,0x25,30,0x75,8,0x95,7,0x81,0,0xc0};
 memcpy(map+n,cc,sizeof(cc));n+=sizeof(cc);
 memcpy(map+n,cc,sizeof(cc));map[n+7]=2;n+=sizeof(cc);
 assert(!lism_input_parse(map,n,&d));memset(&s,0,sizeof(s));
 uint8_t values[]={1,2,3,4,5,6,7};decode(1,values,7);
 for(unsigned i=0;i<7;i++)values[i]+=7;
 bad_decode(2,values,7,LISM_INPUT_ERR_LIMIT);
 /* Unrelated digitizer application cannot reinterpret its contacts as mouse
  * cursor fields, but does not reject the useful mouse in a composite map. */
 const uint8_t digitizer[]={5,13,9,5,0xa1,1,0x85,9,5,1,9,0x30,
  0x15,0,0x26,0xff,0x7f,0x75,16,0x95,1,0x81,2,0xc0};
 n=0;memcpy(map+n,mouse,sizeof(mouse));n+=sizeof(mouse);
 memcpy(map+n,digitizer,sizeof(digitizer));n+=sizeof(digitizer);
 assert(!lism_input_parse(map,n,&d));memset(&s,0,sizeof(s));
 assert((d.capabilities&(LISM_INPUT_CAP_MOUSE|LISM_INPUT_CAP_TOUCHPAD))==
     (LISM_INPUT_CAP_MOUSE|LISM_INPUT_CAP_TOUCHPAD));
 uint8_t xy[2]={0};bad_decode(9,xy,2,LISM_INPUT_ERR_UNSUPPORTED);
 /* Classification alone must not create any decodable mouse/keyboard data. */
 assert(!lism_input_parse(digitizer,sizeof(digitizer),&d));
 assert(d.capabilities&LISM_INPUT_CAP_TOUCHPAD);
 assert(!(d.capabilities&(LISM_INPUT_CAP_MOUSE|LISM_INPUT_CAP_KEYBOARD|LISM_INPUT_CAP_CONSUMER)));
 assert(d.run_count==0 && d.reports[0].updates==0);
 bad_decode(9,xy,2,LISM_INPUT_ERR_UNSUPPORTED);
 /* Touch Screen is not Touch Pad, even though both are digitizers. */
 uint8_t screen[sizeof(digitizer)];memcpy(screen,digitizer,sizeof(screen));screen[3]=4;
 assert(!lism_input_parse(screen,sizeof(screen),&d));assert(!(d.capabilities&LISM_INPUT_CAP_TOUCHPAD));
}
int main(void){composite();malformed_and_fuzz();consumer_bounds_and_metadata();puts("generic HID parser: PASS");}
'''
with tempfile.TemporaryDirectory(prefix="lism-input-parser-") as temp:
    harness = Path(temp) / "test.c"
    harness.write_text(source)
    binary = Path(temp) / "test"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-g",
                    "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-I", str(root / "src"), str(harness),
                    str(root / "src/lism_input_parser.c"), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
