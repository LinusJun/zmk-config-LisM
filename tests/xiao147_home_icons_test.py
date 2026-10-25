"""Actual B home constructors/icons plus actual landscape layout, modeled controller callbacks."""
from pathlib import Path
import hashlib,json,subprocess,tempfile
P=Path(__file__).resolve().parents[2];S=P/'source';V=P/'verification'
base=S/'tests/prospector169_home_layout_test.py'
prefix=base.read_text().split("main=r'"+"''",1)[0].replace('display-dongle_a','display-dongle_b').replace('build-dongle_a','build-dongle_b')
ns={'__file__':str(base)};exec(compile(prefix,str(base),'exec'),ns);parts=ns['parts'];function=ns['function'];parts[0]+='\nstatic void xiao147_ui_start(lv_obj_t*s);\n'
xiao=(S/'src/xiao147_ui.c').read_text()
parts+=['static lv_obj_t *screen;',function(xiao,'xiao147_output_profile_count'),function(xiao,'layout_status')]
parts+=['static void controller_layout_fixture(lv_timer_t*t){(void)t;static unsigned labels;unsigned count=lv_obj_get_child_count(lv_obj_get_child(screen,0));if(count>=11&&count!=labels){layout_status();labels=count;}}']
parts+=[function(xiao,'xiao147_ui_start').replace('lv_timer_create(refresh,33,NULL)','lv_timer_create(controller_layout_fixture,33,NULL)')]
main=r'''
static uint8_t panel[320*172*2],reference[sizeof(panel)];static unsigned writes,ticks,max_flush_pixels;
static void flush(lv_display_t*d,const lv_area_t*a,uint8_t*map){unsigned pixels=(a->x2-a->x1+1)*(a->y2-a->y1+1);if(pixels>max_flush_pixels)max_flush_pixels=pixels;assert(pixels<=320*34);for(int y=a->y1;y<=a->y2;y++)for(int x=a->x1;x<=a->x2;x++){assert(x>=0&&x<320&&y>=0&&y<172);unsigned from=((y-a->y1)*(a->x2-a->x1+1)+x-a->x1)*2,to=(y*320+x)*2;panel[to]=map[from];panel[to+1]=map[from+1];}writes++;lv_display_flush_ready(d);}
static void step(lv_display_t*d){now+=33;lv_tick_inc(33);if(wpm_smooth_work.pending&&now>=wpm_smooth_work.due){wpm_smooth_work.pending=false;wpm_smooth_work.fn(&wpm_smooth_work.work);}lv_timer_handler();lv_refr_now(d);ticks++;}
static lv_area_t rect(lv_obj_t*o){lv_area_t a;lv_obj_get_coords(o,&a);return a;}
static void box_at(lv_obj_t*o,int x,int y,int w,int h){lv_area_t a=rect(o);assert(a.x1==x&&a.y1==y&&a.x2==x+w-1&&a.y2==y+h-1);}
static void contained(lv_obj_t*o,lv_obj_t*p){lv_area_t a=rect(o),b=rect(p);assert(a.x1>=b.x1&&a.y1>=b.y1&&a.x2<=b.x2&&a.y2<=b.y2);}
static void separate(lv_obj_t*a,lv_obj_t*b){lv_area_t x=rect(a),y=rect(b);assert(x.x2<y.x1||y.x2<x.x1||x.y2<y.y1||y.y2<x.y1);}
static void geometry(lv_obj_t*home){
 lv_obj_update_layout(home);
 box_at(modifier_indicator_widget.obj,4,0,312,25);box_at(wpm_meter_widget.obj,4,27,312,51);box_at(icons,200,92,116,22);box_at(signal_icons,4,90,124,26);box_at(layer_display_widget.obj,4,82,312,6);box_at(battery_circles_widget.obj,4,118,132,52);box_at(output_widget.obj,144,118,172,52);
 lv_obj_t*regions[]={modifier_indicator_widget.obj,wpm_meter_widget.obj,layer_display_widget.obj,signal_icons,icons,battery_circles_widget.obj,output_widget.obj};for(unsigned i=0;i<7;i++){contained(regions[i],home);assert(!lv_obj_has_flag(regions[i],LV_OBJ_FLAG_SCROLLABLE));for(unsigned j=i+1;j<7;j++)separate(regions[i],regions[j]);}
 assert(FG_Medium_20.line_height==25);assert(LAYER_DOT_COUNT==7);assert(lv_obj_get_child_cnt(output_widget.obj)==7);
 for(unsigned i=0;i<11;i++)contained(lv_obj_get_child(modifier_indicator_widget.obj,i),modifier_indicator_widget.obj);
 for(unsigned i=0;i<26;i++){contained(wpm_meter_widget.bars[i],wpm_meter_widget.obj);assert(lv_obj_get_height(wpm_meter_widget.bars[i])==51);assert(!lv_obj_has_flag(wpm_meter_widget.bars[i],LV_OBJ_FLAG_SCROLLABLE));}
 assert(lv_obj_get_height(wpm_meter_widget.peak_indicator)==51);contained(wpm_meter_widget.wpm_label,wpm_meter_widget.obj);contained(wpm_meter_widget.layer_label,wpm_meter_widget.obj);
 for(unsigned i=0;i<7;i++)contained(layer_display_widget.dots[i],layer_display_widget.obj);
 for(unsigned i=0;i<2;i++){box_at(peripheral_arcs[i],6+i*72,119,50,50);contained(peripheral_label_boxes[i],peripheral_arcs[i]);contained(peripheral_labels[i],peripheral_label_boxes[i]);}
 for(unsigned i=0;i<7;i++){lv_obj_t*b=lv_obj_get_child(output_widget.obj,i);contained(b,output_widget.obj);contained(lv_obj_get_child(b,0),b);assert(lv_obj_get_height(b)==25);}
}
static void frame(lv_display_t*d,const char*name){lv_refr_now(d);FILE*f=fopen(name,"wb");assert(f);assert(fwrite(panel,1,sizeof(panel),f)==sizeof(panel));fclose(f);}
static void full_equal(lv_display_t*d,lv_obj_t*home){memcpy(reference,panel,sizeof(panel));lv_obj_invalidate(home);lv_refr_now(d);assert(!memcmp(reference,panel,sizeof(panel)));}
int main(int argc,char**argv){assert(argc==4);lv_init();lv_display_t*d=lv_display_create(320,172);static uint8_t buffer[320*34*2];lv_display_set_color_format(d,LV_COLOR_FORMAT_RGB565);lv_display_set_buffers(d,buffer,NULL,sizeof(buffer),LV_DISPLAY_RENDER_MODE_PARTIAL);lv_display_set_flush_cb(d,flush);
 lv_obj_t*home=zmk_display_status_screen();lv_screen_load(home);assert(lv_obj_get_child_cnt(modifier_indicator_widget.obj)==7);for(unsigned i=0;i<5;i++)step(d);
 lock_indicator_update_cb((struct lock_indicator_state){0});set_connection_status(0,true);set_connection_status(1,true);set_battery_level(0,96);set_battery_level(1,88);kb_layer_label_update(wpm_meter_widget.layer_label,"BASE");
 wpm_meter_update_cb((struct wpm_meter_state){38});for(unsigned i=0;i<100;i++)step(d);geometry(home);frame(d,argv[1]);full_equal(d,home);
 for(unsigned i=0;i<6;i++){lv_obj_add_flag(icons,LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(signal_icons,LV_OBJ_FLAG_HIDDEN);for(unsigned j=0;j<5;j++)step(d);lv_obj_remove_flag(icons,LV_OBJ_FLAG_HIDDEN);lv_obj_remove_flag(signal_icons,LV_OBJ_FLAG_HIDDEN);route_mode=i%2?KB_ROUTE_BLE:KB_ROUTE_DONGLE;refresh(NULL);for(unsigned j=0;j<5;j++)step(d);}
 wpm_meter_update_cb((struct wpm_meter_state){88});for(unsigned i=0;i<60;i++)step(d);wpm_meter_update_cb((struct wpm_meter_state){12});for(unsigned i=0;i<30;i++)step(d);wpm_meter_update_cb((struct wpm_meter_state){0});for(unsigned i=0;i<240;i++)step(d);assert(displayed_wpm==0&&peak_position==0);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"0"));geometry(home);frame(d,argv[2]);full_equal(d,home);
 route_mode=KB_ROUTE_BLE;active_transport=ZMK_TRANSPORT_BLE;active_profile_index=4;update_output_widget(&output_widget);refresh(NULL);kb_layer_label_update(wpm_meter_widget.layer_label,"GAMEJOYSTICK");assert(!strcmp(lv_label_get_text(wpm_meter_widget.layer_label),"JOYSTICK"));for(unsigned i=0;i<80;i++){if(i%20==0){set_battery_level(0,100-i);set_battery_level(1,88-i);lock_indicator_update_cb((struct lock_indicator_state){.indicators=i/20});}step(d);}geometry(home);frame(d,argv[3]);full_equal(d,home);
 printf("PASS actual B home: 320x172 native312px wide/no transforms; 25px modifier font contained incl delayed locks; seven layer bars; disjoint graph/icons/footer; full L/R/J/BLE; four modes18px/high seven-pixel gaps; two50px rings; USB/BLE and five profiles25px high; BASE/JOYSTICK, peak-to-zero, hide/show, route and battery updates; continuous_ticks=%u final_partial_full_equal=1 writes=%u max_flush_pixels=%u\n",ticks,writes,max_flush_pixels);
}
'''

