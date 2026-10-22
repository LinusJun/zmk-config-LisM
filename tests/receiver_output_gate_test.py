"""Actual generated USB/BLE output functions + actual source-owned merger.
An inactive local macro cannot ride along with third-party input reports.
"""
from pathlib import Path
import ast,sys,tempfile,subprocess
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'));from prepare_hog_routing import function
status=(P/'src/kb_route/status.c').read_text();host=(P/'src/lism_input_host.c').read_text();B=P.parent/'workspace/build-dongle'
usb=next(B.rglob('lism_inputs_usb.c')).read_text();ep=next(B.rglob('lism_endpoints.c')).read_text()
constants=[n.value for n in ast.walk(ast.parse((P/'tests/lism_input_merge_test.py').read_text())) if isinstance(n,ast.Constant) and isinstance(n.value,str)]
hid=next(x for x in constants if 'struct zmk_hid_keyboard_report_body {' in x);kernel=next(x for x in constants if 'static inline int k_spin_lock' in x)
hid=hid.replace('struct zmk_hid_mouse_report {','struct zmk_hid_mouse_report {uint8_t report_id;')
hid=hid.replace('struct {uint8_t buttons;}', 'struct {uint8_t buttons;int16_t d_x,d_y,d_scroll_y,d_scroll_x;}')
pre=r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "lism_input_merge.h"
#define HID_USAGE_KEY 7
#define HID_USAGE_CONSUMER 12
#define CONFIG_ZMK_USB 1
#define CONFIG_ZMK_BLE 1
#define CONFIG_ZMK_POINTING 1
#define HID_PROTOCOL_REPORT 1
#define HID_PROTOCOL_BOOT 0
#define LOG_ERR(...) ((void)0)
#define LOG_DBG(...) ((void)0)
static unsigned hid_protocol=1,sends;static bool routed,bridge;
static bool kb_route_dongle_key_allowed(void){return routed;}
bool lism_input_usb_nonblocking(void){return bridge;}
static struct zmk_hid_keyboard_report local_key={.report_id=1},sent_key;
static struct zmk_hid_consumer_report local_consumer={.report_id=2},sent_consumer;
static struct zmk_hid_mouse_report local_mouse={.report_id=3},sent_mouse;
static int16_t outgoing_pointer[4]={17,-9,2,-3};
static struct zmk_hid_keyboard_report*zmk_hid_get_keyboard_report(void){return &local_key;}
static struct zmk_hid_consumer_report*zmk_hid_get_consumer_report(void){return &local_consumer;}
static struct zmk_hid_mouse_report*zmk_hid_get_mouse_report(void){return &local_mouse;}
static zmk_hid_boot_report_t*zmk_hid_get_boot_report(void){return &local_key.body;}
enum{ZMK_TRANSPORT_NONE,ZMK_TRANSPORT_USB,ZMK_TRANSPORT_BLE};
static struct{unsigned transport;struct{uint8_t profile_index;}ble;}current_instance={1,{4}};
static int zmk_usb_hid_send_report(const uint8_t*p,size_t n,uint32_t epoch){
 assert(epoch==lism_input_merge_generation());sends++;
 if(n==sizeof(zmk_hid_boot_report_t)&&!hid_protocol){memcpy(&sent_key.body,p,n);return 0;}
 switch(*p){case 1:assert(n==sizeof(sent_key));memcpy(&sent_key,p,n);break;case 2:assert(n==sizeof(sent_consumer));memcpy(&sent_consumer,p,n);break;case 3:assert(n==sizeof(sent_mouse));memcpy(&sent_mouse,p,n);break;default:assert(0);}return 0;
}
static int zmk_hog_send_keyboard_report_to_profile(struct zmk_hid_keyboard_report_body*p,uint8_t profile){assert(profile==4);sends++;sent_key.body=*p;return 0;}
static int zmk_hog_send_consumer_report_to_profile(struct zmk_hid_consumer_report_body*p,uint8_t profile){assert(profile==4);sends++;sent_consumer.body=*p;return 0;}
static int zmk_hog_send_mouse_report_to_profile(__typeof__(local_mouse.body)*p,uint8_t profile){assert(profile==4);sends++;sent_mouse.body=*p;return 0;}
'''
un=usb[usb.index('union lism_usb_keyboard_snapshot {'):usb.index('};',usb.index('union lism_usb_keyboard_snapshot {'))+2]
code=pre+'\n'.join(function(status,n) for n in ['lism_input_merge_local_allowed','lism_input_merge_external_frame','neutral','kb_route_receiver_output_allowed'])+function(host,'lism_input_merge_external_mouse')+un+'\n'+'\n'.join(function(usb,n) for n in ['get_keyboard_report','zmk_usb_hid_send_keyboard_report','zmk_usb_hid_send_consumer_report','zmk_usb_hid_send_mouse_report'])+'\n'.join(function(ep,n) for n in ['send_keyboard_report','send_consumer_report','zmk_endpoint_send_report','zmk_endpoint_send_mouse_report'])+r'''
int main(void){
 struct lism_input_merge_state peer={.modifiers=2,.buttons=4,.consumer={0xea},.consumer_count=1};peer.keys[5/8]|=1u<<(5%8);
 assert(!lism_input_merge_set(0,lism_input_merge_generation(),&peer));
 for(unsigned transport=1;transport<=2;transport++){
  current_instance.transport=transport;
  /* An old macro continues mutating local globals after losing its owner. */
  local_key.body.modifiers=0x80;local_key.body.keys[0]=4;
  local_consumer.body.keys[0]=0xe9;local_mouse.body.buttons=1;local_mouse.body.d_x=999;
  bridge=false;unsigned before=sends;
  assert(!zmk_endpoint_send_report(7)&&!zmk_endpoint_send_report(12)&&!zmk_endpoint_send_mouse_report());assert(sends==before);
  bridge=true;
  assert(!zmk_endpoint_send_report(7)&&!zmk_endpoint_send_report(12)&&!zmk_endpoint_send_mouse_report());
  assert(sent_key.body.modifiers==2&&sent_key.body.keys[0]==5&&!sent_key.body.keys[1]);
  assert(sent_consumer.body.keys[0]==0xea&&!sent_consumer.body.keys[1]);
  assert(sent_mouse.body.buttons==4&&sent_mouse.body.d_x==17&&sent_mouse.body.d_y==-9&&sent_mouse.body.d_scroll_x==2&&sent_mouse.body.d_scroll_y==-3);
  assert(local_key.body.modifiers==0x80&&local_key.body.keys[0]==4&&local_consumer.body.keys[0]==0xe9&&local_mouse.body.buttons==1&&local_mouse.body.d_x==999);
 }
 current_instance.transport=1;hid_protocol=0;assert(!zmk_endpoint_send_report(7));assert(sent_key.body.modifiers==2&&sent_key.body.keys[0]==5);
 hid_protocol=1;bridge=false;routed=true;assert(!zmk_endpoint_send_report(7));assert(sent_key.body.modifiers==0x82&&sent_key.body.keys[0]==4&&sent_key.body.keys[1]==5);
 puts("PASS actual USB/report+boot and BLE copy/merge/send paths: stale macro keys/modifiers/consumer/mouse excluded from third-party frames, owned pointer preserved, global HID unchanged, normal Dongle union restored");
}
'''
with tempfile.TemporaryDirectory() as t:
 d=Path(t);(d/'zmk').mkdir();(d/'zephyr').mkdir();(d/'zmk/hid.h').write_text(hid);(d/'zephyr/kernel.h').write_text(kernel)
 f=d/'output.c';o=d/'output';f.write_text(code)
 subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined','-I',str(d),'-I',str(P/'src'),str(f),str(P/'src/lism_input_merge.c'),'-o',str(o)],check=True);subprocess.run([str(o)],check=True)
