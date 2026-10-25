"""Native execution of actual pinned/patched ST7789 functions and diagnostic cache.

Run inside the project container. SPI, kernel scheduling and logging are mocks;
no hardware, interrupt timing or SPI electrical integrity is modeled.
"""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys
import tempfile

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P / 'tools'))
from prepare_lcd_diagnostics import prepare


def function(text, name):
    match = re.search(r'(?m)^static (?:int|void) ' + re.escape(name) + r'\(', text)
    assert match, name
    begin = text.index('{', match.start())
    depth = 1
    end = begin + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end]


original = P.parent / 'workspace/prospector-zmk-module/drivers/display/display_st7789v.c'
assert original.is_file(), original
baseline = original.read_text()
diag_path = P / 'src/lism_lcd_diag.inc'
diag = '\n'.join(line for line in diag_path.read_text().splitlines()
                 if not line.startswith('#include'))
pre = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#define CONFIG_ST7789V_RGB565 1
#define CONFIG_ST7789V_BGR565 0
#define IS_ENABLED(x) (x)
#define ST7789V_PIXEL_SIZE 2u
#define ST7789V_CMD_CASET 0x2a
#define ST7789V_CMD_RASET 0x2b
#define ST7789V_CMD_RAMWR 0x2c
#define __ASSERT(c,msg) assert(c)
#define LOG_DBG(...) ((void)0)
#define LOG_LEVEL_INF 3
#define LOG_MODULE_REGISTER(...)
#define ARG_UNUSED(x) ((void)(x))
#define SYS_INIT(...)
#define APPLICATION 0
#define K_SECONDS(s) ((s)*1000)
struct display_buffer_descriptor {uint32_t buf_size; uint16_t width,height,pitch;};
struct device {const void *config; void *data;};
struct st7789v_config {const struct device *mipi_dbi; int dbi_config;};
struct st7789v_data {uint16_t x_offset,y_offset;};
enum display_pixel_format {PIXEL_FORMAT_RGB_565,PIXEL_FORMAT_BGR_565,PIXEL_FORMAT_RGB_888};
static uint16_t sys_cpu_to_be16(uint16_t x){return (x<<8)|(x>>8);}
struct k_spinlock {int held;};
typedef unsigned k_spinlock_key_t;
static unsigned locks;
static k_spinlock_key_t k_spin_lock(struct k_spinlock *l){assert(!l->held);l->held=1;locks++;return 0;}
static void k_spin_unlock(struct k_spinlock *l,k_spinlock_key_t k){(void)k;assert(l->held);l->held=0;locks--;}
struct k_work {int unused;};
struct k_work_delayable {struct k_work work;void (*handler)(struct k_work *);};
#define K_WORK_DELAYABLE_DEFINE(n,f) static struct k_work_delayable n={.handler=f}
static uint32_t now,due;
static unsigned schedules,logs,control_logs,summary_logs,color_logs;
static char last_log[1024];
static unsigned observed_seq[64],observed_drop[64];
static uint32_t k_uptime_get_32(void){return now;}
static int k_work_reschedule(struct k_work_delayable *w,unsigned delay){(void)w;assert(!locks);assert(delay==5000);due=now+delay;schedules++;return 0;}
static void log_info(const char *fmt,...){assert(fmt);assert(!locks);va_list a;va_start(a,fmt);vsnprintf(last_log,sizeof(last_log),fmt,a);va_end(a);if(!strncmp(fmt,"LCD15 ",6)){color_logs++;return;}logs++;control_logs+=strstr(fmt,"ctrl=")!=NULL;summary_logs+=strstr(fmt,"cmd=%u err=")!=NULL;if(strstr(fmt,"ctrl=")) {assert(control_logs<=64);assert(sscanf(last_log,"LCD11 ctrl=%u",&observed_seq[control_logs-1])==1);}else {const char *p=strstr(last_log,"ctrl_drop=");assert(p && summary_logs<=64);assert(sscanf(p,"ctrl_drop=%u",&observed_drop[summary_logs-1])==1);}}
#define LOG_INF(...) log_info(__VA_ARGS__)
'''
transport = r'''
struct call {unsigned cmd,len,w,h,pitch;int rc;uint8_t bytes[64];};
static struct call calls[16];static unsigned ncalls,fail_call;
static int record(unsigned cmd,const void *buf,unsigned len,unsigned w,unsigned h,unsigned pitch){
 assert(ncalls<16 && len<=64);struct call *c=&calls[ncalls++];*c=(struct call){.cmd=cmd,.len=len,.w=w,.h=h,.pitch=pitch,.rc=ncalls==fail_call?-73:0};if(len)memcpy(c->bytes,buf,len);return c->rc;
}
static int mipi_dbi_command_write(const struct device *d,const int *cfg,uint8_t cmd,uint8_t *data,size_t len){(void)d;(void)cfg;assert(!locks);return record(cmd,data,len,0,0,0);}
static int mipi_dbi_write_display(const struct device *d,const int *cfg,const void *buf,const struct display_buffer_descriptor *desc,enum display_pixel_format fmt){(void)d;(void)cfg;assert(fmt==PIXEL_FORMAT_RGB_565);assert(!locks);return record(0x100,buf,desc->width*desc->height*2,desc->width,desc->height,desc->pitch);}
'''
main = r'''
static void dispatch(uint32_t at){now=at;if(now>=due)lism_lcd_work.handler(&lism_lcd_work.work);}
static void driver_cases(void){
 struct st7789v_config cfg={0};struct st7789v_data data={20,0};struct device dev={&cfg,&data};
 uint8_t buf[20];for(unsigned i=0;i<sizeof(buf);i++)buf[i]=(uint8_t)(i+1);
 for(unsigned padded=0;padded<2;padded++){
  struct display_buffer_descriptor desc={.width=3,.height=2,.pitch=padded?5:3,.buf_size=sizeof(buf)};
  for(unsigned failure=0;failure<=(padded?5:4);failure++){
   struct call expected[16]={0};ncalls=0;fail_call=failure;memset(calls,0,sizeof(calls));
   int old=orig_st7789v_write(&dev,10,36,&desc,buf);unsigned expected_count=ncalls;memcpy(expected,calls,sizeof(calls));
   memset(&lism_lcd_state,0,sizeof(lism_lcd_state));ncalls=0;memset(calls,0,sizeof(calls));
   unsigned before_logs=logs;int rc=st7789v_write(&dev,10,36,&desc,buf);
   assert(rc==old && rc==(failure?-73:0));assert(ncalls==expected_count);assert(!memcmp(calls,expected,sizeof(calls)));assert(logs==before_logs);
   assert(lism_lcd_state.writes==1 && lism_lcd_state.write_errors==(failure!=0));
   assert(lism_lcd_state.commands==(ncalls>3?3:ncalls));
   assert(lism_lcd_state.command_errors==(failure && failure<=3));
   assert(lism_lcd_state.controls==0);assert(lism_lcd_state.x==10 && lism_lcd_state.y==36 && lism_lcd_state.w==3 && lism_lcd_state.h==2);
   assert(lism_lcd_state.sample_valid==1 && lism_lcd_state.first[0]==1 && lism_lcd_state.first[1]==2);
   unsigned end=((desc.height-1)*desc.pitch+desc.width)*2;
   assert(lism_lcd_state.last[0]==buf[end-2] && lism_lcd_state.last[1]==buf[end-1]);
   printf("PASS actual driver %s failure_call=%u rc=%d transcript=%u\n",padded?"padded":"packed",failure,rc,ncalls);
  }
 }
}
static void diagnostics_cases(void){
 memset(&lism_lcd_state,0,sizeof(lism_lcd_state));logs=control_logs=summary_logs=0;now=0;
 assert(lism_lcd_diag_init()==0 && due==5000 && schedules==1);
 for(unsigned i=0;i<12;i++){
  uint8_t *p=malloc(1);*p=(uint8_t)i;
  lism_lcd_trace_command(0x40+i,p,1,i==4?-9:0);free(p);
 }
 /* Flush commands count attempts/errors but never consume control slots. */
 lism_lcd_trace_command(0x2a,NULL,0,0);lism_lcd_trace_command(0x2b,NULL,0,0);lism_lcd_trace_command(0x2c,NULL,0,0);
 assert(lism_lcd_state.commands==15 && lism_lcd_state.command_errors==1 && lism_lcd_state.controls==12);
 for(unsigned seq=5;seq<=12;seq++)assert(lism_lcd_state.ring[(seq-1)%8].seq==seq);
 struct lism_lcd_control latest=lism_lcd_state.ring[11%8];assert(latest.cmd==0x4b && latest.data0==11);
 assert(logs==0);dispatch(4999);assert(logs==0);
 for(unsigned period=1;period<=4;period++){
  unsigned before=control_logs;dispatch(period*5000);
  assert(control_logs-before==2 && logs==period*3 && summary_logs==period);
  assert(observed_drop[period-1]==4 && due==(period+1)*5000);
 }
 for(unsigned i=0;i<8;i++)assert(observed_seq[i]==5+i);
 dispatch(25000);assert(logs==13 && summary_logs==5 && control_logs==8 && observed_drop[4]==4);
 /* A five-control burst must drain over three periods without silent loss. */
 for(unsigned i=0;i<5;i++)lism_lcd_trace_command(0x60+i,NULL,0,0);
 for(unsigned period=6;period<=8;period++){
  unsigned before=control_logs;dispatch(period*5000);
  assert(control_logs-before==(period==8?1:2));assert(observed_drop[period-1]==4);
 }
 assert(control_logs==13);for(unsigned i=8;i<13;i++)assert(observed_seq[i]==13+(i-8));
 /* Further overwrites are accumulated, not reset between periods. */
 for(unsigned i=0;i<10;i++)lism_lcd_trace_command(0x70+i,NULL,0,0);
 dispatch(45000);assert(observed_drop[8]==6 && control_logs==15 && observed_seq[13]==20 && observed_seq[14]==21);
 for(unsigned period=10;period<=12;period++)dispatch(period*5000);
 assert(control_logs==21);for(unsigned i=13;i<21;i++)assert(observed_seq[i]==20+(i-13));
 assert(observed_drop[11]==6);
 uint8_t *pixels=malloc(16);for(unsigned i=0;i<16;i++)pixels[i]=(uint8_t)(0xa0+i);
 struct display_buffer_descriptor d={.width=2,.height=2,.pitch=4,.buf_size=16};
 lism_lcd_trace_write(2,3,&d,pixels,-12);memset(pixels,0xff,16);free(pixels);
 assert(lism_lcd_state.sample_valid==1 && lism_lcd_state.first[0]==0xa0 && lism_lcd_state.first[1]==0xa1 && lism_lcd_state.last[0]==0xaa && lism_lcd_state.last[1]==0xab);
 char *text=malloc(32);strcpy(text,"255-with-long-value");lism_lcd_trace_ui(0xaa123456,text,1,2,3,4);memset(text,'X',31);free(text);
 assert(lism_lcd_state.bg==0x123456 && !strcmp(lism_lcd_state.text,"255-with-long-v"));
 assert(lism_lcd_state.x1==1 && lism_lcd_state.y2==4);
 unsigned before_logs=logs;dispatch(65000);assert(logs==before_logs+1);assert(lism_lcd_state.first[0]==0xa0);
 /* PROT_NONE catches any dereference, including a first-byte-only read. */
 long page=sysconf(_SC_PAGESIZE);void *guard=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(guard!=MAP_FAILED);
 struct display_buffer_descriptor bad={.width=1,.height=1,.pitch=1,.buf_size=1};
 lism_lcd_trace_write(0,0,&bad,guard,0);assert(lism_lcd_state.sample_valid==0 && lism_lcd_state.first[0]==0 && lism_lcd_state.last[0]==0);
 bad.buf_size=0;lism_lcd_trace_write(0,0,&bad,guard,0);assert(lism_lcd_state.sample_valid==0);
 bad=(struct display_buffer_descriptor){.width=3,.height=2,.pitch=2,.buf_size=12};lism_lcd_trace_write(0,0,&bad,guard,0);assert(lism_lcd_state.sample_valid==0);
 bad=(struct display_buffer_descriptor){.width=0,.height=2,.pitch=2,.buf_size=12};lism_lcd_trace_write(0,0,&bad,guard,0);assert(lism_lcd_state.sample_valid==0);
 bad=(struct display_buffer_descriptor){.width=2,.height=0,.pitch=2,.buf_size=12};lism_lcd_trace_write(0,0,&bad,guard,0);assert(lism_lcd_state.sample_valid==0);
 bad=(struct display_buffer_descriptor){.width=2,.height=2,.pitch=2,.buf_size=8};lism_lcd_trace_write(0,0,&bad,NULL,0);assert(lism_lcd_state.sample_valid==0);
 munmap(guard,page);
 puts("PASS actual diag ring8; ordered drains 12/5/10 bursts; cumulative ctrl_drop=4 then 6; callback logging absent; 5s summary+max2 controls; pitch-tail sample_valid; copied bytes/text survive free; short/invalid buffers valid0 never read");
}
static void color_cases(void){
 memset(&lism_lcd_state,0,sizeof(lism_lcd_state));now=100;
 uint8_t *p=malloc(2);p[0]=0x54;p[1]=0xf4;
 struct display_buffer_descriptor d={.width=1,.height=1,.pitch=1,.buf_size=2};
 lism_lcd_trace_write(214,176,&d,p,0);memset(p,0xff,2);free(p);
 assert(lism_lcd_state.pixel[0].valid&&lism_lcd_state.pixel[0].write==1&&lism_lcd_state.pixel[0].ms==100&&!lism_lcd_state.pixel[0].rc);
 assert(lism_lcd_state.pixel[0].bytes[0]==0x54&&lism_lcd_state.pixel[0].bytes[1]==0xf4&&!lism_lcd_state.pixel[1].valid);
 /* Padded rows: derive pixel offset from pitch, not window width. */
 uint8_t padded[26]={0};padded[12]=0x12;padded[13]=0x34;
 d=(struct display_buffer_descriptor){.width=3,.height=3,.pitch=5,.buf_size=26};now=200;
 lism_lcd_trace_write(213,175,&d,padded,-71);
 assert(lism_lcd_state.pixel[0].write==2&&lism_lcd_state.pixel[0].ms==200&&lism_lcd_state.pixel[0].rc==-71);
 assert(lism_lcd_state.pixel[0].bytes[0]==0x12&&lism_lcd_state.pixel[0].bytes[1]==0x34&&!lism_lcd_state.pixel[1].valid);
 /* Independent partial writes must not retimestamp the other point. */
 uint8_t keyboard[2]={0x54,0xf4};d=(struct display_buffer_descriptor){.width=1,.height=1,.pitch=1,.buf_size=2};now=300;
 lism_lcd_trace_write(218,145,&d,keyboard,0);
 assert(lism_lcd_state.pixel[1].write==3&&lism_lcd_state.pixel[1].ms==300&&lism_lcd_state.pixel[1].valid);
 assert(lism_lcd_state.pixel[0].write==2&&lism_lcd_state.pixel[0].ms==200);
 struct lism_lcd_pixel before[2];memcpy(before,lism_lcd_state.pixel,sizeof(before));
 lism_lcd_trace_write(213,176,&d,keyboard,0);lism_lcd_trace_write(215,176,&d,keyboard,0);lism_lcd_trace_write(214,175,&d,keyboard,0);lism_lcd_trace_write(214,177,&d,keyboard,0);
 assert(!memcmp(before,lism_lcd_state.pixel,sizeof(before)));
 long page=sysconf(_SC_PAGESIZE);void*guard=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(guard!=MAP_FAILED);
 d.buf_size=1;lism_lcd_trace_write(214,176,&d,guard,0);assert(!memcmp(before,lism_lcd_state.pixel,sizeof(before)));munmap(guard,page);
 /* A single larger padded window covers both points at different offsets. */
 uint8_t both[444]={0};both[434]=0xab;both[435]=0xcd;both[8]=0x56;both[9]=0x78;
 d=(struct display_buffer_descriptor){.width=5,.height=32,.pitch=7,.buf_size=444};now=400;
 lism_lcd_trace_write(214,145,&d,both,0);
 assert(lism_lcd_state.pixel[0].bytes[0]==0xab&&lism_lcd_state.pixel[0].bytes[1]==0xcd);
 assert(lism_lcd_state.pixel[1].bytes[0]==0x56&&lism_lcd_state.pixel[1].bytes[1]==0x78);
 assert(lism_lcd_state.pixel[0].write==lism_lcd_state.pixel[1].write&&lism_lcd_state.pixel[0].ms==400&&lism_lcd_state.pixel[1].ms==400);
 now=500;lism_lcd_trace_output_ui(0xff569fa7,255,3,1,-1,2,255,206,170,261,198);
 assert(lism_lcd_state.output_samples==1&&lism_lcd_state.output_ms==500&&lism_lcd_state.output_bg==0x569fa7&&lism_lcd_state.output_opa==255);
 assert(lism_lcd_state.output_state==3&&lism_lcd_state.output_visible&&lism_lcd_state.output_owner==-1&&lism_lcd_state.output_local==2&&lism_lcd_state.output_profile==255);
 assert(lism_lcd_state.output_box[0]==206&&lism_lcd_state.output_box[1]==170&&lism_lcd_state.output_box[2]==261&&lism_lcd_state.output_box[3]==198);
 unsigned old_color_logs=color_logs;lism_lcd_log_work(&lism_lcd_work.work);assert(color_logs-old_color_logs==3);
 puts("PASS LCD15 fixed RGB565 points: pitch offsets, cover boundaries, independent sequence/time/rc, failed-attempt labeling, copied bytes survive free, PROT_NONE short buffer unread, two-point write, copied resolved style and 3 records per 5s work; no panel readback");
}
int main(void){driver_cases();diagnostics_cases();color_cases();puts("PASS lcd_driver_trace_test actual C functions ASan+UBSan");return 0;}
'''
with tempfile.TemporaryDirectory(prefix='lcd-driver-native-') as directory:
    temp = Path(directory)
    driver = temp / 'display_st7789v.c'
    driver.write_text(baseline)
    (temp / 'CMakeLists.txt').write_text('zephyr_library_sources(display_st7789v.c)\n')
    prepare(driver)
    patched = driver.read_text()
    names = ['st7789v_transmit', 'st7789v_set_mem_area', 'st7789v_write']
    original_functions = '\n'.join(function(baseline, name) for name in names)
    for name in names:
        original_functions = re.sub(r'\b' + name + r'\b', 'orig_' + name, original_functions)
    actual = '\n'.join(function(patched, name) for name in
                       ['st7789v_transmit', 'st7789v_set_mem_area', 'lism_st7789v_write_impl', 'st7789v_write'])
    c = temp / 'test.c'
    c.write_text(pre + diag + transport + original_functions + actual + main)
    exe = temp / 'test'
    log = []
    def run(command):
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log.append(result.stdout)
        print(result.stdout, end='')
        return result.returncode
    compile_rc = run(['cc', '-std=gnu11', '-g', '-O1', '-Wall', '-Wextra',
                      '-fsanitize=address,undefined', '-fno-omit-frame-pointer', str(c), '-o', str(exe)])
    rc = compile_rc if compile_rc else run([str(exe)])
    output = P.parent / 'verification/lcd-driver-trace-v50415.log'
    output.write_text(''.join(log))
    sources = [Path(__file__), original, diag_path, P/'tools/prepare_lcd_diagnostics.py']
    record = {'passed': rc == 0, 'exit_code': rc, 'compile_exit_code': compile_rc,
              'actual_driver_cases': 11, 'sanitizers': 'ASan+UBSan for all extracted actual C and mocks',
              'source_sha256': {str(p.relative_to(P.parent)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
              'log': str(output.relative_to(P.parent)), 'log_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
              'boundaries': 'Real pinned transmit/set_mem_area/write, real generator wrapper and diagnostic .inc. Native SPI/kernel/log mocks; no RF, hardware, physical SPI, ISR concurrency or timing validation.'}
    (P.parent/'verification/lcd-driver-trace-v50415-result.json').write_text(json.dumps(record, indent=2)+'\n')
    raise SystemExit(rc)
