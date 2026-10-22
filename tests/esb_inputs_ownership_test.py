"""Execute the actual ESB dispatch/ownership barrier with failing releases."""
from pathlib import Path
import sys,subprocess,tempfile
P=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
code=(P/'src/esb_upstream/link.c').read_text()
pre=r'''
#include <stdbool.h>
#include <stdint.h>
#include <assert.h>
#include <errno.h>
#define LISM_KEYS 46
#define ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT 2
#define INPUT_EV_KEY 1
static bool kb_route_dongle_key_allowed(void){return true;}
#define CONFIG_LISM_BLE_INPUT_BRIDGE 1
#define ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT 1
struct zmk_split_transport_peripheral_event {int type;union {struct {unsigned position;bool pressed;} key_position_event;struct {int type,value;} input_event;} data;};
struct {uint64_t keys;} held[2];
static uint64_t quarantine[2];
static bool page_pending,page_target; /* compiled if used by production */
static int esb_central;
static bool active,toggle,fail_release;
static bool host[2][46];static unsigned presses,releases,navs;
static bool lism_screen_take_toggle(void){bool v=toggle;toggle=false;return v;}
static bool lism_screen_active(void){return active;}
static void lism_screen_commit(bool v){active=v;}
static void lism_screen_nav(unsigned p){assert(p==37||(p>=42&&p<=45));navs++;}
static bool applied(int rc,const struct zmk_split_transport_peripheral_event*e){return rc>=0||(rc==-ENOTSUP&&e->type==1);}
static int zmk_split_transport_central_peripheral_event_handler(void*t,unsigned s,struct zmk_split_transport_peripheral_event e){
 (void)t;if(e.type!=1)return 0;
 unsigned p=e.data.key_position_event.position;bool down=e.data.key_position_event.pressed;
 if(p==2 && s==0 && down){toggle=!toggle;return 0;}
 if(!down && fail_release)return -EINVAL;
 host[s][p]=down;if(down)presses++;else releases++;return 0;
}
'''
main=r'''
static void inject(unsigned s,unsigned pos,bool down){
 struct zmk_split_transport_peripheral_event e={.type=1,.data.key_position_event={.position=pos,.pressed=down}};
 int rc=dispatch(s,e);if(applied(rc,&e)){if(down)held[s].keys|=UINT64_C(1)<<pos;else held[s].keys&=~(UINT64_C(1)<<pos);}
}
int main(void){
 inject(0,42,true);assert(host[0][42]);
 inject(0,2,true);assert(active && !host[0][42]);
 inject(0,42,false);assert(!host[0][42]&&!quarantine[0]);
 inject(0,43,true);inject(0,43,false);assert(navs==1&&!host[0][43]);
 inject(1,37,true);inject(1,37,false);assert(navs==2&&!host[1][37]);
 inject(0,37,true);assert(host[0][37]);inject(0,37,false); /* source-specific Enter */
 inject(1,10,true);assert(host[1][10]);inject(1,10,false);
 inject(0,44,true);assert(navs==3);inject(0,2,false);inject(0,2,true);assert(!active);
 inject(0,44,false);assert(!host[0][44]);inject(0,44,true);assert(host[0][44]);inject(0,44,false);
 /* A failed old-owner release cannot be swallowed into a successful page handoff. */
 inject(0,45,true);fail_release=true;inject(0,2,false);inject(0,2,true);
 assert(!active&&host[0][45]);
 fail_release=false;apply_page();assert(active&&!host[0][45]);
 inject(0,45,false);assert(!quarantine[0]);
 inject(1,37,true);toggle=true;apply_page();assert(!active);
 inject(1,37,false);assert(!host[1][37]);inject(1,37,true);assert(host[1][37]);inject(1,37,false);
 return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
 f=Path(tmp)/'test.c';f.write_text(pre+'\n'+ '\n'.join(function(code,n) for n in ['menu_key','apply_page','dispatch'])+main)
 out=Path(tmp)/'test';subprocess.run(['cc','-std=c11','-Wall','-fsanitize=address,undefined',str(f),'-o',str(out)],check=True);subprocess.run([str(out)],check=True)
print('PASS: ESB menu ownership, exact physical Enter, held controls, failed release retry')
