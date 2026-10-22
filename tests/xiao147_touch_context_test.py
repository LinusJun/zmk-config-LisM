"""Actual touch identity guard: volatile status versus action replacement."""
from pathlib import Path
import sys,re,tempfile,subprocess
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'));from prepare_hog_routing import function
s=(P/'src/lism_screen.c').read_text()
strip=lambda x:re.sub(r'^\s*#(?:include|pragma).*$', '',x,flags=re.M)
pre=r'''
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
typedef uint8_t bt_addr_le_t;
'''+strip((P/'src/lism_hid_host.h').read_text())+strip((P/'src/lism_input_host.h').read_text())+r'''
enum view {DEVICES,SEARCH};enum dialog {NONE,DEVICE,CONNECT,FORGET};
static enum view view;static enum dialog dialog;
static uint8_t slot,page,choice,pin_cursor,chosen_index,focus;
static int generation=1;static int atomic_get(int*p){return *p;}
static bool visible=true,touch_armed,touching,touch_blocked;static int touch_target;
static uint32_t touch_generation;static uint16_t touch_x,touch_y;
static struct lism_input_status shown,latest;static int hit;
static unsigned actions,last;
static int touch_at(uint16_t x,uint16_t y){(void)x;(void)y;return hit;}
void lism_input_host_snapshot(struct lism_input_status*p){*p=latest;}
static void action(unsigned n){actions++;last=n;}static void render(void){}
'''
start=s.index('struct touch_context {');end=s.index('void lism_screen_touch(',start)
code=pre+s[start:end]+function(s,'lism_screen_touch')+r'''
static void reset(void){memset(&shown,0,sizeof(shown));shown.enabled=true;latest=shown;view=DEVICES;dialog=NONE;slot=page=choice=pin_cursor=chosen_index=0;touch_armed=touching=touch_blocked=false;generation++;}
static void down(int target){hit=target;lism_screen_touch(1,20,40);}static void up(void){lism_screen_touch(0,0,0);}
int main(void){
 _Static_assert(sizeof(struct touch_context)<=64,"bounded press identity");
 for(unsigned n=0;n<1000;n++){
  reset();down(n%3);unsigned before=actions;latest.generation++;latest.seconds_left=n%31;latest.candidates[1].rssi=-n;strcpy(latest.peers[1].message,"background");shown=latest;up();assert(actions==before+1);
 }
 reset();down(0);unsigned old=actions;latest.peers[0].saved=true;latest.peers[0].legacy_token=4;strcpy(latest.peers[0].name,"replacement");shown=latest;up();assert(actions==old);
 reset();view=SEARCH;shown.searching=true;shown.candidates[0].valid=true;shown.candidates[0].token=77;strcpy(shown.candidates[0].address,"01:02:03:04:05:06");latest=shown;
 down(0);old=actions;latest.seconds_left=29;latest.candidates[0].rssi=-88;strcpy(latest.candidates[0].name,"completed name");shown=latest;up();assert(actions==old+1);
 down(0);old=actions;latest.candidates[0].token++;shown=latest;up();assert(actions==old);
 down(0);old=actions;latest.searching=false;up();assert(actions==old);
 reset();view=SEARCH;shown.searching=true;shown.candidates[0].valid=true;shown.candidates[0].token=77;strcpy(shown.candidates[0].address,"01:02:03:04:05:06");latest=shown;
 down(0);old=actions;strcpy(latest.candidates[0].address,"11:02:03:04:05:06");up();assert(actions==old); /* same token, different address, no poll */
 reset();dialog=DEVICE;shown.peers[0].saved=true;shown.peers[0].legacy_token=3;latest=shown;down(14);old=actions;choice++;up();assert(actions==old); /* changed action, no poll */
 reset();dialog=DEVICE;shown.peers[0].saved=true;shown.peers[0].legacy_token=3;latest=shown;down(14);old=actions;latest.peers[0].ready=true;shown=latest;up();assert(actions==old);
 reset();shown.auth_type=2;shown.auth_token=100;latest=shown;down(14);old=actions;latest.auth_token++;up();assert(actions==old); /* no poll */
 reset();down(3);old=actions;generation++;up();assert(actions==old);
 reset();down(2);old=actions;latest.enabled=false;up();assert(actions==old);
 reset();down(0);old=actions;lism_screen_touch(255,0,0);up();assert(actions==old);
 puts("PASS actual XIAO touch guard: 1000 volatile updates accepted; replacement peers/candidates, scan expiry, auth replacement, enabled flip, ready flip, page generation and cancel all rejected");
}
'''
with tempfile.TemporaryDirectory() as d:
 f=Path(d)/'touch.c';o=Path(d)/'touch';f.write_text(code);subprocess.run(['cc','-std=c11','-Werror','-Wall','-Wextra','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True);subprocess.run([str(o)],check=True)
