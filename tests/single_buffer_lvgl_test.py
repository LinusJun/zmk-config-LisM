"""Real pinned LVGL + Zephyr flush callbacks; delayed display_write is modeled."""
from pathlib import Path
import sys,hashlib,subprocess,json
P=Path(__file__).resolve().parents[2];sys.path.insert(0,str(P/'source/tools'))
from prepare_hog_routing import function
src=Path('/sdk/zephyr/modules/lvgl/lvgl_display.c').read_text()
pre=r'''
#include <lvgl.h>
#include <assert.h>
#include <pthread.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <sched.h>
#define CONFIG_LV_Z_FLUSH_THREAD 1
#define K_NO_WAIT 0
#define K_FOREVER (-1)
struct semaphore {int value;pthread_mutex_t lock;pthread_cond_t changed;};
static struct semaphore flush_complete={0,PTHREAD_MUTEX_INITIALIZER,PTHREAD_COND_INITIALIZER};
static struct semaphore flush_required={0,PTHREAD_MUTEX_INITIALIZER,PTHREAD_COND_INITIALIZER};
static unsigned blocking_waits;
static int k_sem_take(struct semaphore*s,int timeout){
 pthread_mutex_lock(&s->lock);
 if(timeout==K_NO_WAIT && !s->value){pthread_mutex_unlock(&s->lock);return -1;}
 if(timeout==K_FOREVER)blocking_waits++;
 while(!s->value)pthread_cond_wait(&s->changed,&s->lock);
 s->value=0;pthread_mutex_unlock(&s->lock);return 0;
}
static void k_sem_give(struct semaphore*s){pthread_mutex_lock(&s->lock);s->value=1;pthread_cond_signal(&s->changed);pthread_mutex_unlock(&s->lock);}
static void k_sem_reset(struct semaphore*s){pthread_mutex_lock(&s->lock);s->value=0;pthread_mutex_unlock(&s->lock);}
struct descriptor {unsigned width,height,pitch,buf_size;bool frame_incomplete;};
struct lvgl_display_flush {lv_display_t*display;unsigned x,y;struct descriptor desc;uint8_t *buf;};
struct lvgl_disp_data {void*display_dev;};
static struct {bool occupied,stop;struct lvgl_display_flush item;pthread_mutex_t lock;pthread_cond_t changed;} flush_queue={.lock=PTHREAD_MUTEX_INITIALIZER,.changed=PTHREAD_COND_INITIALIZER};
static void k_msgq_get(void*queue,struct lvgl_display_flush*out,int timeout){
 (void)queue;(void)timeout;pthread_mutex_lock(&flush_queue.lock);
 while(!flush_queue.occupied && !flush_queue.stop)pthread_cond_wait(&flush_queue.changed,&flush_queue.lock);
 if(flush_queue.stop){pthread_mutex_unlock(&flush_queue.lock);pthread_exit(NULL);}
 *out=flush_queue.item;flush_queue.occupied=false;pthread_cond_signal(&flush_queue.changed);pthread_mutex_unlock(&flush_queue.lock);
}
static void k_msgq_put(void*queue,const struct lvgl_display_flush*item,int timeout){
 (void)queue;(void)timeout;pthread_mutex_lock(&flush_queue.lock);assert(!flush_queue.occupied);
 flush_queue.item=*item;flush_queue.occupied=true;pthread_cond_signal(&flush_queue.changed);pthread_mutex_unlock(&flush_queue.lock);
}
static void k_yield(void){sched_yield();}
static unsigned writes,corruptions;
static unsigned checksum(const uint8_t*p,unsigned n){unsigned s=0;for(unsigned i=0;i<n;i++)s=s*31+p[i];return s;}
static int display_write(void*d,unsigned x,unsigned y,struct descriptor*desc,uint8_t*buf){
 (void)d;(void)x;(void)y;unsigned before=checksum(buf,desc->buf_size);
 usleep(3000); /* synchronous SPI-like write returns only when finished */
 if(before!=checksum(buf,desc->buf_size))corruptions++;
 writes++;return 0;
}
'''
main=r'''
static void *worker(void*p){(void)p;lvgl_flush_thread_entry(NULL,NULL,NULL);return NULL;}
static void submit(lv_display_t*d,const lv_area_t*a,uint8_t*map){
 struct lvgl_display_flush req={.display=d,.x=a->x1,.y=a->y1,.buf=map,
  .desc={.width=a->x2-a->x1+1,.height=a->y2-a->y1+1,.pitch=a->x2-a->x1+1,
         .buf_size=(a->x2-a->x1+1)*(a->y2-a->y1+1)*2}};
 lvgl_flush_display(&req);
}
int main(void){
 lv_init();lv_display_t*d=lv_display_create(280,240);static uint8_t buffer[26880];
 struct lvgl_disp_data data={0};lv_display_set_user_data(d,&data);
 lv_display_set_color_format(d,LV_COLOR_FORMAT_RGB565);
 lv_display_set_buffers(d,buffer,NULL,sizeof(buffer),LV_DISPLAY_RENDER_MODE_PARTIAL);
 lv_display_set_flush_cb(d,submit);lv_display_set_flush_wait_cb(d,lvgl_wait_cb);
 lvgl_wait_cb(d);assert(blocking_waits==0); /* no outstanding flush must not block */
 pthread_t thread;assert(!pthread_create(&thread,NULL,worker,NULL));
 lv_obj_t*screen=lv_screen_active();
 for(unsigned frame=0;frame<30;frame++){
  lv_obj_set_style_bg_color(screen,lv_color_hex(frame*0x050709),0);
  lv_obj_invalidate(screen);lv_refr_now(d);lvgl_wait_cb(d);
  unsigned before=writes;lvgl_wait_cb(d);assert(writes==before); /* already complete */
 }
 assert(writes>=150 && !corruptions && blocking_waits>=150);
 pthread_mutex_lock(&flush_queue.lock);flush_queue.stop=true;pthread_cond_signal(&flush_queue.changed);pthread_mutex_unlock(&flush_queue.lock);pthread_join(thread,NULL);
 printf("PASS real LVGL single-buffer partial renders + pinned Zephyr flush worker/wait callback: %u delayed writes, zero reuse corruption, no-pending waits return\n",writes);
 return 0;
}
'''
# stdio is needed by native harness only; firmware source is unchanged.
c=P/'preview-host/single_buffer.c';c.write_text('#include <stdio.h>\n'+pre+'\n'+'\n'.join(function(src,n) for n in ['lvgl_flush_thread_entry','lvgl_wait_cb','lvgl_flush_display'])+main)
cm=P/'preview-host/CMakeLists.txt';t=cm.read_text()
if 'single_buffer_test' not in t:cm.write_text(t+'\nfind_package(Threads REQUIRED)\nadd_executable(single_buffer_test single_buffer.c)\ntarget_link_libraries(single_buffer_test PRIVATE lvgl Threads::Threads m)\n')
subprocess.run(['cmake','-S',str(cm.parent),'-B',str(cm.parent/'build')],check=True,capture_output=True)
r=subprocess.run(['cmake','--build',str(cm.parent/'build'),'--target','single_buffer_test','-j4'],capture_output=True,text=True)
assert not r.returncode,r.stdout+r.stderr
r=subprocess.run([str(cm.parent/'build/single_buffer_test')],capture_output=True,text=True,timeout=45)
assert not r.returncode,r.stdout+r.stderr
print(r.stdout)
(P/'verification/single-buffer-lvgl.json').write_text(json.dumps({'passed':True,'driver_sha256':hashlib.sha256(src.encode()).hexdigest(),'scope':'Actual LVGL and Zephyr wait/worker functions; pthread scheduling, display_write and semaphores modeled; no real LCD/SPI performance proof.','stdout':r.stdout},indent=2))
