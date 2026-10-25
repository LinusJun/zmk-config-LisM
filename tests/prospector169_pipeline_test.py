"""Actual A home and Zephyr flush functions with 10/33 ms native scheduling.

Pthreads model the single-message flush queue/semaphores and synchronous LCD
copy. Fork references never invalidate or force refresh in the parent process.
No radio, physical SPI, electrical behavior or real Zephyr timing is modeled.
"""
from pathlib import Path
import argparse,hashlib,json,re,subprocess,tempfile
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--double-buffer",action="store_true");args=parser.parse_args()
P=Path(__file__).resolve().parents[2];S=P/'source'
base=S/'tests/prospector169_home_layout_test.py'
ns={'__file__':str(base)}
exec(compile(base.read_text().split("main=r'''",1)[0],str(base),'exec'),ns)
parts=ns['parts'];function=ns['function']
# Diagnostic sampling only observes LVGL state; it has no rendering effect.
parts[0]+='\nstatic void lism_lcd_ui_diagnostics_start(lv_obj_t*r,lv_obj_t*n,lv_obj_t*b){(void)r;(void)n;(void)b;}\n'
Z=P/'workspace/zephyr/modules/lvgl'
flush_source=(Z/'lvgl_display.c').read_text()
flush16_source=(Z/'lvgl_display_16bit.c').read_text()
tracked=[Z/'lvgl_display.c',Z/'lvgl_display_16bit.c',S/'src/kb_route/icons.c',
         ns['D']/'wpm_meter.c',ns['D']/'status_screen.c',base,S/'tests/wpm_refresh_native_test.py',
         S/'src/kb_route/prospector169_layout.h',S/'src/kb_route/icon_input.h',S/'src/kb_route/signal.h']
source_hashes={str(p.relative_to(P)):hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked}
# The shared extractor includes static/extern return types supported here.
def actual(name,source):
    pattern=re.search(r'(?m)^(?:static )?void '+name+r'\(',source);assert pattern,name
    begin=source.index('{',pattern.start());end=begin+1;depth=1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[pattern.start():end]
