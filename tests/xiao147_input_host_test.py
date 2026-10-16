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
sleep_query = function("bool xiao147_ui_is_sleeping(void)")
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
static void host_log(const char *format, ...) {(void)format;}
#define LOG_INF(...) host_log(__VA_ARGS__)
#define LOG_WRN(...) host_log(__VA_ARGS__)
#define LOG_ERR(...) host_log(__VA_ARGS__)
#define LOG_DBG(...) host_log(__VA_ARGS__)
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
static unsigned touch_io_phase, settle_calls;
static enum xiao147_orientation pose;
static int rel_code[128],rel_value[128],key_code[128],key_value[128];
static unsigned failed_press,failed_release,failed_relative,key_attempts;
static int failed_relative_code;
static uint8_t sensor[32];
static uint16_t sample_x,sample_y;
static struct k_work_delayable *k_work_delayable_from_work(struct k_work *w) {return (void *)w;}
static void k_work_reschedule(struct k_work_delayable *w,int t) {(void)w;(void)t;}
static void k_work_schedule(struct k_work_delayable *w,int t) {(void)w;(void)t;}
static void k_work_init_delayable(struct k_work_delayable *w,void(*f)(struct k_work *)) {(void)w;(void)f;}
static uint32_t k_uptime_get_32(void) {return now;}
static bool i2c_is_ready_dt(const struct i2c_dt_spec *b) {(void)b;return true;}
static int i2c_write_dt(const struct i2c_dt_spec *b,const void *p,size_t n) {
    (void)b;(void)p;touch_io_phase=n==1&&!read_error?1:0;return read_error;
}
static void k_busy_wait(uint32_t us) {
    assert(us==100&&touch_io_phase==1);touch_io_phase=2;settle_calls++;
}
static int i2c_read_dt(const struct i2c_dt_spec *b,void *p,size_t n) {
    (void)b;assert(n==3||n==14||n==32);
    assert(touch_io_phase==2);touch_io_phase=0;
    if(n==32){extended_calls++;if(reject_extended)return -EIO;}
    if(n==14||n==32){memcpy(p,sensor,n);}return read_error;
}
static int input_report_rel(const struct device*d,int c,int v,bool sync,int wait) {
    assert(wait==K_NO_WAIT&&sync);
    if(failed_relative&&c==failed_relative_code){failed_relative--;return -EIO;}
    assert(nrel<128);rel_code[nrel]=c;rel_value[nrel++]=v;
    route_event(INPUT_EV_REL,c,v,sync,d);return 0;
}
static int input_report_key(const struct device*d,int c,int v,bool sync,int wait) {
    assert(wait==K_NO_WAIT&&sync);key_attempts++;
    if(v&&failed_press){failed_press--;return -EIO;}
    if(!v&&failed_release){failed_release--;return -EIO;}
    assert(nkey<128);key_code[nkey]=c;key_value[nkey++]=v;
    route_event(INPUT_EV_KEY,c,v,sync,d);return 0;
}
static enum xiao147_page xiao147_ui_page(void) {return page;}
static enum xiao147_orientation xiao147_rotation_get(void) {return pose;}
static bool xiao147_ui_wake_on_touch(void) {return false;}
static void xiao147_ui_touch(uint8_t f,uint16_t x,uint16_t y,enum xiao147_orientation o) {assert(f==XIAO147_TOUCH_CANCEL||o==pose);if(f==XIAO147_TOUCH_CANCEL){cancel_samples++;}else if(f){sample_x=x;sample_y=y;nsample++;}}
typedef int atomic_val_t;
typedef int atomic_t;
static int imu_mode,auto_rotate_mode,restore_landscape,sleeping;
bool xiao147_ui_is_sleeping(void);
static int auto_updates,last_auto_pose;
static uint8_t imu_axes[6];
static uint8_t accel_axes[6]={0,0,0,0x40,0,0}; /* buttons-bottom upright: +Y up */
static bool xiao147_ui_imu_enabled(void) {return imu_mode!=0;}
static bool xiao147_ui_auto_rotate_enabled(void) {return auto_rotate_mode!=0;}
static void xiao147_ui_set_auto_orientation(enum xiao147_orientation o) {auto_updates++;last_auto_pose=o;}
static bool xiao147_ui_wake_on_motion(void) {return false;}
static int active_endpoint;
static void (*imu_read_hook)(void);
static int xiao147_pointer_endpoint(void) {return active_endpoint;}
static int i2c_write_read_dt(const struct i2c_dt_spec *b,const void *p,size_t n,void *out,size_t len) {
    (void)b;assert(n==1);if(read_error)return read_error;
    if(*(const uint8_t *)p==0x0f){assert(len==1);*(uint8_t *)out=0x6a;}
    else {assert(len==6);memcpy(out,*(const uint8_t *)p==0x22?imu_axes:accel_axes,6);}
    if (*(const uint8_t *)p==0x22 && imu_read_hook) {imu_read_hook();}
    return 0;
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
struct host_report {int x,y,wheel,pan,buttons;};
static struct host_report host_reports[512];
static int zmk_endpoint_send_mouse_report(void) {
    assert(hid_reports<512);
    host_reports[hid_reports++]=(struct host_report){hid_x,hid_y,hid_wheel,hid_pan,hid_buttons};
    if(hid_wheel){last_wheel=hid_wheel;}
    if(hid_pan){last_pan=hid_pan;}return 0;
}
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
static int imu_edge;
static void change_during_imu_read(void) {
    if(imu_edge==0)page=1;
    if(imu_edge==1)imu_mode=0;
    if(imu_edge==2)active_endpoint=1;
    if(imu_edge==3)sleeping=1;
    if(imu_edge==4)atomic_set(&normal_button_gate,1);
}
static void imu_transition_tests(void) {
    for(imu_edge=0;imu_edge<5;imu_edge++) {
        struct imu_data id={0};const struct imu_config ic={0};struct device iv={&id,&ic};
        page=0;imu_mode=1;auto_rotate_mode=0;sleeping=0;active_endpoint=0;pose=1;
        read_error=0;atomic_set(&normal_button_gate,0);
        assert(imu_init(&iv)==0);id.gyro_on=true;id.last_pose=pose;id.output_allowed=true;
        id.motion.bias_valid=true;id.motion.have_time=true;id.motion.last_ms=now;
        id.motion.filtered[0]=-570*256;id.motion.filtered[1]=1220*256;
        imu_axes[0]=0x14;imu_axes[1]=5;imu_axes[2]=0x8a;imu_axes[3]=2;
        int before=hid_reports;imu_read_hook=change_during_imu_read;
        now+=20;imu_poll(&id.poll_work.work);imu_read_hook=NULL;
        assert(hid_reports==before&&!id.motion.have_time);
    }
    page=0;imu_mode=0;sleeping=0;active_endpoint=0;atomic_set(&normal_button_gate,0);
}
static void ordinary_failure_tests(void) {
    const struct axs_touch_config cfg={0};struct axs_touch_data d={0};
    struct device dev={&d,&cfg};
    page=0;pose=0;read_error=0;reject_extended=false;
    memset(sensor,0,sizeof(sensor));nrel=nkey=0;assert(!hid_buttons);
    assert(axs_touch_init(&dev)==0);poll(&d);
    contact(0,40,70);contact(1,60,90);
    failed_press=1;sensor[1]=1;poll(&d);sensor[1]=0;poll(&d);
    assert(!nkey&&!d.pending_button&&!xiao147_touch_button_pending()&&d.input_errors==1);
    for(int i=0;i<4;i++){poll(&d);}assert(!nkey); /* No delayed click. */

    /* Right press accepted, release repeatedly fails. Release work is independent
     * of page, touch bus errors and held contacts; at most one retry per tick. */
    failed_release=3;sensor[1]=2;poll(&d);sensor[1]=0;poll(&d);
    assert(nkey==1&&d.pending_button==INPUT_BTN_1&&xiao147_touch_button_pending());
    assert(hid_buttons==BIT(1));
    page=XIAO147_PAGE_SETTINGS;read_error=-EIO;sensor[1]=1;
    unsigned io=extended_calls;
    for(int i=0;i<3;i++){
        unsigned attempts=key_attempts;poll(&d);
        assert(key_attempts==attempts+1&&extended_calls==(int)io);
    }
    assert(nkey==2&&!hid_buttons&&!d.pending_button&&!xiao147_touch_button_pending());
    assert(d.swallow_until_release);poll(&d);assert(nkey==2);
    read_error=0;page=0;poll(&d);assert(d.swallow_until_release);
    sensor[1]=0;poll(&d);assert(!d.swallow_until_release);
    sensor[1]=1;poll(&d);sensor[1]=0;poll(&d);assert(nkey==4&&!hid_buttons);

    /* A large discontinuity followed by lift cannot generate a phantom click. */
    int keys=nkey;sensor[1]=1;contact(0,40,70);poll(&d);
    contact(0,40,200);poll(&d);assert(d.swallow_until_release);
    sensor[1]=0;poll(&d);assert(nkey==keys);

    const int codes[]={INPUT_REL_X,INPUT_REL_Y,INPUT_REL_HWHEEL,INPUT_REL_WHEEL};
    for(unsigned i=0;i<4;i++){
        sensor[1]=0;poll(&d);contact(0,40,70);contact(1,60,90);
        sensor[1]=i<2?1:2;poll(&d);
        failed_relative=1;failed_relative_code=codes[i];
        int before=hid_reports;keys=nkey;
        contact(0,48,78);contact(1,68,98);poll(&d);
        assert(!failed_relative&&d.swallow_until_release&&!d.fingers);
        assert(!d.scroll_x_remainder&&!d.scroll_y_remainder);
        assert(hid_reports-before==(int)(i&1U));
        if(i&1U){
            struct host_report r=host_reports[before];
            assert(!r.y&&!r.wheel&&!r.buttons);
            if(i==1){assert(r.x==8&&!r.pan);}else{assert(r.pan==-1&&!r.x);}
        }
        contact(0,50,80);poll(&d);sensor[1]=0;poll(&d);
        assert(nkey==keys&&hid_reports==before+(int)(i&1U));
        /* A later orthogonal event must not flush the failed event or residue. */
        sensor[1]=1;contact(0,40,70);poll(&d);
        before=hid_reports;contact(0,44,70);poll(&d);
        assert(hid_reports==before+1);
        struct host_report r=host_reports[before];
        assert(!r.x&&r.y==4&&!r.wheel&&!r.pan&&!r.buttons);
        contact(0,60,70);poll(&d);sensor[1]=0;poll(&d);assert(nkey==keys);
    }

    /* The same complete-event/no-replay rule applies to both gyro axes. */
    struct imu_data id={0};const struct imu_config ic={0};struct device iv={&id,&ic};
    assert(imu_init(&iv)==0);imu_mode=1;auto_rotate_mode=0;pose=1;id.last_pose=pose;
    id.gyro_on=true;
    imu_axes[0]=0x14;imu_axes[1]=5;imu_axes[2]=0x8a;imu_axes[3]=2;
    for(int i=0;i<2;i++){
        failed_relative=1;failed_relative_code=i?INPUT_REL_Y:INPUT_REL_X;
        id.motion.calibrating=false;id.motion.bias_valid=true;id.output_allowed=true;
        xiao147_air_clear(&id.motion);id.motion.have_time=true;id.motion.last_ms=now;
        id.motion.filtered[0]=-570*256;id.motion.filtered[1]=1220*256;
        int before=hid_reports;
        now+=20;imu_poll(&id.poll_work.work);
        assert(!failed_relative&&hid_reports==before+i&&id.input_errors==(unsigned)(i+1));
        assert(!id.motion.remainder[0]&&!id.motion.remainder[1]);
        if(i){assert(host_reports[before].x<0&&!host_reports[before].y);}
        imu_mode=0;now+=20;imu_poll(&id.poll_work.work);
        assert(hid_reports==before+i);
        imu_mode=1;id.gyro_on=true;
    }
    /* Touch gates output but must not power-cycle or recalibrate the gyro. */
    atomic_set(&normal_button_gate,1);int before=hid_reports;
    now+=20;imu_poll(&id.poll_work.work);assert(id.gyro_on&&hid_reports==before);
    atomic_set(&normal_button_gate,0);now+=20;imu_poll(&id.poll_work.work);
    assert(id.gyro_on&&!id.motion.calibrating&&id.motion.bias_valid&&hid_reports==before);
    puts("PASS: normal press/release failures, page/bus-independent retry, discontinuity cancellation, complete pointer/wheel/IMU events and no stale movement replay");
}
static void interrupted_contact_tests(void) {
    const struct axs_touch_config cfg={0};struct axs_touch_data d={0};
    struct device dev={&d,&cfg};
    page=0;pose=0;read_error=0;reject_extended=false;imu_mode=0;
    memset(sensor,0,sizeof(sensor));nrel=nkey=0;assert(!hid_buttons);
    assert(axs_touch_init(&dev)==0);poll(&d);
    for(int fingers=1;fingers<=2;fingers++) {
        contact(0,40,70);contact(1,60,90);sensor[1]=fingers;poll(&d);
        now+=85; /* poll adds 16: a 101 ms gap, still inside tap duration */
        contact(0,48,78);contact(1,68,98);poll(&d);
        assert(d.swallow_until_release&&!nrel&&!nkey);
        sensor[1]=0;poll(&d);assert(!d.swallow_until_release&&!nkey);
        sensor[1]=fingers;poll(&d);now+=85;sensor[1]=0;poll(&d);
        assert(!nkey&&!d.swallow_until_release); /* Late zero is not a tap. */
    }
    /* Unsigned uptime wrap does not cancel a genuinely short gesture. */
    now=UINT32_MAX-24;sensor[1]=1;poll(&d);sensor[1]=0;poll(&d);
    assert(nkey==2&&key_code[0]==INPUT_BTN_0&&!hid_buttons);
    nkey=0;
    /* The boundary is inclusive, not a blanket ban on delayed valid frames. */
    sensor[1]=1;poll(&d);now+=84;sensor[1]=0;poll(&d);assert(nkey==2);
    nkey=0;
    page=XIAO147_PAGE_SETTINGS;poll(&d);sensor[1]=1;poll(&d);
    int cancels=cancel_samples,samples=nsample;
    now+=85;poll(&d);assert(cancel_samples==cancels+1&&nsample==samples);
    sensor[1]=0;poll(&d);assert(!nkey);
    sensor[1]=1;poll(&d);cancels=cancel_samples;page=XIAO147_PAGE_STATUS;
    poll(&d);assert(cancel_samples==cancels+1&&d.swallow_until_release);
    sensor[1]=0;poll(&d);assert(!nkey);
    puts("PASS: late one/two contact frames and releases cancel without HID, menu drag cancellation, exact gap boundary and uptime wrap");
}
static void pair_tap_geometry_tests(void) {
    /* Synthetic native two-record reports test code behaviour, NOT proof
     * that the physical panel has begun reporting a second contact. */
    page=0;read_error=0;reject_extended=false;imu_mode=0;
    for(pose=0;pose<4;pose++) {
        for(int scenario=0;scenario<8;scenario++) {
            const struct axs_touch_config cfg={0};struct axs_touch_data d={0};
            struct device dev={&d,&cfg};
            memset(sensor,0,sizeof(sensor));nrel=nkey=hid_reports=0;
            assert(!hid_buttons);assert(axs_touch_init(&dev)==0);poll(&d);
            contact(0,40,80);contact(1,120,240);
            if(scenario==5) {sensor[1]=1;poll(&d);contact(0,60,80);}
            sensor[1]=2;poll(&d);
            if(scenario==0) {
                contact(0,120,240);contact(1,40,80);poll(&d);
            } else if(scenario==1||scenario==4||scenario==7) {
                int offset=scenario==4?12:13;
                contact(0,40-offset,80);contact(1,120+offset,240);poll(&d);
                assert(!nrel); /* Mean unchanged even while both move. */
                contact(0,40,80);contact(1,120,240);poll(&d);
                if(scenario==7) {
                    assert(d.moved); /* Tap-only rejection must not block scroll. */
                    contact(0,48,88);contact(1,128,248);poll(&d);
                    const int signs[4][2]={{-1,-1},{1,-1},{1,1},{-1,1}};
                    assert(nrel==2&&last_pan==signs[pose][0]&&last_wheel==signs[pose][1]);
                }
            } else if(scenario==2||scenario==3) {
                sensor[1]=1;contact(0,40+(scenario==2?13:12),80);poll(&d);
                contact(0,40,80);poll(&d);assert(!nrel);
            } else if(scenario==6) {
                sensor[1]=1;poll(&d);sensor[1]=2;
                contact(1,140,240);poll(&d); /* Must not re-anchor pair. */
            }
            sensor[1]=0;poll(&d);
            bool click=scenario==0||scenario==3||scenario==4;
            assert(nkey==(click?2:0)&&!hid_buttons&&!d.tap_pair_valid);
            if(click) {assert(key_code[0]==INPUT_BTN_1&&key_value[0]==1&&key_value[1]==0);}
            /* Clean full release permits the next genuine stationary tap. */
            nkey=0;contact(0,40,80);contact(1,120,240);
            sensor[1]=2;poll(&d);sensor[1]=0;poll(&d);
            assert(nkey==2&&key_code[0]==INPUT_BTN_1&&!hid_buttons);
        }
    }
    puts("PASS: all-four-pose pair anchors, record reorder, opposed motion, moving lift tail, exact12 boundary, staggered touchdown and no re-anchor before lift");
}
int main(void) {
    sleeping=1;assert(xiao147_ui_is_sleeping());assert(sleeping==1&&!submitted);
    sleeping=0;assert(!xiao147_ui_is_sleeping());
    const struct axs_touch_config cfg={0};struct axs_touch_data d={0};
    struct device dev={&d,&cfg};assert(axs_touch_init(&dev)==0);
    /* Independent corner expectations for raw (0,0),(171,319), and point(40,70). */
    const int expected[4][6]={{0,0,319,171,70,40},{171,0,0,319,131,70},
                              {319,171,0,0,249,131},{0,319,171,0,40,249}};
    for(pose=0;pose<4;pose++) {
        sensor[1]=0;poll(&d);assert(settle_calls>=2);
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
    /* Real ZMK listener wheel/pan: both signs, both axes, all four poses. */
    const int natural[4][2]={{-1,-1},{1,-1},{1,1},{-1,1}};
    for(pose=0;pose<4;pose++) {
        sensor[1]=0;poll(&d);contact(0,40,70);contact(1,60,90);
        sensor[1]=2;poll(&d);contact(0,48,78);contact(1,68,98);nrel=0;poll(&d);
        assert(nrel==2&&last_pan==natural[pose][0]&&last_wheel==natural[pose][1]);
        contact(0,40,70);contact(1,60,90);nrel=0;poll(&d);
        assert(nrel==2&&last_pan==-natural[pose][0]&&last_wheel==-natural[pose][1]);
        int clicks=nkey;sensor[1]=0;poll(&d);assert(nkey==clicks);
    }
    pose=0;page=XIAO147_PAGE_SETTINGS;sensor[1]=0;poll(&d);
    sensor[1]=1;contact(0,40,70);poll(&d);assert(sample_x==70&&sample_y==40);
    /* All 180-degree changes cancel an active gesture, including settings. */
    int before=nsample;pose=2;poll(&d);assert(nsample==before&&d.swallow_until_release);
    poll(&d);assert(nsample==before);sensor[1]=0;poll(&d);
    sensor[1]=1;poll(&d);assert(sample_x==249&&sample_y==131);
    /* I2C cancellation swallows a held finger across menu rows until lift. */
    int samples=nsample;unsigned waits=settle_calls;read_error=-EIO;poll(&d);
    assert(settle_calls==waits);
    assert(cancel_samples==2&&d.swallow_until_release&&d.fingers==0);
    poll(&d);assert(cancel_samples==2&&d.swallow_until_release);
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
    memset(accel_axes,0,6);accel_axes[0]=0x80;accel_axes[1]=0x3e; /* +16000 X */
    for(int i=0;i<30;i++){now+=20;imu_poll(&id.poll_work.work);}assert(auto_updates==0);
    now+=20;imu_poll(&id.poll_work.work);assert(auto_updates==1&&last_auto_pose==2);
    memset(accel_axes,0,6);now+=20;imu_poll(&id.poll_work.work);assert(!id.auto_rotate_active);
    accel_axes[2]=0x80;accel_axes[3]=0xc1; /* -16000 Y */
    now+=20;imu_poll(&id.poll_work.work);assert(id.orientation_candidate==3);
    read_error=-EIO;now+=20;imu_poll(&id.poll_work.work);assert(!id.auto_rotate_active);
    read_error=0;
    for(int i=0;i<30;i++){now+=20;imu_poll(&id.poll_work.work);}assert(auto_updates==1);
    now+=20;imu_poll(&id.poll_work.work);assert(auto_updates==2&&last_auto_pose==3);
    auto_rotate_mode=0;imu_mode=1;pose=1; /* desired buttons-bottom gyro */
    memset(accel_axes,0,6);accel_axes[3]=0x40;
    imu_axes[0]=0x14;imu_axes[1]=5;imu_axes[2]=0x8a;imu_axes[3]=2; /* gx=1300 gy=650 */
    nrel=0;now+=20;imu_poll(&id.poll_work.work);assert(nrel==0);
    now+=3000;imu_poll(&id.poll_work.work);
    assert(nrel==0 && !id.motion.bias_valid); /* no unsafe uncalibrated fallback */
    id.motion.bias_valid=true;id.motion.calibrating=false;
    now+=20;imu_poll(&id.poll_work.work); /* anchor, not a backlog */
    now+=20;imu_poll(&id.poll_work.work);
    now+=20;imu_poll(&id.poll_work.work);
    bool saw_left=false,saw_down=false;
    for(int i=0;i<nrel;i++){
        if(rel_code[i]==INPUT_REL_X&&rel_value[i]<0)saw_left=true;
        if(rel_code[i]==INPUT_REL_Y&&rel_value[i]>0)saw_down=true;
    }
    assert(saw_left&&saw_down); /* fractional axes need not emit on one poll */
    /* Full rate/filter/calibration invariants have their own portable suite. */
    imu_transition_tests();
    ordinary_failure_tests();
    interrupted_contact_tests();
    pair_tap_geometry_tests();
    puts("PASS: actual touch gestures/four poses, cancellation, actual IMU stability/error recovery/vectors and USR1 edge toggles");
}
'''
with tempfile.TemporaryDirectory(prefix="xiao147-input-") as directory:
    c=Path(directory)/"test.c"
    c.write_text(prelude+listener_types+hid_mocks+listener_functions+hid_route+
                 source+imu_source+toggle+sleep_query+callback+test)
    binary=Path(directory)/"test"
    subprocess.run(["cc","-std=gnu11","-Wall","-Wextra","-Werror",
                    "-Wno-unused-parameter","-fsanitize=address,undefined",
                    "-I",str(root/"src"),str(c),"-o",str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
    # Mutation proof: the new geometry scenarios must actually catch the old
    # centroid-only policy, not just compile/run without exercising the guard.
    original = c.read_text()
    call = "guard_pair_tap(data, fingers, p, q);"
    assert original.count(call) == 1
    c.write_text(original.replace(call, "if (false) { " + call + " }"))
    subprocess.run(["cc","-std=gnu11","-Wall","-Wextra","-Werror",
                    "-Wno-unused-parameter","-fsanitize=address,undefined",
                    "-I",str(root/"src"),str(c),"-o",str(binary)],check=True)
    mutation = subprocess.run([str(binary)],capture_output=True,text=True)
    assert mutation.returncode != 0 and "nkey==(click?2:0)" in mutation.stderr, mutation.stderr
    print("PASS: removing native pair guard reproduces the phantom-right-click regression")

# Exercise the real HID observation wrapper independently of the input queue.
# Fake transports return success/failure but never replace the implementation
# under test; every pointer/wheel/button field must pass through unchanged.
trace = re.sub(r"^#include .*$", "", (root / "src/xiao147_hid_trace.c").read_text(), flags=re.M)
trace_prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define IS_ENABLED(x) (x)
#define CONFIG_ZMK_USB 1
#define CONFIG_ZMK_BLE 1
#define LOG_MODULE_REGISTER(...)
typedef int atomic_t;
typedef int atomic_val_t;
static int atomic_get(int *p){return *p;}
static int atomic_inc(int *p){return (*p)++;}
static bool atomic_cas(int *p,int old,int next){if(*p!=old)return false;*p=next;return true;}
static uint32_t now;
static uint32_t k_uptime_get_32(void){return now;}
enum zmk_transport {ZMK_TRANSPORT_NONE,ZMK_TRANSPORT_USB,ZMK_TRANSPORT_BLE};
struct zmk_endpoint_instance {enum zmk_transport transport;struct {int profile_index;} ble;};
static struct zmk_endpoint_instance selected;
static bool usb_ready,ble_connected;
static struct zmk_endpoint_instance zmk_endpoint_get_selected(void){return selected;}
static bool zmk_usb_is_hid_ready(void){return usb_ready;}
static bool zmk_ble_profile_is_connected(int profile){assert(profile==selected.ble.profile_index);return ble_connected;}
static int zmk_endpoint_instance_to_index(struct zmk_endpoint_instance ep){return ep.transport==ZMK_TRANSPORT_USB?0:1+ep.ble.profile_index;}
struct zmk_hid_mouse_report_body {uint8_t buttons;int16_t d_x,d_y,d_scroll_y,d_scroll_x;};
struct zmk_hid_mouse_report {uint8_t report_id;struct zmk_hid_mouse_report_body body;};
static struct zmk_hid_mouse_report current,forwarded;
static const struct zmk_hid_mouse_report *zmk_hid_get_mouse_report(void){return &current;}
static unsigned sends,logs;
static int send_result;
static char message[384];
static void host_log(const char *format,...){
 va_list args;va_start(args,format);vsnprintf(message,sizeof(message),format,args);va_end(args);logs++;
}
#define LOG_INF(...) host_log(__VA_ARGS__)
int __real_zmk_endpoint_send_mouse_report(void){sends++;forwarded=current;return send_result;}
'''
trace_test = r'''
int main(void){
 selected.transport=ZMK_TRANSPORT_NONE;assert(xiao147_pointer_endpoint()==-1);
 selected.transport=ZMK_TRANSPORT_USB;assert(xiao147_pointer_endpoint()==-1);
 usb_ready=true;assert(xiao147_pointer_endpoint()==0);
 selected.transport=ZMK_TRANSPORT_BLE;selected.ble.profile_index=4;
 assert(xiao147_pointer_endpoint()==-1);ble_connected=true;assert(xiao147_pointer_endpoint()==5);
 selected.transport=ZMK_TRANSPORT_USB;
 current.report_id=4;current.body=(struct zmk_hid_mouse_report_body){.d_x=37,.d_y=-19};
 now=2000;assert(__wrap_zmk_endpoint_send_mouse_report()==0&&sends==1&&logs==1);
 assert(!memcmp(&current,&forwarded,sizeof(current)));
 assert(strstr(message,"endpoint 0 pointer 37 -19 wheel 0 0 buttons 0x0, send result 0"));
 /* Observation is rate limited, forwarding never is. */
 current.body.d_x=-2;now=2100;assert(__wrap_zmk_endpoint_send_mouse_report()==0);
 assert(sends==2&&logs==1&&!memcmp(&current,&forwarded,sizeof(current)));
 /* Snapshot remains correct even when the underlying transport fails. */
 selected.transport=ZMK_TRANSPORT_BLE;selected.ble.profile_index=2;
 current.body=(struct zmk_hid_mouse_report_body){.buttons=2,.d_x=-300,.d_y=400,.d_scroll_x=3,.d_scroll_y=-4};
 send_result=-EIO;now=3200;assert(__wrap_zmk_endpoint_send_mouse_report()==-EIO);
 assert(sends==3&&logs==2&&!memcmp(&current,&forwarded,sizeof(current)));
 assert(strstr(message,"endpoint 3 pointer -300 400 wheel 3 -4 buttons 0x2, send result -5"));
 assert(strstr(message,"reports 3 failures 1"));
 /* An empty failed send still appears; an empty successful send still forwards. */
 memset(&current.body,0,sizeof(current.body));now=4400;
 assert(__wrap_zmk_endpoint_send_mouse_report()==-EIO&&sends==4&&logs==3);
 send_result=0;now=5600;assert(__wrap_zmk_endpoint_send_mouse_report()==0&&sends==5&&logs==3);
 assert(!memcmp(&current,&forwarded,sizeof(current)));
 puts("PASS: real HID wrapper observes pointer/wheels/button/transport result, forwards every field unchanged, and gates disconnected USB/BLE endpoints");
}
'''
with tempfile.TemporaryDirectory(prefix="xiao147-hid-trace-") as directory:
    c = Path(directory)/"trace.c"
    c.write_text(trace_prelude + trace + trace_test)
    binary = Path(directory)/"trace"
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", str(c), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
