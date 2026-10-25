"""Actual generated modifier and lock code rendered by real LVGL partial buffers.

Zephyr listener/work scheduling and LCD RAM are mocked. Injecting stale glyph
color tests top-band cleanup; it does NOT reproduce or establish the physical
cause of the photographed fragments. Independent temporary builds touch no
other native test's source/build directories.
"""
from pathlib import Path
import hashlib
import subprocess
import sys
import tempfile

P = Path(__file__).resolve().parents[2]
S = P / 'source'
sys.path.insert(0, str(S / 'tools'))
from prepare_hog_routing import function
from prepare_mirror_display import prepare

U = Path('/deps/prospector-zmk-module/boards/shields/prospector_adapter')
lock = (S / 'src/prospector_lock_indicators.c').read_text()
builder = (S / 'tools/build_upstream_esb.py').read_text()
assert 'kb_modifier_band_layout(zmk_widget_modifier_indicator_obj(&modifier_indicator_widget));' in builder

pre = r'''
#include <lvgl.h>
#include <fonts.h>
#include <modifier_order.h>
#include <kb_route/modifier_band.h>
#include <assert.h>
#include <string.h>
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include "display_colors.h"
#define K_MSEC(x) (x)
#define K_SECONDS(x) (1000*(x))
struct k_work {void(*fn)(struct k_work*);};
struct k_work_delayable {struct k_work work;bool initialized,pending;int delay;};
static int display_q;
static struct k_work *submitted;
static bool display_initialized;
static int *zmk_display_work_q(void){return &display_q;}
static bool zmk_display_is_initialized(void){return display_initialized;}
static void k_work_init(struct k_work*w,void(*f)(struct k_work*)){w->fn=f;}
static void k_work_init_delayable(struct k_work_delayable*w,void(*f)(struct k_work*)){assert(!w->initialized);w->initialized=true;w->work.fn=f;}
static int k_work_schedule(struct k_work_delayable*w,int delay){assert(w->initialized);w->pending=true;w->delay=delay;return 0;}
static int k_work_reschedule(struct k_work_delayable*w,int delay){return k_work_schedule(w,delay);}
static int k_work_submit_to_queue(int*q,struct k_work*w){assert(q==&display_q);submitted=w;return 0;}
static void dispatch_deferred(struct k_work_delayable*w){assert(w->pending);w->pending=false;w->work.fn(&w->work);}
typedef int sys_slist_t;
struct zmk_widget_modifier_indicator {void*node;lv_obj_t *obj,*mod_labels[4];};
static struct zmk_widget_modifier_indicator *registered;
#define SYS_SLIST_STATIC_INIT(x) 0
#define SYS_SLIST_FOR_EACH_CONTAINER(list,item,node) for(item=registered;item;item=NULL)
static void sys_slist_append(void*l,void*n){(void)l;registered=n;}
static void widget_modifier_indicator_init(void);
static void widget_lism_lock_indicators_init(void);
struct kb_mirror {uint8_t mods,indicators;};
static int mirror_active;static struct kb_mirror mirror;
static int kb_mirror_snapshot(struct kb_mirror*m){*m=mirror;return mirror_active;}
typedef int zmk_event_t;
struct zmk_hid_indicators_changed {uint8_t indicators;};
static uint8_t host_indicators;
static const struct zmk_hid_indicators_changed *as_zmk_hid_indicators_changed(const zmk_event_t*e){(void)e;return NULL;}
static uint8_t zmk_hid_indicators_get_current_profile(void){return host_indicators;}
'''
main = r'''
static void widget_modifier_indicator_init(void){modifier_indicator_update_cb((struct modifier_indicator_state){0});}
static void widget_lism_lock_indicators_init(void){lock_indicator_update_cb(lock_indicator_get_state(NULL));}
static unsigned width,height,checks,writes,cleans;
static uint8_t panel[320*240*2],reference[320*240*2],outside[320*240*2];
static void flush(lv_display_t*d,const lv_area_t*a,uint8_t*map){
 assert(a->x1>=0&&a->y1>=0&&a->x2<(int)width&&a->y2<(int)height);
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
static void color(lv_obj_t*o,uint32_t expected){assert(lv_color_eq(lv_obj_get_style_text_color(o,0),lv_color_hex(expected)));}
static void assert_row_bounds(lv_obj_t*row){
 lv_obj_update_layout(row);
#ifndef CONFIG_LISM_XIAO147_TOUCH
 lv_area_t area,content,hor,ver;lv_obj_get_coords(row,&area);lv_obj_get_content_coords(row,&content);
 assert(area.x1==10&&area.y1==0&&area.x2==269&&area.y2==32);
 assert(lv_obj_get_style_pad_top(row,0)==8&&lv_obj_get_style_pad_column(row,0)==0);
 assert(lv_obj_get_content_height(row)==25);
 assert(!lv_obj_has_flag(row,LV_OBJ_FLAG_SCROLLABLE)&&lv_obj_get_scrollbar_mode(row)==LV_SCROLLBAR_MODE_OFF);

 // LVGL returns negative bottom/right extents for unused space. Require
 // zero actual offset and no positive scrollable overflow, not zero slack.
 assert(!lv_obj_get_scroll_x(row)&&!lv_obj_get_scroll_y(row)&&!lv_obj_get_scroll_top(row)&&!lv_obj_get_scroll_left(row));
 assert(lv_obj_get_scroll_bottom(row)<=0&&lv_obj_get_scroll_right(row)<=0);
 lv_obj_get_scrollbar_area(row,&hor,&ver);
 assert((hor.x2<hor.x1||hor.y2<hor.y1)&&(ver.x2<ver.x1||ver.y2<ver.y1));
 for(unsigned i=0;i<lv_obj_get_child_cnt(row);i++){lv_area_t a;lv_obj_get_coords(lv_obj_get_child(row,i),&a);assert(a.x1>=content.x1&&a.x2<=content.x2&&a.y1>=content.y1&&a.y2<=content.y2);}
#endif
}
static void assert_layout(struct zmk_widget_modifier_indicator*w){
 assert_row_bounds(w->obj);
 lv_obj_update_layout(w->obj);assert(lv_obj_get_child_cnt(w->obj)==11);
 const char *names[]={"GUI","ALT","CTL","SFT","CAP","NUM"};int baseline=-1,end=-1;
 for(unsigned i=0;i<6;i++){
  lv_obj_t*label=lv_obj_get_child(w->obj,2*i);lv_area_t a;lv_obj_get_coords(label,&a);
  assert(!strcmp(lv_label_get_text(label),names[i]));assert(lv_obj_get_style_text_font(label,0)==&FG_Medium_20);
  if(baseline<0)baseline=a.y1;assert(a.y1==baseline&&a.x1>end&&a.x2<(int)width);end=a.x2;
 }
#ifndef CONFIG_LISM_XIAO147_TOUCH
 lv_area_t a;lv_obj_get_coords(w->obj,&a);assert(a.x1==10&&a.y1==0&&a.x2==269&&a.y2==32);
 assert(lv_obj_get_style_pad_top(w->obj,0)==8);
 assert(lv_obj_get_style_bg_opa(w->obj,0)==LV_OPA_COVER);
 assert(lv_color_eq(lv_obj_get_style_bg_color(w->obj,0),lv_color_black()));
 assert(baseline==8+(24-lv_obj_get_height(w->mod_labels[0]))/2);
#else
 // Keep the existing 147 row geometry: six-field extension at y8,h24.
 assert(lv_obj_get_y(w->obj)==8&&lv_obj_get_height(w->obj)==24);
#endif
}
static void inject_band(void){
 memcpy(outside,panel,width*height*2);
 for(unsigned y=0;y<8;y++)for(unsigned x=10;x<270;x++){
  unsigned n=(y*width+x)*2;panel[n]=0xe0;panel[n+1]=0x07; // visible old-glyph color in LCD RAM
 }
}
static void check_clean(lv_display_t*d,lv_obj_t*screen){
 lv_refr_now(d);
 for(unsigned y=0;y<height;y++)for(unsigned x=0;x<width;x++){
  unsigned n=(y*width+x)*2;
  if(y<8&&x>=10&&x<270){if(panel[n]!=reference[n]||panel[n+1]!=reference[n+1])fprintf(stderr,"top-band mismatch x=%u y=%u got=%02x%02x full_reference=%02x%02x\n",x,y,panel[n],panel[n+1],reference[n],reference[n+1]);assert(panel[n]==reference[n]&&panel[n+1]==reference[n+1]);}
  if(y>=33||x<10||x>=270)assert(panel[n]==outside[n]&&panel[n+1]==outside[n+1]);
 }
 cleans++;check_frame(d,screen);
}
int main(void){
#ifdef CONFIG_LISM_XIAO147_TOUCH
 width=320;height=172;
#else
 width=280;height=240;
#endif
 lv_init();lv_display_t*d=lv_display_create(width,height);static uint8_t buf[280*12*2];
 lv_display_set_color_format(d,LV_COLOR_FORMAT_RGB565);lv_display_set_buffers(d,buf,NULL,sizeof(buf),LV_DISPLAY_RENDER_MODE_PARTIAL);lv_display_set_flush_cb(d,flush);
 lv_obj_t*screen=lv_screen_active();lv_obj_set_style_pad_all(screen,0,0);lv_obj_set_style_bg_color(screen,lv_color_black(),0);lv_obj_set_style_bg_opa(screen,255,0);
 lism_lock_indicators_init();dispatch_deferred(&deferred_init_work);assert(deferred_init_work.pending&&!submitted);
 display_initialized=true;dispatch_deferred(&deferred_init_work);assert(submitted==&display_init_work);submitted->fn(submitted);submitted=NULL;
 assert(!caps_label&&!num_label&&deferred_init_work.pending);
 struct zmk_widget_modifier_indicator widget={0};zmk_widget_modifier_indicator_init(&widget,screen);lv_obj_set_pos(widget.obj,25,8);kb_modifier_band_layout(widget.obj);assert(lv_obj_get_child_cnt(widget.obj)==7);assert_row_bounds(widget.obj);
 lock_indicator_update_cb((struct lock_indicator_state){0});assert(!caps_label&&!num_label);
 // The real operator has four later widgets. Keep equivalent child ordering,
 // and colored objects below the row as pixel sentinels for over-clearing.
 for(unsigned i=0;i<4;i++){
  lv_obj_t*o=lv_obj_create(screen);lv_obj_set_pos(o,0,36+i*24);lv_obj_set_size(o,width,12);
  lv_obj_set_style_bg_color(o,lv_color_hex(0x25374b+i*0x122211),0);lv_obj_set_style_bg_opa(o,255,0);
  lv_obj_set_style_border_width(o,0,0);lv_obj_set_style_radius(o,0,0);
 }
 dispatch_deferred(&deferred_init_work);assert(submitted==&display_init_work);submitted->fn(submitted);submitted=NULL;
 assert(caps_label&&num_label);assert_layout(&widget);check_frame(d,screen);
 lv_area_t content,hor,ver,child_union={.x1=32767,.y1=32767,.x2=-32768,.y2=-32768};
 lv_obj_get_content_coords(widget.obj,&content);lv_obj_get_scrollbar_area(widget.obj,&hor,&ver);
 printf("ROW_STYLE %ux%u gap_column=%d gap_row=%d\n",width,height,(int)lv_obj_get_style_pad_column(widget.obj,0),(int)lv_obj_get_style_pad_row(widget.obj,0));
 for(unsigned i=0;i<6;i++){lv_area_t a;lv_obj_t*l=lv_obj_get_child(widget.obj,2*i);lv_obj_get_coords(l,&a);printf("LABEL %s coords=%d,%d..%d,%d\n",lv_label_get_text(l),a.x1,a.y1,a.x2,a.y2);}

 for(unsigned i=0;i<lv_obj_get_child_cnt(widget.obj);i++){lv_area_t a;lv_obj_get_coords(lv_obj_get_child(widget.obj,i),&a);if(a.x1<child_union.x1)child_union.x1=a.x1;if(a.y1<child_union.y1)child_union.y1=a.y1;if(a.x2>child_union.x2)child_union.x2=a.x2;if(a.y2>child_union.y2)child_union.y2=a.y2;}
 printf("DIAG %ux%u scrollable=%d mode=%d scroll_xy=%d,%d scroll_tblr=%d,%d,%d,%d content=%d,%d..%d,%d content_wh=%d,%d child_union=%d,%d..%d,%d font_line=%d label_height=%d scrollbar_h=%d,%d..%d,%d scrollbar_v=%d,%d..%d,%d pixel260_6=%02x%02x\n",width,height,lv_obj_has_flag(widget.obj,LV_OBJ_FLAG_SCROLLABLE),lv_obj_get_scrollbar_mode(widget.obj),(int)lv_obj_get_scroll_x(widget.obj),(int)lv_obj_get_scroll_y(widget.obj),(int)lv_obj_get_scroll_top(widget.obj),(int)lv_obj_get_scroll_bottom(widget.obj),(int)lv_obj_get_scroll_left(widget.obj),(int)lv_obj_get_scroll_right(widget.obj),content.x1,content.y1,content.x2,content.y2,(int)lv_obj_get_content_width(widget.obj),(int)lv_obj_get_content_height(widget.obj),child_union.x1,child_union.y1,child_union.x2,child_union.y2,(int)lv_font_get_line_height(&FG_Medium_20),(int)lv_obj_get_height(widget.mod_labels[0]),hor.x1,hor.y1,hor.x2,hor.y2,ver.x1,ver.y1,ver.x2,ver.y2,panel[(6*width+260)*2],panel[(6*width+260)*2+1]);

 for(unsigned cycle=0;cycle<4;cycle++)for(unsigned mask=0;mask<16;mask++){
  struct modifier_indicator_state mods={0};for(unsigned i=0;i<4;i++)mods.mods[i]=(mask>>i)&1;
  modifier_indicator_update_cb(mods);
  for(unsigned i=0;i<4;i++)color(widget.mod_labels[i],mods.mods[modifier_order_get(i)]?DISPLAY_COLOR_MOD_ACTIVE:DISPLAY_COLOR_MOD_INACTIVE);
  host_indicators=mask&3;lock_indicator_update_cb(lock_indicator_get_state(NULL));
  color(caps_label,host_indicators&2?COLOR_CAPS_ACTIVE:COLOR_INACTIVE);color(num_label,host_indicators&1?COLOR_NUM_ACTIVE:COLOR_INACTIVE);
  assert_layout(&widget);check_frame(d,screen);
 }
#ifdef CONFIG_DT_HAS_ZMK_BEHAVIOR_CAPS_WORD_ENABLED
 struct modifier_indicator_state caps_word={.caps_word=true};modifier_indicator_update_cb(caps_word);
 for(unsigned i=0;i<4;i++)color(widget.mod_labels[i],modifier_order_get(i)==MOD_TYPE_SHIFT?DISPLAY_COLOR_MOD_CAPS_WORD:DISPLAY_COLOR_MOD_INACTIVE);
 check_frame(d,screen);
#endif
 // NAVMARK-associated modifier transitions use the actual callback; the test
 // does not execute a keymap/layer behavior or assume layer-specific UI code.
 mirror_active=1;mirror=(struct kb_mirror){.mods=0xff,.indicators=3};
 modifier_indicator_update_cb((struct modifier_indicator_state){0});lock_indicator_update_cb((struct lock_indicator_state){0});
 for(unsigned i=0;i<4;i++)color(widget.mod_labels[i],DISPLAY_COLOR_MOD_ACTIVE);
 color(caps_label,COLOR_CAPS_ACTIVE);color(num_label,COLOR_NUM_ACTIVE);check_frame(d,screen);
 mirror=(struct kb_mirror){0};modifier_indicator_update_cb((struct modifier_indicator_state){.mods={1,1,1,1}});lock_indicator_update_cb((struct lock_indicator_state){3});
 for(unsigned i=0;i<4;i++)color(widget.mod_labels[i],DISPLAY_COLOR_MOD_INACTIVE);
 color(caps_label,COLOR_INACTIVE);color(num_label,COLOR_INACTIVE);check_frame(d,screen);mirror_active=0;
#ifndef CONFIG_LISM_XIAO147_TOUCH
 // Injection is a cleanup capability check, not an old-defect reproduction.
 for(unsigned n=0;n<8;n++){
  struct modifier_indicator_state target_mods={.mods={n&1,1,0,1}};
  modifier_indicator_update_cb(target_mods);check_frame(d,screen);
  // Same-state replay isolates explicit container invalidation from automatic
  // label recoloring. The full reference includes legitimate LVGL pixels in
  // the top band (e.g. x260,y6); assuming every such pixel is black is invalid.
  inject_band();modifier_indicator_update_cb(target_mods);check_clean(d,screen);
  struct lock_indicator_state target_locks={.indicators=n&3};
  lock_indicator_update_cb(target_locks);check_frame(d,screen);
  inject_band();lock_indicator_update_cb(target_locks);check_clean(d,screen);
 }
#endif
 printf("PASS modifier/lock actual code: %ux%u swap=%d checks=%u writes=%u cleanup=%u; late row, six fields, active/mirror colors, partial/full equality, outside-row preservation. Injected glyph color tests cleanup only, not physical root cause.\n",width,height,LV_COLOR_16_SWAP,checks,writes,cleans);
}
'''
with tempfile.TemporaryDirectory(prefix='modifier-refresh-') as directory:
    D = Path(directory)
    operator = D / 'operator'
    operator.mkdir()
    for name in ['wpm_meter.c', 'layer_display.c', 'modifier_indicator.c']:
        (operator / name).write_bytes((U / 'src/layouts/operator' / name).read_bytes())
    prepare(operator)
    src = (operator / 'modifier_indicator.c').read_text()
    print('generated modifier SHA256', hashlib.sha256(src.encode()).hexdigest(), flush=True)
    print('actual lock SHA256', hashlib.sha256(lock.encode()).hexdigest(), flush=True)
    state = src[src.index('static sys_slist_t'):src.index('static void set_modifier_color')]
    modifier_functions = ['set_modifier_color', 'modifier_indicator_update_cb', 'create_separator', 'create_mod_label', 'zmk_widget_modifier_indicator_init']
    lock_state = lock[lock.index('#define HID_INDICATOR_NUM_LOCK'):lock.index('static lv_obj_t *get_operator_modifier_row')]
    lock_functions = ['get_operator_modifier_row', 'create_lock_label', 'create_separator', 'create_lock_indicator_row', 'lock_indicator_update_cb', 'lock_indicator_get_state', 'display_init_work_handler', 'deferred_init_work_handler', 'lism_lock_indicators_init']
    (D / 'main.c').write_text(pre + state + '\n'.join(function(src, n) for n in modifier_functions) + '\n#define create_separator lock_create_separator\n' + lock_state + '\n'.join(function(lock, n) for n in lock_functions) + '\n#undef create_separator\n' + main)
    (D / 'display_colors.h').write_bytes((U / 'src/layouts/operator/display_colors.h').read_bytes())
    (D / 'lv_conf.h').write_text('#define LV_CONF_H\n#define LV_COLOR_DEPTH 16\n#define LV_MEM_SIZE (96*1024U)\n#define LV_USE_OS LV_OS_NONE\n#define LV_DRAW_BUF_STRIDE_ALIGN 1\n#define LV_USE_ASSERT_MALLOC 1\n#define LV_USE_LOG 1\n#define LV_LOG_LEVEL LV_LOG_LEVEL_WARN\n#define LV_FONT_MONTSERRAT_12 1\n')
    (D / 'zephyr/logging').mkdir(parents=True)
    (D / 'zephyr/kernel.h').write_text('#define SYS_INIT(...)\n#define IS_ENABLED(x) 0\n')
    (D / 'zephyr/logging/log.h').write_text('#define LOG_MODULE_REGISTER(...)\n#define LOG_WRN(...)\n')
    (D / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.20)
