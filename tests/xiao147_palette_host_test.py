"""Actual palette persistence and menu geometry, without a radio or flash device."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = re.sub(r'^#include .*$', '', (root/'src/xiao147_palette.c').read_text(), flags=re.M)
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define CONFIG_LISM_XIAO147_TOUCH 1
#include "xiao147_palette.h"
#include "xiao147_settings_touch.h"
typedef int atomic_t;
#define ATOMIC_INIT(x) (x)
static int atomic_get(int *p){return *p;}
static void atomic_set(int*p,int v){*p=v;}
#define K_MUTEX_DEFINE(x) static int x
#define K_FOREVER -1
static int lock_depth;
static void k_mutex_lock(int*p,int t){(void)p;(void)t;assert(!lock_depth++);}
static void k_mutex_unlock(int*p){(void)p;assert(lock_depth--==1);}
#define LOG_MODULE_REGISTER(...)
#define LOG_LEVEL_INF 3
#define LOG_WRN(...)
#define ARG_UNUSED(x) (void)(x)
#define K_MSEC(x) (x)
#define SETTINGS_STATIC_HANDLER_DEFINE(...)
struct k_work {int dummy;};
struct k_work_delayable {struct k_work work;bool pending;unsigned delay;};
#define K_WORK_DELAYABLE_DEFINE(n,fn) static struct k_work_delayable n
static int queue;
static void *zmk_workqueue_lowprio_work_q(void){return &queue;}
static unsigned schedules,writes;
static uint8_t stored;
static int write_result;
static bool change_during_save;
static int k_work_reschedule_for_queue(void*q,struct k_work_delayable*w,unsigned ms){
 assert(q==&queue);assert(ms==2000||ms==5000);w->pending=true;w->delay=ms;schedules++;return 0;
}
static int settings_save_one(const char*key,const void*data,size_t len){
 assert(!lock_depth&&len==1&&!strcmp(key,"lism_display/palette"));writes++;
 stored=*(const uint8_t*)data;
 if(change_during_save){change_during_save=false;xiao147_palette_set_bright(!xiao147_palette_bright());}
 return write_result;
}
typedef int (*settings_read_cb)(void*,void*,size_t);
static int read_result=1;
static int read_byte(void*ctx,void*data,size_t len){assert(len==1);memcpy(data,ctx,1);return read_result;}
'''
test = r'''
static void boot(void){
 bright=0;loaded=dirty=saved_valid=false;saved=0;generation=failures=0;
 palette_save_work=(struct k_work_delayable){0};schedules=writes=0;
 write_result=0;read_result=1;change_during_save=false;
}
static void flush(void){assert(palette_save_work.pending);palette_save_work.pending=false;save_palette(&palette_save_work.work);}
int main(void){
 boot();assert(!xiao147_palette_bright());palette_settings_commit();assert(!writes&&!schedules);
 uint8_t value=2;assert(palette_settings_set("palette",1,read_byte,&value)==-EINVAL);
 assert(palette_settings_set("other",1,read_byte,&value)==-ENOENT);
 assert(palette_settings_set("palette",2,read_byte,&value)==-EINVAL);
 value=1;read_result=0;assert(palette_settings_set("palette",1,read_byte,&value)==-EINVAL);
 read_result=-EIO;assert(palette_settings_set("palette",1,read_byte,&value)==-EINVAL);
 read_result=1;assert(!xiao147_palette_bright()&&!writes&&!schedules);
 boot();value=1;assert(!palette_settings_set("palette",1,read_byte,&value));
 palette_settings_commit();assert(xiao147_palette_bright()&&!writes&&!schedules);
 xiao147_palette_set_bright(false);assert(!xiao147_palette_bright()&&dirty&&palette_save_work.delay==2000);
 flush();assert(writes==1&&stored==0&&!dirty&&!palette_save_work.pending);
 /* Coalesce rapid on/off changes back to the already stored value. */
 xiao147_palette_set_bright(true);xiao147_palette_set_bright(false);flush();assert(writes==1&&!dirty);
 xiao147_palette_set_bright(true);write_result=-EIO;
 flush();assert(dirty&&palette_save_work.pending&&failures==1);
 flush();flush();assert(dirty&&!palette_save_work.pending&&writes==4&&failures==3);
 /* A new real choice resumes after bounded failure; no rewriting on load. */
 xiao147_palette_set_bright(false);write_result=0;flush();assert(writes==4&&!dirty);
 xiao147_palette_set_bright(true);change_during_save=true;flush();
 assert(dirty&&palette_save_work.pending&&stored==1&&!xiao147_palette_bright());
 flush();assert(!dirty&&stored==0&&writes==6);
 /* An explicit choice made before late settings load always wins. */
 boot();xiao147_palette_set_bright(true);assert(dirty&&!schedules);
 value=0;palette_settings_set("palette",1,read_byte,&value);assert(xiao147_palette_bright());
 palette_settings_commit();flush();assert(stored==1&&!dirty);
 /* Reboot restores the saved choice, without writing settings or any bonds. */
 value=stored;boot();palette_settings_set("palette",1,read_byte,&value);
 palette_settings_commit();assert(xiao147_palette_bright()&&!writes&&!schedules);
 puts("PASS: actual palette default/load/save, malformed settings, coalescing, bounded retries, concurrent generation and cold boot");
}
'''

# Execute the actual menu layout against logical-coordinate objects.
ui = (root/'src/xiao147_ui.c').read_text()
start = ui.index('static void layout_settings(bool portrait)')
end = ui.index('\n}\n', start)+3
layout = ui[start:end]
geometry = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include "xiao147_settings_touch.h"
#define SCREEN_W 320
#define SCREEN_H 172
typedef struct {int x,y,w,h;}lv_obj_t;
static lv_obj_t group,labels[6],switches[5],slider,value;
static lv_obj_t *settings_group=&group,*settings_slider=&slider,*brightness_value=&value;
static lv_obj_t *settings_labels[6],*settings_switches[5];
static void lv_obj_set_size(lv_obj_t*o,int w,int h){o->w=w;o->h=h;}
static void lv_obj_set_pos(lv_obj_t*o,int x,int y){o->x=x;o->y=y;}
'''+layout+r'''
int main(void){
 for(int i=0;i<6;i++){settings_labels[i]=&labels[i];labels[i].w=95;labels[i].h=18;}
 for(int i=0;i<5;i++){settings_switches[i]=&switches[i];switches[i].w=43;switches[i].h=23;}
 for(int p=0;p<2;p++){
  layout_settings(p);int width=p?172:320,height=p?320:172;
  for(int i=0;i<5;i++){
   lv_obj_t *s=&switches[i],*l=&labels[i+1];
   assert(s->x>=0&&s->x+s->w<=width&&s->y+s->h<=height);
   assert(l->x+l->w<=s->x&&l->y+l->h<=height);
   assert(xiao147_settings_control_at(p,s->x+s->w/2,s->y+s->h/2)==i);
   assert(xiao147_settings_control_at(p,l->x,l->y+5)==i);
  }
  struct xiao147_settings_gesture g={.armed=-1};
  lv_obj_t*s=&switches[4];int x=s->x+10,y=s->y+10;
  assert(xiao147_settings_touch(&g,p,1,x,y,false)==-1);
  assert(xiao147_settings_touch(&g,p,0,0,0,false)==4);
  xiao147_settings_touch(&g,p,1,x,y,false);xiao147_settings_touch(&g,p,2,x,y,false);
  assert(xiao147_settings_touch(&g,p,0,0,0,false)==-1);
  xiao147_settings_touch(&g,p,1,x,y,false);xiao147_settings_cancel(&g);
  assert(xiao147_settings_touch(&g,p,0,0,0,false)==-1);
 }
 assert(xiao147_settings_control_at(false,158,145)==-1);
 puts("PASS: actual landscape/portrait menu geometry, fifth switch, full-size labels, disjoint IMU/palette touch controls");
}
'''
with tempfile.TemporaryDirectory(prefix='lism-palette-') as tmp:
    for name, code in (('persist',prelude+source+test), ('geometry',geometry)):
        path=Path(tmp)/f'{name}.c';exe=path.with_suffix('');path.write_text(code)
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                        '-I'+str(root/'src'),str(path),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
