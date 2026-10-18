"""Actual UI source with bounded LVGL/host fakes: geometry and stale action safety."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
lvgl = r'''
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
typedef int lv_color_t; typedef struct {int x,y;}lv_point_t;
typedef struct {int x1,y1,x2,y2;}lv_area_t;
typedef struct {int dummy;}lv_layer_t;
typedef struct {int color,width;lv_point_t p1,p2;}lv_draw_line_dsc_t;
typedef struct obj {int x,y,w,h,flags; char text[160];}lv_obj_t;
typedef struct {lv_obj_t *obj;void *user;}lv_event_t;
static lv_obj_t objects[100];static int objects_n;
#define LV_FONT_DECLARE(a) static int a
#define LV_PART_MAIN 0
#define LV_LABEL_LONG_CLIP 0
#define LV_TEXT_ALIGN_CENTER 0
#define LV_OPA_COVER 255
#define LV_OBJ_FLAG_HIDDEN 1
#define LV_OBJ_FLAG_SCROLLABLE 2
#define LV_OBJ_FLAG_CLICKABLE 4
#define LV_EVENT_DRAW_MAIN 1
static inline lv_obj_t *lv_obj_create(lv_obj_t *p){(void)p;return &objects[objects_n++];}
#define lv_label_create lv_obj_create
static inline void lv_obj_remove_style_all(lv_obj_t *o){(void)o;}
static inline void lv_obj_set_pos(lv_obj_t *o,int x,int y){o->x=x;o->y=y;}
static inline void lv_obj_set_size(lv_obj_t *o,int w,int h){o->w=w;o->h=h;}
static inline void lv_obj_set_width(lv_obj_t *o,int w){o->w=w;}
static inline void lv_obj_add_flag(lv_obj_t *o,int f){o->flags|=f;}
static inline void lv_obj_clear_flag(lv_obj_t *o,int f){o->flags&=~f;}
static inline void lv_label_set_text(lv_obj_t *o,const char *s){snprintf(o->text,sizeof(o->text),"%s",s);}
static inline void lv_obj_center(lv_obj_t *o){(void)o;}
static inline int lv_color_hex(unsigned c){return (int)c;}
#define STYLE(name,type) static inline void name(lv_obj_t *o,type v,int p){(void)o;(void)v;(void)p;}
STYLE(lv_obj_set_style_text_font,const int *)
STYLE(lv_obj_set_style_text_color,int)
STYLE(lv_obj_set_style_text_align,int)
STYLE(lv_obj_set_style_bg_color,int)
STYLE(lv_obj_set_style_bg_opa,int)
STYLE(lv_obj_set_style_radius,int)
STYLE(lv_obj_set_style_border_color,int)
STYLE(lv_obj_set_style_border_width,int)
static inline void lv_label_set_long_mode(lv_obj_t *o,int m){(void)o;(void)m;}
static inline void lv_draw_line_dsc_init(lv_draw_line_dsc_t *d){memset(d,0,sizeof(*d));}
static inline void lv_draw_line(lv_layer_t *c,const lv_draw_line_dsc_t *d){(void)c;(void)d;}
static inline lv_obj_t *lv_event_get_target(lv_event_t *e){return e->obj;}
static inline void *lv_event_get_user_data(lv_event_t *e){return e->user;}
static inline lv_layer_t *lv_event_get_layer(lv_event_t *e){(void)e;return NULL;}
static inline void lv_obj_get_coords(lv_obj_t *o,lv_area_t *a){*a=(lv_area_t){o->x,o->y,o->x+o->w-1,o->y+o->h-1};}
static inline void lv_obj_remove_event_cb(lv_obj_t *o,void (*f)(lv_event_t*)){(void)o;(void)f;}
static inline void lv_obj_add_event_cb(lv_obj_t *o,void (*f)(lv_event_t*),int n,void *u){(void)o;(void)f;(void)n;(void)u;}
static inline void lv_obj_invalidate(lv_obj_t *o){(void)o;}
'''
test = r'''
#include <stdio.h>
#include <assert.h>
#define CONFIG_LISM_TWO_KEYBOARD_SETS 1
#include "xiao147_inputs_ui.c"
static struct lism_input_status fake;
static unsigned calls,last_v;static uint32_t last_token;static bool last_allow;
static struct lism_split_set_candidate candidates[8];static size_t count;
void lism_input_host_snapshot(struct lism_input_status *s){*s=fake;}
void lism_input_host_search(uint8_t s,bool other){calls++;last_v=s;(void)other;}
void lism_input_host_cancel(void){calls++;}
void lism_input_host_select(uint8_t i,uint32_t t){calls++;last_v=i;last_token=t;}
void lism_input_host_forget(uint8_t i){calls++;last_v=i;}
void lism_input_host_retry(uint8_t i){calls++;last_v=i;}
void lism_input_host_legacy(uint8_t i,uint32_t t,bool allow){calls++;last_v=i;last_token=t;last_allow=allow;}
void lism_input_host_auth(uint32_t t,bool accept,uint32_t p){calls++;last_token=t;last_v=accept?p:9999999;}
int lism_split_sets_register_begin(uint8_t i){calls++;last_v=i;return 0;}
void lism_split_sets_register_cancel(void){calls++;}
size_t lism_split_sets_candidates(struct lism_split_set_candidate *o,size_t n){if(n>count)n=count;memcpy(o,candidates,n*sizeof(*o));return n;}
int lism_split_sets_register_select(uint32_t t){calls++;last_token=t;return 0;}
void lism_split_sets_get_status(char *out,size_t n){snprintf(out,n,"Register B-L");}
static unsigned button_for(enum action a,unsigned v){for(unsigned i=0;i<BUTTONS;i++)if(commands[i].action==a && commands[i].value==v)return i;fprintf(stderr,"missing action %u value %u\n",a,v);assert(0);return 0;}
static void press(unsigned i){xiao147_inputs_press(commands[i].x+1,commands[i].y+1);}
static void release(unsigned i){xiao147_inputs_release(commands[i].x+1,commands[i].y+1);}
static void tap(enum action a,unsigned v){unsigned i=button_for(a,v);press(i);release(i);}
static void geometry(bool p){
 xiao147_inputs_draw(p);int w=p?172:320,h=p?320:172;
 for(unsigned i=0;i<BUTTONS;i++)if(commands[i].action){
  struct command *c=&commands[i];assert(c->x>=0 && c->y>=0 && c->x+c->w<=w && c->y+c->h<=h);
  for(unsigned j=0;j<i;j++)if(commands[j].action){struct command *b=&commands[j];
   assert(c->x>=b->x+b->w || b->x>=c->x+c->w || c->y>=b->y+b->h || b->y>=c->y+c->h);}
 }
}
static void focus_to(enum action a,unsigned v) {
 for(unsigned i=0;i<=BUTTONS;i++) {
  if(focused>=0 && commands[focused].action==a && commands[focused].value==v)return;
  xiao147_inputs_focus_next();
 }
 fprintf(stderr,"focus target unreachable: %u/%u\n",a,v);assert(0);
}
static void activate_focus(void) {
 uint32_t serial=xiao147_inputs_focus_capture();assert(serial);
 xiao147_inputs_focus_activate(serial);
}
static void focus_navigation(void) {
 memset(&fake,0,sizeof(fake));sets=false;selected=0;page=0;forget_armed=false;show_other=false;
 xiao147_inputs_draw(false);calls=0;
 unsigned enabled=0;for(unsigned i=0;i<BUTTONS;i++)if(commands[i].action)enabled++;
 assert(focused==0 && enabled==9); /* two tabs, two slots, five controls */
 int first=focused;int previous=focused;unsigned seen=1U<<focused;
 for(unsigned i=1;i<enabled;i++) {
  xiao147_inputs_focus_next();assert(focused!=previous && !(seen&(1U<<focused)));
  assert(visual_before(previous,focused));seen|=1U<<focused;previous=focused;
 }
 xiao147_inputs_focus_next();assert(focused==first && !calls && selected==0);
 focus_to(SLOT,1);assert(selected==0);activate_focus();assert(selected==1 && !calls);
 focus_to(TAB_SET,0);activate_focus();assert(sets);
 focus_to(SET_SLOT,4);activate_focus();assert(set_slot==4);
 focus_to(SET_BEGIN,4);activate_focus();assert(enrolling && calls==1 && last_v==4);
 focus_to(TAB_INPUT,0);activate_focus();assert(!sets && !enrolling && calls==2);
 /* Next-page navigation is also reachable by the physical button. */
 focus_to(MORE,selected);activate_focus();assert(page==1);
 page=0;selected=0;fake.searching=true;fake.candidates[0].valid=true;fake.candidates[0].token=111;
 strcpy(fake.candidates[0].name,"first");xiao147_inputs_draw(false);focus_to(PICK,0);
 uint32_t serial=xiao147_inputs_focus_capture();calls=0;
 fake.candidates[0].token=112;xiao147_inputs_focus_activate(serial);assert(!calls);
 focus_to(PICK,0);serial=xiao147_inputs_focus_capture();fake.candidates[0].valid=false;
 xiao147_inputs_focus_activate(serial);assert(!calls && commands[focused].action!=NONE);
 fake.candidates[0].valid=true;xiao147_inputs_draw(false);focus_to(PICK,0);
 serial=xiao147_inputs_focus_capture();fake.generation++;fake.seconds_left--;
 strcpy(fake.message,"timer update only");xiao147_inputs_draw(false);
 xiao147_inputs_focus_activate(serial);assert(calls==1 && last_token==112);
 xiao147_inputs_focus_activate(serial);assert(calls==1); /* single-use capture */
 focus_to(PICK,0);serial=xiao147_inputs_focus_capture();xiao147_inputs_draw(true);
 xiao147_inputs_focus_activate(serial);assert(calls==1);
 focus_to(PICK,0);serial=xiao147_inputs_focus_capture();xiao147_inputs_press(0,0);
 xiao147_inputs_focus_activate(serial);assert(calls==1); /* even blank touch invalidates */
 focus_to(PICK,0);serial=xiao147_inputs_focus_capture();xiao147_inputs_focus_cancel_pending();
 xiao147_inputs_focus_activate(serial);assert(calls==1);
 fake.searching=false;fake.peers[0].saved=true;strcpy(fake.peers[0].name,"saved");
 xiao147_inputs_draw(true);focus_to(FORGET,0);calls=0;activate_focus();assert(!calls && forget_armed);
 activate_focus();assert(calls==1 && !forget_armed); /* still needs two confirmations */
 fake.auth_type=LISM_INPUT_AUTH_CONFIRM;fake.auth_token=999;xiao147_inputs_draw(false);
 focus_to(AUTH_ACCEPT,0);serial=xiao147_inputs_focus_capture();calls=0;fake.auth_token=1000;
 xiao147_inputs_focus_activate(serial);assert(!calls);
 focus_to(AUTH_CANCEL,0);activate_focus();assert(calls==1 && last_token==1000 && last_v==9999999);
 fake.auth_type=LISM_INPUT_AUTH_ENTRY;fake.auth_token=1001;xiao147_inputs_draw(true);
 focus_to(AUTH_CANCEL,0);serial=xiao147_inputs_focus_capture();fake.auth_token=1002;
 xiao147_inputs_focus_activate(serial);assert(calls==1);
 fake.auth_token=1001;xiao147_inputs_draw(true);
 focus_to(DIGIT,4);activate_focus();focus_to(DIGIT,2);activate_focus();
 focus_to(ENTER,0);activate_focus();assert(last_token==1001 && last_v==42);
 fake.auth_type=LISM_INPUT_AUTH_NONE;xiao147_inputs_draw(true);
}
static unsigned context_callbacks;
static void context_callback(void) {
 assert(previous_valid); /* callback sees a finished, consistent redraw */
 if(focused>=0)assert(same_command(&focus_target,&commands[focused]));
 context_callbacks++;
 xiao147_inputs_focus_cancel_pending(); /* permitted non-reentrant owner use */
}
static void focus_context_handoff(void) {
 memset(&fake,0,sizeof(fake));sets=false;selected=0;page=0;forget_armed=false;
 xiao147_inputs_draw(false);context_callbacks=0;
 xiao147_inputs_focus_set_context_callback(context_callback);
 uint32_t serial=xiao147_inputs_focus_capture();assert(serial);
 fake.generation++;fake.seconds_left=20;strcpy(fake.message,"RSSI/timer refresh");
 fake.candidates[0].rssi=-70;xiao147_inputs_draw(false);
 assert(!context_callbacks && focus_serial==serial);
 /* An edge queued under the old screen must not capture the new prompt. */
 fake.auth_type=LISM_INPUT_AUTH_CONFIRM;fake.auth_token=3000;
 assert(xiao147_inputs_focus_capture()==0 && context_callbacks==1);
 assert(commands[focused].action==AUTH_ACCEPT && !focus_serial);
 serial=xiao147_inputs_focus_capture();assert(serial);
 fake.auth_token=3001;
 xiao147_inputs_focus_next(); /* no movement into Cancel on a new prompt */
 assert(context_callbacks==2 && commands[focused].action==AUTH_ACCEPT);
 xiao147_inputs_focus_activate(serial);assert(!focus_serial);
 /* Candidate replacement similarly rejects deferred first clicks, while
  * signal-strength changes alone do not reset the button FSM. */
 fake.auth_type=LISM_INPUT_AUTH_NONE;fake.searching=true;fake.candidates[0].valid=true;
 fake.candidates[0].token=700;xiao147_inputs_draw(false);focus_to(PICK,0);
 unsigned before=context_callbacks;fake.candidates[0].token=701;
 assert(xiao147_inputs_focus_capture()==0 && context_callbacks==before+1);
 focus_to(PICK,0);serial=xiao147_inputs_focus_capture();assert(serial);
 fake.candidates[0].rssi=-40;xiao147_inputs_draw(false);
 assert(context_callbacks==before+1 && focus_serial==serial);
 xiao147_inputs_focus_set_context_callback(NULL);
}
static void legacy_confirmation(void) {
 memset(&fake,0,sizeof(fake));sets=false;selected=0;page=0;forget_armed=false;legacy_armed=false;
 fake.peers[0].legacy_available=true;fake.peers[0].legacy_token=42;
 strcpy(fake.peers[0].name,"CheerPod");geometry(false);geometry(true);calls=0;
 tap(LEGACY,0);assert(legacy_armed&&!calls);
 geometry(false);geometry(true);assert(commands[focused].action==LEGACY_CANCEL);
 tap(LEGACY_CANCEL,0);assert(!legacy_armed&&!calls);
 /* Stale touch and physical-button confirmations cannot grant a replacement
  * target or cross an auth request, page exit, rotation or timeout. */
 tap(LEGACY,0);unsigned i=button_for(LEGACY_ALLOW,0);press(i);
 fake.peers[0].legacy_token=43;release(i);assert(!legacy_armed&&!calls);
 xiao147_inputs_draw(false);tap(LEGACY,0);focus_to(LEGACY_ALLOW,0);
 uint32_t serial=xiao147_inputs_focus_capture();fake.peers[0].legacy_token=44;
 xiao147_inputs_focus_activate(serial);assert(!calls&&!legacy_armed);
 tap(LEGACY,0);focus_to(LEGACY_ALLOW,0);serial=xiao147_inputs_focus_capture();
 xiao147_inputs_draw(true);xiao147_inputs_focus_activate(serial);assert(!calls);
 xiao147_inputs_cancel();assert(!legacy_armed&&!calls);
 xiao147_inputs_draw(true);tap(LEGACY,0);ui_now=legacy_until;xiao147_inputs_draw(true);
 assert(!legacy_armed&&!calls);ui_now=1000;
 tap(LEGACY,0);fake.auth_type=LISM_INPUT_AUTH_CONFIRM;fake.auth_token=500;
 xiao147_inputs_draw(true);assert(!legacy_armed&&!calls);
 fake.auth_type=LISM_INPUT_AUTH_NONE;xiao147_inputs_draw(true);tap(LEGACY,0);
 tap(LEGACY_ALLOW,0);assert(calls==1&&last_v==0&&last_token==44&&last_allow);
 fake.peers[0].legacy_allowed=true;fake.peers[0].legacy_token=45;
 xiao147_inputs_draw(false);tap(LEGACY,0);
 assert(calls==2&&last_v==0&&last_token==45&&!last_allow);
 fake.peers[0].legacy_allowed=false;fake.peers[0].legacy_available=false;
 xiao147_inputs_draw(false);assert(commands[12].action==NONE);
}
int main(void){
 lv_obj_t parent={0};assert(xiao147_inputs_init(&parent));xiao147_inputs_status_init(&parent);
 geometry(false);geometry(true);
 fake.searching=true;fake.candidates[0].valid=true;fake.candidates[0].token=42;
 strcpy(fake.candidates[0].name,"Keyboard");xiao147_inputs_draw(false);
 unsigned i=button_for(PICK,0);press(i);fake.candidates[0].token=43;release(i);assert(!calls);
 xiao147_inputs_draw(false);tap(PICK,0);assert(calls==1 && last_token==43);
 calls=0;xiao147_inputs_draw(false);i=button_for(PICK,0);press(i);xiao147_inputs_draw(true);release(i);assert(!calls);
 fake.searching=false;fake.peers[0].saved=true;xiao147_inputs_draw(false);
 tap(FORGET,0);assert(!calls);tap(FORGET,0);assert(calls==1);
 calls=0;fake.auth_type=LISM_INPUT_AUTH_CONFIRM;fake.auth_token=100;fake.passkey=1234;
 geometry(false);geometry(true);i=button_for(AUTH_ACCEPT,0);press(i);fake.auth_token=101;release(i);assert(!calls);
 xiao147_inputs_draw(true);tap(AUTH_ACCEPT,0);assert(calls==1 && last_token==101);
 calls=0;fake.auth_type=LISM_INPUT_AUTH_ENTRY;fake.auth_token=102;geometry(false);geometry(true);
 tap(DIGIT,0);tap(DIGIT,1);tap(DIGIT,2);tap(BACK,0);tap(DIGIT,3);tap(ENTER,0);
 assert(calls==1 && last_token==102 && last_v==13); /* leading zero allowed */
 calls=0;xiao147_inputs_cancel();assert(calls==1 && last_v==9999999);
 fake.auth_type=LISM_INPUT_AUTH_NONE;xiao147_inputs_draw(true);tap(TAB_SET,0);geometry(false);geometry(true);
 tap(SET_SLOT,4);tap(SET_BEGIN,4);assert(last_v==4);
 count=1;candidates[0].token=900;strcpy(candidates[0].name,"LisM");xiao147_inputs_draw(true);
 i=button_for(SET_PICK,0);press(i);candidates[0].token=901;unsigned before=calls;release(i);assert(calls==before);
 xiao147_inputs_draw(true);tap(SET_PICK,0);assert(last_token==901);
 fake.peers[0].connected=true;fake.peers[0].ready=true;fake.peers[0].type=LISM_INPUT_DEVICE_MOUSE;
 xiao147_inputs_status_draw(false,true);assert(!(icons[0]->flags&LV_OBJ_FLAG_HIDDEN));assert(icons[1]->flags&LV_OBJ_FLAG_HIDDEN);
 assert(icons[0]->x>=169 && icons[0]->x+icons[0]->w<=197);
 xiao147_inputs_status_draw(true,true);assert(icons[0]->y+icons[0]->h<=289);
 xiao147_inputs_status_draw(true,false);assert(icons[0]->flags&LV_OBJ_FLAG_HIDDEN);
 focus_navigation();
 focus_context_handoff();
 legacy_confirmation();
 puts("input UI geometry/stale-token/auth/icons/focus: PASS");
}
'''
with tempfile.TemporaryDirectory(prefix="lism-input-ui-") as temp:
    directory = Path(temp)
    (directory / "zephyr/bluetooth").mkdir(parents=True)
    (directory / "lvgl.h").write_text(lvgl)
    (directory / "zephyr/bluetooth/addr.h").write_text("#pragma once\n#include <stdint.h>\ntypedef struct {uint8_t val[7];} bt_addr_le_t;\n")
    (directory / "zephyr/kernel.h").write_text("#pragma once\n#include <stdint.h>\nstatic uint32_t ui_now=1000;\nstatic inline uint32_t k_uptime_get_32(void){return ui_now;}\n")
    harness = directory / "test.c"
    harness.write_text(test)
    binary = directory / "test"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-g",
                    "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-I", str(directory), "-I", str(root / "src"),
                    str(harness), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
    auto_main = r'''
static bool automatic_pending;
bool lism_split_auto_pending(void){return automatic_pending;}
int main(void){
 lv_obj_t parent={0};assert(xiao147_inputs_init(&parent));
 geometry(false);geometry(true);assert(!commands[12].action);
 tap(TAB_SET,0);automatic_pending=true;xiao147_inputs_draw(false);
 assert(commands[8].action==SET_CANCEL&&!strcmp(labels[8]->text,"Cancel"));
 geometry(true);unsigned before=calls;tap(SET_CANCEL,0);assert(calls==before+1);
 puts("PASS: automatic pairing UI, five controls, no per-device Legacy toggle, auto Cancel geometry");
}
'''
    auto=directory/"auto.c"
    auto.write_text(test.split("int main(void){",1)[0]+auto_main)
    subprocess.run(["cc","-std=c11","-Wall","-Wextra","-Werror","-Wno-unused-function",
                    "-DCONFIG_LISM_INPUT_AUTO_PAIR=1","-DCONFIG_LISM_AUTO_SPLIT_SETS=1",
                    "-fsanitize=address,undefined","-I",str(directory),"-I",str(root/"src"),
                    str(auto),"-o",str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
