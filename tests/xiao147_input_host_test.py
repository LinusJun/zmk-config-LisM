"""Actual AXS polling/gesture C + actual USR1 callback/toggle, sanitised host test."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = re.sub(r"^#include .*$", "", (root / "src/axs5106l_touchpad.c").read_text(), flags=re.M)
imu_source = re.sub(r"^#(?:include|define DT_DRV_COMPAT).*$", "",
                    (root / "src/lsm6ds3_air_mouse.c").read_text(), flags=re.M)
ui = (root / "src/xiao147_ui.c").read_text()
def function(signature):
    start = ui.index(signature + "\n{")
    end = ui.index("\n}\n", start)+3
    return ui[start:end]
toggle = function("void xiao147_ui_toggle_imu(void)")
callback = function("static void imu_button_input(struct input_event *evt, void *user_data)")
# Unmodified upstream fixture from the pinned ZMK revision, MIT licensed.
listener = (root / "tests/fixtures/zmk_input_listener.c").read_text()
listener_types = listener[listener.index("enum input_listener_xy_data_mode"):
                          listener.index("static void handle_rel_code")]
def listener_function(signature):
    start = listener.index(signature)
    return listener[start:listener.index("\n}\n", start)+3]
listener_functions = "\n".join(listener_function(s) for s in (
    "static void handle_rel_code", "static void handle_abs_code",
    "static void handle_key_code", "static void clear_xy_data", "static void input_handler"))
# handle_abs_code is a one-line empty function; avoid capturing handle_key twice.
listener_functions = listener_functions.replace(listener_function("static void handle_abs_code"),
    listener[listener.index("static void handle_abs_code"):listener.index("static void handle_key_code")])
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include "xiao147_orientation.h"
#include "xiao147_air_motion.h"
enum xiao147_page {XIAO147_PAGE_STATUS,XIAO147_PAGE_SETTINGS,XIAO147_PAGE_DEVICES};
#define LOG_MODULE_REGISTER(...)
#define LOG_INF(...) ((void)0)
#define LOG_WRN(...) ((void)0)
#define LOG_ERR(...) ((void)0)
#define LOG_DBG(...) ((void)0)
#define LOG_HEXDUMP_INF(...) ((void)0)
#define INPUT_BTN_0 256
#define INPUT_BTN_1 257
#define INPUT_EV_KEY 1
#define INPUT_EV_REL 2
#define INPUT_EV_ABS 3
#define INPUT_BTN_TOUCH 330
#define INPUT_BTN_2 258
#define INPUT_BTN_3 259
#define INPUT_BTN_4 260
#define WRITE_BIT(v,b,n) ((v)=(n)?((v)|(1U<<(b))):((v)&~(1U<<(b))))
#define BIT(b) (1U<<(b))
#define IS_ENABLED(x) (x)
#define CONFIG_ZMK_POINTING_SMOOTH_SCROLLING 0
#define ZMK_INPUT_PROC_STOP 1
#define ZMK_HID_MOUSE_NUM_BUTTONS 5
#define INPUT_REL_X 0
#define INPUT_REL_Y 1
#define INPUT_REL_WHEEL 8
#define INPUT_REL_HWHEEL 6
#define K_NO_WAIT 0
#define XIAO147_TOUCH_CANCEL UINT8_MAX
#define K_MSEC(x) (x)
#define DT_INST_FOREACH_STATUS_OKAY(x)
#define CONTAINER_OF(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct k_work {int unused;};
struct k_work_delayable {struct k_work work;};
struct i2c_dt_spec {int unused;};
struct device {void *data; const void *config;};
struct input_event {uint8_t type;uint16_t code;int value;bool sync;const struct device *dev;};
static void route_event(uint8_t type,uint16_t code,int value,bool sync,const struct device *dev);
static uint32_t now;
static int page,read_error,nrel,nkey,nsample,submitted,cancel_samples,extended_calls;
static bool reject_extended;
static enum xiao147_orientation pose;
static int rel_code[32],rel_value[32],key_code[32],key_value[32];
static uint8_t sensor[32];
static uint16_t sample_x,sample_y;
static struct k_work_delayable *k_work_delayable_from_work(struct k_work *w) {return (void *)w;}
static void k_work_reschedule(struct k_work_delayable *w,int t) {(void)w;(void)t;}
static void k_work_schedule(struct k_work_delayable *w,int t) {(void)w;(void)t;}
static void k_work_init_delayable(struct k_work_delayable *w,void(*f)(struct k_work *)) {(void)w;(void)f;}
static uint32_t k_uptime_get_32(void) {return now;}
static bool i2c_is_ready_dt(const struct i2c_dt_spec *b) {(void)b;return true;}
static int i2c_write_dt(const struct i2c_dt_spec *b,const void *p,size_t n) {(void)b;(void)p;(void)n;return read_error;}
static int i2c_read_dt(const struct i2c_dt_spec *b,void *p,size_t n) {
    (void)b;assert(n==3||n==14||n==32);
    if(n==32){extended_calls++;if(reject_extended)return -EIO;}
    if(n==14||n==32){memcpy(p,sensor,n);}return read_error;
}
static int input_report_rel(const struct device*d,int c,int v,bool sync,int wait) {(void)wait;rel_code[nrel]=c;rel_value[nrel++]=v;route_event(INPUT_EV_REL,c,v,sync,d);return 0;}
static void input_report_key(const struct device*d,int c,int v,bool sync,int wait) {(void)wait;key_code[nkey]=c;key_value[nkey++]=v;route_event(INPUT_EV_KEY,c,v,sync,d);}
static enum xiao147_page xiao147_ui_page(void) {return page;}
static enum xiao147_orientation xiao147_rotation_get(void) {return pose;}
static bool xiao147_ui_wake_on_touch(void) {return false;}
static void xiao147_ui_touch(uint8_t f,uint16_t x,uint16_t y,enum xiao147_orientation o) {assert(o==pose);if(f==XIAO147_TOUCH_CANCEL){cancel_samples++;}else if(f){sample_x=x;sample_y=y;nsample++;}}
typedef int atomic_val_t;
static int imu_mode,auto_rotate_mode,restore_landscape,sleeping;
static int auto_updates,last_auto_pose;
static uint8_t imu_axes[6];
static bool xiao147_ui_imu_enabled(void) {return imu_mode!=0;}
static bool xiao147_ui_auto_rotate_enabled(void) {return auto_rotate_mode!=0;}
static void xiao147_ui_set_auto_orientation(enum xiao147_orientation o) {auto_updates++;last_auto_pose=o;}
static bool xiao147_ui_wake_on_motion(void) {return false;}
static int i2c_write_read_dt(const struct i2c_dt_spec *b,const void *p,size_t n,void *out,size_t len) {
    (void)b;assert(n==1);if(read_error)return read_error;
    if(*(const uint8_t *)p==0x0f){assert(len==1);*(uint8_t *)out=0x6a;}
    else {assert(len==6);memcpy(out,imu_axes,6);}return 0;
}
static uint32_t last_activity_ms;
static struct k_work redraw_work;
static int atomic_get(int *v) {return *v;}
static bool atomic_cas(int *v,int old,int next) {if(*v!=old)return false;*v=next;return true;}
static int atomic_set(int *v,int next) {int old=*v;*v=next;return old;}
static bool zmk_display_is_initialized(void) {return true;}
static void *zmk_display_work_q(void) {return &redraw_work;}
static void k_work_submit_to_queue(void *q,struct k_work *w) {assert(q==&redraw_work&&w==&redraw_work);submitted++;}
'''
hid_mocks = r'''
static int hid_x,hid_y,hid_wheel,hid_pan,hid_buttons,hid_reports,last_wheel,last_pan;
static void zmk_hid_mouse_scroll_set(int x,int y) {hid_pan=x;hid_wheel=y;}
static void zmk_hid_mouse_movement_set(int x,int y) {hid_x=x;hid_y=y;}
static void zmk_hid_mouse_button_press(int b) {hid_buttons|=BIT(b);}
static void zmk_hid_mouse_button_release(int b) {hid_buttons&=~BIT(b);}
static int zmk_endpoint_send_mouse_report(void) {hid_reports++;last_wheel=hid_wheel;last_pan=hid_pan;return 0;}
static int filter_with_input_config(const struct input_listener_config *c,
    struct input_listener_data *d,struct input_event *e) {
    (void)d;(void)e;assert(!c->base.processors_len&&!c->layer_overrides_len);return 0;
}
'''
hid_route = r'''
static void route_event(uint8_t type,uint16_t code,int value,bool sync,const struct device *dev) {
    static const struct input_listener_config cfg={0};
    static struct input_listener_data data={0};
    struct input_event event={type,code,value,sync,dev};input_handler(&cfg,&data,&event);
}
'''
test = r'''
static void contact(int index,int x,int y) {
    int off=2+6*index;sensor[off]=x>>8;sensor[off+1]=x;
    sensor[off+2]=y>>8;sensor[off+3]=y;
}
static void poll(struct axs_touch_data *d) {now+=16;axs_poll(&d->poll_work.work);}
int main(void) {
    const struct axs_touch_config cfg={0};struct axs_touch_data d={0};
    struct device dev={&d,&cfg};assert(axs_touch_init(&dev)==0);
    /* Independent corner expectations for raw (0,0),(171,319), and point(40,70). */
    const int expected[4][6]={{0,0,319,171,70,40},{171,0,0,319,131,70},
                              {319,171,0,0,249,131},{0,319,171,0,40,249}};
    for(pose=0;pose<4;pose++) {
        sensor[1]=0;poll(&d);
        for(int i=0;i<3;i++) {
            int x=i==0?0:i==1?171:40,y=i==0?0:i==1?319:70;
            contact(0,x,y);sensor[1]=1;
            struct point p=decode_point(sensor,0,pose);
            assert(p.x==expected[pose][i*2]&&p.y==expected[pose][i*2+1]);
        }
        sensor[1]=0;poll(&d);contact(0,40,70);sensor[1]=1;poll(&d);
        contact(0,42,73);nrel=0;poll(&d);
        const int deltas[4][2]={{3,2},{-2,3},{-3,-2},{2,-3}};
        assert(nrel==2&&rel_code[0]==INPUT_REL_X&&rel_value[0]==deltas[pose][0]);
        assert(rel_code[1]==INPUT_REL_Y&&rel_value[1]==deltas[pose][1]);
        sensor[1]=0;poll(&d);
    }
    pose=0;page=XIAO147_PAGE_SETTINGS;sensor[1]=0;poll(&d);
    sensor[1]=1;contact(0,40,70);poll(&d);assert(sample_x==70&&sample_y==40);
    /* All 180-degree changes cancel an active gesture, including settings. */
    int before=nsample;pose=2;poll(&d);assert(nsample==before&&d.swallow_until_release);
    poll(&d);assert(nsample==before);sensor[1]=0;poll(&d);
    sensor[1]=1;poll(&d);assert(sample_x==249&&sample_y==131);
    /* I2C cancellation swallows a held finger across menu rows until lift. */
    int samples=nsample;read_error=-EIO;poll(&d);
    assert(cancel_samples==1&&d.swallow_until_release&&d.fingers==0);
    poll(&d);assert(cancel_samples==1&&d.swallow_until_release);
    read_error=0;contact(0,80,140);poll(&d);assert(nsample==samples&&d.swallow_until_release);
    sensor[1]=0;poll(&d);assert(!d.swallow_until_release);
    sensor[1]=1;poll(&d);assert(nsample==samples+1);
    page=0;pose=0;sensor[1]=0;poll(&d);
    contact(0,40,70);contact(1,60,90);sensor[1]=2;poll(&d);
    contact(0,48,78);contact(1,68,98);nrel=0;poll(&d);
    assert(nrel==2&&rel_code[0]==INPUT_REL_HWHEEL&&rel_value[0]==-1);
    assert(rel_code[1]==INPUT_REL_WHEEL&&rel_value[1]==-1);
    assert(last_pan==-1&&last_wheel==-1&&hid_reports>0&&!hid_pan&&!hid_wheel);
    /* One scroll tick (<tap move threshold) must suppress any tap, and a
     * 2->1 transition must not turn the rest of the gesture into a pointer. */
    int scrolled_keys=nkey;sensor[1]=1;poll(&d);nrel=0;
    contact(0,50,80);poll(&d);assert(nrel==0);
    sensor[1]=0;poll(&d);assert(nkey==scrolled_keys);
    sensor[1]=1;poll(&d);sensor[1]=2;poll(&d);
    /* Orientation switch while two fingers down must not click/scroll. */
    int keys=nkey;pose=2;poll(&d);nrel=0;poll(&d);assert(nrel==0);
    sensor[1]=0;poll(&d);assert(nkey==keys);
    sensor[1]=2;poll(&d);sensor[1]=0;poll(&d);
    assert(nkey==keys+2&&key_code[keys]==INPUT_BTN_1&&key_value[keys]==1&&key_value[keys+1]==0);
    /* 2->3->2->1->0 cancels the full gesture, not just the 3-point sample. */
    keys=nkey;sensor[1]=2;poll(&d);
    for(int i=0;i<5;i++)contact(i,20+i*20,40+i*40);
    sensor[1]=3;poll(&d);assert(d.swallow_until_release&&!d.fingers);
    sensor[1]=2;nrel=0;poll(&d);sensor[1]=1;poll(&d);sensor[1]=0;poll(&d);
    assert(nkey==keys&&!nrel&&!d.swallow_until_release);
    for(int i=3;i<=5;i++){sensor[1]=i;poll(&d);sensor[1]=0;poll(&d);}
    assert(d.count_histogram[3]&&d.count_histogram[4]&&d.count_histogram[5]&&!d.bad_reports);
    /* Full-frame idle tail FF is normal if count=1, invalid if claimed count=5. */
    memset(sensor+8,0xff,24);sensor[1]=1;poll(&d);assert(!d.bad_reports);
    sensor[1]=5;poll(&d);assert(d.bad_reports==1&&d.swallow_until_release);
    sensor[1]=0;poll(&d);assert(nkey==keys);
    sensor[1]=5;poll(&d);assert(d.bad_reports==2&&d.swallow_until_release);
    sensor[1]=1;poll(&d);sensor[1]=0;poll(&d);assert(nkey==keys);
    /* Unsupported 32-byte read locks to 14 once. Poison unseen tail: never parse it. */
    reject_extended=true;poll(&d);assert(d.legacy_only&&d.extended_failures==1);
    int attempts=extended_calls;
    sensor[1]=1;contact(0,40,70);poll(&d);sensor[1]=0;poll(&d);
    assert(extended_calls==attempts&&d.reads_legacy>=3);
    unsigned bad=d.bad_reports;sensor[1]=3;poll(&d);assert(d.bad_reports==bad+1);
    keys=nkey;assert(d.swallow_until_release);
    sensor[1]=1;poll(&d);sensor[1]=0;poll(&d);
    assert(nkey==keys&&!d.swallow_until_release);reject_extended=false;
    sensor[1]=1;poll(&d);sensor[1]=0;poll(&d);
    assert(nkey==keys+2&&key_code[keys]==INPUT_BTN_0);
    enum xiao147_orientation o;
    assert(xiao147_gravity_orientation(-16000,0,&o)&&o==XIAO147_USB_LEFT);
    assert(xiao147_gravity_orientation(16000,0,&o)&&o==XIAO147_USB_RIGHT);
    assert(xiao147_gravity_orientation(0,16000,&o)&&o==XIAO147_BUTTONS_BOTTOM);
    assert(xiao147_gravity_orientation(0,-16000,&o)&&o==XIAO147_BUTTONS_TOP);
    assert(!xiao147_gravity_orientation(1000,1000,&o));
    assert(!xiao147_gravity_orientation(10000,11000,&o));
    const int gyro[4][2]={{1300,-650},{650,1300},{-1300,650},{-650,-1300}};
    for(int i=0;i<4;i++){struct xiao147_xy p=xiao147_gyro_to_logical(i,1300,650);assert(p.x==gyro[i][0]&&p.y==gyro[i][1]);}
    struct input_event e={.type=INPUT_EV_KEY,.code=INPUT_BTN_0,.value=1};
    auto_rotate_mode=1;sleeping=1;imu_button_input(&e,NULL);
    assert(imu_mode==1&&auto_rotate_mode==0&&sleeping==0&&submitted==1);
    e.value=0;imu_button_input(&e,NULL);assert(imu_mode==1&&submitted==1);
    e.value=1;imu_button_input(&e,NULL);assert(imu_mode==0&&restore_landscape==1&&submitted==2);
    e.code=INPUT_BTN_1;imu_button_input(&e,NULL);assert(imu_mode==0&&submitted==2);
    /* Actual IMU poll: stable 600 ms, diagonal/flat hold and I2C failure reset. */
    struct imu_data id={0};const struct imu_config ic={0};struct device iv={&id,&ic};
    assert(imu_init(&iv)==0);auto_rotate_mode=1;
    imu_axes[0]=0x80;imu_axes[1]=0x3e; /* +16000 X */
    for(int i=0;i<30;i++){now+=20;imu_poll(&id.poll_work.work);}assert(auto_updates==0);
    now+=20;imu_poll(&id.poll_work.work);assert(auto_updates==1&&last_auto_pose==2);
    memset(imu_axes,0,6);now+=20;imu_poll(&id.poll_work.work);assert(!id.auto_rotate_active);
    imu_axes[2]=0x80;imu_axes[3]=0xc1; /* -16000 Y */
    now+=20;imu_poll(&id.poll_work.work);assert(id.orientation_candidate==3);
    read_error=-EIO;now+=20;imu_poll(&id.poll_work.work);assert(!id.auto_rotate_active);
    read_error=0;
    for(int i=0;i<30;i++){now+=20;imu_poll(&id.poll_work.work);}assert(auto_updates==1);
    now+=20;imu_poll(&id.poll_work.work);assert(auto_updates==2&&last_auto_pose==3);
    auto_rotate_mode=0;imu_mode=1;pose=1; /* desired buttons-bottom gyro */
    imu_axes[0]=0x14;imu_axes[1]=5;imu_axes[2]=0x8a;imu_axes[3]=2; /* gx=1300 gy=650 */
    nrel=0;now+=20;imu_poll(&id.poll_work.work);assert(nrel==0);
    now+=3000;imu_poll(&id.poll_work.work);
    assert(nrel==2&&rel_value[0]==2&&rel_value[1]==4);
    /* Stable bias, no moving calibration, fractional +/- symmetry, clearing. */
    struct xiao147_air_motion m={0};int16_t stationary[3]={100,-100,50};
    xiao147_air_begin(&m,0);
    for(int i=0;i<50;i++) {struct xiao147_xy v=xiao147_air_sample(&m,i*20,0,stationary);assert(!v.x&&!v.y);}
    assert(m.bias_valid&&m.bias[0]==100&&m.bias[1]==-100&&!m.calibrating);
    int16_t slow[3]={200,-100,50};int sum=0;
    for(int i=0;i<13;i++)sum+=xiao147_air_sample(&m,1100+i*20,0,slow).x;
    assert(sum==4);xiao147_air_clear(&m);slow[0]=0;sum=0;
    for(int i=0;i<13;i++)sum+=xiao147_air_sample(&m,1500+i*20,0,slow).x;
    assert(sum==-4);xiao147_air_clear(&m);assert(!m.remainder[0]&&!m.remainder[1]);
    xiao147_air_begin(&m,2000);int16_t moving[3]={2000,1000,0};
    for(int i=0;i<149;i++)xiao147_air_sample(&m,2000+i*20,0,moving);
    xiao147_air_sample(&m,5000,0,moving);assert(m.bias[0]==100&&m.bias[1]==-100&&!m.calibrating);
    xiao147_air_begin(&m,6000);moving[0]=300;moving[1]=moving[2]=0;
    for(int i=0;i<150;i++)xiao147_air_sample(&m,6000+i*20,0,moving);
    xiao147_air_sample(&m,9000,0,moving);assert(m.bias[0]==100&&m.bias[1]==-100);
    /* Even below the admission bound, don't silently overwrite a trusted zero. */
    xiao147_air_begin(&m,10000);moving[0]=150;moving[1]=-100;moving[2]=50;
    for(int i=0;i<151;i++)xiao147_air_sample(&m,10000+i*20,0,moving);
    assert(m.bias[0]==100&&m.bias[1]==-100);
    puts("PASS: actual touch gestures/four poses, cancellation, actual IMU stability/error recovery/vectors and USR1 edge toggles");
}
'''
with tempfile.TemporaryDirectory(prefix="xiao147-input-") as directory:
    c=Path(directory)/"test.c"
    c.write_text(prelude+listener_types+hid_mocks+listener_functions+hid_route+
                 source+imu_source+toggle+callback+test)
    binary=Path(directory)/"test"
    subprocess.run(["cc","-std=gnu11","-Wall","-Wextra","-Werror",
                    "-Wno-unused-parameter","-fsanitize=address,undefined",
                    "-I",str(root/"src"),str(c),"-o",str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
