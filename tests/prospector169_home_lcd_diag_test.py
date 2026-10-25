"""Actual A generated full home; native LVGL LCD RAM and telemetry fixture.

Uses display-dongle_a explicitly, never the older display-dongle alias. Actual
five constructors/status_screen/final hooks, lock row, icons, WPM scheduler and
battery drawing execute. Observer registration/telemetry/transport are modeled;
modifier/layer active colors are fixture inputs. Reuses read-only native LVGL
and font objects: ASan/UBSan instruments this executable, not that LVGL library.
"""
from pathlib import Path
import ast,hashlib,json,re,subprocess,sys,tempfile
from elftools.elf.elffile import ELFFile
P=Path(__file__).resolve().parents[2];S=P/'source';sys.path.insert(0,str(S/'tools'))
from prepare_hog_routing import function
from build_upstream_esb import production_fingerprint
D=P/'workspace/display-dongle_a/boards/shields/prospector_adapter/src/layouts/operator'
assert D.is_dir() and 'display-dongle_a' in str(D)
with (P/'workspace/build-dongle_a/zephyr/zmk.elf').open('rb') as f:
    elf=ELFFile(f);symbol_size=elf.get_section_by_name('.symtab').get_symbol_by_name('layer_display_widget')[0]['st_size']
    assert elf.elfclass==32
layers=(symbol_size-8)//4
assert symbol_size==8+4*layers
hdr=(P/'workspace/build-dongle_a/zephyr/include/generated/zephyr/devicetree_generated.h').read_text()
assert layers==int(re.search(r'^#define DT_N_S_keymap_CHILD_NUM_STATUS_OKAY (\d+)$',hdr,re.M)[1])
sources={n:(D/(n+'.c')).read_text() for n in ['modifier_indicator','wpm_meter','layer_display','battery_circles','output','status_screen']}
assert 'kb_prospector169_wpm_layout(' in sources['status_screen']
strip=lambda s:re.sub(r'^\s*#\s*(?:include|pragma once).*$', '',s,flags=re.M)
tree=ast.parse((S/'tests/wpm_refresh_native_test.py').read_text())
pre=next(n.value.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='pre' for t in n.targets))
pre=pre.replace('struct kb_mirror{uint8_t wpm;}','struct kb_mirror{uint8_t wpm,mods,indicators,profile;}')
pre=pre.replace('void kb_route_snapshot(struct kb_route_view*v){*v=(struct kb_route_view){.mode=KB_ROUTE_DONGLE};}', 'static uint8_t route_mode=KB_ROUTE_DONGLE;void kb_route_snapshot(struct kb_route_view*v){*v=(struct kb_route_view){.mode=route_mode};}')
pre+=f'\n#define ZMK_KEYMAP_LAYERS_LEN {layers}\n#define ZMK_BLE_PROFILE_COUNT 5\n#define PERIPHERAL_COUNT 2\n#define ARG_UNUSED(x) (void)(x)\ntypedef void *sys_snode_t;\n'
pre+='\n#include <kb_route/modifier_band.h>\n#include <kb_route/prospector169_layout.h>\n'
for n in ['modifier_indicator','layer_display','battery_circles','output']:pre+=strip((D/(n+'.h')).read_text())
pre+=r'''
enum zmk_transport{ZMK_TRANSPORT_USB,ZMK_TRANSPORT_BLE};
static enum zmk_transport active_transport=ZMK_TRANSPORT_USB;static unsigned active_profile_index;
static const char*modifier_order_get_text(int i){const char*n[]={"GUI","ALT","CTRL","SHFT"};return n[i];}
static unsigned lock_retry_requests;
static int k_work_reschedule(struct k_work_delayable*w,int t){(void)w;(void)t;lock_retry_requests++;return 0;}
static void kb_mirror_ui_start(void){}static void lism_screen_create(lv_obj_t*s){(void)s;}
'''
pre += '\nstatic unsigned lcd_ui_samples; static uint32_t lcd_bg; static char lcd_text[16]; static lv_area_t lcd_box;\nvoid lism_lcd_trace_ui(uint32_t bg,const char *text,int16_t x1,int16_t y1,int16_t x2,int16_t y2){lcd_ui_samples++;lcd_bg=bg;snprintf(lcd_text,sizeof(lcd_text),"%s",text);lcd_box=(lv_area_t){x1,y1,x2,y2};}\n'
pre += strip((S/'src/kb_route/lcd_ui_diagnostics.h').read_text())
parts=[pre]
# Only external listener/list registration is removed from the four secondary
# constructors. The WPM constructor and scheduler remain verbatim.
def constructor(src,name):
    f=function(src,name)
    f=f.replace('    sys_slist_append(&widgets, &widget->node);','')
    for call in ['widget_modifier_indicator_init();','widget_layer_display_init();','widget_battery_circles_battery_init();','widget_battery_circles_connection_init();','lism_esb_battery_sync_start();']:
        f=f.replace('    '+call,'')
    if name=='zmk_widget_output_init':
        f=re.sub(r'    if \(sys_slist_is_empty\(&widgets\)\) \{.*?\n    \}', '',f,flags=re.S)
        f=f.replace('    if(!output_timer)output_timer=lv_timer_create(output_refresh,100,NULL);','')
    return f
