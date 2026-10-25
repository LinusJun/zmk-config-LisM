"""Actual B home and Zephyr flush functions with 10/33 ms native scheduling.

Pthreads model the single-message flush queue/semaphores and synchronous LCD
copy. Fork references never invalidate or force refresh in the parent process.
No radio, physical SPI, electrical behavior or real Zephyr timing is modeled.
"""
from pathlib import Path
import argparse,hashlib,json,re,subprocess,tempfile
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--double-buffer",action="store_true",default=True);args=parser.parse_args()
P=Path(__file__).resolve().parents[2];S=P/'source'
base=S/'tests/xiao147_home_icons_test.py'
ns={'__file__':str(base)}
exec(compile(base.read_text().split("main=r'''",1)[0],str(base),'exec'),ns)
parts=ns['parts'];function=ns['function'];D=ns['ns']['D']

Z=P/'workspace/zephyr/modules/lvgl'
flush_source=(Z/'lvgl_display.c').read_text()
flush16_source=(Z/'lvgl_display_16bit.c').read_text()
tracked=[Z/'lvgl_display.c',Z/'lvgl_display_16bit.c',S/'src/kb_route/icons.c',
         D/'wpm_meter.c',D/'status_screen.c',base,S/'tests/prospector169_home_layout_test.py',S/'tests/wpm_refresh_native_test.py',
         S/'src/kb_route/prospector169_layout.h',S/'src/kb_route/icon_input.h',S/'src/kb_route/signal.h']