B=P/'wpm-native/build-0';F=P/'wpm-native/build-1';fonts=[str(p) for p in (F/'CMakeFiles/wpm.dir').rglob('*.o') if p.name!='main.c.o'];assert fonts
with tempfile.TemporaryDirectory(prefix='b-home-icons-') as td:
 t=Path(td);(t/'main.c').write_text('\n'.join(parts)+main);(t/'zephyr/logging').mkdir(parents=True);(t/'zephyr/kernel.h').write_text('#define SYS_INIT(...)\n#define IS_ENABLED(x) 0\n');(t/'zephyr/logging/log.h').write_text('#define LOG_MODULE_REGISTER(...)\n#define LOG_WRN(...)\n')
 cmd=['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined','-DLV_CONF_INCLUDE_SIMPLE','-DLV_COLOR_16_SWAP=0','-DCONFIG_PROSPECTOR_STATUS_SCREEN_OPERATOR=1','-DCONFIG_LISM_XIAO147_TOUCH=1','-I'+str(t),'-I/deps/modules/lib/gui/lvgl','-I'+str(P/'wpm-native'),'-I'+str(S/'src'),'-I/deps/prospector-zmk-module/boards/shields/prospector_adapter/include',str(t/'main.c'),*fonts,str(B/'liblvgl.a'),'-lm','-o',str(t/'home')]
 r=subprocess.run(cmd,capture_output=True,text=True);assert not r.returncode,r.stdout+r.stderr
 r=subprocess.run([str(t/'home'),*[str(V/f'home-b-v50418-{name}.rgb565') for name in ['active','idle','ble']]],capture_output=True,text=True);print(r.stdout,end='');assert not r.returncode,r.stdout+r.stderr
from sys import path
path.insert(0,str(S/'tools'));from build_upstream_esb import production_fingerprint
(V/'home-b-v50418-result.json').write_text(json.dumps({'passed':True,'hardware_tested':False,'display':{'width':320,'height':172,'content_width':312},'exit_code':r.returncode,'production_fingerprint':production_fingerprint()[0],'test_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'output':r.stdout,'boundary':'Actual B five constructors/WPM/icons/lock/layout_status and xiao147_ui_start; mock controller timer applies layout only, ownership/telemetry/device-page model mocked; reused LVGL library not sanitizer instrumented.'},indent=2)+'\n')