project(modifier_pixels C)
file(GLOB_RECURSE LV_SOURCES /deps/modules/lib/gui/lvgl/src/*.c)
file(GLOB FONTS /deps/prospector-zmk-module/boards/shields/prospector_adapter/src/fonts/*.c)
add_library(lvgl STATIC ${LV_SOURCES})
target_include_directories(lvgl PUBLIC /deps/modules/lib/gui/lvgl ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_options(lvgl PRIVATE -fsanitize=address,undefined)
target_compile_definitions(lvgl PUBLIC LV_CONF_INCLUDE_SIMPLE LV_COLOR_16_SWAP=${MOD_SWAP})
add_executable(modifier main.c ${FONTS} /deps/prospector-zmk-module/boards/shields/prospector_adapter/src/modifier_order.c)
target_compile_definitions(modifier PRIVATE CONFIG_PROSPECTOR_STATUS_SCREEN_OPERATOR=1 CONFIG_DT_HAS_ZMK_BEHAVIOR_CAPS_WORD_ENABLED=1)
target_include_directories(modifier PRIVATE /work/source/src /deps/prospector-zmk-module/boards/shields/prospector_adapter/include)
if(MOD_SWAP EQUAL 0)
 target_compile_definitions(modifier PRIVATE CONFIG_LISM_XIAO147_TOUCH=1)
endif()
target_compile_options(modifier PRIVATE -Werror=implicit-function-declaration -fsanitize=address,undefined)
target_link_options(modifier PRIVATE -fsanitize=address,undefined)
target_link_libraries(modifier PRIVATE lvgl m)
''')
    for swap in [1, 0]:
        build = D / f'build-{swap}'
        for cmd in [['cmake', '-S', str(D), '-B', str(build), f'-DMOD_SWAP={swap}'], ['cmake', '--build', str(build), '-j6']]:
            r = subprocess.run(cmd, capture_output=True, text=True)
            assert r.returncode == 0, r.stdout + r.stderr
        r = subprocess.run([str(build / 'modifier')], capture_output=True, text=True, timeout=45)
        assert r.returncode == 0, r.stdout + r.stderr
        print(r.stdout, end='', flush=True)
