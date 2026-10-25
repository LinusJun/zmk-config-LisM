"""Actual output TU decisions/styles/rendering, asserting raw A flush wire bytes.

No byte normalization is performed in the mock LCD. The real LVGL archive must
produce 54 f4 for the teal background, independently of main's compile defines.
"""
from pathlib import Path
import hashlib,json,re,subprocess,tempfile
P=Path(__file__).resolve().parents[2];S=P/'source';V=P/'verification'
D=P/'workspace/display-dongle_a/boards/shields/prospector_adapter/src/layouts/operator'
source=S/'src/esb_upstream/host_output.c';header=S/'src/esb_upstream/host_output.h';palette=D/'display_colors.h';observer=S/'src/kb_route/lcd_ui_diagnostics.h'
assert source.read_bytes()==(D/'output.c').read_bytes()
assert header.read_bytes()==(D/'output.h').read_bytes()
strip=lambda text:re.sub(r'^\s*#\s*(?:include|pragma once).*$', '',text,flags=re.M)
B=P/'wpm-native/build-1';archive=B/'liblvgl.a';flags=B/'CMakeFiles/lvgl.dir/flags.make'
assert '-DLV_COLOR_16_SWAP=1' in flags.read_text(), 'LVGL library itself must have swap1'
fonts=[str(p) for p in (B/'CMakeFiles/wpm.dir').rglob('*.o') if p.name!='main.c.o'];assert fonts
pre=r'''
#include <lvgl.h>
#include <src/display/lv_display_private.h>
#include <src/core/lv_refr_private.h>
#include <fonts.h>
#include <kb_route/mirror.h>
#include <kb_route/view.h>
#include <kb_route/signal.h>
#include <kb_route/icons.h>
#include <kb_route/icon_input.h>
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#define ZMK_BLE_PROFILE_COUNT 5
#define ARG_UNUSED(x) (void)(x)
typedef struct {void *next;} sys_snode_t;
typedef struct {void *head;} sys_slist_t;
#define SYS_SLIST_STATIC_INIT(x) {0}
static bool sys_slist_is_empty(sys_slist_t*l){return !l->head;}
static void sys_slist_append(sys_slist_t*l,sys_snode_t*n){assert(!l->head);l->head=n;}
#define SYS_SLIST_FOR_EACH_CONTAINER(list,item,node) for(item=(__typeof__(item))(list)->head;item;item=NULL)
enum zmk_transport{ZMK_TRANSPORT_NONE,ZMK_TRANSPORT_USB,ZMK_TRANSPORT_BLE};
struct zmk_endpoint_instance{enum zmk_transport transport;};
static struct zmk_endpoint_instance local_endpoint={ZMK_TRANSPORT_USB};static uint8_t local_profile;
static struct kb_mirror mirror_fixture;static int mirror_owner;
static struct zmk_endpoint_instance zmk_endpoint_get_selected(void){return local_endpoint;}
static uint8_t zmk_ble_active_profile_index(void){return local_profile;}
int kb_mirror_snapshot(struct kb_mirror*out){*out=mirror_fixture;return mirror_owner;}
static uint8_t route_mode=KB_ROUTE_DONGLE;
void kb_route_snapshot(struct kb_route_view*v){*v=(struct kb_route_view){.mode=route_mode};}
bool kb_esb_signal(unsigned source,int8_t*dbm){(void)source;(void)dbm;return false;}

'''
observer_mock=r'''
static struct {uint32_t bg,calls;int16_t x1,y1,x2,y2;char text[16];} ui_number;
static struct {uint32_t bg,calls;uint8_t opa,visible,local,profile;uint16_t state;int8_t owner;int16_t x1,y1,x2,y2;} ui_output;
void lism_lcd_trace_ui(uint32_t bg,const char*text,int16_t x1,int16_t y1,int16_t x2,int16_t y2){ui_number.bg=bg;ui_number.calls++;ui_number.x1=x1;ui_number.y1=y1;ui_number.x2=x2;ui_number.y2=y2;snprintf(ui_number.text,sizeof(ui_number.text),"%s",text);}
void lism_lcd_trace_output_ui(uint32_t bg,uint8_t opa,uint16_t state,uint8_t visible,int8_t owner,uint8_t local,uint8_t profile,int16_t x1,int16_t y1,int16_t x2,int16_t y2){ui_output.bg=bg;ui_output.calls++;ui_output.opa=opa;ui_output.state=state;ui_output.visible=visible;ui_output.owner=owner;ui_output.local=local;ui_output.profile=profile;ui_output.x1=x1;ui_output.y1=y1;ui_output.x2=x2;ui_output.y2=y2;}
'''
main=r'''
static uint8_t wire_panel[280*240*2];static unsigned writes,button_samples,keyboard_samples,checks,phases;
static const unsigned point_x=214,point_y=176;
static lv_display_t *display;static lv_obj_t *screen,*number;static struct zmk_widget_output output;
static void flush(lv_display_t*d,const lv_area_t*a,uint8_t*map){
 unsigned w=a->x2-a->x1+1;
 /* Inspect the exact bytes handed to the transport BEFORE any conversion. */
 if(a->x1<=(int)point_x&&a->x2>=(int)point_x&&a->y1<=(int)point_y&&a->y2>=(int)point_y){
  unsigned from=((point_y-a->y1)*w+point_x-a->x1)*2;
  bool active=active_transport==ZMK_TRANSPORT_BLE;
  if(active){if(map[from]!=0x54||map[from+1]!=0xf4)fprintf(stderr,"WIRE_FAIL owner=%d transport=%d profile=%u raw=%02x %02x\n",mirror_owner,active_transport,active_profile_index,map[from],map[from+1]);assert(map[from]==0x54&&map[from+1]==0xf4);}
  else assert(map[from]==0&&map[from+1]==0);
  button_samples++;
 }
 if(a->x1<=218&&a->x2>=218&&a->y1<=145&&a->y2>=145){
  unsigned from=((145-a->y1)*w+218-a->x1)*2;
  if(route_mode==KB_ROUTE_BLE)assert(map[from]==0x54&&map[from+1]==0xf4);
  else assert(map[from]==0x31&&map[from+1]==0xe8);
  keyboard_samples++;
 }
 for(int y=a->y1;y<=a->y2;y++)for(int x=a->x1;x<=a->x2;x++){
  unsigned from=((y-a->y1)*w+x-a->x1)*2,to=(y*280+x)*2;
  wire_panel[to]=map[from];wire_panel[to+1]=map[from+1]; /* RAW, no swap/decode. */
 }
 writes++;lv_display_flush_ready(d);
}
static void natural(unsigned ms){for(unsigned t=0;t<ms;t+=10){lv_tick_inc(10);lv_timer_handler();}}
static void full_reference(void){
 assert(!display->inv_p);fflush(NULL);pid_t child=fork();assert(child>=0);
 if(!child){uint8_t expected[sizeof(wire_panel)];memcpy(expected,wire_panel,sizeof(expected));lv_obj_invalidate(screen);lv_display_refr_timer(display->refr_timer);_exit(memcmp(expected,wire_panel,sizeof(expected))?21:0);}
 int status;assert(waitpid(child,&status,0)==child&&WIFEXITED(status)&&WEXITSTATUS(status)==0);checks++;
}
static void observer_check(void){
 unsigned before=ui_output.calls;lism_lcd_ui_sample(NULL);assert(ui_output.calls==before+1);
 lv_area_t a;lv_obj_get_coords(output.ble_btn,&a);
 assert(ui_output.bg==(lv_color_to_u32(lv_obj_get_style_bg_color(output.ble_btn,LV_PART_MAIN))&0xffffff));
 assert(ui_output.opa==lv_obj_get_style_bg_opa(output.ble_btn,LV_PART_MAIN)&&ui_output.state==lv_obj_get_state(output.ble_btn)&&ui_output.visible==lv_obj_is_visible(output.ble_btn));
 assert(ui_output.owner==mirror_owner&&ui_output.local==local_endpoint.transport&&ui_output.profile==mirror_fixture.profile);
 assert(ui_output.x1==a.x1&&ui_output.y1==a.y1&&ui_output.x2==a.x2&&ui_output.y2==a.y2);
 lv_obj_get_coords(number,&a);assert(ui_number.bg==0&&!strcmp(ui_number.text,lv_label_get_text(number)));
 assert(ui_number.x1==a.x1&&ui_number.y1==a.y1&&ui_number.x2==a.x2&&ui_number.y2==a.y2);
}
static void phase(const char*name,int owner,enum zmk_transport local,unsigned profile,unsigned mirror_profile,enum zmk_transport expected){
 mirror_owner=owner;local_endpoint.transport=local;local_profile=profile;mirror_fixture.profile=mirror_profile;
 route_mode=owner>0?KB_ROUTE_BLE:owner<0?KB_ROUTE_UNKNOWN:KB_ROUTE_DONGLE;
 output_refresh(NULL); /* Real decision, cache comparison and setter path. */
 refresh(NULL); /* Actual mode icon path. */
 assert(active_transport==expected&&active_profile_index==(owner?mirror_profile:profile));
 uint32_t bg=lv_color_to_u32(lv_obj_get_style_bg_color(output.ble_btn,LV_PART_MAIN))&0xffffff;
 lv_opa_t opa=lv_obj_get_style_bg_opa(output.ble_btn,LV_PART_MAIN);
 if(expected==ZMK_TRANSPORT_BLE)assert(bg==0x569fa7&&opa==255);else assert(opa==0);
 /* Force only the requested button's partial repaint, never a full parent frame. */
 unsigned before=button_samples,kbefore=keyboard_samples;lv_obj_invalidate(output.ble_btn);lv_obj_invalidate(icons);natural(100);assert(button_samples>before&&keyboard_samples>kbefore);
 unsigned n=(point_y*280+point_x)*2;
 if(expected==ZMK_TRANSPORT_BLE)assert(wire_panel[n]==0x54&&wire_panel[n+1]==0xf4);
 else assert(!wire_panel[n]&&!wire_panel[n+1]);
 observer_check();full_reference();phases++;
 printf("PHASE %s owner=%d local=%d resolved=%d profile=%u style=%06x opa=%u state=%u raw=%02x %02x\n",name,owner,local,expected,active_profile_index,bg,opa,(unsigned)lv_obj_get_state(output.ble_btn),wire_panel[n],wire_panel[n+1]);
}
int main(void){_Static_assert(offsetof(struct zmk_widget_output,node)==0,"one-node observer mock preserves actual container ABI");lv_init();display=lv_display_create(280,240);static uint8_t buffer[26880];lv_display_set_color_format(display,LV_COLOR_FORMAT_RGB565);lv_display_set_buffers(display,buffer,NULL,sizeof(buffer),LV_DISPLAY_RENDER_MODE_PARTIAL);lv_display_set_flush_cb(display,flush);screen=lv_obj_create(NULL);lv_obj_set_style_bg_color(screen,lv_color_black(),0);lv_obj_set_style_bg_opa(screen,255,0);lv_screen_load(screen);assert(!zmk_widget_output_init(&output,screen));lv_obj_set_pos(output.obj,148,170);kb_route_icons_create(screen);number=lv_label_create(screen);lv_label_set_text(number,"38");lv_obj_set_style_text_font(number,&FR_Medium_32,0);lv_obj_set_pos(number,10,36);lv_obj_update_layout(screen);lism_lcd_ui_diagnostics_start(screen,number,output.ble_btn);natural(100);
 lv_area_t a;lv_obj_get_coords(output.ble_btn,&a);assert(a.x1==206&&a.y1==170&&a.x2==261&&a.y2==198);assert(point_x>a.x1+6&&point_y>=a.y1+6);
 lv_area_t text;lv_obj_get_coords(lv_obj_get_child(output.ble_btn,0),&text);assert(point_x<text.x1||point_x>text.x2||point_y<text.y1||point_y>text.y2);printf("GEOMETRY BLE button206,170..261,198 label=%d,%d..%d,%d sample214,176 outside glyph; actual keyboard210..233,144..161 sample218,145 solid topborder\n",text.x1,text.y1,text.x2,text.y2);
 for(unsigned cycle=0;cycle<10;cycle++){
  phase("USB",0,ZMK_TRANSPORT_USB,0,0,ZMK_TRANSPORT_USB);
  phase("DG-own-BLE",0,ZMK_TRANSPORT_BLE,2,0,ZMK_TRANSPORT_BLE);
  phase("remote-BLE-same-cache",1,ZMK_TRANSPORT_USB,0,2,ZMK_TRANSPORT_BLE);
  phase("remote-BLE-new-profile",1,ZMK_TRANSPORT_USB,0,4,ZMK_TRANSPORT_BLE);
  phase("stale-NONE",-1,ZMK_TRANSPORT_USB,0,255,ZMK_TRANSPORT_NONE);
  phase("DG-local-USB",0,ZMK_TRANSPORT_USB,1,0,ZMK_TRANSPORT_USB);
 }
 lv_obj_add_flag(output.ble_btn,LV_OBJ_FLAG_HIDDEN);observer_check();assert(!ui_output.visible);
 lv_obj_remove_flag(output.ble_btn,LV_OBJ_FLAG_HIDDEN);lv_obj_add_state(output.ble_btn,LV_STATE_FOCUSED);observer_check();assert(ui_output.visible&&(ui_output.state&LV_STATE_FOCUSED));lv_obj_remove_state(output.ble_btn,LV_STATE_FOCUSED);
 printf("PASS actual LCD15 observer root/number/ble: calls=%u rgb/opa/state/visible/owner/local/mirrorprofile/box match objects, hidden+focused states covered\n",ui_output.calls);
 printf("PASS actual output TU decision/setters/render/palette localBLE vs remoteBLE identical raw54f4: phases=%u fork_fullrefs=%u raw_samples=%u keyboard_samples=%u flushes=%u; library swap1, no native byte normalization; no physical color proof\n",phases,checks,button_samples,keyboard_samples,writes);
}
'''
code=pre+strip(header.read_text())+strip(palette.read_text())+strip(source.read_text())+strip((S/'src/kb_route/icons.c').read_text())+observer_mock+strip(observer.read_text())+main
tracked=[source,header,palette,D/'output.c',D/'output.h',S/'src/kb_route/icons.c',S/'src/kb_route/icon_input.h',observer,archive,flags];hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked}
with tempfile.TemporaryDirectory(prefix='output-color-native-') as directory:
 t=Path(directory);(t/'main.c').write_text(code)
 cmd=['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined','-DLV_CONF_INCLUDE_SIMPLE','-DLV_COLOR_16_SWAP=1','-DCONFIG_PROSPECTOR_STATUS_SCREEN_OPERATOR=1','-I/deps/modules/lib/gui/lvgl','-I'+str(P/'wpm-native'),'-I'+str(S/'src'),'-I/deps/prospector-zmk-module/boards/shields/prospector_adapter/include',str(t/'main.c'),*fonts,str(archive),'-lm','-o',str(t/'test')]
 r=subprocess.run(cmd,capture_output=True,text=True);logs=[r.stdout+r.stderr];compile_rc=r.returncode
 if not compile_rc:r=subprocess.run([str(t/'test')],capture_output=True,text=True);logs.append(r.stdout+r.stderr)
 log=V/'output-color-pipeline-v50414.log';log.write_text(''.join(logs));print(''.join(logs),end='');changed=[n for n,h in hashes.items() if hashlib.sha256(Path(n).read_bytes()).hexdigest()!=h];rc=r.returncode or bool(changed)
 (V/'output-color-pipeline-v50414-result.json').write_text(json.dumps({'passed':rc==0,'exit_code':int(rc),'compile_exit_code':compile_rc,'test_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'log':log.name,'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'source_sha256':hashes,'changed_during_run':changed,'library_swap_evidence':flags.read_text(),'actual_lcd15_observer':True,'raw_point':[214,176],'keyboard_raw_point':[218,145],'expected_active_style_rgb':'569fa7','expected_active_wire_bytes':'54 f4','normalization':'none: mock LCD stores transport bytes unchanged','boundary':'Whole actual host_output.c with actual output.h/generated A palette and actual icons draw/refresh; list/endpoint/profile/mirror providers and observer copy sinks mocked. Actual output_refresh timer/cache/setters/LVGL renderer execute. 60 controlled button/icon partial invalidations plus child-only full references. Actual LCD15 UI observer samples real root/number/ble objects; mock copy sinks retain values only. No physical SPI/panel/colorimetric/RF proof. Executable ASanUBSan; reused LVGL/font archive not instrumented.'},indent=2)+'\n');raise SystemExit(rc)
