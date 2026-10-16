"""Compile actual bridge input functions with real HID parser and owned-state mocks."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
runtime = (root / "src/lism_hid_host.c").read_text()


def extract(signature):
    start = runtime.index(signature)
    return runtime[start:runtime.index("\n}\n", start) + 3]


source = "\n".join(extract(signature) for signature in (
    "static void schedule_release(void)",
    "static bool retry_release(void)",
    "static void release_owned(void)",
    "static int16_t clamp_axis(int32_t value, int32_t limit)",
    "static bool frame_current(",
    "static void bridge_input(struct input_event *event, void *unused)"))
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "lism_hid_parser.h"
#define ARG_UNUSED(x) (void)(x)
#define BIT(x) (1U<<(x))
#define BIT_MASK(x) (BIT(x)-1)
#define CLAMP(x,lo,hi) ((x)<(lo)?(lo):((x)>(hi)?(hi):(x)))
#define ZMK_HID_MOUSE_NUM_BUTTONS 5
#define INPUT_EV_MSC 4
#define INPUT_KICK 0x7ffe
#define K_NO_WAIT 0
#define K_FOREVER -1
struct input_event {unsigned type,code;};
typedef int atomic_t;
static int atomic_set(atomic_t *a,int v){int old=*a;*a=v;return old;}
static int atomic_get(atomic_t *a){return *a;}
static void atomic_clear(atomic_t *a){*a=0;}
struct raw_frame {uint32_t epoch,endpoint_generation;int endpoint;uint8_t id,len;uint8_t bytes[LISM_HID_MAX_REPORT_BYTES];};
static struct lism_hid_descriptor descriptor;
static struct lism_hid_state decoded;
static atomic_t epoch,input_ready,input_fault,clear_input;
static atomic_t pending_release,endpoint_generation,endpoint_reset_generation;
static uint8_t own_buttons;
static uint16_t button_reports_seen;
static int input_endpoint=-1;
static int release_destination=-1;
static uint32_t input_generation,input_reset_generation,release_reset_generation;
static bool require_release=true;
static int report_lock,control_work,destination=10,submitted;
static struct {struct raw_frame data[16];unsigned read,count;} frames;
static int lock_depth;
static void k_mutex_lock(int *lock,int timeout){assert(lock==&report_lock&&timeout==-1);assert(!lock_depth++);}
static void k_mutex_unlock(int *lock){assert(lock==&report_lock&&lock_depth==1);lock_depth--;}
static int k_msgq_get(void *queue,void *out,int timeout){
    assert(queue==&frames&&timeout==0);if(!frames.count)return -1;
    memcpy(out,&frames.data[frames.read++%16],sizeof(struct raw_frame));frames.count--;return 0;
}
static void k_msgq_purge(void *queue){assert(queue==&frames);frames.count=0;frames.read=0;}
static int xiao147_pointer_endpoint(void){return destination;}
static int k_work_submit(int *work){assert(work==&control_work);submitted++;return 0;}
static int buttons[5],presses[5],releases[5],dx,dy,pan,wheel,fail_next;
static int decode_inject,send_inject;
struct sent_report {int endpoint,buttons[5],dx,dy,pan,wheel,result;};
static struct sent_report sent[256];
static unsigned sent_count;
static void zmk_hid_mouse_button_press(int i){assert(lock_depth==1);buttons[i]++;presses[i]++;}
static void zmk_hid_mouse_button_release(int i){assert(lock_depth==1&&buttons[i]>0);buttons[i]--;releases[i]++;}
static void zmk_hid_mouse_movement_set(int x,int y){assert(lock_depth==1);dx=x;dy=y;}
static void zmk_hid_mouse_scroll_set(int p,int w){assert(lock_depth==1);pan=p;wheel=w;}
static void inject_transition(int mode){
    if(mode==1){epoch++;input_ready=0;clear_input=1;}
    if(mode==2){destination++;endpoint_generation++;endpoint_reset_generation++;clear_input=1;}
    if(mode==3){endpoint_generation++;clear_input=1;}
}
static int zmk_endpoint_send_mouse_report(void){
    assert(lock_depth==1&&sent_count<256);
    struct sent_report *s=&sent[sent_count++];s->endpoint=destination;
    memcpy(s->buttons,buttons,sizeof(buttons));s->dx=dx;s->dy=dy;s->pan=pan;s->wheel=wheel;
    s->result=fail_next?-5:0;if(fail_next)fail_next--;
    if(send_inject){int mode=send_inject;send_inject=0;inject_transition(mode);}
    return s->result;
}
static enum lism_hid_error instrumented_decode(const struct lism_hid_descriptor *d,
    struct lism_hid_state *s,uint8_t id,const uint8_t *payload,size_t length,struct lism_hid_event *out){
    enum lism_hid_error error=lism_hid_decode(d,s,id,payload,length,out);
    if(decode_inject){int mode=decode_inject;decode_inject=0;inject_transition(mode);}
    return error;
}
#define lism_hid_decode instrumented_decode
'''
checks = r'''
/* Descriptor is parsed, not a hand-populated decoder shortcut. HOGP payload
 * excludes an implicit Report ID byte. Wheel and AC Pan have independent axes. */
static const uint8_t mouse_map[]={
  0x05,0x01,0x09,0x02,0xa1,0x01,0x09,0x01,0xa1,0x00,
  0x05,0x09,0x19,0x01,0x29,0x03,0x15,0x00,0x25,0x01,
  0x75,0x01,0x95,0x03,0x81,0x02,0x75,0x05,0x95,0x01,0x81,0x01,
  0x05,0x01,0x09,0x30,0x09,0x31,0x09,0x38,0x15,0x81,0x25,0x7f,
  0x75,0x08,0x95,0x03,0x81,0x06,
  0x05,0x0c,0x0a,0x38,0x02,0x95,0x01,0x81,0x06,0xc0,0xc0};
static const uint8_t hires_map[]={
  0x05,0x01,0x09,0x02,0xa1,0x01,0x09,0x01,0xa1,0x00,
  0x05,0x09,0x19,0x01,0x29,0x03,0x15,0x00,0x25,0x01,
  0x75,0x01,0x95,0x03,0x81,0x02,0x75,0x05,0x95,0x01,0x81,0x01,
  0x05,0x01,0x09,0x30,0x09,0x31,0x09,0x38,0x16,0x00,0x80,0x26,0xff,0x7f,
  0x75,0x10,0x95,0x03,0x81,0x06,
  0x05,0x0c,0x0a,0x38,0x02,0x95,0x01,0x81,0x06,0xc0,0xc0};
static const uint8_t multi_map[]={
  0x05,0x01,0x09,0x02,0xa1,0x01,
  0x85,0x01,0x05,0x09,0x09,0x01,0x15,0x00,0x25,0x01,
  0x75,0x01,0x95,0x01,0x81,0x02,0x75,0x07,0x81,0x01,
  0x85,0x02,0x09,0x02,0x75,0x01,0x81,0x02,0x75,0x07,0x81,0x01,
  0x85,0x03,0x05,0x01,0x09,0x30,0x09,0x31,0x15,0x81,0x25,0x7f,
  0x75,0x08,0x95,0x02,0x81,0x06,0xc0};
static void frame_at(uint32_t gen,int target,int b,int x,int y,int w,int p,unsigned len){
  assert(frames.count<16);
  struct raw_frame *f=&frames.data[(frames.read+frames.count++)%16];
  *f=(struct raw_frame){.epoch=gen,.endpoint_generation=endpoint_generation,.endpoint=target,.id=0,.len=len,
    .bytes={(uint8_t)b,(uint8_t)x,(uint8_t)y,(uint8_t)w,(uint8_t)p}};
}
static void enqueue(int b,int x,int y,int w,int p){frame_at(epoch,destination,b,x,y,w,p,5);}
static void idframe(unsigned id,int first,int second){
  frame_at(epoch,destination,first,second,0,0,0,id==3?2:1);
  frames.data[(frames.read+frames.count-1)%16].id=id;
}
static void pump(void){struct input_event event={INPUT_EV_MSC,INPUT_KICK};bridge_input(&event,NULL);assert(!lock_depth);}
static void zero_motion(void){assert(!dx&&!dy&&!wheel&&!pan);}
static void reset(void){
  memset(&decoded,0,sizeof(decoded));memset(buttons,0,sizeof(buttons));
  memset(presses,0,sizeof(presses));memset(releases,0,sizeof(releases));
  memset(&frames,0,sizeof(frames));memset(sent,0,sizeof(sent));sent_count=0;
  epoch=7;input_ready=1;input_fault=clear_input=0;own_buttons=0;button_reports_seen=0;
  destination=10;input_endpoint=10;require_release=true;submitted=0;fail_next=0;
  pending_release=endpoint_generation=endpoint_reset_generation=0;
  input_generation=input_reset_generation=release_reset_generation=0;release_destination=-1;
  decode_inject=send_inject=0;
  dx=dy=pan=wheel=0;
  assert(lism_hid_parse(mouse_map,sizeof(mouse_map),&descriptor)==LISM_HID_OK);
}
int main(void){
  assert(clamp_axis(40000,32767)==32767&&clamp_axis(-40000,32767)==-32767);
  assert(clamp_axis(200,32767)==200&&clamp_axis(-200,32767)==-200);
  assert(clamp_axis(12,32767)==12&&clamp_axis(-12,32767)==-12);
  reset();
  struct input_event unrelated={0,INPUT_KICK};bridge_input(&unrelated,NULL);
  assert(!sent_count&&!lock_depth);
  unrelated=(struct input_event){INPUT_EV_MSC,0};bridge_input(&unrelated,NULL);assert(!sent_count);

  /* Require an actual all-up report on initial connection. */
  enqueue(1,20,30,2,3);pump();assert(require_release&&!own_buttons&&!sent_count);
  enqueue(0,0,0,0,0);pump();assert(!require_release);
  enqueue(1,4,-5,-6,7);pump();assert(own_buttons==1&&buttons[0]==1&&presses[0]==1);
  struct sent_report *s=&sent[sent_count-1];
  assert(s->dx==4&&s->dy==-5&&s->wheel==-6&&s->pan==7);zero_motion();
  enqueue(1,0,0,0,0);enqueue(1,0,0,0,0);pump();assert(presses[0]==1&&!releases[0]);
  enqueue(0,0,0,9,-11);pump();assert(!own_buttons&&!buttons[0]&&releases[0]==1);
  s=&sent[sent_count-1];assert(s->wheel==9&&s->pan==-11);zero_motion();

  /* Two unrelated input sources hold the same button. A bridge transition or
   * disconnect releases exactly its own reference, never either other owner. */
  buttons[0]=2;enqueue(1,0,0,0,0);pump();assert(buttons[0]==3);
  enqueue(0,0,0,0,0);pump();assert(buttons[0]==2);
  enqueue(1,0,0,0,0);pump();input_ready=0;clear_input=1;pump();
  assert(buttons[0]==2&&!own_buttons&&require_release&&!clear_input);zero_motion();

  reset();enqueue(0,0,0,0,0);enqueue(1,0,0,0,0);pump();
  /* Destination transition cancels bridge ownership and suppresses held
   * packets until all-up; queued old-destination packets cannot leak. */
  enqueue(1,70,70,70,70);destination=11;enqueue(1,80,80,80,80);pump();
  assert(input_endpoint==11&&!own_buttons&&!buttons[0]&&require_release);
  unsigned before=sent_count;
  enqueue(1,80,80,80,80);pump();assert(sent_count==before);
  enqueue(0,0,0,0,0);enqueue(1,-9,12,13,-14);pump();assert(own_buttons==1);
  s=&sent[sent_count-1];assert(s->endpoint==11&&s->dx==-9&&s->dy==12&&s->wheel==13&&s->pan==-14);
  before=sent_count;frame_at(epoch-1,11,0,77,77,77,77,5);
  frame_at(epoch,10,0,77,77,77,77,5);pump();assert(sent_count==before&&own_buttons==1);
  destination=-1;pump();assert(!own_buttons&&!buttons[0]&&require_release);
  before=sent_count;enqueue(1,99,99,99,99);pump();assert(sent_count==before);

  /* Malformed report fails closed, clears only this source, schedules control
   * and stops consuming the backlog until the session is repaired. */
  reset();buttons[0]=2;enqueue(0,0,0,0,0);enqueue(1,0,0,0,0);pump();
  frame_at(epoch,destination,0,0,0,0,0,4);enqueue(1,99,99,99,99);pump();
  assert(input_fault&&!input_ready&&submitted==1&&!own_buttons&&buttons[0]==2&&frames.count==1);
  before=sent_count;pump();assert(!frames.count&&sent_count==before);zero_motion();

  /* Endpoint output failure during a backlog never dispatches later deltas
   * or leaves bridge-held buttons behind. */
  reset();buttons[0]=2;enqueue(0,0,0,0,0);pump();before=sent_count;
  fail_next=1;enqueue(1,3,4,5,6);enqueue(1,88,88,88,88);pump();
  assert(input_fault&&!input_ready&&submitted==1&&!own_buttons&&buttons[0]==2);
  assert(sent_count==before+2&&sent[before].result<0&&frames.count==1);
  assert(!sent[before+1].dx&&!sent[before+1].dy&&!sent[before+1].pan&&!sent[before+1].wheel);
  before=sent_count;pump();assert(!frames.count&&sent_count==before);zero_motion();
  /* A full legal backlog preserves all relative reports in order. */
  reset();enqueue(0,0,0,0,0);pump();before=sent_count;
  for(int i=0;i<16;i++)enqueue(0,i,-i,i-8,8-i);
  pump();assert(!frames.count&&sent_count==before+16);
  for(int i=0;i<16;i++){
    s=&sent[before+i];assert(s->dx==i&&s->dy==-i&&s->wheel==i-8&&s->pan==8-i);
  }
  zero_motion();
  /* High-resolution wheel/pan must not be truncated to an 8-bit wheel even
   * though ordinary low-resolution mouse reports happen to use 8-bit fields. */
  reset();assert(lism_hid_parse(hires_map,sizeof(hires_map),&descriptor)==LISM_HID_OK);
  frame_at(epoch,destination,0,0,0,0,0,9);
  memset(frames.data[0].bytes,0,9);pump();
  frame_at(epoch,destination,0,0,0,0,0,9);
  const uint8_t high_values[]={0,0xd2,0x04,0x2e,0xfb,0x2c,0x01,0x70,0xfe};
  memcpy(frames.data[1].bytes,high_values,sizeof(high_values));pump();
  s=&sent[sent_count-1];assert(s->dx==1234&&s->dy==-1234&&s->wheel==300&&s->pan==-400);
  frame_at(epoch,destination,0,0,0,0,0,9);
  const uint8_t negative_limit[]={0,0,0,0,0,0,0x80,0xff,0x7f};
  memcpy(frames.data[2].bytes,negative_limit,sizeof(negative_limit));pump();
  s=&sent[sent_count-1];assert(s->wheel==-32767&&s->pan==32767);zero_motion();
  /* Axis-only reports must not imply unseen button report IDs are released.
   * Neither a held startup report nor partial all-up observation may create
   * phantom clicks or release the gate. Every declared button ID is required. */
  reset();assert(lism_hid_parse(multi_map,sizeof(multi_map),&descriptor)==LISM_HID_OK);
  idframe(3,7,8);pump();assert(require_release&&!button_reports_seen&&!sent_count);
  idframe(1,1,0);pump();assert(require_release&&!own_buttons&&!sent_count);
  idframe(2,0,0);pump();assert(require_release&&!own_buttons&&!sent_count);
  idframe(3,9,10);pump();assert(require_release&&!own_buttons&&!sent_count);
  idframe(1,0,0);pump();assert(!require_release&&sent_count==1&&!own_buttons);
  idframe(1,1,0);pump();assert(own_buttons==1&&buttons[0]==1&&presses[0]==1);
  /* A profile transition may retain the same numeric destination index. Its
   * clear latch must purge queued old all-up/press pairs before rearming. */
  before=sent_count;idframe(1,0,0);idframe(2,0,0);idframe(1,1,0);clear_input=1;pump();
  assert(!frames.count&&sent_count==before+1&&!own_buttons&&!buttons[0]);
  assert(require_release&&!button_reports_seen&&presses[0]==1);
  idframe(3,12,13);idframe(1,0,0);pump();assert(require_release&&!own_buttons);
  idframe(2,0,0);pump();assert(!require_release&&!own_buttons);
  idframe(1,1,0);pump();assert(own_buttons==1&&presses[0]==2);
  /* A host may have accepted the press but reject both the normal all-up
   * packet and its neutral recovery packet. Logical references are released
   * once; neutral retries remain independent of own_buttons becoming zero. */
  reset();buttons[0]=2;enqueue(0,0,0,0,0);enqueue(1,0,0,0,0);pump();
  assert(buttons[0]==3&&presses[0]==1);before=sent_count;
  fail_next=20;enqueue(0,0,0,0,0);enqueue(1,99,99,99,99);pump();
  assert(pending_release&&!own_buttons&&buttons[0]==2&&releases[0]==1);
  assert(sent_count>=before+2&&sent[before].result<0&&sent[before+1].result<0);
  /* An unrelated transport event invalidates frames, not an owed release:
   * only the separate confirmed-reset generation may cancel that obligation. */
  endpoint_generation++;clear_input=1;
  input_ready=1;enqueue(1,88,88,88,88);before=sent_count;pump();
  assert(pending_release&&!own_buttons&&buttons[0]==2&&releases[0]==1&&presses[0]==1);
  for(unsigned i=before;i<sent_count;i++)assert(!sent[i].dx&&!sent[i].dy&&!sent[i].pan&&!sent[i].wheel);
  fail_next=0;pump();assert(!pending_release&&!own_buttons&&releases[0]==1&&presses[0]==1);
  enqueue(0,0,0,0,0);pump();enqueue(1,0,0,0,0);pump();
  assert(own_buttons==1&&buttons[0]==3&&presses[0]==2);
  /* Use the real decoder, then invalidate its session between decode and
   * commit. No pending relative deltas or stale presses may reach a new host,
   * including a host transition which keeps the same endpoint index. */
  for(int mode=1;mode<=3;mode++){
    reset();buttons[0]=2;enqueue(0,0,0,0,0);enqueue(1,0,0,0,0);pump();
    before=sent_count;decode_inject=mode;
    enqueue(1,30,31,32,33);enqueue(1,40,41,42,43);pump();
    assert(!frames.count&&!own_buttons&&buttons[0]==2&&releases[0]==1);
    for(unsigned i=before;i<sent_count;i++)assert(!sent[i].dx&&!sent[i].dy&&!sent[i].pan&&!sent[i].wheel);
    pump();assert(!own_buttons&&presses[0]==1);zero_motion();
  }
  /* A transition while the first queued report is submitted must suppress all
   * later reports, rather than reuse the entry-time destination snapshot. */
  for(int mode=1;mode<=3;mode++){
    reset();enqueue(0,0,0,0,0);pump();before=sent_count;
    send_inject=mode;enqueue(1,10,11,12,13);enqueue(1,20,21,22,23);pump();
    assert(!frames.count&&!own_buttons&&!buttons[0]&&presses[0]==1&&releases[0]==1);
    assert(sent[before].endpoint==10&&sent[before].dx==10&&sent[before].dy==11);
    for(unsigned i=before+1;i<sent_count;i++)assert(!sent[i].dx&&!sent[i].dy&&!sent[i].pan&&!sent[i].wheel);
    pump();assert(presses[0]==1);zero_motion();
  }
  puts("PASS: actual bridge input: owned buttons, all-report all-up gate, pending release retries, decode/send transition guards, same-endpoint purge, faults, 16-frame backlog and high-resolution wheel/pan");
  return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="lism-hid-input-") as directory:
    c = Path(directory) / "test.c"
    executable = Path(directory) / "test"
    c.write_text(prelude + source + checks)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-I", str(root / "src"),
                    str(c), str(root / "src/lism_hid_parser.c"), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
