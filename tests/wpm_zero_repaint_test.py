"""Actual generated A zero-only parent repaint, with a counting call-site hook.

Injecting stale LCD pixels demonstrates bounded repair, not the original cause
of the user's hardware artifact. No production files or timing are changed.
"""
from pathlib import Path
import hashlib,json,subprocess,tempfile
P=Path(__file__).resolve().parents[2];S=P/'source'
base=S/'tests/prospector169_pipeline_test.py';ns={'__file__':str(base)}
exec(compile(base.read_text().split("bridge=r'''",1)[0],str(base),'exec'),ns)
parts=list(ns['parts']);parts[0]+='\nstatic unsigned zero_repaints;static void zero_repaint_hook(lv_obj_t*o){zero_repaints++;lv_obj_invalidate(o);}\n'
count=0
for i,s in enumerate(parts):
    if 'static void wpm_smooth_work_handler(struct k_work *work) {' in s:
        assert s.count('lv_obj_invalidate(widget->obj);')==1
        parts[i]=s.replace('lv_obj_invalidate(widget->obj);','zero_repaint_hook(widget->obj);');count+=1
assert count==1,'Actual A zero-only branch must be present exactly once'
main=r'''
static uint8_t panel[280*240*2];static unsigned writes;
static void flush(lv_display_t*d,const lv_area_t*a,uint8_t*map){for(int y=a->y1;y<=a->y2;y++)for(int x=a->x1;x<=a->x2;x++){unsigned from=((y-a->y1)*(a->x2-a->x1+1)+x-a->x1)*2,to=(y*280+x)*2;panel[to]=map[from+1];panel[to+1]=map[from];}writes++;lv_display_flush_ready(d);}
static void run(unsigned ms){unsigned end=now+ms;while(now<end){unsigned next=((now/10)+1)*10;if(wpm_smooth_work.pending&&wpm_smooth_work.due<next)next=wpm_smooth_work.due;if(next>end)next=end;if(next>now){lv_tick_inc(next-now);now=next;}if(now%10==0)lv_timer_handler();if(wpm_smooth_work.pending&&now>=wpm_smooth_work.due){assert(wpm_smooth_work.q==&display_q);wpm_smooth_work.pending=false;wpm_smooth_work.fn(&wpm_smooth_work.work);}}}
static void target(unsigned n,unsigned ms){wpm_meter_update_cb((struct wpm_meter_state){n});run(ms);}
int main(void){lv_init();lv_display_t*d=lv_display_create(280,240);static uint8_t buffer[26880];lv_display_set_color_format(d,LV_COLOR_FORMAT_RGB565);lv_display_set_buffers(d,buffer,NULL,sizeof(buffer),LV_DISPLAY_RENDER_MODE_PARTIAL);lv_display_set_flush_cb(d,flush);lv_obj_t*screen=zmk_display_status_screen();lv_screen_load(screen);run(200);assert(zero_repaints==0);for(unsigned i=0;i<4;i++)target(0,100);assert(zero_repaints==0);puts("PASS startup/continuous zero parent-repaint count=0");
 target(38,5000);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"38")&&zero_repaints==0);target(9,6000);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"9")&&zero_repaints==0);puts("PASS ordinary rise/descent never call zero parent repaint");
 unsigned inside=(80*280+265)*2,outside=(34*280+20)*2;uint8_t correct[2]={panel[inside],panel[inside+1]};panel[inside]=0xde;panel[inside+1]=0xad;panel[outside]=0xbe;panel[outside+1]=0xef;
 target(0,8000);assert(zero_repaints==1 && !strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"0"));assert(panel[inside]==correct[0]&&panel[inside+1]==correct[1]);assert(panel[outside]==0xbe&&panel[outside+1]==0xef);for(unsigned i=0;i<4;i++)target(0,100);assert(zero_repaints==1);puts("PASS nonzero->zero once; injected inactive far-bar stale bytes repaired; outside y34 sentinel preserved; continued0 no extra");
 /* A stale held peak with zero numeric/bar state still needs exactly one repair. */
 displayed_wpm=target_wpm=0;prev_active_bars=0;peak_position=3;peak_hold_counter=8;wpm_meter_render(0);run(100);assert(!lv_obj_has_flag(wpm_meter_widget.peak_indicator,LV_OBJ_FLAG_HIDDEN));
 target(0,100);assert(zero_repaints==2&&peak_position==0&&lv_obj_has_flag(wpm_meter_widget.peak_indicator,LV_OBJ_FLAG_HIDDEN));target(0,100);assert(zero_repaints==2);puts("PASS old peak->zero once, zero afterward no extra");
 lv_obj_add_flag(wpm_meter_widget.obj,LV_OBJ_FLAG_HIDDEN);target(88,5000);assert(zero_repaints==2);target(0,8000);assert(zero_repaints==3);lv_obj_remove_flag(wpm_meter_widget.obj,LV_OBJ_FLAG_HIDDEN);run(200);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"0"));assert(panel[outside]==0xbe&&panel[outside+1]==0xef);puts("PASS another input/zero while page region hidden calls once; show restores zero");
 mirror_active=1;mirror_wpm=38;target(0,5000);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"38")&&zero_repaints==3);mirror_wpm=0;target(99,8000);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"0")&&zero_repaints==4);target(1,200);assert(zero_repaints==4);puts("PASS mirrored WPM overrides local input, mirrored nonzero->zero once");
 printf("PASS actual A zero-only repaint calls=%u writes=%u; 48row real LVGL natural display10ms/smooth33ms; no production changes; injected residue is bounded-repair test, not root-cause reproduction\n",zero_repaints,writes);
}
'''
code='\n'.join(parts)+main;B=P/'wpm-native/build-1';fonts=[str(p) for p in (B/'CMakeFiles/wpm.dir').rglob('*.o') if p.name!='main.c.o'];V=P/'verification';output=[]
with tempfile.TemporaryDirectory(prefix='wpm-zero-native-') as directory:
 t=Path(directory);(t/'main.c').write_text(code);(t/'zephyr/logging').mkdir(parents=True);(t/'zephyr/kernel.h').write_text('#define SYS_INIT(...)\n#define IS_ENABLED(x) 0\n');(t/'zephyr/logging/log.h').write_text('#define LOG_MODULE_REGISTER(...)\n#define LOG_WRN(...)\n')
 cmd=['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined','-DLV_CONF_INCLUDE_SIMPLE','-DLV_COLOR_16_SWAP=1','-DCONFIG_PROSPECTOR_STATUS_SCREEN_OPERATOR=1','-I'+str(t),'-I/deps/modules/lib/gui/lvgl','-I'+str(P/'wpm-native'),'-I'+str(S/'src'),'-I/deps/prospector-zmk-module/boards/shields/prospector_adapter/include',str(t/'main.c'),*fonts,str(B/'liblvgl.a'),'-lm','-o',str(t/'zero')]
 r=subprocess.run(cmd,capture_output=True,text=True);output.append(r.stdout+r.stderr);compile_rc=r.returncode
 if not compile_rc:r=subprocess.run([str(t/'zero')],capture_output=True,text=True);output.append(r.stdout+r.stderr)
 print(''.join(output),end='');log=V/'wpm-zero-repaint-v50412.log';log.write_text(''.join(output));hashes=ns['source_hashes'];changed=[n for n,h in hashes.items() if hashlib.sha256((P/n).read_bytes()).hexdigest()!=h];rc=r.returncode or bool(changed)
 (V/'wpm-zero-repaint-v50412-result.json').write_text(json.dumps({'passed':rc==0,'exit_code':int(rc),'compile_exit_code':compile_rc,'source_changed_during_run':changed,'test_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'actual_sources':hashes,'log':log.name,'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'hook':'Only replace the single actual A zero-branch lv_obj_invalidate(widget->obj) call with counter+same lv_obj_invalidate. All actual branch conditions and rendering execute unchanged.','boundary':'Real LVGL actual generated A WPM/constructors; ideal synchronous LCD memory, external telemetry mocks. Padded far-bar residue injection demonstrates zero-path repair and outside sentinel preservation, not artifact cause. Executable ASanUBSan; LVGL reused uninstrumented static library. No B/RF/SPI/hardware proof.'},indent=2)+'\n');raise SystemExit(rc)
