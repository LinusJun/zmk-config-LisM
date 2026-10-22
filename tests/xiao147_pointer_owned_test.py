"""Actual touch coordinator + actual source merger, with modeled endpoint I/O."""
from pathlib import Path
import re,tempfile,subprocess
P=Path(__file__).resolve().parents[1]
def strip(p):return re.sub(r'^\s*#\s*(?:include|pragma once).*$', '',p.read_text(),flags=re.M)
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define CONFIG_LISM_XIAO147_TOUCH 1
#define CONFIG_ZMK_USB_BOOT 0
#define CONFIG_ZMK_HID_REPORT_TYPE_HKRO 1
#define CONFIG_ZMK_HID_CONSUMER_REPORT_USAGES_BASIC 0
#define CONFIG_ZMK_POINTING 1
#define IS_ENABLED(x) (x)
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define BIT(n) (1U<<(n))
#define ARG_UNUSED(x) (void)(x)
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
#define POST_KERNEL 0
#define APPLICATION 0
#define CONFIG_INPUT_INIT_PRIORITY 0
#define CONFIG_APPLICATION_INIT_PRIORITY 0
#define INPUT_EV_MSC 4
#define INPUT_BTN_0 256
#define INPUT_BTN_4 260
#define INPUT_REL_X 0
#define INPUT_REL_Y 1
#define INPUT_REL_HWHEEL 6
#define INPUT_REL_WHEEL 8
#define XIAO147_PAGE_STATUS 0
struct device{int x;};typedef int k_timeout_t;
#define DEVICE_DEFINE(n,...) static struct device n
#define DEVICE_GET(n) (&n)
#define INPUT_CALLBACK_DEFINE(...)
#define SYS_INIT(...)
struct input_event{int type,code;};struct k_work{int x;};struct k_work_delayable{int x;};
#define K_WORK_DELAYABLE_DEFINE(n,f) struct k_work_delayable n
static unsigned schedules,kicks;static int64_t now=1000;
static int k_work_reschedule(struct k_work_delayable*w,int timeout){(void)w;assert(timeout>=0);schedules++;return 0;}
static int input_report(const struct device*d,int type,int code,int v,bool sync,int timeout){(void)d;(void)v;assert(type==4&&code==0x7ffb&&sync&&timeout==0);kicks++;return 0;}
static int64_t k_uptime_get(void){return now;}
struct k_spinlock{int x;};typedef int k_spinlock_key_t;
static int k_spin_lock(struct k_spinlock*l){assert(!l->x);l->x=1;return 0;}
static void k_spin_unlock(struct k_spinlock*l,int k){(void)k;assert(l->x);l->x=0;}
struct queue{unsigned head,count,size,capacity;uint8_t data[2048];};
#define K_MSGQ_DEFINE(n,s,c,a) struct queue n={.size=(s),.capacity=(c)}
static int k_msgq_put(struct queue*q,const void*p,int timeout){assert(timeout==0);if(q->count==q->capacity)return -EAGAIN;memcpy(q->data+((q->head+q->count)%q->capacity)*q->size,p,q->size);q->count++;return 0;}
static int k_msgq_get(struct queue*q,void*p,int timeout){assert(timeout==0);if(!q->count)return -ENOMSG;memcpy(p,q->data+q->head*q->size,q->size);q->head=(q->head+1)%q->capacity;q->count--;return 0;}
static void k_msgq_purge(struct queue*q){q->count=q->head=0;}
struct zmk_hid_keyboard_report{struct{uint8_t modifiers,keys[6];}body;};
struct zmk_hid_consumer_report{struct{uint16_t keys[6];}body;};
struct zmk_hid_mouse_report{struct{uint8_t buttons;union{int16_t dx;int16_t d_x;};union{int16_t dy;int16_t d_y;};union{int16_t pan;int16_t d_scroll_x;};union{int16_t wheel;int16_t d_scroll_y;};}body;};
static bool awake=true,page_status=true,usb_ready=true;static uint32_t ui_generation;static uint32_t xiao147_ui_pointer_generation(void){return ui_generation;}static int host=0,send_error;static unsigned fence,sent,presses;
static struct zmk_hid_mouse_report physical,last;
static int xiao147_ui_page(void){return page_status?0:1;}
static bool xiao147_ui_is_sleeping(void){return !awake;}
static int xiao147_pointer_endpoint(void){return host;}
static bool zmk_usb_is_hid_ready(void){return usb_ready;}
static void lism_input_endpoint_transition_begin(void){assert(!fence);fence=1;}
static void lism_input_endpoint_transition_end(void){assert(fence);fence=0;}
static void lism_input_usb_bridge(bool on){(void)on;assert(fence);}
static void zmk_hid_mouse_movement_set(int16_t x,int16_t y){assert(fence);physical.body.dx=x;physical.body.dy=y;}
static void zmk_hid_mouse_scroll_set(int16_t p,int16_t w){assert(fence);physical.body.pan=p;physical.body.wheel=w;}
void lism_input_merge_mouse(struct zmk_hid_mouse_report*);
static int zmk_endpoint_send_mouse_report(void){assert(fence);if(send_error)return send_error;last=physical;lism_input_merge_mouse(&last);sent++;if(last.body.buttons)presses++;return 0;}
'''
merge_code=strip(P/'src/lism_input_merge.c').replace('__attribute__((weak)) bool lism_input_merge_local_allowed(void){return true;}','bool lism_input_merge_local_allowed(void){return kb_route_dongle_key_allowed();}')
code=pre+strip(P/'src/kb_route/view.h')+r'''
static struct kb_route_view route={.mode=KB_ROUTE_DONGLE,.last_mode=KB_ROUTE_DONGLE,.epoch=1};
void kb_route_snapshot(struct kb_route_view*v){*v=route;}
static uint32_t local_generation=1;
uint32_t kb_receiver_route_generation(void){return local_generation;}
static bool kb_route_dongle_key_allowed(void){return route.mode==KB_ROUTE_DONGLE;}
'''+strip(P/'src/lism_input_merge.h')+merge_code+strip(P/'src/xiao147_pointer.c')+r'''
static void pump(void){struct input_event e={.type=INPUT_EV_MSC,.code=TOUCH_KICK};process(&e,NULL);assert(!fence);}
static void reset_case(void){send_error=0;route.mode=0;route.epoch++;host=0;awake=page_status=usb_ready=true;memset(&physical,0,sizeof(physical));lism_input_merge_endpoint_reset();pump();}
static int key(bool down){return xiao147_touch_key(NULL,INPUT_BTN_0,down,true,0);}
int main(void){
 /* First authoritative new-context frame is retained, not purged on handoff. */
 assert(!key(true));pump();assert(last.body.buttons==1);assert(!key(false));pump();assert(!last.body.buttons);
 reset_case();struct lism_input_merge_state peer={.buttons=1};assert(!lism_input_merge_set(0,lism_input_merge_generation(),&peer));physical.body.buttons=1;
 assert(!key(true));assert(!key(false));pump();assert(last.body.buttons==1);
 assert(!lism_input_merge_clear(0,lism_input_merge_generation()));struct zmk_hid_mouse_report copy=physical;lism_input_merge_mouse(&copy);assert(copy.body.buttons==1);physical.body.buttons=0;copy=physical;lism_input_merge_mouse(&copy);assert(!copy.body.buttons);
 /* Expired release must neutralize; old queued button snapshots never re-press. */
 reset_case();assert(!key(true));pump();assert(last.body.buttons==1);assert(!key(false));assert(!xiao147_touch_rel(NULL,INPUT_REL_X,12,true,0));now+=300;pump();assert(!last.body.buttons);assert(!xiao147_touch_rel(NULL,INPUT_REL_X,3,true,0));pump();assert(!last.body.buttons&&last.body.dx==3);
 /* Delayed unsent press is canceled, not replayed after host backpressure. */
 reset_case();unsigned prior=presses;assert(!key(true));now+=151;pump();assert(presses==prior&&!last.body.buttons);assert(!xiao147_touch_rel(NULL,INPUT_REL_Y,2,true,0));pump();assert(!last.body.buttons);
 /* Another sender can expose a merged press while this source send is busy. */
 reset_case();assert(!key(true));send_error=-EAGAIN;pump();copy=physical;lism_input_merge_mouse(&copy);assert(copy.body.buttons==1);
 route.mode=KB_ROUTE_BLE;route.epoch++;pump();copy=physical;lism_input_merge_mouse(&copy);assert(!copy.body.buttons);
 send_error=0;pump();assert(!last.body.buttons);assert(key(true)==-ECANCELED);assert(!key(false));
 route.mode=KB_ROUTE_DONGLE;route.epoch++;assert(!key(true));pump();assert(last.body.buttons==1);assert(!key(false));pump();
 /* Sleep, page, endpoint and merger epoch all fence queued input. */
 for(unsigned change=0;change<4;change++){
  reset_case();assert(!key(true));pump();assert(last.body.buttons==1);assert(!xiao147_touch_rel(NULL,INPUT_REL_X,100,true,0));
  int before=xiao147_touch_endpoint();if(change==0)awake=false;else if(change==1)page_status=false;else if(change==2)host=2;else lism_input_merge_endpoint_reset();
  assert(xiao147_touch_endpoint()!=before);pump();copy=physical;lism_input_merge_mouse(&copy);assert(!copy.body.buttons);assert(!physical.body.dx);
 }
 /* A complete page/sleep round trip while consumer is stalled must invalidate old input. */
 for(unsigned transition=0;transition<2;transition++){
  reset_case();assert(!key(true));pump();assert(last.body.buttons==1);
  assert(!key(false));assert(!key(true));assert(!xiao147_touch_rel(NULL,INPUT_REL_X,99,true,0));
  unsigned before=presses;
  if(transition==0){page_status=false;ui_generation++;page_status=true;ui_generation++;}
  else {awake=false;ui_generation++;awake=true;ui_generation++;}
  pump();assert(presses==before&&!last.body.buttons&&!last.body.dx);
  assert(!key(true));pump();assert(last.body.buttons==1);assert(!key(false));pump();assert(!last.body.buttons);
 }
 /* An independent third-party frame must not revive stale touch while this consumer is paused. */
 for(unsigned change=0;change<4;change++){
  reset_case();physical.body.buttons=2;struct lism_input_merge_state external={.buttons=4};
  assert(!lism_input_merge_set(0,lism_input_merge_generation(),&external));assert(!key(true));pump();assert(last.body.buttons==7);
  if(change==0)local_generation++; /* same wire epoch after authority change, still DG */
  else if(change==1){page_status=false;ui_generation++;page_status=true;ui_generation++;}
  else if(change==2){route.mode=KB_ROUTE_BLE;local_generation++;route.mode=KB_ROUTE_DONGLE;local_generation++;}
  else {awake=false;ui_generation++;awake=true;ui_generation++;}
  copy=physical;lism_input_merge_mouse(&copy);assert(copy.body.buttons==6 && physical.body.buttons==2);
  pump();copy=physical;lism_input_merge_mouse(&copy);assert(copy.body.buttons==6);
 }
 /* A right reboot may repeat wire epoch. Local generation still invalidates old touch. */
 reset_case();assert(!key(true));pump();assert(!key(false));assert(!key(true));assert(!xiao147_touch_rel(NULL,INPUT_REL_X,99,true,0));
 unsigned before_reboot=presses;local_generation++;pump();assert(presses==before_reboot&&!last.body.buttons&&!last.body.dx);
 assert(!key(true));pump();assert(last.body.buttons==1);assert(!key(false));pump();
 reset_case();unsigned admitted=0;while(!xiao147_touch_rel(NULL,INPUT_REL_X,1,true,0))admitted++;assert(admitted==16);pump();pump();
 puts("PASS actual touch coordinator + merger: three-source overlapping buttons, expired release/press and old-snapshot cancellation, busy neutral retry, first new frame, mode/page/sleep/endpoint fences and bounded queue");return 0;}
'''
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'pointer.c';exe=Path(t)/'pointer';f.write_text(code);subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',str(f),'-o',str(exe)],check=True);subprocess.run([str(exe)],check=True)
