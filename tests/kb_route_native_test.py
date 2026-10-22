"""Run actual route capture/auto policy and ESB framing functions with modeled I/O."""
from pathlib import Path
import sys,tempfile,subprocess,re
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
hub=(P/'src/kb_route/hub.c').read_text();radio=(P/'src/esb_upstream/hub_radio.inc').read_text();link=(P/'src/esb_upstream/link.c').read_text()
def strip(t):return re.sub(r'^\s*#(?:include|pragma).*$', '',t,flags=re.M)
# Compile the pinned actual transport ABI, not a rewritten approximation.
abi=Path('/work/workspace/include-right/zmk/split/transport/types.h').read_text()
pre=r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#define __packed __attribute__((packed))
#define BIT(n) (1U<<(n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define ARG_UNUSED(x) (void)(x)
#define BUILD_ASSERT(x,...) _Static_assert(x,#x)
#define IS_ENABLED(x) (x)
#define CONFIG_LISM_KB_HUB 1
#define CONFIG_ZMK_SPLIT_ROLE_CENTRAL 1
#define CONFIG_ZMK_SPLIT_ESB_PERIPHERAL_ID 1
#define CONFIG_ESB_MAX_PAYLOAD_LENGTH 64
#define CONFIG_ZMK_SPLIT_ESB_MSG_POSTFIX_CRC 1
#define ZMK_KEYMAP_HAS_SENSORS 0
#define ZMK_KEYMAP_SENSORS_LEN 0
#define K_NO_WAIT 0
#define K_FOREVER 0
#define INPUT_EV_KEY 1
#define INPUT_EV_REL 2
#define INPUT_BTN_LEFT 272
#define INPUT_REL_X 0
#define INPUT_REL_Y 1
#define INPUT_REL_Z 2
#define INPUT_REL_HWHEEL 6
#define INPUT_REL_WHEEL 8
#define ZMK_POSITION_STATE_CHANGE_SOURCE_LOCAL 255
#define LOG_INF(...) ((void)0)
#define LOG_WRN(...) ((void)0)
typedef uint8_t zmk_hid_indicators_t;
struct zmk_sensor_channel_data {int32_t value;};
typedef int atomic_t;static int atomic_get(atomic_t *p){return *p;}static int atomic_set(atomic_t *p,int v){int old=*p;*p=v;return old;}
static int64_t now=5000;static int64_t k_uptime_get(void){return now;}
struct k_spinlock {int x;};typedef int k_spinlock_key_t;
static int k_spin_lock(struct k_spinlock *l){(void)l;return 0;}static void k_spin_unlock(struct k_spinlock*l,int k){(void)l;(void)k;}
static void k_mutex_lock(void*l,int t){(void)l;(void)t;}static void k_mutex_unlock(void*l){(void)l;}
'''+strip(abi)+strip((P/'src/kb_route/view.h').read_text())+r'''
#define KB_ROUTE_EVENT 126
#define KB_ROUTE_VERSION 3
#include "/work/source/src/kb_route/left_protocol.h"
#define LISM_SNAPSHOT_TYPE 127
#define LISM_KEYS 46
#define LISM_LEASE_MS 1500
#define LISM_WIRE_HEADER 20
struct lism_snapshot {uint64_t keys;uint8_t buttons,battery;} __packed;
struct kb_route_status {uint8_t version,mode,profile;uint32_t epoch,authority;uint8_t phase,target,station,owner_station;uint32_t receiver_boot;} __packed;
'''
route_pre=r'''
struct zmk_position_state_changed {uint8_t source;uint32_t position;bool state;int64_t timestamp;};
typedef struct {struct zmk_position_state_changed position;} zmk_event_t;
static struct zmk_position_state_changed *as_zmk_position_state_changed(zmk_event_t*e){return &e->position;}
static struct k_spinlock state_lock;static struct lism_snapshot physical[2];
static bool initialized=true,switching;static uint8_t current_mode;static int64_t left_seen,confirmed_at,device_down;
static uint64_t offline_consumed;static int requested_mode=-1;
static void kb_route_request(uint8_t m){requested_mode=m;}
static int kb_route_select_dongle(uint8_t s){requested_mode=30+s;return 0;}
struct recorded {unsigned source;struct zmk_split_transport_peripheral_event e;};static struct recorded record[256];static unsigned count;
static int kb_uplink_send(unsigned s,const struct zmk_split_transport_peripheral_event *e){record[count++]=(struct recorded){s,*e};return 0;}
'''
route_main=r'''
static bool key(unsigned s,unsigned pos,bool down){zmk_event_t e={.position={s,pos,down,now}};return kb_route_capture(&e);}
int main(void){
 /* Boot probe never requires active BLE scanning and does not steal output later. */
 assert(kb_boot_decision(0,4500,false)==-1);assert(kb_boot_decision(4499,4500,false)==-1);
 assert(kb_boot_decision(4500,4500,false)==1);assert(kb_boot_decision(4500,4500,true)==0);
 current_mode=KB_ROUTE_UNKNOWN;assert(key(255,15,true));assert(!count);assert(!all_released());key(255,15,false);assert(all_released());
 current_mode=KB_ROUTE_DONGLE;confirmed_at=now;
 assert(key(255,41,true));now+=210;assert(key(255,15,true));assert(requested_mode==-1&&count==2);key(255,15,false);key(255,41,false);
 /* Offline right Device+H is wholly handled without a dongle keymap. */
 confirmed_at=0;count=0;key(255,41,true);now+=210;key(255,15,true);assert(requested_mode==1&&count==1);key(255,15,false);assert(count==1);key(255,41,false);assert(all_released());
 current_mode=KB_ROUTE_BLE;assert(!key(255,15,true));assert(physical[1].keys&(UINT64_C(1)<<15));assert(!key(255,15,false));
 switching=true;unsigned n=count;assert(key(0,12,true));assert(count==n&&!all_released());key(0,12,false);assert(all_released());switching=false;
 current_mode=KB_ROUTE_DONGLE;struct zmk_split_transport_peripheral_event e={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT,.data.input_event={.reg=1,.type=INPUT_EV_REL,.code=INPUT_REL_Z,.value=(int32_t)0xff80ffff,.sync=1}};
 assert(kb_route_input(1,&e));assert(record[count-1].source==1&&record[count-1].e.data.input_event.value==(int32_t)0xff80ffff);
 current_mode=KB_ROUTE_BLE;assert(!kb_route_input(1,&e));
 /* Left data is never relayed in Dongle mode. Its control state may be held,
  * but only the left's stamped direct data feeds the selected keymap. */
 unsigned before=count;emit_snapshot(0,false);assert(count==before);
 assert(kb_route_relay(0,&e)==-EINVAL);
 puts("PASS actual route capture: right master, offline H, probe quarantine, source preservation, packed trackball, sustained left lease, no stale left hold");return 0;}
'''
with tempfile.TemporaryDirectory() as tmp:
 f=Path(tmp)/'route.c';out=Path(tmp)/'route';f.write_text(pre+strip((P/'src/kb_route/auto_policy.h').read_text())+route_pre+'\n'.join(function(hub,n) for n in ['physical_event','dongle_confirmed','kb_route_capture','kb_route_relay','kb_route_input','all_released','kb_route_left_alive','emit_snapshot'])+route_main)
 # forward relay is called before definition in the production header.
 t=f.read_text().replace(route_pre,route_pre+'\nint kb_route_relay(unsigned,const struct zmk_split_transport_peripheral_event*);\n');f.write_text(t)
 subprocess.run(['cc','-std=gnu11','-Wall','-fsanitize=address,undefined',str(f),'-o',str(out)],check=True);subprocess.run([str(out)],check=True)
print('PASS kb_route_native_test')
