"""Actual ring renderer must use transport snapshot, not stale A/R UI events."""
from pathlib import Path
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
source=(root/'src/xiao147_battery_rings.c').read_text()
def function(signature):
    start=source.index(signature);brace=source.index('{',start);depth=1;end=brace+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]
prelude=r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#define CONFIG_LISM_XIAO147_TOUCH 1
static bool palette_bright;
bool xiao147_palette_bright(void){return palette_bright;}
#include "lism_charge.h"
#include "xiao147_battery_colors.h"
#define CONFIG_LISM_TWO_KEYBOARD_SETS 1
#define XIAO147_PERIPHERAL_COUNT 6
#define RING_COUNT 3
#define LV_PART_MAIN 0
#define LV_PART_INDICATOR 1
#define LV_OBJ_FLAG_HIDDEN 1
typedef struct {unsigned color[2],value;bool hidden;char text[8];}lv_obj_t;
static lv_obj_t objects[12],*ring_obj[3],*ring_badge[3],*ring_badge_text[3],*ring_charge[3];
static bool connected[6],battery_seen[6],physical[6];static uint8_t battery_level[6];
static struct lism_charge_state charge_states[6];static unsigned active=1;
static unsigned xiao147_active_keyboard_slot(unsigned role){return (active?4:0)+role;}
static unsigned xiao147_active_joystick_slot(const bool*c){(void)c;return 2;}
static void lism_split_sets_connected_snapshot(bool*out){memcpy(out,physical,sizeof(physical));}
struct lism_charge_state lism_charge_peripheral(uint8_t s){return charge_states[s];}
static unsigned lv_color_hex(unsigned c){return c;}
static void lv_obj_set_style_arc_color(lv_obj_t*o,unsigned c,unsigned part){o->color[part]=c;}
static void lv_obj_set_style_arc_width(lv_obj_t*o,unsigned w,unsigned part){(void)o;(void)w;(void)part;}
static void lv_arc_set_value(lv_obj_t*o,unsigned v){o->value=v;}
static void lv_obj_set_style_bg_color(lv_obj_t*o,unsigned c,unsigned p){o->color[p]=c;}
static void lv_label_set_text(lv_obj_t*o,const char*s){snprintf(o->text,sizeof(o->text),"%s",s);}
static void lv_obj_set_style_text_color(lv_obj_t*o,unsigned c,unsigned p){o->color[p]=c;}
static void lv_obj_clear_flag(lv_obj_t*o,unsigned f){(void)f;o->hidden=false;}
static void lv_obj_add_flag(lv_obj_t*o,unsigned f){(void)f;o->hidden=true;}
struct connection_ring_state {bool connected[6];};
'''
test=r'''
int main(void){
 for(unsigned i=0;i<3;i++){ring_obj[i]=&objects[i];ring_badge[i]=&objects[i+3];ring_badge_text[i]=&objects[i+6];ring_charge[i]=&objects[i+9];}
 physical[5]=true;battery_seen[5]=true;battery_level[5]=80;
 charge_states[5]=(struct lism_charge_state){.flags=111,.percent=80};
 /* Deliberately no observer event for physical5, then a stale all-off event. */
 render_ring(1);assert(ring_obj[1]->value==80&&!ring_charge[1]->hidden);
 assert(ring_obj[1]->color[1]==xiao147_battery_color(80));
 assert(ring_obj[1]->color[1]==0x54806c);
 palette_bright=true;render_ring(1);assert(ring_obj[1]->color[1]==0x6da68c);
 palette_bright=false;render_ring(1);assert(ring_obj[1]->color[1]==0x54806c);
 connection_update_cb((struct connection_ring_state){0});
 assert(battery_seen[5]&&ring_obj[1]->value==80&&!ring_charge[1]->hidden);
 charge_states[1]=(struct lism_charge_state){.flags=111,.percent=29};
 render_ring(1);assert(ring_obj[1]->value==80); /* A-R cannot overwrite B-R */
 /* Actual USB transition to unknown must not masquerade as disconnection,
  * recover a stale BAS percent, or fabricate charging. */
 charge_states[5]=(struct lism_charge_state){.flags=65};render_ring(1);
 assert(ring_obj[1]->value==0&&ring_charge[1]->hidden);
 assert(!strcmp(ring_badge_text[1]->text,"USB"));
 physical[5]=false;render_ring(1);
 assert(ring_obj[1]->value==0&&ring_charge[1]->hidden);
 assert(!strcmp(ring_badge_text[1]->text,"--"));
 active=0;physical[1]=true;render_ring(1);
 assert(ring_obj[1]->value==29&&!ring_charge[1]->hidden);
 puts("PASS: actual A/B right ring, missed/stale observer events, physical slot5 charging and no cross-set state");
}
'''
with tempfile.TemporaryDirectory(prefix='lism-b-r-ring-') as tmp:
    path=Path(tmp);c=path/'ring.c'
    c.write_text(prelude+function('static void render_ring(')+function('static void connection_update_cb(')+test)
    subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                    '-I'+str(root/'src'),str(c),'-o',str(path/'test')],check=True)
    subprocess.run([str(path/'test')],check=True)
