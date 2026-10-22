"""Real WPM code + LVGL pixels, with modeled display queue and LCD memory."""
from pathlib import Path
import sys,subprocess,json,hashlib
P=Path(__file__).resolve().parents[2];S=P/'source';sys.path.insert(0,str(S/'tools'))
from prepare_hog_routing import function
from prepare_mirror_display import prepare
D=P/'wpm-native';D.mkdir(exist_ok=True)
U=Path('/deps/prospector-zmk-module/boards/shields/prospector_adapter/src/layouts/operator')
for n in ['wpm_meter.c','layer_display.c','modifier_indicator.c']:(D/n).write_bytes((U/n).read_bytes())
prepare(D)
# Use the exact final generated translation units compiled for the receivers.
actual=P/'workspace/display-dongle_a/boards/shields/prospector_adapter/src/layouts/operator/wpm_meter.c'
src=actual.read_text()
assert src==(P/'workspace/display-dongle_b/boards/shields/prospector_adapter/src/layouts/operator/wpm_meter.c').read_text()
original=(U/'wpm_meter.c').read_text()
pre=r'''
#include <lvgl.h>
#include <fonts.h>
#include <assert.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <kb_route/view.h>
#include <kb_route/icons.c>
#include <kb_route/layer_label.h>
#include "display_colors.h"
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
#define WPM_BAR_COUNT 26
#define WPM_MAX 120
struct k_work{int dummy;};struct k_work_q{int id;};
struct k_work_delayable{struct k_work work;void(*fn)(struct k_work*);struct k_work_q*q;bool initialized,pending;uint32_t due;};
static struct k_work_q display_q={1},system_q={2};static uint32_t now;
static struct k_work_q *zmk_display_work_q(void){return &display_q;}
static void k_work_init_delayable(struct k_work_delayable*d,void(*fn)(struct k_work*)){assert(!d->initialized);d->fn=fn;d->initialized=true;}
static int schedule(struct k_work_q*q,struct k_work_delayable*d,int delay){assert(d->initialized && "work scheduled before init");assert(q==&display_q && "LVGL work scheduled on system queue");if(!d->pending){d->pending=true;d->due=now+delay;d->q=q;}return 0;}
static int k_work_schedule_for_queue(struct k_work_q*q,struct k_work_delayable*d,int delay){return schedule(q,d,delay);}
static int k_work_schedule(struct k_work_delayable*d,int delay){return schedule(&system_q,d,delay);}
typedef int sys_slist_t;
struct zmk_widget_wpm_meter{void*node;lv_obj_t*obj,*bars[26],*peak_indicator,*wpm_label,*layer_label;};
static struct zmk_widget_wpm_meter*registered;
#define SYS_SLIST_STATIC_INIT(x) 0
#define SYS_SLIST_FOR_EACH_CONTAINER(list,item,node) for(item=registered;item;item=NULL)
static void sys_slist_append(void*l,void*n){(void)l;registered=n;}
static void widget_wpm_meter_init(void);static void widget_wpm_meter_layer_init(void){}
struct kb_mirror{uint8_t wpm;};static int mirror_active;static uint8_t mirror_wpm;
static int kb_mirror_snapshot(struct kb_mirror*m){m->wpm=mirror_wpm;return mirror_active;}
void kb_route_snapshot(struct kb_route_view*v){*v=(struct kb_route_view){.mode=KB_ROUTE_DONGLE};}
bool kb_esb_signal(unsigned s,int8_t*d){*d=s?-72:-50;return true;}
bool lism_input_host_signal(int8_t*d){*d=-61;return true;}
'''
state=src[src.index('static sys_slist_t'):src.index('struct layer_state')]
functions=['wpm_meter_render','wpm_smooth_work_handler','wpm_meter_update_cb','zmk_widget_wpm_meter_init']
main=r'''
static void widget_wpm_meter_init(void){wpm_meter_update_cb((struct wpm_meter_state){.wpm=0});}
static unsigned width,height,writes,checks;static uint8_t panel[320*240*2],reference[320*240*2];
static void flush(lv_display_t*d,const lv_area_t*a,uint8_t*map){
 for(int y=a->y1;y<=a->y2;y++)for(int x=a->x1;x<=a->x2;x++){
  unsigned from=((y-a->y1)*(a->x2-a->x1+1)+x-a->x1)*2,to=(y*width+x)*2;
#if LV_COLOR_16_SWAP
  panel[to]=map[from+1];panel[to+1]=map[from];
#else
  panel[to]=map[from];panel[to+1]=map[from+1];
#endif
 }writes++;lv_display_flush_ready(d);
}
static void check_frame(lv_display_t*d,lv_obj_t*screen){
 lv_refr_now(d);memcpy(reference,panel,width*height*2);
 lv_obj_invalidate(screen);lv_refr_now(d);assert(!memcmp(reference,panel,width*height*2));checks++;
}
static void step(lv_display_t*d,lv_obj_t*screen){
 now+=33;lv_tick_inc(33);
 if(wpm_smooth_work.pending && now>=wpm_smooth_work.due){assert(wpm_smooth_work.q==&display_q);wpm_smooth_work.pending=false;wpm_smooth_work.fn(&wpm_smooth_work.work);}
 lv_timer_handler();check_frame(d,screen);
}
static void idle(lv_display_t*d,lv_obj_t*screen){
 wpm_meter_update_cb((struct wpm_meter_state){.wpm=0});
 for(unsigned n=0;n<240 && wpm_smooth_work.pending;n++)step(d,screen);
 assert(!wpm_smooth_work.pending && displayed_wpm==0 && !peak_position && !prev_active_bars);
 assert(lv_obj_has_flag(registered->peak_indicator,LV_OBJ_FLAG_HIDDEN));assert(!strcmp(lv_label_get_text(registered->wpm_label),"0"));
 for(unsigned n=0;n<26;n++)assert(lv_color_eq(lv_obj_get_style_bg_color(registered->bars[n],0),lv_color_hex(DISPLAY_COLOR_WPM_BAR_INACTIVE)));
 check_frame(d,screen);
}
int main(void){
#ifdef CONFIG_LISM_XIAO147_TOUCH
 width=320;height=172;
#else
 width=280;height=240;
#endif
 lv_init();lv_display_t*d=lv_display_create(width,height);static uint8_t buf[26880];
 lv_display_set_color_format(d,LV_COLOR_FORMAT_RGB565);lv_display_set_buffers(d,buf,NULL,sizeof(buf),LV_DISPLAY_RENDER_MODE_PARTIAL);lv_display_set_flush_cb(d,flush);
 lv_obj_t*screen=lv_screen_active();lv_obj_set_style_pad_all(screen,0,0);lv_obj_set_style_bg_color(screen,lv_color_black(),0);lv_obj_set_style_bg_opa(screen,255,0);
 struct zmk_widget_wpm_meter meter={0};zmk_widget_wpm_meter_init(&meter,screen);lv_obj_set_pos(meter.obj,10,42);
#ifdef CONFIG_LISM_XIAO147_TOUCH
 lv_obj_set_pos(meter.obj,4,28);lv_obj_set_size(meter.obj,312,65);
 for(unsigned i=0;i<26;i++){unsigned x=i*314/26,end=(i+1)*314/26-2;lv_obj_set_pos(meter.bars[i],x,0);lv_obj_set_size(meter.bars[i],end-x,65);}lv_obj_set_height(meter.peak_indicator,65);
#endif
 kb_layer_label_update(meter.layer_label,"GameJoystick");kb_route_icons_create(screen);check_frame(d,screen);
 /* Startup and a tiny positive input must still progress past zero. */
 wpm_meter_update_cb((struct wpm_meter_state){.wpm=1});for(unsigned i=0;i<15;i++)step(d,screen);assert(displayed_wpm>0.5f);idle(d,screen);
 for(unsigned cycle=0;cycle<6;cycle++){
  uint8_t rates[]={120,85,12,110,3,55,7};
  for(unsigned n=0;n<sizeof(rates);n++){
   wpm_meter_update_cb((struct wpm_meter_state){.wpm=rates[n]});
   for(unsigned t=0;t<7+cycle;t++)step(d,screen);
  }idle(d,screen);
 }
 /* Mirror source arriving/expiring also uses the same display queue. */
 mirror_active=1;mirror_wpm=77;wpm_meter_update_cb((struct wpm_meter_state){.wpm=199});assert(target_wpm==77);for(unsigned n=0;n<24;n++)step(d,screen);
 mirror_wpm=0;idle(d,screen);mirror_active=0;wpm_meter_update_cb((struct wpm_meter_state){.wpm=33});for(unsigned n=0;n<12;n++)step(d,screen);idle(d,screen);
 printf("PASS WPM: swap=%d screen=%ux%u, %u partial/full pixel comparisons, %u writes; display-queue/init order, peak move/clear, low nonzero, restart, source switching, actual signal/mode overlay\n",LV_COLOR_16_SWAP,width,height,checks,writes);
}
'''
(D/'display_colors.h').write_bytes((U/'display_colors.h').read_bytes())
(D/'lv_conf.h').write_text('#define LV_CONF_H\n#define LV_COLOR_DEPTH 16\n#define LV_MEM_SIZE (96*1024U)\n#define LV_USE_OS LV_OS_NONE\n#define LV_DRAW_BUF_STRIDE_ALIGN 1\n#define LV_USE_ASSERT_MALLOC 1\n#define LV_USE_LOG 1\n#define LV_LOG_LEVEL LV_LOG_LEVEL_WARN\n#define LV_FONT_MONTSERRAT_12 1\n')
fixed=pre+state+'\n'.join(function(src,n) for n in functions)+main
(D/'main.c').write_text(fixed)
(D/'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.20)
project(wpm_pixels C)
file(GLOB_RECURSE LV_SOURCES /deps/modules/lib/gui/lvgl/src/*.c)
file(GLOB FONTS /deps/prospector-zmk-module/boards/shields/prospector_adapter/src/fonts/*.c)
add_library(lvgl STATIC ${LV_SOURCES})
target_include_directories(lvgl PUBLIC /deps/modules/lib/gui/lvgl ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_definitions(lvgl PUBLIC LV_CONF_INCLUDE_SIMPLE LV_COLOR_16_SWAP=${WPM_SWAP})
add_executable(wpm main.c ${FONTS})
target_compile_definitions(wpm PRIVATE CONFIG_PROSPECTOR_STATUS_SCREEN_OPERATOR=1)
target_include_directories(wpm PRIVATE /work/source/src /deps/prospector-zmk-module/boards/shields/prospector_adapter/include)
if(WPM_SWAP EQUAL 0)
 target_compile_definitions(wpm PRIVATE CONFIG_LISM_XIAO147_TOUCH=1)
endif()
target_compile_options(wpm PRIVATE -fsanitize=address,undefined)
target_link_options(wpm PRIVATE -fsanitize=address,undefined)
target_link_libraries(wpm PRIVATE lvgl m)
''')
results=[]
for swap in [1,0]:
 B=D/f'build-{swap}'
 for cmd in [['cmake','-S',str(D),'-B',str(B),f'-DWPM_SWAP={swap}'],['cmake','--build',str(B),'-j6']]:
  r=subprocess.run(cmd,capture_output=True,text=True);assert not r.returncode,r.stdout+r.stderr
 r=subprocess.run([str(B/'wpm')],capture_output=True,text=True,timeout=45);assert not r.returncode,r.stdout+r.stderr
 results.append({'swap':swap,'stdout':r.stdout});print(r.stdout)
# Reproduce both concrete defects using the unpatched production functions.
negative=[]
try:
 for case,expected in [('original-init','work scheduled before init'),('original-queue','LVGL work scheduled on system queue')]:
  old=original
  if case=='original-queue':
   init='    k_work_init_delayable(&wpm_smooth_work, wpm_smooth_work_handler);'
   old=old.replace(init,'').replace('    widget_wpm_meter_init();',init+'\n    widget_wpm_meter_init();')
  old_state=old[old.index('static sys_slist_t'):old.index('struct layer_state')]
  (D/'main.c').write_text(pre+old_state+'\n'.join(function(old,n) for n in functions)+main)
  r=subprocess.run(['cmake','--build',str(D/'build-1'),'--target','wpm','-j6'],capture_output=True,text=True);assert not r.returncode,r.stdout+r.stderr
  r=subprocess.run([str(D/'build-1/wpm')],capture_output=True,text=True,timeout=20)
  assert r.returncode!=0 and expected in r.stderr,(case,r.returncode,r.stdout,r.stderr)
  negative.append({'case':case,'expected_assertion':expected,'reproduced':True})
finally:
 (D/'main.c').write_text(fixed)
 r=subprocess.run(['cmake','--build',str(D/'build-1'),'--target','wpm','-j6'],capture_output=True,text=True);assert not r.returncode,r.stdout+r.stderr
print('PASS original WPM defects reproduced: scheduling before init, LVGL work on system queue')
report={'passed':True,'original_defect_negative_controls':negative,'hardware_tested':False,'generated_wpm_sha256':hashlib.sha256(src.encode()).hexdigest(),'results':results,'scope':'Actual generated WPM functions, real LVGL partial rendering and byte swap; modeled workqueue scheduling and LCD RAM. Does not establish the physical cause or resolution of reported green lines.'}
(P/'verification/wpm-refresh-native.json').write_text(json.dumps(report,indent=2)+'\n')