tracked += [S/'src/xiao147_ui.c',S/'src/display_jd9853a.c'] + sorted(D.glob('*.c')) + sorted(D.glob('*.h'))
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
struct device{const void *config;};
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
static uint8_t panel[320*172*2];static unsigned writes,max_pixels,display_ticks,smooth_ticks,checkpoints,page_changes;
static uint8_t render_buffer[22016];
#if PIPELINE_DOUBLE_BUFFER
static uint8_t second_buffer[22016];
#endif
static unsigned buffer_uses[2],immutable_checks,overlap_checks;
static pthread_mutex_t ownership_mutex=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t ownership_cond=PTHREAD_COND_INITIALIZER;
static const void *in_flight_buffer,*pending_buffer,*other_buffer;
static uint8_t other_before[22016];
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
 (void)dev;assert(d->pitch==d->width&&d->buf_size==d->width*d->height*2);unsigned pixels=d->width*d->height;if(pixels>max_pixels)max_pixels=pixels;assert(pixels<=22016/2);assert(x+d->width<=320&&y+d->height<=172);
 int index=buffer_index(buf,d->buf_size);uint8_t snapshot[22016];memcpy(snapshot,buf,d->buf_size);
 pthread_mutex_lock(&ownership_mutex);assert(!in_flight_buffer&&pending_buffer==buf);in_flight_buffer=buf;started_generation=queued_generation;pthread_cond_broadcast(&ownership_cond);pthread_mutex_unlock(&ownership_mutex);
 usleep(PIPELINE_DOUBLE_BUFFER?2000:100);assert(!memcmp(snapshot,buf,d->buf_size));
 assert(st7789v_write(dev,x,y,d,buf)==0);
 assert(!memcmp(snapshot,buf,d->buf_size));immutable_checks++;buffer_uses[index]++;writes++;
 pthread_mutex_lock(&ownership_mutex);in_flight_buffer=NULL;completed_generation=started_generation;pthread_mutex_unlock(&ownership_mutex);return 0;
}
void lvgl_flush_display(struct lvgl_display_flush *request);
'''
driver=(S/'src/display_jd9853a.c').read_text()
driver_bridge=r'''
#define EINVAL 22
#define MIN(a,b) ((a)<(b)?(a):(b))
#define ST7789V_CMD_RAMWR 0x2c
#define PIXEL_FORMAT_RGB_565 1
struct st7789v_config {const struct device*mipi_dbi;int dbi_config;unsigned width,height;};
static unsigned wire_x,wire_y,wire_w,wire_h,wire_cursor,wire_chunks;
static void sys_put_be16(uint16_t p,uint8_t*b){b[0]=p>>8;b[1]=p;}
static int st7789v_set_mem_area(const struct device*d,uint16_t x,uint16_t y,uint16_t w,uint16_t h){(void)d;wire_x=x;wire_y=y;wire_w=w;wire_h=h;wire_cursor=0;return 0;}
static int st7789v_transmit(const struct device*d,uint8_t cmd,uint8_t*p,size_t n){(void)d;(void)p;assert(cmd==ST7789V_CMD_RAMWR&&!n);return 0;}
static int mipi_dbi_write_display(const struct device*d,const void*c,const uint8_t*b,const struct display_buffer_descriptor*q,int format){(void)d;(void)c;assert(format==PIXEL_FORMAT_RGB_565&&q->buf_size<=512&&q->height==1&&q->width==q->pitch&&q->buf_size==q->width*2);for(unsigned i=0;i<q->width;i++){unsigned at=wire_cursor++;assert(at<wire_w*wire_h);unsigned dest=((wire_y+at/wire_w)*320+wire_x+at%wire_w)*2;panel[dest]=b[i*2+1];panel[dest+1]=b[i*2];}wire_chunks++;return 0;}
static uint8_t wire_pixels[512];
'''
driver_bridge+=function(driver,'st7789v_write')
# The original B display driver is invoked by the asynchronous Zephyr backend.
pos=bridge.index('static int display_write(')
bridge=bridge[:pos]+driver_bridge+bridge[pos:]
bridge+='\n'.join(actual(n,flush_source) for n in ['lvgl_flush_thread_entry','lvgl_wait_cb','lvgl_flush_display'])
bridge+='\n'+actual('lvgl_flush_cb_16bit',flush16_source)
main=r'''
static pthread_t worker;static bool child_reference;static unsigned next_checkpoint;
static void *worker_entry(void*x){(void)x;lvgl_flush_thread_entry(NULL,NULL,NULL);return NULL;}
static void worker_start(void){flush_queue.stop=false;assert(!pthread_create(&worker,NULL,worker_entry,NULL));}
static void drain(lv_display_t*d){lvgl_wait_cb(d);}
static void worker_stop(lv_display_t*d){drain(d);pthread_mutex_lock(&flush_queue.mutex);assert(!flush_queue.full);flush_queue.stop=true;pthread_cond_broadcast(&flush_queue.cond);pthread_mutex_unlock(&flush_queue.mutex);assert(!pthread_join(worker,NULL));}
static void checkpoint(lv_display_t*d,lv_obj_t*screen){
 if(d->inv_p) return; /* A natural refresh must have consumed all dirty regions. */
 worker_stop(d);fflush(NULL);pid_t child=fork();assert(child>=0);
 if(!child){child_reference=true;uint8_t expected[sizeof(panel)];memcpy(expected,panel,sizeof(panel));worker_start();lv_obj_invalidate(screen);lv_display_refr_timer(d->refr_timer);drain(d);int mismatch=memcmp(expected,panel,sizeof(panel));if(mismatch){for(unsigned i=0;i<sizeof(panel);i+=2)if(expected[i]!=panel[i]||expected[i+1]!=panel[i+1]){fprintf(stderr,"FORK_DIFF t=%u xy=%u,%u partial=%02x%02x full=%02x%02x label=%s\n",now,(i/2)%320,(i/2)/320,expected[i],expected[i+1],panel[i],panel[i+1],lv_label_get_text(wpm_meter_widget.wpm_label));break;}}worker_stop(d);_exit(mismatch?23:0);}
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
  if(now==next_display){lv_timer_handler();display_ticks++;}
  if(wpm_smooth_work.pending&&now>=wpm_smooth_work.due){assert(wpm_smooth_work.q==&display_q);wpm_smooth_work.pending=false;wpm_smooth_work.fn(&wpm_smooth_work.work);smooth_ticks++;}
 }
}
static void set_wpm(lv_display_t*d,lv_obj_t*s,unsigned value,unsigned ms){wpm_meter_update_cb((struct wpm_meter_state){value});advance(d,s,ms);drain(d);printf("STATE now=%u target=%u text=%s peak=%d writes=%u checkpoints=%u\n",now,value,lv_label_get_text(wpm_meter_widget.wpm_label),peak_position,writes,checkpoints);}
static void completed_frame(lv_event_t*e){if(!child_reference&&now>=next_checkpoint){next_checkpoint=now+200;lv_display_t*d=lv_event_get_target(e);checkpoint(d,lv_display_get_screen_active(d));}}
static void test_driver_packing(void){struct st7789v_config cfg={.width=320,.height=172};struct device dev={.config=&cfg};uint16_t pixels[]={0xf800,0x07e0,0x1234,0x001f,0xffff,0x5678},before[6];memcpy(before,pixels,sizeof(pixels));struct display_buffer_descriptor q={.buf_size=sizeof(pixels),.width=2,.height=2,.pitch=3};assert(st7789v_write(&dev,318,170,&q,pixels)==0);assert(!memcmp(before,pixels,sizeof(pixels)));uint16_t expected[]={0xf800,0x07e0,0x001f,0xffff};for(unsigned i=0;i<4;i++){unsigned at=((170+i/2)*320+318+i%2)*2;assert(panel[at]==(expected[i]&255)&&panel[at+1]==(expected[i]>>8));}assert(st7789v_write(&dev,319,170,&q,pixels)==-EINVAL);q.buf_size=10;assert(st7789v_write(&dev,0,0,&q,pixels)==-EINVAL);q.buf_size=12;q.pitch=1;assert(st7789v_write(&dev,0,0,&q,pixels)==-EINVAL);memset(panel,0,sizeof(panel));puts("DRIVER actual native RGB565/pitch padding/bounds/immutable source PASS");}
int main(void){test_driver_packing();lv_init();lv_display_t*d=lv_display_create(320,172);lv_display_set_color_format(d,LV_COLOR_FORMAT_RGB565);
#if PIPELINE_DOUBLE_BUFFER
 lv_display_set_buffers(d,render_buffer,second_buffer,sizeof(render_buffer),LV_DISPLAY_RENDER_MODE_PARTIAL);
#else
 lv_display_set_buffers(d,render_buffer,NULL,sizeof(render_buffer),LV_DISPLAY_RENDER_MODE_PARTIAL);
#endif
 assert(lv_display_is_double_buffered(d)==PIPELINE_DOUBLE_BUFFER);
 printf("BUFFERS count=%u first=%p second=%p bytes_each=22016\n",PIPELINE_DOUBLE_BUFFER?2:1,(void*)render_buffer,
#if PIPELINE_DOUBLE_BUFFER
 (void*)second_buffer
#else
 (void*)NULL
#endif
 );lv_display_set_flush_cb(d,lvgl_flush_cb_16bit);lv_display_set_flush_wait_cb(d,lvgl_wait_cb);static struct st7789v_config config={.width=320,.height=172};static struct device device={.config=&config};static struct lvgl_disp_data dd={&device};lv_display_set_user_data(d,&dd);worker_start();lv_display_add_event_cb(d,completed_frame,LV_EVENT_REFR_READY,NULL);
 lock_indicator_update_cb((struct lock_indicator_state){0});lv_obj_t*screen=zmk_display_status_screen();lv_screen_load(screen);lock_indicator_update_cb((struct lock_indicator_state){0});set_connection_status(0,true);set_connection_status(1,true);set_battery_level(0,96);set_battery_level(1,89);kb_layer_label_update(wpm_meter_widget.layer_label,"BASE");
 set_wpm(d,screen,255,1200);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"255"));set_wpm(d,screen,100,6000);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"100"));set_wpm(d,screen,99,6000);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"99"));set_wpm(d,screen,38,6000);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"38"));set_wpm(d,screen,9,6000);assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"9"));set_wpm(d,screen,0,8000);
 assert(!strcmp(lv_label_get_text(wpm_meter_widget.wpm_label),"0")&&peak_position==0&&lv_obj_has_flag(wpm_meter_widget.peak_indicator,LV_OBJ_FLAG_HIDDEN));
 for(unsigned i=0;i<6;i++){lock_indicator_update_cb((struct lock_indicator_state){i%4});set_battery_level(0,30+i*9);route_mode=i%2?KB_ROUTE_DONGLE:KB_ROUTE_BLE;refresh(NULL);lv_obj_t*devices=lv_obj_create(NULL);lv_obj_set_style_bg_color(devices,lv_color_hex(0x102030),0);lv_screen_load(devices);advance(d,devices,160);lv_screen_load(screen);lv_obj_delete(devices);page_changes+=2;set_wpm(d,screen,i%2?0:88,1300);}
 set_wpm(d,screen,0,8000);drain(d);checkpoint(d,screen);assert(checkpoints>100&&display_ticks>300&&smooth_ticks>300&&max_pixels<=11008);worker_stop(d);assert(buffer_uses[0]&&immutable_checks==writes&&no_pending_waits);
