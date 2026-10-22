"""Exercise instrumented display/backlight paths; no claim to reproduce black LCD."""
from pathlib import Path
import argparse,hashlib,json,subprocess,sys,tempfile
P=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);W=p.parse_args().workspace
sys.path.insert(0,str(P/'source/tools'))
from prepare_hog_routing import function
from prepare_startup_trace import prepare_display,prepare_brightness
generated=next((W/'build-dongle').rglob('lism_display_startup.c'))
brightness=W/'display-dongle/boards/shields/prospector_adapter/src/brightness.c'
original=W/'prospector-zmk-module/boards/shields/prospector_adapter/src/brightness.c'
trace=P/'source/src/esb_upstream/startup_trace.c'
with tempfile.TemporaryDirectory() as d:
    a,b=Path(d)/'display.c',Path(d)/'brightness.c'
    prepare_display(W/'zmk/app/src/display/main.c',a);assert a.read_bytes()==generated.read_bytes()
    b.write_bytes(original.read_bytes());prepare_brightness(b);assert b.read_bytes()==brightness.read_bytes()
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <stdio.h>
#define IS_ENABLED(n) (n)
#define CONFIG_ARCH_POSIX 0
#define CONFIG_ZMK_DISPLAY_TICK_PERIOD_MS 10
#define CONFIG_PROSPECTOR_FIXED_BRIGHTNESS 90
#define CONFIG_BT_MAX_CONN 5
#define DT_HAS_CHOSEN(n) 0
#define DT_CHOSEN(n) 0
#define DEVICE_DT_GET(n) display
#define K_MSEC(n) (n)
#define K_SECONDS(n) (n*1000)
#define ARG_UNUSED(n) ((void)(n))
static int trace_count,error_count,theme_calls,screen_loads,timer_calls,led_calls,sleep_calls;
#define LOG_INF(...) (trace_count++)
#define LOG_DBG(...) ((void)0)
#define LOG_ERR(...) (error_count++)
struct device {bool ready;};static struct device lcd={true},pwm={true};
static const struct device*display=&lcd,*pwm_leds_dev=&pwm;
static bool device_is_ready(const struct device*d){return d->ready;}
struct k_work {int unused;};
static bool initialized,screen_present=true;
typedef int lv_obj_t;static lv_obj_t screen_object,*screen;
static int display_timer,blank_rc,led_rc;
#define DISP_BL 0
static int display_blanking_off(const struct device*d){assert(d==display);return blank_rc;}
static void k_timer_start(int*t,int first,int period){assert(t==&display_timer && first==10 && period==10);timer_calls++;}
static void initialize_theme(void){theme_calls++;}
static lv_obj_t*zmk_display_status_screen(void){return screen_present?&screen_object:NULL;}
static void lv_scr_load(lv_obj_t*s){assert(s==&screen_object);screen_loads++;}
static int led_set_brightness(const struct device*d,int index,int value){assert(d==pwm_leds_dev && !index && value==90);led_calls++;return led_rc;}
static struct {unsigned RESETREAS;} power={0x123};
#define NRF_POWER (&power)
static int zmk_display_is_initialized(void){return initialized;}
static void k_sleep(int ms){assert(ms==2000);sleep_calls++;}
'''
main=r'''
int main(void){
 initialize_display(NULL);assert(initialized && theme_calls==1 && screen_loads==1 && timer_calls==1);
 lcd.ready=false;initialized=false;initialize_display(NULL);assert(!initialized && error_count==1 && timer_calls==1);
 lcd.ready=true;screen_present=false;initialize_display(NULL);assert(error_count==2 && timer_calls==1);
 blank_rc=-EIO;unblank_display_cb(NULL);assert(timer_calls==2); // Existing behavior, logs rc.
 assert(!init_fixed_brightness() && led_calls==1);
 led_rc=-EIO;assert(init_fixed_brightness()==-EIO && led_calls==2);
 pwm.ready=false;assert(init_fixed_brightness()==-ENODEV && led_calls==2);
 assert(!startup_marker());assert(power.RESETREAS==0x123);
 startup_observer(NULL,NULL,NULL);assert(sleep_calls==3 && power.RESETREAS==0x123);
 puts("PASS actual UI readiness/screen/unblank, checked brightness result, bounded observer and read-only reset cause; no extra LCD reset introduced");
}
'''
code=pre+'\n'+function(generated.read_text(),'unblank_display_cb')+'\n'+function(generated.read_text(),'initialize_display')+'\n'+function(brightness.read_text(),'init_fixed_brightness')+'\n'+function(trace.read_text(),'startup_marker')+'\n'+function(trace.read_text(),'startup_observer')+'\n'+main
with tempfile.TemporaryDirectory() as d:
    c,exe=Path(d)/'test.c',Path(d)/'test';c.write_text(code)
    subprocess.run(['cc','-std=c11','-fsanitize=address,undefined','-fno-omit-frame-pointer','-Werror=implicit-function-declaration',str(c),'-o',str(exe)],check=True)
    r=subprocess.run([str(exe)],capture_output=True,text=True,check=True)
report={'passed':True,'generated_copies_reproduced':True,'no_display_reset_or_timing_change':True,
        'source_hashes':{str(q):hashlib.sha256(q.read_bytes()).hexdigest() for q in [generated,brightness,trace]},
        'stdout':r.stdout,'black_screen_root_cause_proven':False,'scope':'Modeled LCD/PWM/LVGL/timer calls; diagnostic paths, not panel or bootloader hardware emulation'}
(W/'startup-trace-test.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