bridge=r'''
#include <pthread.h>
#include <sched.h>
#include <unistd.h>
#include <sys/wait.h>
#include <src/display/lv_display_private.h>
#include <src/core/lv_refr_private.h>
#define CONFIG_LV_Z_FLUSH_THREAD 1
#define K_FOREVER (-1)
struct device{int dummy;};
struct display_buffer_descriptor{uint32_t buf_size;uint16_t width,height,pitch;bool frame_incomplete;};
struct lvgl_disp_data{const struct device *display_dev;};
struct lvgl_display_flush{lv_display_t *display;uint16_t x,y;struct display_buffer_descriptor desc;void *buf;};
struct sem_mock{pthread_mutex_t mutex;pthread_cond_t cond;unsigned value;};
#define K_SEM_DEFINE(name,initial,limit) static struct sem_mock name={PTHREAD_MUTEX_INITIALIZER,PTHREAD_COND_INITIALIZER,initial}
K_SEM_DEFINE(flush_complete,0,1);K_SEM_DEFINE(flush_required,0,1);
static unsigned no_pending_waits,completion_waits,blocking_waits;
static void observe_render_overlap(void);static void assert_completion_owner(void);
static void prepare_transfer(const struct lvgl_display_flush*request);static void wait_for_transfer_start(void);
static void k_sem_give(struct sem_mock*s){pthread_mutex_lock(&s->mutex);s->value=1;pthread_cond_broadcast(&s->cond);pthread_mutex_unlock(&s->mutex);}
static void k_sem_reset(struct sem_mock*s){pthread_mutex_lock(&s->mutex);s->value=0;pthread_mutex_unlock(&s->mutex);}
static int k_sem_take(struct sem_mock*s,int timeout){pthread_mutex_lock(&s->mutex);if(!timeout&&!s->value){if(s==&flush_required)no_pending_waits++;pthread_mutex_unlock(&s->mutex);return -1;}if(s==&flush_complete){completion_waits++;if(!s->value)blocking_waits++;observe_render_overlap();}while(!s->value)pthread_cond_wait(&s->cond,&s->mutex);s->value--;if(s==&flush_complete)assert_completion_owner();pthread_mutex_unlock(&s->mutex);return 0;}
static struct {pthread_mutex_t mutex;pthread_cond_t cond;bool full,stop;struct lvgl_display_flush request;} flush_queue={.mutex=PTHREAD_MUTEX_INITIALIZER,.cond=PTHREAD_COND_INITIALIZER};
static void k_msgq_get(void*q,void*out,int timeout){(void)q;(void)timeout;pthread_mutex_lock(&flush_queue.mutex);while(!flush_queue.full&&!flush_queue.stop)pthread_cond_wait(&flush_queue.cond,&flush_queue.mutex);if(flush_queue.stop){pthread_mutex_unlock(&flush_queue.mutex);pthread_exit(NULL);}memcpy(out,&flush_queue.request,sizeof(flush_queue.request));flush_queue.full=false;pthread_cond_broadcast(&flush_queue.cond);pthread_mutex_unlock(&flush_queue.mutex);}
static void k_msgq_put(void*q,const void*in,int timeout){(void)q;(void)timeout;prepare_transfer(in);pthread_mutex_lock(&flush_queue.mutex);while(flush_queue.full)pthread_cond_wait(&flush_queue.cond,&flush_queue.mutex);memcpy(&flush_queue.request,in,sizeof(flush_queue.request));flush_queue.full=true;pthread_cond_broadcast(&flush_queue.cond);pthread_mutex_unlock(&flush_queue.mutex);}
static void k_yield(void){wait_for_transfer_start();sched_yield();}
static uint8_t panel[280*240*2];static unsigned writes,max_pixels,display_ticks,smooth_ticks,checkpoints,page_changes;
static uint8_t render_buffer[26880];
#if PIPELINE_DOUBLE_BUFFER
static uint8_t second_buffer[26880];
#endif
static unsigned buffer_uses[2],immutable_checks,overlap_checks;
static pthread_mutex_t ownership_mutex=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t ownership_cond=PTHREAD_COND_INITIALIZER;
static const void *in_flight_buffer,*pending_buffer,*other_buffer;
static uint8_t other_before[26880];
static unsigned queued_generation,started_generation,completed_generation;
static int buffer_index(const void*buf,size_t len){uintptr_t p=(uintptr_t)buf,a=(uintptr_t)render_buffer;if(p>=a&&p-a<sizeof(render_buffer)&&len<=sizeof(render_buffer)-(p-a))return 0;
#if PIPELINE_DOUBLE_BUFFER
 a=(uintptr_t)second_buffer;if(p>=a&&p-a<sizeof(second_buffer)&&len<=sizeof(second_buffer)-(p-a))return 1;
#endif
 assert(!"flush pointer outside registered draw buffers");return -1;}
static void prepare_transfer(const struct lvgl_display_flush*r){int index=buffer_index(r->buf,r->desc.buf_size);pthread_mutex_lock(&ownership_mutex);assert(!in_flight_buffer);pending_buffer=r->buf;queued_generation++;
#if PIPELINE_DOUBLE_BUFFER
 other_buffer=index?render_buffer:second_buffer;memcpy(other_before,other_buffer,sizeof(other_before));
#else
 (void)index;
#endif
 pthread_mutex_unlock(&ownership_mutex);}
static void wait_for_transfer_start(void){
#if PIPELINE_DOUBLE_BUFFER
 /* Model cooperative flush priority: do not return owner queue until SPI began. */
 pthread_mutex_lock(&ownership_mutex);while(started_generation<queued_generation)pthread_cond_wait(&ownership_cond,&ownership_mutex);pthread_mutex_unlock(&ownership_mutex);
#endif
}
static void observe_render_overlap(void){
#if PIPELINE_DOUBLE_BUFFER
 /* Called on owner thread after drawing next buffer, before waiting for DMA.
  * Only this owner reads/writes the other buffer, so the observer adds no race. */
 pthread_mutex_lock(&ownership_mutex);if(in_flight_buffer&&other_buffer&&memcmp(other_before,other_buffer,sizeof(other_before)))overlap_checks++;pthread_mutex_unlock(&ownership_mutex);
#endif
}
static void assert_completion_owner(void){pthread_mutex_lock(&ownership_mutex);assert(!in_flight_buffer&&completed_generation==queued_generation);pthread_mutex_unlock(&ownership_mutex);}
static int display_write(const struct device*dev,uint16_t x,uint16_t y,const struct display_buffer_descriptor*d,const void*buf){
 (void)dev;assert(d->pitch==d->width&&d->buf_size==d->width*d->height*2);unsigned pixels=d->width*d->height;if(pixels>max_pixels)max_pixels=pixels;assert(pixels<=26880/2);assert(x+d->width<=280&&y+d->height<=240);
 int index=buffer_index(buf,d->buf_size);uint8_t snapshot[26880];memcpy(snapshot,buf,d->buf_size);
 pthread_mutex_lock(&ownership_mutex);assert(!in_flight_buffer&&pending_buffer==buf);in_flight_buffer=buf;started_generation=queued_generation;pthread_cond_broadcast(&ownership_cond);pthread_mutex_unlock(&ownership_mutex);
 usleep(PIPELINE_DOUBLE_BUFFER?2000:100);assert(!memcmp(snapshot,buf,d->buf_size));
 const uint8_t*b=buf;for(unsigned row=0;row<d->height;row++)for(unsigned col=0;col<d->width;col++){unsigned from=(row*d->pitch+col)*2,to=((y+row)*280+x+col)*2;panel[to]=b[from+1];panel[to+1]=b[from];}
 assert(!memcmp(snapshot,buf,d->buf_size));immutable_checks++;buffer_uses[index]++;writes++;
 pthread_mutex_lock(&ownership_mutex);in_flight_buffer=NULL;completed_generation=started_generation;pthread_mutex_unlock(&ownership_mutex);return 0;
}
void lvgl_flush_display(struct lvgl_display_flush *request);
'''
bridge+='\n'.join(actual(n,flush_source) for n in ['lvgl_flush_thread_entry','lvgl_wait_cb','lvgl_flush_display'])
bridge+='\n'+actual('lvgl_flush_cb_16bit',flush16_source)
main=r'''
static pthread_t worker;
static void *worker_entry(void*x){(void)x;lvgl_flush_thread_entry(NULL,NULL,NULL);return NULL;}
static void worker_start(void){flush_queue.stop=false;assert(!pthread_create(&worker,NULL,worker_entry,NULL));}
static void drain(lv_display_t*d){lvgl_wait_cb(d);}
static void worker_stop(lv_display_t*d){drain(d);pthread_mutex_lock(&flush_queue.mutex);assert(!flush_queue.full);flush_queue.stop=true;pthread_cond_broadcast(&flush_queue.cond);pthread_mutex_unlock(&flush_queue.mutex);assert(!pthread_join(worker,NULL));}
static void checkpoint(lv_display_t*d,lv_obj_t*screen){
 if(d->inv_p) return; /* A natural refresh must have consumed all dirty regions. */
 worker_stop(d);fflush(NULL);pid_t child=fork();assert(child>=0);
 if(!child){uint8_t expected[sizeof(panel)];memcpy(expected,panel,sizeof(panel));worker_start();lv_obj_invalidate(screen);lv_display_refr_timer(d->refr_timer);drain(d);int mismatch=memcmp(expected,panel,sizeof(panel));if(mismatch){for(unsigned i=0;i<sizeof(panel);i+=2)if(expected[i]!=panel[i]||expected[i+1]!=panel[i+1]){fprintf(stderr,"FORK_DIFF t=%u xy=%u,%u partial=%02x%02x full=%02x%02x label=%s\n",now,(i/2)%280,(i/2)/280,expected[i],expected[i+1],panel[i],panel[i+1],lv_label_get_text(wpm_meter_widget.wpm_label));break;}}worker_stop(d);_exit(mismatch?23:0);}
 int status;assert(waitpid(child,&status,0)==child);assert(WIFEXITED(status)&&WEXITSTATUS(status)==0);worker_start();checkpoints++;
}
static void advance(lv_display_t*d,lv_obj_t*screen,unsigned ms){
 unsigned until=now+ms;
 while(now<until){
  unsigned next_display=((now/10)+1)*10;
  unsigned next=next_display;
  if(wpm_smooth_work.pending&&wpm_smooth_work.due<next)next=wpm_smooth_work.due;
  if(next>until)next=until;
  if(next>now){lv_tick_inc(next-now);now=next;}
  /* At equal deadlines, display tick was queued by the periodic timer first. */
  if(now==next_display){lv_timer_handler();display_ticks++;if(now%(PIPELINE_DOUBLE_BUFFER?200:40)==0)checkpoint(d,screen);}
  if(wpm_smooth_work.pending&&now>=wpm_smooth_work.due){assert(wpm_smooth_work.q==&display_q);wpm_smooth_work.pending=false;wpm_smooth_work.fn(&wpm_smooth_work.work);smooth_ticks++;}
 }
}
static void set_wpm(lv_display_t*d,lv_obj_t*s,unsigned value,unsigned ms){wpm_meter_update_cb((struct wpm_meter_state){value});advance(d,s,ms);drain(d);printf("STATE now=%u target=%u text=%s peak=%d writes=%u checkpoints=%u\n",now,value,lv_label_get_text(wpm_meter_widget.wpm_label),peak_position,writes,checkpoints);}
int main(void){lv_init();lv_display_t*d=lv_display_create(280,240);lv_display_set_color_format(d,LV_COLOR_FORMAT_RGB565);
#if PIPELINE_DOUBLE_BUFFER
 lv_display_set_buffers(d,render_buffer,second_buffer,sizeof(render_buffer),LV_DISPLAY_RENDER_MODE_PARTIAL);
#else
 lv_display_set_buffers(d,render_buffer,NULL,sizeof(render_buffer),LV_DISPLAY_RENDER_MODE_PARTIAL);
#endif
 assert(lv_display_is_double_buffered(d)==PIPELINE_DOUBLE_BUFFER);
 printf("BUFFERS count=%u first=%p second=%p bytes_each=26880\n",PIPELINE_DOUBLE_BUFFER?2:1,(void*)render_buffer,
#if PIPELINE_DOUBLE_BUFFER
 (void*)second_buffer
#else
 (void*)NULL
#endif
 );lv_display_set_flush_cb(d,lvgl_flush_cb_16bit);lv_display_set_flush_wait_cb(d,lvgl_wait_cb);static struct device device;static struct lvgl_disp_data dd={&device};lv_display_set_user_data(d,&dd);worker_start();
 lock_indicator_update_cb((struct lock_indicator_state){0});lv_obj_t*screen=zmk_display_status_screen();lv_screen_load(screen);lock_indicator_update_cb((struct lock_indicator_state){0});set_connection_status(0,true);set_connection_status(1,true);set_battery_level(0,96);set_battery_level(1,89);kb_layer_label_update(wpm_meter_widget.layer_label,"BASE");
 set_wpm(d,screen,255,1200);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"255"));set_wpm(d,screen,100,6000);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"100"));set_wpm(d,screen,99,6000);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"99"));set_wpm(d,screen,38,6000);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"38"));set_wpm(d,screen,9,6000);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"9"));set_wpm(d,screen,0,8000);
 assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"0")&&peak_position==0&&lv_obj_has_flag(wpm_meter_widget.peak_indicator,LV_OBJ_FLAG_HIDDEN));
 for(unsigned i=0;i<6;i++){lock_indicator_update_cb((struct lock_indicator_state){i%4});set_battery_level(0,30+i*9);route_mode=i%2?KB_ROUTE_DONGLE:KB_ROUTE_BLE;refresh(NULL);lv_obj_add_flag(wpm_meter_widget.obj,LV_OBJ_FLAG_HIDDEN);advance(d,screen,160);lv_obj_remove_flag(wpm_meter_widget.obj,LV_OBJ_FLAG_HIDDEN);page_changes+=2;set_wpm(d,screen,i%2?0:88,1300);}
 set_wpm(d,screen,0,8000);drain(d);checkpoint(d,screen);assert(checkpoints>100&&display_ticks>300&&smooth_ticks>300&&max_pixels<=13440);worker_stop(d);assert(buffer_uses[0]&&immutable_checks==writes&&no_pending_waits);
#if PIPELINE_DOUBLE_BUFFER
 assert(buffer_uses[1]&&overlap_checks>0&&blocking_waits>0);
#endif
 printf("OWNERSHIP buffers=%u buf0_uses=%u buf1_uses=%u immutable_checks=%u actual_overlap=%u completion_waits=%u blocking_waits=%u no_pending_waits=%u\n",PIPELINE_DOUBLE_BUFFER?2:1,buffer_uses[0],buffer_uses[1],immutable_checks,overlap_checks,completion_waits,blocking_waits,no_pending_waits);
 printf("PASS actual A pipeline display10ms=%u smooth33ms_runs=%u elapsed_ms=%u fork_checkpoints=%u page_hide_show=%u writes=%u max_flush_pixels=%u buffer_bytes=26880; no parent forced refresh; actual Zephyr flush16/queue/wait functions; independent fork full references equal\n",display_ticks,smooth_ticks,now,checkpoints,page_changes,writes,max_pixels);
}
'''
code='\n'.join(parts)+bridge+main
B=P/'wpm-native/build-1';fonts=[str(p) for p in (B/'CMakeFiles/wpm.dir').rglob('*.o') if p.name!='main.c.o'];assert fonts
V=P/'verification';output=[]
with tempfile.TemporaryDirectory(prefix='pipeline-native-') as directory:
 t=Path(directory);(t/'main.c').write_text(code);(t/'zephyr/logging').mkdir(parents=True);(t/'zephyr/kernel.h').write_text('#define SYS_INIT(...)\n#define IS_ENABLED(x) 0\n');(t/'zephyr/logging/log.h').write_text('#define LOG_MODULE_REGISTER(...)\n#define LOG_WRN(...)\n')
 cmd=['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined','-pthread','-DPIPELINE_DOUBLE_BUFFER='+str(int(args.double_buffer)),'-DLV_CONF_INCLUDE_SIMPLE','-DLV_COLOR_16_SWAP=1','-DCONFIG_PROSPECTOR_STATUS_SCREEN_OPERATOR=1','-I'+str(t),'-I/deps/modules/lib/gui/lvgl','-I'+str(P/'wpm-native'),'-I'+str(S/'src'),'-I/deps/prospector-zmk-module/boards/shields/prospector_adapter/include',str(t/'main.c'),*fonts,str(B/'liblvgl.a'),'-lm','-o',str(t/'pipeline')]
 r=subprocess.run(cmd,capture_output=True,text=True);output.append(r.stdout+r.stderr);compile_rc=r.returncode
 if not compile_rc:r=subprocess.run([str(t/'pipeline')],capture_output=True,text=True);output.append(r.stdout+r.stderr)
 print(''.join(output),end='');log=V/('prospector169-pipeline-double-v50416.log' if args.double_buffer else 'prospector169-pipeline-v50412.log');log.write_text(''.join(output))
 current_hashes={str(p.relative_to(P)):hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked}
 changed=[name for name in source_hashes if source_hashes[name]!=current_hashes[name]]
 final_rc=r.returncode or bool(changed)
 if changed:print('Source changed during native run; rerun required:',changed)
 metrics={k:int(v) for k,v in re.findall(r'(display10ms|smooth33ms_runs|elapsed_ms|fork_checkpoints|page_hide_show|writes|max_flush_pixels|buffer_bytes|buffers|buf0_uses|buf1_uses|immutable_checks|actual_overlap|completion_waits|blocking_waits|no_pending_waits)=(\d+)',r.stdout)}
 record={'passed':final_rc==0,'double_buffer':args.double_buffer,'actual_buffer_count':2 if args.double_buffer else 1,'bytes_per_buffer':26880,'transport_delay_us':2000 if args.double_buffer else 100,'source_changed_during_run':changed,'metrics':metrics,'parent_forced_refresh':False,'sampling_boundary':'Fork checkpoints sample naturally consumed dirty states every'+('200' if args.double_buffer else '40')+'ms when inv_p==0; transient errors between checkpoints could be overwritten before comparison. Equal deadlines use display-first order only.','checkpoint_reference':'fork child actual refresh timer callback, preserves animation time and parent dirty state','settled_numeric_values':[255,100,99,38,9,0],'exit_code':int(final_rc),'compile_exit_code':compile_rc,'test_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'log':log.name,'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'actual_sources':source_hashes,'mock_boundary':'Actual generated A five constructors/WPM scheduler/lock/icons; inherited secondary observer/list/telemetry mocks and no-op diagnostic observer. Real Zephyr lvgl_flush_thread_entry/lvgl_wait_cb/lvgl_flush_display/16bit callback run against pthread queue/semaphore mocks and synchronous ideal LCD copy. Single 26880-byte buffer, display10ms and smooth33ms share deterministic due-order main queue. Each mocked LCD copy stalls100us to exercise outstanding-transfer ownership; no artificial per-display-tick drain. No failure injection or RF/SPI/real Zephyr scheduler/electrical timing. Checkpoint forks stop/join worker, force full only in child using the actual refresh timer callback (no animation time advance); parent dirty state unchanged. ASanUBSan executable, reused LVGL static library uninstrumented.'}
 record['mock_boundary']=record['mock_boundary'].replace('Single 26880-byte buffer',('Two actual static 26880-byte buffers' if args.double_buffer else 'Single 26880-byte buffer')).replace('stalls100us',('stalls2000us' if args.double_buffer else 'stalls100us'))
 (V/('prospector169-pipeline-double-v50416-result.json' if args.double_buffer else 'prospector169-pipeline-v50412-result.json')).write_text(json.dumps(record,indent=2)+'\n');raise SystemExit(final_rc)