#if PIPELINE_DOUBLE_BUFFER
 assert(buffer_uses[1]&&overlap_checks>0&&blocking_waits>0);
#endif
 printf("OWNERSHIP buffers=%u buf0_uses=%u buf1_uses=%u immutable_checks=%u actual_overlap=%u completion_waits=%u blocking_waits=%u no_pending_waits=%u\n",PIPELINE_DOUBLE_BUFFER?2:1,buffer_uses[0],buffer_uses[1],immutable_checks,overlap_checks,completion_waits,blocking_waits,no_pending_waits);
 printf("PASS actual B pipeline display10ms=%u smooth33ms_runs=%u elapsed_ms=%u fork_checkpoints=%u page_switch_return=%u writes=%u max_flush_pixels=%u buffer_bytes=22016; no parent forced refresh; actual Zephyr flush16/queue/wait and B JD9853A write functions; independent fork full references equal\n",display_ticks,smooth_ticks,now,checkpoints,page_changes,writes,max_pixels);
}
'''
code='\n'.join(parts)+bridge+main
B=P/'wpm-native/build-0';F=P/'wpm-native/build-1';fonts=[str(p) for p in (F/'CMakeFiles/wpm.dir').rglob('*.o') if p.name!='main.c.o'];assert fonts
V=P/'verification';output=[]
with tempfile.TemporaryDirectory(prefix='pipeline-native-') as directory:
 t=Path(directory);(t/'main.c').write_text(code);(t/'zephyr/logging').mkdir(parents=True);(t/'zephyr/kernel.h').write_text('#define SYS_INIT(...)\n#define IS_ENABLED(x) 0\n');(t/'zephyr/logging/log.h').write_text('#define LOG_MODULE_REGISTER(...)\n#define LOG_WRN(...)\n')
 cmd=['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined','-pthread','-DPIPELINE_DOUBLE_BUFFER='+str(int(args.double_buffer)),'-DLV_CONF_INCLUDE_SIMPLE','-DLV_COLOR_16_SWAP=0','-DCONFIG_LISM_XIAO147_TOUCH=1','-DCONFIG_PROSPECTOR_STATUS_SCREEN_OPERATOR=1','-I'+str(t),'-I/deps/modules/lib/gui/lvgl','-I'+str(P/'wpm-native'),'-I'+str(S/'src'),'-I/deps/prospector-zmk-module/boards/shields/prospector_adapter/include',str(t/'main.c'),*fonts,str(B/'liblvgl.a'),'-lm','-o',str(t/'pipeline')]
 r=subprocess.run(cmd,capture_output=True,text=True);output.append(r.stdout+r.stderr);compile_rc=r.returncode
 if not compile_rc:r=subprocess.run([str(t/'pipeline')],capture_output=True,text=True);output.append(r.stdout+r.stderr)
 print(''.join(output),end='');log=V/('xiao147-pipeline-v50418-native.log');log.write_text(''.join(output))
 current_hashes={str(p.relative_to(P)):hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked}
 changed=[name for name in source_hashes if source_hashes[name]!=current_hashes[name]]
 final_rc=r.returncode or bool(changed)
 if changed:print('Source changed during native run; rerun required:',changed)
 metrics={k:int(v) for k,v in re.findall(r'(display10ms|smooth33ms_runs|elapsed_ms|fork_checkpoints|page_switch_return|writes|max_flush_pixels|buffer_bytes|buffers|buf0_uses|buf1_uses|immutable_checks|actual_overlap|completion_waits|blocking_waits|no_pending_waits)=(\d+)',r.stdout)}
 record={'production_fingerprint':ns['ns']['production_fingerprint']()[0],'passed':final_rc==0,'double_buffer':args.double_buffer,'actual_buffer_count':2 if args.double_buffer else 1,'bytes_per_buffer':22016,'transport_delay_us':2000 if args.double_buffer else 100,'source_changed_during_run':changed,'metrics':metrics,'parent_forced_refresh':False,'sampling_boundary':'Fork checkpoints sample naturally completed LV_EVENT_REFR_READY states no more often than every'+('200' if args.double_buffer else '40')+'ms when inv_p==0; a completed refresh callback records the frame before later timers mutate widgets. Transient errors between checkpoints could be overwritten before comparison. Equal deadlines use display-first order only.','checkpoint_reference':'fork child actual refresh timer callback, preserves animation time and parent dirty state','settled_numeric_values':[255,100,99,38,9,0],'exit_code':int(final_rc),'compile_exit_code':compile_rc,'test_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'log':log.name,'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'actual_sources':source_hashes,'mock_boundary':'Actual generated B five constructors plus actual landscape layout_status and xiao147_ui_start/WPM scheduler/lock/icons; inherited secondary observer/list/telemetry mocks and modeled page-controller callbacks; actual alternate page content is a fixture screen. Real Zephyr lvgl_flush_thread_entry/lvgl_wait_cb/lvgl_flush_display/16bit callback run against pthread queue/semaphore mocks and actual JD9853A st7789v_write including native-endian packing into 512-byte MSB-first chunks against synchronous MIPI mock panel RAM. Single 22016-byte buffer, display10ms and smooth33ms share deterministic due-order main queue. Each mocked LCD copy stalls100us to exercise outstanding-transfer ownership; no artificial per-display-tick drain. No failure injection or RF/SPI/real Zephyr scheduler/electrical timing. Checkpoint forks stop/join worker, force full only in child using the actual refresh timer callback (no animation time advance); parent dirty state unchanged. ASanUBSan executable, reused LVGL static library uninstrumented.'}
 record['mock_boundary']=record['mock_boundary'].replace('Single 22016-byte buffer',('Two actual static 22016-byte buffers' if args.double_buffer else 'Single 22016-byte buffer')).replace('stalls100us',('stalls2000us' if args.double_buffer else 'stalls100us'))
 (V/('xiao147-pipeline-v50418-result.json')).write_text(json.dumps(record,indent=2)+'\n');raise SystemExit(final_rc)