src=sources['modifier_indicator']
parts += [function(src,n) for n in ['create_separator','create_mod_label']]
parts += [constructor(src,'zmk_widget_modifier_indicator_init'),function(src,'zmk_widget_modifier_indicator_obj')]
src=sources['wpm_meter'];parts += [src[src.index('static sys_slist_t'):src.index('struct layer_state')]]
parts += [function(src,n) for n in ['wpm_meter_render','wpm_smooth_work_handler','wpm_meter_update_cb','zmk_widget_wpm_meter_init','zmk_widget_wpm_meter_obj']]
parts += ['static void widget_wpm_meter_init(void){wpm_meter_update_cb((struct wpm_meter_state){0});}']
src=sources['layer_display'];parts += [constructor(src,'zmk_widget_layer_display_init'),function(src,'zmk_widget_layer_display_obj')]
src=sources['battery_circles'];parts += [src[src.index('#ifndef PERIPHERAL_COUNT'):src.index('static void init_styles')]]
parts += [function(src,n) for n in ['init_styles','cubic_bezier_y','cubic_bezier_x','cubic_bezier_solve','lv_anim_path_bezier_battery','arc_width_anim_cb','animate_arc_width','arc_value_anim_cb','animate_arc_value','update_peripheral_display','set_battery_level','set_connection_status']]
parts += [constructor(src,'zmk_widget_battery_circles_init'),function(src,'zmk_widget_battery_circles_obj')]
src=sources['output'];parts += [function(src,n) for n in ['set_usb_btn_state','set_ble_btn_state','set_slot_active','update_output_widget','create_toggle_btn','create_slot_btn']]
parts += [constructor(src,'zmk_widget_output_init'),function(src,'zmk_widget_output_obj')]
lock=(S/'src/prospector_lock_indicators.c').read_text();parts += ['#define create_separator lock_create_separator\n',lock[lock.index('#define HID_INDICATOR_NUM_LOCK'):lock.index('static lv_obj_t *get_operator_modifier_row')]]
parts += [function(lock,n) for n in ['get_operator_modifier_row','create_lock_label','create_separator','create_lock_indicator_row','lock_indicator_update_cb']]
parts += ['#undef create_separator\n']
src=sources['status_screen'];parts += [src[src.index('static struct zmk_widget_modifier_indicator'):src.index('lv_obj_t *zmk_display_status_screen')],function(src,'zmk_display_status_screen')]
main=r'''
static uint8_t panel[280*240*2],reference[sizeof(panel)];static unsigned writes,ticks,max_flush_pixels;
static void flush(lv_display_t*d,const lv_area_t*a,uint8_t*map){
 unsigned pixels=(a->x2-a->x1+1)*(a->y2-a->y1+1);if(pixels>max_flush_pixels)max_flush_pixels=pixels;assert(pixels<=280*12);
 for(int y=a->y1;y<=a->y2;y++)for(int x=a->x1;x<=a->x2;x++){unsigned from=((y-a->y1)*(a->x2-a->x1+1)+x-a->x1)*2,to=(y*280+x)*2;
#if LV_COLOR_16_SWAP
 panel[to]=map[from+1];panel[to+1]=map[from];
#else
 panel[to]=map[from];panel[to+1]=map[from+1];
#endif
 }writes++;lv_display_flush_ready(d);
}
static void top_band(void){for(unsigned y=33;y<36;y++)for(unsigned x=0;x<280;x++){unsigned n=(y*280+x)*2;if(panel[n]||panel[n+1])fprintf(stderr,"top-band x=%u y=%u value=%02x%02x\n",x,y,panel[n],panel[n+1]);assert(!panel[n]&&!panel[n+1]);}}
static void step(lv_display_t*d){now+=33;lv_tick_inc(33);if(wpm_smooth_work.pending&&now>=wpm_smooth_work.due){assert(wpm_smooth_work.q==&display_q);wpm_smooth_work.pending=false;wpm_smooth_work.fn(&wpm_smooth_work.work);}lv_timer_handler();lv_refr_now(d);top_band();ticks++;}
static void frame(lv_display_t*d,const char*path){lv_refr_now(d);top_band();FILE*f=fopen(path,"wb");assert(f);assert(fwrite(panel,1,sizeof(panel),f)==sizeof(panel));fclose(f);printf("FRAME %s bytes=%zu tick=%u label=%s\n",path,sizeof(panel),ticks,lv_label_get_text(wpm_meter_widget.wpm_label));}
static lv_area_t rect(lv_obj_t*o){lv_area_t a;lv_obj_get_coords(o,&a);return a;}
static void box_at(lv_obj_t*o,int x,int y,int w,int h){lv_area_t a=rect(o);assert(a.x1==x&&a.y1==y&&a.x2==x+w-1&&a.y2==y+h-1);}
static void separate(lv_obj_t*a,lv_obj_t*b){lv_area_t x=rect(a),y=rect(b);assert(x.x2<y.x1||y.x2<x.x1||x.y2<y.y1||y.y2<x.y1);}
static void geometry(void){
 lv_obj_update_layout(modifier_indicator_widget.obj);
 box_at(modifier_indicator_widget.obj,10,0,260,33);box_at(wpm_meter_widget.obj,10,36,260,84);box_at(layer_display_widget.obj,10,130,260,6);box_at(signal_icons,10,140,124,26);box_at(icons,148,142,116,22);box_at(battery_circles_widget.obj,11,170,132,62);box_at(output_widget.obj,148,170,116,62);
 assert(LAYER_DOT_COUNT==EXPECTED_LAYERS);assert(sizeof(layer_display_widget.dots)/sizeof(layer_display_widget.dots[0])==EXPECTED_LAYERS);assert(sizeof(output_widget.slots)/sizeof(output_widget.slots[0])==5);
 lv_obj_t*regions[]={modifier_indicator_widget.obj,wpm_meter_widget.obj,layer_display_widget.obj,signal_icons,icons,battery_circles_widget.obj,output_widget.obj};for(unsigned i=0;i<7;i++)for(unsigned j=i+1;j<7;j++)separate(regions[i],regions[j]);
 for(unsigned i=0;i<26;i++){lv_obj_t*o=wpm_meter_widget.bars[i];assert(lv_obj_get_height(o)==84&&!lv_obj_has_flag(o,LV_OBJ_FLAG_SCROLLABLE)&&lv_obj_get_scrollbar_mode(o)==LV_SCROLLBAR_MODE_OFF);assert(!lv_obj_get_style_pad_all(o,0));lv_area_t a=rect(o),p=rect(wpm_meter_widget.obj);assert(a.x1>=p.x1&&a.x2<=p.x2&&a.y1==p.y1&&a.y2==p.y2);}
 lv_obj_t*p=wpm_meter_widget.peak_indicator;assert(lv_obj_get_height(p)==84&&!lv_obj_has_flag(p,LV_OBJ_FLAG_SCROLLABLE)&&lv_obj_get_scrollbar_mode(p)==LV_SCROLLBAR_MODE_OFF);assert(!lv_obj_get_style_pad_top(p,0)&&!lv_obj_get_style_pad_bottom(p,0)&&!lv_obj_get_style_pad_left(p,0)&&!lv_obj_get_style_pad_right(p,0));
 printf("GEOMETRY actual A: WPM10,36/260x84 layer10,130/260x6 count=%d signal10,142/124x22 mode160,140/100x27 battery11,170 output148,170; five slots; all top-level regions disjoint\n",LAYER_DOT_COUNT);
}
static void telem(unsigned n){
 lock_indicator_update_cb((struct lock_indicator_state){.indicators=n&3});
 for(unsigned i=0;i<4;i++)lv_obj_set_style_text_color(modifier_indicator_widget.mod_labels[i],lv_color_hex((n>>i)&1?DISPLAY_COLOR_MOD_ACTIVE:DISPLAY_COLOR_MOD_INACTIVE),0);
 set_battery_level(0,25+n%73);set_battery_level(1,12+n%80);
 for(unsigned i=0;i<LAYER_DOT_COUNT;i++)lv_obj_set_style_bg_color(layer_display_widget.dots[i],lv_color_hex(i==n%LAYER_DOT_COUNT?DISPLAY_COLOR_LAYER_DOT_ACTIVE:DISPLAY_COLOR_LAYER_DOT_INACTIVE),0);
}
int main(int argc,char**argv){
 assert(argc==4);lv_init();lv_display_t*d=lv_display_create(280,240);static uint8_t buffer[280*12*2];lv_display_set_color_format(d,LV_COLOR_FORMAT_RGB565);lv_display_set_buffers(d,buffer,NULL,sizeof(buffer),LV_DISPLAY_RENDER_MODE_PARTIAL);lv_display_set_flush_cb(d,flush);
 lock_indicator_update_cb((struct lock_indicator_state){0});assert(!caps_label&&!num_label&&lock_retry_requests);
 lv_obj_t*screen=zmk_display_status_screen();lv_screen_load(screen);assert(lv_obj_get_child_cnt(modifier_indicator_widget.obj)==7);
 lock_indicator_update_cb((struct lock_indicator_state){0});assert(caps_label&&num_label&&lv_obj_get_child_cnt(modifier_indicator_widget.obj)==11);
 set_connection_status(0,true);set_connection_status(1,true);set_battery_level(0,96);set_battery_level(1,89);kb_layer_label_update(wpm_meter_widget.layer_label,"BASE");lv_obj_update_layout(screen);geometry();
 wpm_meter_update_cb((struct wpm_meter_state){38});for(unsigned i=0;i<100;i++){if(i%20==0)telem(i);step(d);}assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"38"));frame(d,argv[1]);
 wpm_meter_update_cb((struct wpm_meter_state){88});for(unsigned i=0;i<40;i++)step(d);wpm_meter_update_cb((struct wpm_meter_state){12});for(unsigned i=0;i<30;i++){if(i%10==0)telem(i+17);step(d);}assert(!lv_obj_has_flag(wpm_meter_widget.peak_indicator,LV_OBJ_FLAG_HIDDEN));
 wpm_meter_update_cb((struct wpm_meter_state){0});for(unsigned i=0;i<240;i++){if(i%30==0)telem(i+41);step(d);}assert(displayed_wpm==0&&peak_position==0&&lv_obj_has_flag(wpm_meter_widget.peak_indicator,LV_OBJ_FLAG_HIDDEN));assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"0"));frame(d,argv[2]);
 route_mode=KB_ROUTE_BLE;active_transport=ZMK_TRANSPORT_BLE;active_profile_index=4;update_output_widget(&output_widget);refresh(NULL);kb_layer_label_update(wpm_meter_widget.layer_label,"GAMEJOYSTICK");assert(!strcmp(lv_label_get_text(wpm_meter_widget.layer_label),"JOYSTICK"));
 for(unsigned i=0;i<120;i++){if(i%25==0)telem(i+7);step(d);}lv_area_t label=rect(wpm_meter_widget.layer_label),chart=rect(wpm_meter_widget.obj);assert(label.x1>=chart.x1&&label.x2<=chart.x2&&label.y1>=chart.y1&&label.y2<=chart.y2);frame(d,argv[3]);
 assert(ticks>=300);assert(lcd_ui_samples>=4 && lcd_bg==0 && !strcmp(lcd_text,"0"));lv_area_t numeric=rect(wpm_meter_widget.wpm_label);assert(!memcmp(&numeric,&lcd_box,sizeof(numeric)));printf("LCD UI timer samples=%u cached bg=%06x text=%s box=%d,%d,%d,%d\n",lcd_ui_samples,lcd_bg,lcd_text,lcd_box.x1,lcd_box.y1,lcd_box.x2,lcd_box.y2);memcpy(reference,panel,sizeof(panel));lv_obj_invalidate(screen);lv_refr_now(d);assert(!memcmp(reference,panel,sizeof(panel)));top_band();geometry();
 printf("PASS full actual A home: continuous_partial_ticks=%u final_partial_full_equal=1 max_flush_pixels=%u writes=%u; startup four and delayed six modifiers, peak-to-zero, mock mode/battery/locks, actual WPM scheduler/constructors/icons/lock/helper; no intermediate forced full reference\n",ticks,max_flush_pixels,writes);
}
'''
# pad_all has no getter in LVGL; check all four sides explicitly.
main=main.replace('!lv_obj_get_style_pad_all(o,0)','!lv_obj_get_style_pad_top(o,0)&&!lv_obj_get_style_pad_bottom(o,0)&&!lv_obj_get_style_pad_left(o,0)&&!lv_obj_get_style_pad_right(o,0)')
code='\n'.join(parts)+f'\n#define EXPECTED_LAYERS {layers}\n'+main
B=P/'wpm-native/build-1';font_objects=[str(p) for p in (B/'CMakeFiles/wpm.dir').rglob('*.o') if p.name!='main.c.o'];assert font_objects and (B/'liblvgl.a').exists()
V=P/'verification';images=[V/f'home-v50411-{n}.rgb565' for n in ['active','idle','ble']]
with tempfile.TemporaryDirectory(prefix='home-v50411-') as td:
    T=Path(td);(T/'main.c').write_text(code);(T/'zephyr/logging').mkdir(parents=True)
    (T/'zephyr/kernel.h').write_text('#define SYS_INIT(...)\n#define IS_ENABLED(x) 0\n');(T/'zephyr/logging/log.h').write_text('#define LOG_MODULE_REGISTER(...)\n#define LOG_WRN(...)\n')
    cmd=['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined','-DLV_CONF_INCLUDE_SIMPLE','-DLV_COLOR_16_SWAP=1','-DCONFIG_PROSPECTOR_STATUS_SCREEN_OPERATOR=1','-I'+str(T),'-I/deps/modules/lib/gui/lvgl','-I'+str(P/'wpm-native'),'-I'+str(S/'src'),'-I/deps/prospector-zmk-module/boards/shields/prospector_adapter/include',str(T/'main.c'),*font_objects,str(B/'liblvgl.a'),'-lm','-o',str(T/'home')]
    r=subprocess.run(cmd,capture_output=True,text=True);assert r.returncode==0,r.stdout+r.stderr
    r=subprocess.run([str(T/'home'),*[str(p) for p in images]],capture_output=True,text=True);print(r.stdout,end='');assert r.returncode==0,r.stdout+r.stderr
result={'passed':True,'production_fingerprint':production_fingerprint()[0],'test_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'actual_display_source':str(D),'elf_layer_widget_size':symbol_size,'actual_enabled_layer_count':layers,'continuous_partial_ticks':530,'final_partial_full_equal':True,'rgb565_format':'280x240, little-endian RGB565 (LCD panel bytes after configured swap)','images':[{'file':str(p.relative_to(P)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in images],'source_hashes':{n:hashlib.sha256(s.encode()).hexdigest() for n,s in sources.items()},'sanitizer_boundary':'ASan/UBSan on executable/actual extracted C and reused font objects; reused LVGL static library is not instrumented.','mock_boundary':'External ZMK observer/list registration for secondary widgets, telemetry, transport and controller UI attachment mocked; modifier/layer activation color inputs supplied directly. Actual generated five constructors/status_screen/final layout hooks, WPM render/scheduler/update, battery styles/animations/update, output paint functions, lock delayed row/update and actual icons execute.','hardware_tested':False,'output':r.stdout}
(V/'home-layout-v50411-result.json').write_text(json.dumps(result,indent=2)+'\n')
