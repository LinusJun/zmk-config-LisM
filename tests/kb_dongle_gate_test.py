"""Execute complete production link/common code, including applied-state gate."""
from pathlib import Path
import ast,subprocess,tempfile,re
P=Path(__file__).resolve().parents[1];W=Path('/work/workspace')
tree=ast.parse((P/'tests/upstream_esb_host_test.py').read_text());values={}
for n in tree.body:
 if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str):
  for t in n.targets:
   if isinstance(t,ast.Name):values[t.id]=n.value.value
pre=values['pre']+'\n#include "/work/source/src/kb_route/stations.h"\n'+r'''

#define CONFIG_LISM_BLE_INPUT_BRIDGE 1
#define MAX(a,b) ((a)>(b)?(a):(b))
#define KB_ROUTE_EVENT 126
#define KB_MIRROR_EVENT 125
#define KB_ROUTE_VERSION 3
#define CONFIG_LISM_DONGLE_STATION 0
#define KB_ROUTE_DONGLE 0
#define KB_ROUTE_BLE 1
#define KB_ROUTE_SWITCHING 2
struct kb_route_status{uint8_t version,mode,profile;uint32_t epoch,authority;uint8_t phase,target,station,owner_station;uint32_t receiver_boot;} __packed;
struct kb_mirror{uint32_t epoch;uint8_t version,wpm,layer,mods,indicators,profile,host_connected,left_battery,left_online;} __packed;
static bool gate,page_active,page_toggle;static unsigned mirror_count;
static bool kb_route_dongle_key_allowed(void){return gate;}
static bool lism_screen_take_toggle(void){bool v=page_toggle;page_toggle=false;return v;}
static bool lism_screen_active(void){return page_active;}
static void lism_screen_commit(bool v){page_active=v;}
static void lism_screen_nav(unsigned p){(void)p;}
static bool kb_route_status_valid(const struct kb_route_status*s){return s->version==3 && s->station==0 && s->receiver_boot==777;}
static bool kb_route_discovery_release(const struct kb_discovery*p){(void)p;return false;}
static int kb_route_discovery_received(const struct kb_discovery*p){(void)p;return 0;}
static void kb_route_status_received(const struct kb_route_status*s){if(s->version==3&&s->mode<=2&&s->profile<5&&s->epoch)gate=s->mode==0;}
static void kb_mirror_received(const struct kb_mirror*m){(void)m;mirror_count++;}
'''
main=r'''
static void status(unsigned mode){struct zmk_split_transport_peripheral_event e={.type=KB_ROUTE_EVENT};struct kb_route_status s={.version=3,.mode=mode,.profile=0,.epoch=2,.receiver_boot=777};memcpy(&e.data,&s,sizeof(s));int rc=lism_handle_event(1,&e);assert(rc==(forced_return==-EINVAL?-EAGAIN:0));}
int main(void){
 struct zmk_split_transport_peripheral_event key={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,.data.key_position_event={.position=4,.pressed=1}};
 assert(!gate);now=1;lism_handle_event(0,&key);assert(!actual_keys[0]&&!held[0].keys);
 struct zmk_split_transport_peripheral_event e={.type=LISM_SNAPSHOT_TYPE};struct lism_snapshot s={.keys=1,.battery=0};memcpy(&e.data,&s,sizeof(s));lism_handle_event(0,&e);assert(!actual_keys[0]&&!held[0].keys&&battery_seen[0]); /* real 0% valid */
 status(KB_ROUTE_DONGLE);assert(gate);lism_handle_event(0,&e);assert(actual_keys[0]==1&&held[0].keys==1);
 forced_return=-EINVAL;status(KB_ROUTE_BLE);assert(gate&&actual_keys[0]==1); /* failed release cannot close gate */
 forced_return=0;page_active=page_toggle=page_pending=page_target=true;status(KB_ROUTE_BLE);assert(!gate&&!actual_keys[0]&&!held[0].keys&&!page_active&&!page_toggle&&!page_pending&&!page_target);
 lism_handle_event(0,&key);assert(!actual_keys[0]&&!held[0].keys);lism_handle_event(0,&e);assert(!held[0].keys);
 struct zmk_split_transport_peripheral_event btn={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT,.data.input_event={.reg=1,.type=INPUT_EV_KEY,.code=INPUT_BTN_LEFT,.value=1,.sync=1}};lism_handle_event(1,&btn);assert(!actual_buttons[1]&&!held[1].buttons);
 status(KB_ROUTE_DONGLE);lism_handle_event(0,&e);assert(actual_keys[0]==1);gate=false;now+=100;tick(NULL);assert(!actual_keys[0]&&!held[0].keys); /* unknown control state releases, never hides held state */
 status(KB_ROUTE_DONGLE);lism_handle_event(0,&e);assert(actual_keys[0]==1);s.keys=0;memcpy(&e.data,&s,sizeof(s));lism_handle_event(0,&e);assert(!actual_keys[0]);
 unsigned before=emitted;struct zmk_split_transport_peripheral_event mirror={.type=KB_MIRROR_EVENT};lism_handle_event(1,&mirror);assert(mirror_count==1&&emitted==before&&!actual_keys[0]);
 puts("PASS complete production dongle link: no applied hidden press, zero-percent battery, failed old-release retry, all-source gate, timeout release, status-page auto-return, telemetry never enters HID sink");return 0;}
'''
def c(p):return re.sub(r'^\s*#\s*(?:include|pragma once).*$', '',Path(p).read_text(),flags=re.M)
D=W/'module-dongle/src/split/esb'
parts=[pre,c(W/'zephyr/include/zephyr/sys/ring_buffer.h'),c(W/'zephyr/lib/utils/ring_buffer.c'),c(W/'zephyr/lib/crc/crc32_sw.c'),c(W/'include-dongle/zmk/split/transport/types.h'),c(D/'app_esb.h'),c(D/'common.h'),c(P/'src/esb_upstream/link.h'),values['sink'],c(P/'src/esb_upstream/common.c'),c(P/'src/esb_upstream/link.c'),main]
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'gate.c';out=Path(t)/'gate';f.write_text('\n'.join(parts));subprocess.run(['cc','-std=c11','-fsanitize=address,undefined','-Werror=implicit-function-declaration',str(f),'-o',str(out)],check=True);subprocess.run([str(out)],check=True)
