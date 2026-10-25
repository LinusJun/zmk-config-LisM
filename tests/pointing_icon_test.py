"""Execute actual status icon predicate, independent from bond persistence."""
from pathlib import Path
import sys,re,tempfile,subprocess
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
strip=lambda s:re.sub(r'^\s*#(?:include|pragma).*$', '',s,flags=re.M)
pre=r'''
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>
#include <stdio.h>
#define CONFIG_LISM_BLE_INPUT_BRIDGE 1
#define LV_UNUSED(x) ((void)(x))
typedef int lv_timer_t;typedef unsigned lv_obj_t;
typedef uint8_t bt_addr_le_t;
'''+strip((P/'src/lism_hid_host.h').read_text())+strip((P/'src/lism_input_host.h').read_text())+strip((P/'src/kb_route/icon_input.h').read_text())+r'''
struct kb_route_view{uint8_t mode;};
static struct lism_input_status fixture;
static lv_obj_t node,*icons=&node,*signal_icons=&node;
static uint8_t shown,bars[4];static bool pad_ready,joystick_ready;
static void kb_route_snapshot(struct kb_route_view*v){v->mode=0;}
void lism_input_host_snapshot(struct lism_input_status*v){*v=fixture;}
static void lv_obj_invalidate(lv_obj_t*o){assert(o);}
static bool kb_esb_signal(unsigned i,int8_t*d){(void)i;(void)d;return false;}
static bool lism_input_host_slot_signal(unsigned slot,int8_t*d){*d=slot?-82:-50;return fixture.enabled && fixture.peers[slot].connected && fixture.peers[slot].ready;}
static uint8_t kb_signal_bars(bool v,int8_t d){(void)d;return !v?0:d>=-55?4:d>=-67?3:d>=-78?2:1;}
'''
code=pre+function((P/'src/kb_route/icons.c').read_text(),'refresh')+r'''
int main(void){
 fixture.enabled=true;fixture.peers[0].connected=fixture.peers[0].ready=true;
 fixture.peers[0].saved=false;fixture.peers[0].type=LISM_INPUT_DEVICE_MOUSE;refresh(NULL);assert(pad_ready);
 fixture.peers[0].type=LISM_INPUT_DEVICE_TOUCHPAD;refresh(NULL);assert(pad_ready);
 fixture.peers[0].type=LISM_INPUT_DEVICE_KEYBOARD;refresh(NULL);assert(!pad_ready);
 fixture.peers[0].type=LISM_INPUT_DEVICE_MOUSE;fixture.peers[0].ready=false;refresh(NULL);assert(!pad_ready);
 fixture.peers[0].ready=true;fixture.peers[0].connected=false;refresh(NULL);assert(!pad_ready);
 fixture.peers[0].connected=true;fixture.enabled=false;refresh(NULL);assert(!pad_ready);
 fixture.enabled=true;fixture.peers[0].connected=false;fixture.peers[1]=fixture.peers[0];fixture.peers[1].connected=true;refresh(NULL);assert(pad_ready);
 strcpy(fixture.peers[0].name,"JP19");fixture.peers[0].connected=fixture.peers[0].ready=true;fixture.peers[0].type=LISM_INPUT_DEVICE_MOUSE;
 strcpy(fixture.peers[1].name,"CheerPod2");fixture.peers[1].connected=fixture.peers[1].ready=true;fixture.peers[1].type=LISM_INPUT_DEVICE_MOUSE;
 refresh(NULL);assert(joystick_ready && pad_ready && bars[2]==1 && bars[3]==4);
 fixture.peers[0].type=LISM_INPUT_DEVICE_UNKNOWN;refresh(NULL);assert(!joystick_ready && pad_ready && bars[3]==0);
 fixture.peers[0].type=LISM_INPUT_DEVICE_MOUSE;strcpy(fixture.peers[0].name,"CirclePad");fixture.peers[1].connected=false;refresh(NULL);assert(joystick_ready && !pad_ready && bars[2]==0 && bars[3]==4);
 fixture.peers[0].ready=false;refresh(NULL);assert(!joystick_ready && !pad_ready && !bars[3]);
 puts("PASS actual 4 icons and RSSI partition: ready JP19/CirclePad -> J, CheerPod -> BT; unknown/notready excluded");
 puts("PASS actual pointing icon: ready connected mouse/touchpad, RAM-only identity, either slot; keyboard/off/not-ready/disconnected remain dim");
}
'''
with tempfile.TemporaryDirectory() as d:
 f=Path(d)/'icon.c';o=Path(d)/'icon';f.write_text(code)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True)
 subprocess.run([str(o)],check=True)
