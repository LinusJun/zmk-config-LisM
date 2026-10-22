"""Actual ordinary AXS driver, plus optional legacy UART/RAW debug fixture.

--ordinary-only runs RAW_MODE=0 without AUTO_TOUCH: ordinary single-tap and
UART-debug exclusion only. Production AUTO_TOUCH is covered separately by
xiao147_auto_touch_host_test, which imports this file's prelude through AST.
The full legacy debug fixture needs its separate raw_control support files.
The actual C engine is linked, not replaced by a mock. Only hardware/Zephyr
boundaries are mocked. These tests do not establish physical multi-touch.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import sys

root = Path(__file__).resolve().parents[1]
driver = re.sub(r"^#include .*$", "", (root / "src/axs5106l_touchpad.c").read_text(), flags=re.M)
# This driver test models the pointer sink; its production ownership coordinator
# is executed independently by xiao147_pointer_owned_test.
driver = driver.replace('#define input_report_key xiao147_touch_key','').replace('#define input_report_rel xiao147_touch_rel','')

prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include "xiao147_orientation.h"
#if RAW_MODE
#define CONFIG_LISM_XIAO147_RAW_CAPTURE 1
#define CONFIG_I2C_NRFX_TRANSFER_TIMEOUT 100
#include "xiao147_raw_capture.h"
#include "xiao147_raw_control.h"
#endif

#define LOG_MODULE_REGISTER(...)
static void host_log(const char *format, ...) {(void)format;}
#define LOG_INF(...) host_log(__VA_ARGS__)
#define LOG_WRN(...) host_log(__VA_ARGS__)
#define LOG_ERR(...) host_log(__VA_ARGS__)
#define LOG_DBG(...) host_log(__VA_ARGS__)
#define LOG_HEXDUMP_INF(...) ((void)0)
#define BUILD_ASSERT(condition,message) _Static_assert(condition,message)
#define INPUT_BTN_0 256
#define INPUT_BTN_1 257
#define INPUT_REL_X 0
#define INPUT_REL_Y 1
#define INPUT_REL_HWHEEL 6
#define INPUT_REL_WHEEL 8
#define XIAO147_TOUCH_CANCEL UINT8_MAX
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
#define DT_INST_FOREACH_STATUS_OKAY(x)
#define CONTAINER_OF(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
enum xiao147_page {XIAO147_PAGE_STATUS,XIAO147_PAGE_DEVICES};
struct k_work {int unused;};
struct k_work_delayable {struct k_work work;};
struct i2c_dt_spec {int unused;};
struct device {void *data;const void *config;};
static uint32_t now;
static enum xiao147_page page;
static enum xiao147_orientation pose;
static uint8_t sensor[32];
static uint8_t selected_register;
static unsigned io_phase, register_selects, ordinary_reads, normal_validation_reads;
static unsigned hid_events, key_events, menu_samples, menu_cancels;
static int last_key;
static struct k_work_delayable *k_work_delayable_from_work(struct k_work *w) {return (void *)w;}
static void k_work_init_delayable(struct k_work_delayable *w,void(*f)(struct k_work *)) {(void)w;(void)f;}
static void k_work_schedule(struct k_work_delayable *w,int delay) {(void)w;assert(delay==1000);}
static void k_work_reschedule(struct k_work_delayable *w,int delay) {(void)w;assert(delay==16);}
static uint32_t k_uptime_get_32(void) {return now;}
static bool i2c_is_ready_dt(const struct i2c_dt_spec *bus) {(void)bus;return true;}
static int i2c_write_dt(const struct i2c_dt_spec *bus,const void *bytes,size_t length);
static int i2c_read_dt(const struct i2c_dt_spec *bus,void *bytes,size_t length);
static void k_busy_wait(uint32_t us) {assert(us==100&&io_phase==1);io_phase=2;}
static bool input_must_be_blocked(void);
static int input_report_key(const struct device *dev,int code,int value,bool sync,int wait) {
    (void)dev;(void)value;assert(sync&&wait==K_NO_WAIT&&!input_must_be_blocked());
    hid_events++;key_events++;last_key=code;return 0;
}
static int input_report_rel(const struct device *dev,int code,int value,bool sync,int wait) {
    (void)dev;(void)code;(void)value;(void)sync;
    assert(wait==K_NO_WAIT&&!input_must_be_blocked());hid_events++;return 0;
}
static enum xiao147_page xiao147_ui_page(void) {return page;}
static enum xiao147_orientation xiao147_rotation_get(void) {return pose;}
static bool xiao147_ui_wake_on_touch(void) {return false;}
#if RAW_MODE
static bool screen_sleeping;
static bool xiao147_ui_is_sleeping(void) {return screen_sleeping;}
#endif
static void xiao147_ui_touch(uint8_t fingers,uint16_t x,uint16_t y,enum xiao147_orientation o) {
    (void)x;(void)y;assert(fingers==XIAO147_TOUCH_CANCEL||o==pose);
    if(fingers==XIAO147_TOUCH_CANCEL){menu_cancels++;}
    else {assert(!input_must_be_blocked());menu_samples++;}
}

typedef int atomic_t;
static int atomic_get(atomic_t *value) {return *value;}
static int atomic_set(atomic_t *value,int next) {int old=*value;*value=next;return old;}
#if RAW_MODE
static struct device host_console;
#define DT_CHOSEN(x) 0
#define DEVICE_DT_GET(x) (&host_console)
#define UART_LINE_CTRL_DTR 1
static bool dtr=true, console_ready=true;
static int uart_error;
static uint8_t uart_bytes[4096];
static size_t uart_used, uart_cursor;
static bool factory_page, block_restore, wrong_id;
static uint16_t firmware=0x0101;
static unsigned factory_writes, page_enters, page_restores, type_writes, scan_writes, samples_read;
static uint8_t selected_type;
static bool device_is_ready(const struct device *dev) {assert(dev==&host_console);return console_ready;}
static int uart_line_ctrl_get(const struct device *dev,unsigned control,uint32_t *value) {
    assert(dev==&host_console&&control==UART_LINE_CTRL_DTR);*value=dtr;return 0;
}
static int uart_poll_in(const struct device *dev,uint8_t *byte) {
    assert(dev==&host_console);if(uart_error)return uart_error;
    if(uart_cursor==uart_used)return -1;
    *byte=uart_bytes[uart_cursor++];return 0;
}
#endif
'''

hardware = r'''
static struct axs_touch_data state;
static const struct axs_touch_config config={0};
static struct device touch_device={&state,&config};
static bool input_must_be_blocked(void) {
#if RAW_MODE
    return xiao147_raw_capture_active(&state.capture)||factory_page;
#else
    return false;
#endif
}
static int i2c_write_dt(const struct i2c_dt_spec *bus,const void *bytes,size_t length) {
    assert(bus==&config.bus);const uint8_t *data=bytes;
    if(length==1){selected_register=data[0];io_phase=1;register_selects++;return 0;}
#if RAW_MODE
    /* Transport-level allowlist catches any escape from the actual wrapper. */
    assert(xiao147_raw_capture_active(&state.capture));
    assert((length==4&&data[0]==0xfe&&data[1]==0x5a&&data[2]==0xa5&&
           (data[3]==0x00||data[3]==0x20))||
           (length==2&&data[0]==0x00&&(data[1]==1||data[1]==2))||
           (length==2&&data[0]==0x03&&data[1]==1));
    factory_writes++;io_phase=0;
    if(data[0]==0xfe){
        if(data[3]==0x20){assert(!factory_page);factory_page=true;page_enters++;}
        else {page_restores++;if(block_restore)return -EIO;factory_page=false;}
    } else {
        assert(factory_page);
        if(data[0]==0x00){selected_type=data[1];type_writes++;}
        else {scan_writes++;}
    }
    return 0;
#else
    assert(false);return -EINVAL;
#endif
}
static int i2c_read_dt(const struct i2c_dt_spec *bus,void *bytes,size_t length) {
    assert(bus==&config.bus&&io_phase==2);io_phase=0;uint8_t *out=bytes;
    if(selected_register==0x08){assert(length==3);out[0]=0x51;out[1]=0x06;out[2]=0x01;
#if RAW_MODE
        if(wrong_id)out[2]=0x02;
#endif
        return 0;
    }
#if RAW_MODE
    if(selected_register==0x05){assert(!factory_page&&length==2);out[0]=firmware>>8;out[1]=firmware;return 0;}
    if(selected_register==0xfe){assert(length==2);out[0]=0;out[1]=factory_page?0x20:0;return 0;}
    if(selected_register==0x0b){assert(factory_page&&length==3);out[0]=7;out[1]=6;out[2]=0xf0;return 0;}
    if(selected_register==0x03){assert(factory_page&&length==1);out[0]=0;return 0;}
    if(selected_register==0x02){
        assert(factory_page&&length==26&&(selected_type==1||selected_type==2));
        for(size_t i=0;i<length;i+=2){out[i]=selected_type==1?0x12:0xff;out[i+1]=selected_type==1?0x34:0xff;}
        samples_read++;return 0;
    }
#endif
    assert(selected_register==0x01);
    assert(length==32||length==14);
#if RAW_MODE
    /* Restoration validation may read 14 while engine is still gated. */
    if(xiao147_raw_capture_active(&state.capture)){
        assert(!factory_page&&length==14&&state.capture.phase==XIAO147_RAW_RESTORE_REPORT);
        normal_validation_reads++;
    } else {assert(!factory_page);ordinary_reads++;}
#else
    ordinary_reads++;
#endif
    memcpy(out,sensor,length);return 0;
}
static void poll(void) {now+=16;axs_poll(&state.poll_work.work);}
static void contact(unsigned fingers,int x,int y) {
    memset(sensor,0xff,sizeof(sensor));sensor[0]=0;sensor[1]=fingers;
    sensor[2]=(uint8_t)(x>>8);sensor[3]=(uint8_t)x;
    sensor[4]=(uint8_t)(y>>8);sensor[5]=(uint8_t)y;sensor[6]=0x40;sensor[7]=0x10;
}
static void fixture(void) {
    memset(&state,0,sizeof(state));now=2000;page=XIAO147_PAGE_STATUS;pose=XIAO147_USB_LEFT;
    io_phase=register_selects=ordinary_reads=normal_validation_reads=0;
    hid_events=key_events=menu_samples=menu_cancels=0;last_key=-1;contact(0,40,70);
#if RAW_MODE
    screen_sleeping=false;
    atomic_set(&capture_gate,0);dtr=console_ready=true;uart_error=0;
    uart_used=uart_cursor=0;factory_page=block_restore=wrong_id=false;firmware=0x0101;
    factory_writes=page_enters=page_restores=type_writes=scan_writes=samples_read=0;selected_type=0;
#endif
    assert(axs_touch_init(&touch_device)==0);
    assert(!register_selects&&!ordinary_reads&&!hid_events);
#if RAW_MODE
    assert(!factory_writes&&!xiao147_touch_capture_active());
#endif
}
'''

test = r'''
#if RAW_MODE
static void feed_bytes(const uint8_t *bytes,size_t length) {
    if(uart_cursor==uart_used){uart_cursor=uart_used=0;}
    assert(uart_used+length<=sizeof(uart_bytes));memcpy(uart_bytes+uart_used,bytes,length);uart_used+=length;
}
static void feed(const char *line) {feed_bytes((const uint8_t *)line,strlen(line));}
static void drain(void) {
    for(unsigned i=0;uart_cursor<uart_used&&i<200;i++){poll();}
    assert(uart_cursor==uart_used);
}
static void info(void) {
    feed("AXS_RAW_INFO\r\n");drain();
    for(unsigned i=0;state.info_stage&&i<10;i++){poll();}
    assert(!state.info_stage&&state.info_valid&&state.info_fw==0x0101&&!factory_writes);
}
static void start(void) {
    feed("AXS_RAW_START 0101\n");drain();
    assert(xiao147_raw_capture_active(&state.capture)&&xiao147_touch_capture_active());
}
static void until_phase(enum xiao147_raw_phase phase,unsigned limit) {
    for(unsigned i=0;state.capture.phase!=phase&&i<limit;i++){poll();}
    assert(state.capture.phase==phase);
}
static void finish_restore(void) {
    for(unsigned i=0;xiao147_raw_capture_active(&state.capture)&&i<150;i++){poll();}
    assert(!xiao147_raw_capture_active(&state.capture)&&!factory_page&&!xiao147_touch_capture_active());
    assert(state.swallow_until_release);
}
static enum xiao147_raw_command parse(struct xiao147_raw_control *control,const char *text) {
    enum xiao147_raw_command command=XIAO147_RAW_COMMAND_NONE;
    while(*text){command=xiao147_raw_control_byte(control,(uint8_t)*text++);}
    return command;
}
static void parser_tests(void) {
    struct xiao147_raw_control c={0};
    assert(parse(&c,"AXS_RAW_START 1a2B\r\n")==XIAO147_RAW_COMMAND_START&&c.expected_fw==0x1a2b);
    assert(parse(&c,"AXS_RAW_START 0000\n")==XIAO147_RAW_COMMAND_START&&c.expected_fw==0);
    const char *invalid[]={" AXS_RAW_START 0101\n","AXS_RAW_START 101\n",
        "AXS_RAW_START 01010\n","AXS_RAW_START 0g01\n","axs_raw_start 0101\n",
        "AXS_RAW_START 0101 extra\n","AXS_RAW_START\n","AXS_RAW_STOP extra\n"};
    for(size_t i=0;i<sizeof(invalid)/sizeof(invalid[0]);i++){
        assert(parse(&c,invalid[i])==XIAO147_RAW_COMMAND_INVALID);
    }
    assert(parse(&c,"\r\n")==XIAO147_RAW_COMMAND_NONE);
    assert(parse(&c,"AXS_RAW_ST")==XIAO147_RAW_COMMAND_NONE);
    assert(parse(&c,"OP\r")==XIAO147_RAW_COMMAND_NONE);
    assert(parse(&c,"\n")==XIAO147_RAW_COMMAND_STOP);
}
static void command_tests(void) {
    fixture();poll();unsigned before=ordinary_reads;
    feed("AXS_RAW_START 0101\n");drain();assert(!factory_writes&&!xiao147_touch_capture_active());
    assert(ordinary_reads>before);
    feed("AXS_RAW_I");drain();assert(!state.info_stage&&!state.info_valid);
    feed("NFO\r");drain();assert(!state.info_stage);
    feed("\n");drain();assert(state.info_stage==2&&!state.info_valid);
    poll();assert(state.info_valid&&state.info_fw==0x0101&&!factory_writes);
    feed("AXS_RAW_START 0102\n");drain();assert(!factory_writes&&!xiao147_touch_capture_active());
    uint8_t oversized[100];memset(oversized,'A',sizeof(oversized));feed_bytes(oversized,sizeof(oversized));
    feed("AXS_RAW_START 0101\n");drain();assert(!factory_writes&&!xiao147_touch_capture_active());
    const uint8_t nul_command[]="AXS_RAW_START\0 0101\n";
    feed_bytes(nul_command,sizeof(nul_command)-1);drain();assert(!factory_writes);
    uint8_t bad=3;
    assert(axs_raw_write(&state,0x05,&bad,1)==-EINVAL);
    assert(axs_raw_write(&state,0xff,&bad,1)==-EINVAL);
    assert(axs_raw_write(&state,0x19,&bad,1)==-EINVAL);
    assert(axs_raw_write(&state,0x00,&bad,1)==-EINVAL);
    assert(axs_raw_write(&state,0x03,&bad,1)==-EINVAL);
    uint8_t bytes[3]={0x5a,0xa5,0x21};
    assert(axs_raw_write(&state,0xfe,bytes,3)==-EINVAL&&!factory_writes);
    assert(axs_raw_read(&state,0x01,bytes,3)==-EINVAL);
    start();uint32_t started=state.capture.started_ms;
    feed("AXS_RAW_START 0101\n");drain();assert(state.capture.started_ms==started);
    until_phase(XIAO147_RAW_PAIR_WAIT,40);
    assert(page_enters==1&&samples_read==2&&type_writes==2&&scan_writes==2&&!hid_events);
    unsigned regular=ordinary_reads;feed("AXS_RAW_STOP\r");drain();
    assert(factory_page&&xiao147_touch_capture_active());
    feed("\n");drain();finish_restore();
    assert(ordinary_reads==regular&&normal_validation_reads==1&&page_restores==1);
    feed("AXS_RAW_START 0101\n");drain();assert(!xiao147_touch_capture_active()&&page_enters==1);
}
static void verify_identity_before_write(void) {
    fixture();wrong_id=true;feed("AXS_RAW_INFO\n");drain();poll();assert(!state.info_valid);
    feed("AXS_RAW_START 0101\n");drain();assert(!factory_writes&&!xiao147_touch_capture_active());
    fixture();info();wrong_id=true;feed("AXS_RAW_START 0101\n");drain();
    assert(!factory_writes&&!xiao147_touch_capture_active()&&state.capture.last_error==XIAO147_RAW_ID_MISMATCH);
    fixture();info();firmware=0x0102;start();poll();
    assert(!factory_writes&&!xiao147_touch_capture_active()&&state.capture.last_error==XIAO147_RAW_VERSION_MISMATCH);
    feed("AXS_RAW_START 0101\n");drain();assert(!factory_writes);
}
static void input_ownership(void) {
    fixture();page=XIAO147_PAGE_DEVICES;poll();contact(1,40,70);poll();
    assert(state.fingers==1&&menu_samples==1);
    info();assert(menu_cancels==1&&!state.fingers&&state.swallow_until_release);
    page=XIAO147_PAGE_STATUS;start();until_phase(XIAO147_RAW_PAIR_WAIT,40);
    assert(!hid_events&&menu_cancels==1);unsigned reads=ordinary_reads;
    contact(1,80,120);feed("AXS_RAW_STOP\n");drain();finish_restore();
    assert(!hid_events&&ordinary_reads==reads);
    for(int i=0;i<8;i++){contact(1,80+i,120+i);poll();}
    assert(!hid_events&&state.swallow_until_release);
    contact(0,80,120);poll();assert(!state.swallow_until_release&&!hid_events);
    contact(1,40,70);poll();contact(0,40,70);poll();
    assert(key_events==2&&last_key==INPUT_BTN_0);
}
static void disconnected_cleanup(void) {
    fixture();info();start();until_phase(XIAO147_RAW_PAIR_WAIT,40);
    unsigned reads=ordinary_reads;
    feed("AXS_RAW_START 01");drain();assert(state.control.length!=0);
    dtr=false;poll();assert(!state.control.length&&!state.info_valid);
    finish_restore();assert(ordinary_reads==reads&&page_restores==1&&!hid_events);
    dtr=true;feed("01\nAXS_RAW_START 0101\n");drain();assert(page_enters==1&&!xiao147_touch_capture_active());
    fixture();info();start();until_phase(XIAO147_RAW_PAIR_WAIT,40);
    uart_error=-EIO;poll();uart_error=0;finish_restore();assert(!hid_events);
}
static void sticky_fault(void) {
    fixture();info();start();until_phase(XIAO147_RAW_PAIR_WAIT,40);
    unsigned reads=ordinary_reads;block_restore=true;feed("AXS_RAW_STOP\n");drain();
    until_phase(XIAO147_RAW_STICKY_FAULT,150);
    assert(xiao147_touch_capture_active()&&factory_page&&page_restores==3&&!hid_events);
    unsigned writes=factory_writes;
    for(int i=0;i<200;i++){contact(1,40+i%50,70);poll();}
    feed("AXS_RAW_STOP\nAXS_RAW_INFO\nAXS_RAW_START 0101\n");drain();
    dtr=false;poll();dtr=true;poll();
    assert(state.capture.phase==XIAO147_RAW_STICKY_FAULT&&xiao147_touch_capture_active());
    assert(factory_writes==writes&&ordinary_reads==reads&&!normal_validation_reads&&!hid_events);
}
#endif
int main(void) {
    fixture();poll();assert(ordinary_reads==1&&!hid_events);
    contact(1,40,70);poll();contact(0,40,70);poll();assert(key_events==2&&last_key==INPUT_BTN_0);
#if RAW_MODE
    assert(!factory_writes);parser_tests();command_tests();verify_identity_before_write();
    input_ownership();disconnected_cleanup();sticky_fault();
    puts("PASS: actual RAW UART/driver/engine, explicit INFO/version gates, whitelist, sole ownership, held-touch recovery, disconnect and sticky fault");
#else
    puts("PASS: default actual AXS driver has normal single tap, no factory writes or UART paths");
#endif
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="xiao147-raw-integration-") as directory:
    c = Path(directory) / "test.c"
    c.write_text(prelude + driver + hardware + test)
    default_preprocessed = subprocess.run(
        ["cc", "-std=c11", "-DRAW_MODE=0", "-E", "-P", "-I", str(root / "src"), str(c)],
        check=True, capture_output=True, text=True,
    ).stdout
    for forbidden in ("axs_raw_write", "axs_raw_poll", "capture_console", "uart_poll_in", "AXS_RAW_READY"):
        assert forbidden not in default_preprocessed, f"factory/UART path present in default: {forbidden}"
    for enabled in ((0,) if '--ordinary-only' in sys.argv else (0, 1)):
        binary = Path(directory) / f"test-{enabled}"
        sources = [str(c)]
        if enabled:
            sources.append(str(root / "src/xiao147_raw_capture.c"))
        subprocess.run(
            ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
             f"-DRAW_MODE={enabled}", "-I", str(root / "src"), *sources, "-o", str(binary)],
            check=True,
        )
        subprocess.run([str(binary)], check=True)
    if '--ordinary-only' in sys.argv:
        print('SCOPE: ordinary AXS branch only (AUTO_TOUCH disabled in this fixture); UART_RAW debug branch not executed; production AUTO covered by xiao147_auto_touch_host_test')
