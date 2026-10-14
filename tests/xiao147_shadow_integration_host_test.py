"""Actual AXS UART/driver + FAST DIFF engine + shadow morphology integration.

Only Zephyr and physical I/O boundaries are mocked. Default shadow morphology
must remain incapable of HID, even when it produces a candidate tap/scroll;
the separate opt-in functional experiment is disabled in all cases here.
These synthetic tests establish safety boundaries, NOT real multi-touch.
"""
import ast
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = re.sub(r"^#include .*$", "", (root / "src/axs5106l_touchpad.c").read_text(), flags=re.M)

# Reuse the real-driver boundary mocks without importing/executing the other
# test's compiler or silently accepting source drift in the targeted hooks.
tree = ast.parse((root / "tests/xiao147_raw_integration_host_test.py").read_text())
parts = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        if node.targets[0].id in {"prelude", "hardware", "test"}:
            parts[node.targets[0].id] = ast.literal_eval(node.value)
assert set(parts) == {"prelude", "hardware", "test"}


def replace_once(text, old, new):
    assert text.count(old) == 1, f"integration mock hook changed: {old[:100]}"
    return text.replace(old, new, 1)


prelude = replace_once(parts["prelude"], '#include <errno.h>', '#include <errno.h>\n#include <stdarg.h>')
prelude = replace_once(prelude, '#include "xiao147_raw_control.h"', '''#include "xiao147_raw_control.h"
#if SHADOW_MODE
#define CONFIG_LISM_XIAO147_GESTURE_SHADOW 1
#include "xiao147_raw_gestures.h"
#endif''')
prelude = replace_once(prelude, 'static void host_log(const char *format, ...) {(void)format;}', r'''
static char latest_shadow[512];
static unsigned shadow_logs, armed_logs, rate_logs, rejected_logs;
static void host_log(const char *format, ...) {
    if(strstr(format,"AXS_SHADOW t=")){
        va_list args;va_start(args,format);
        int written=vsnprintf(latest_shadow,sizeof(latest_shadow),format,args);va_end(args);
        assert(written>0&&(size_t)written<sizeof(latest_shadow));shadow_logs++;
        assert(strstr(latest_shadow,"HID=0"));
    }
    if(strstr(format,"AXS_SHADOW_ARMED"))armed_logs++;
    if(strstr(format,"AXS_SHADOW_RATE"))rate_logs++;
    if(strstr(format,"command rejected"))rejected_logs++;
}''')
prelude = replace_once(prelude, 'static enum xiao147_orientation pose;', '''static enum xiao147_orientation pose;
static unsigned scheduled_ms=16;
#if SHADOW_MODE
static bool imu_enabled;
static bool xiao147_ui_imu_enabled(void) {return imu_enabled;}
#endif''')
prelude = replace_once(prelude,
    'static void k_work_reschedule(struct k_work_delayable *w,int delay) {(void)w;assert(delay==16);}',
    '''static void host_reschedule_check(unsigned delay);
static void k_work_reschedule(struct k_work_delayable *w,int delay) {
    (void)w;assert(delay==4||delay==16);scheduled_ms=(unsigned)delay;
    host_reschedule_check(scheduled_ms);
}''')
prelude = replace_once(prelude, 'static uint8_t selected_type;', '''static uint8_t selected_type;
#if SHADOW_MODE
static uint8_t channel_layout[3];
static int16_t diff_values[13];
static bool fail_sample;
static uint32_t last_scan_ms;
#endif''')

hardware = replace_once(parts["hardware"],
    'static struct device touch_device={&state,&config};', '''static struct device touch_device={&state,&config};
static void host_reschedule_check(unsigned delay) {
#if SHADOW_MODE
    bool fast=state.shadow_active&&xiao147_raw_capture_active(&state.capture)&&
        state.capture.mode==XIAO147_RAW_MODE_FAST_DIFF&&
        state.capture.phase<XIAO147_RAW_RESTORE_WRITE;
    assert(delay==(fast?4U:16U));
#else
    assert(delay==16);
#endif
}''')
hardware = replace_once(hardware, 'else {scan_writes++;}', '''else {
#if SHADOW_MODE
            if(scan_writes){assert(now-last_scan_ms>=20);}
            last_scan_ms=now;
#endif
            scan_writes++;
        }''')
hardware = replace_once(hardware,
    'if(selected_register==0x0b){assert(factory_page&&length==3);out[0]=7;out[1]=6;out[2]=0xf0;return 0;}',
    '''if(selected_register==0x0b){assert(factory_page&&length==3);
#if SHADOW_MODE
        memcpy(out,channel_layout,3);
#else
        out[0]=7;out[1]=6;out[2]=0xf0;
#endif
        return 0;
    }''')
hardware = replace_once(hardware,
    'if(selected_register==0x03){assert(factory_page&&length==1);out[0]=0;return 0;}',
    '''if(selected_register==0x03){assert(factory_page&&length==1);
#if SHADOW_MODE
        assert(now-last_scan_ms>=5);
#endif
        out[0]=0;return 0;
    }''')
hardware = replace_once(hardware, '''assert(factory_page&&length==26&&(selected_type==1||selected_type==2));
        for(size_t i=0;i<length;i+=2){out[i]=selected_type==1?0x12:0xff;out[i+1]=selected_type==1?0x34:0xff;}''',
    '''assert(factory_page&&(selected_type==1||selected_type==2));
#if SHADOW_MODE
        assert(length==(size_t)state.capture.channels*2&&length<=26);
        assert(selected_type==XIAO147_RAW_DIFF&&type_writes==1);
        if(fail_sample){fail_sample=false;return -EIO;}
        for(size_t i=0;i<length;i+=2){
            uint16_t word=(uint16_t)diff_values[i/2];out[i]=(uint8_t)(word>>8);out[i+1]=(uint8_t)word;
        }
#else
        assert(length==26);
        for(size_t i=0;i<length;i+=2){out[i]=selected_type==1?0x12:0xff;out[i+1]=selected_type==1?0x34:0xff;}
#endif''')
hardware = replace_once(hardware, 'static void poll(void) {now+=16;axs_poll(&state.poll_work.work);}',
    '''static void poll(void) {
    now+=scheduled_ms;
#if RAW_MODE
    uint32_t calls=state.capture.io_calls;
#endif
    axs_poll(&state.poll_work.work);
#if RAW_MODE
    /* One synchronous, bounded engine I/O operation at most per work tick. */
    assert(state.capture.io_calls>=calls&&state.capture.io_calls-calls<=1);
#endif
}''')
hardware = replace_once(hardware,
    'memset(&state,0,sizeof(state));now=2000;page=XIAO147_PAGE_STATUS;pose=XIAO147_USB_LEFT;',
    '''memset(&state,0,sizeof(state));now=2000;page=XIAO147_PAGE_STATUS;pose=XIAO147_USB_LEFT;
    scheduled_ms=16;shadow_logs=armed_logs=rate_logs=rejected_logs=0;latest_shadow[0]=0;
#if SHADOW_MODE
    imu_enabled=false;channel_layout[0]=6;channel_layout[1]=2;channel_layout[2]=0;
    memset(diff_values,0,sizeof(diff_values));fail_sample=false;last_scan_ms=0;
#endif''')

shadow_test = r'''
#if SHADOW_MODE
static void feed_bytes(const uint8_t *bytes,size_t length) {
    if(uart_cursor==uart_used){uart_cursor=uart_used=0;}
    assert(uart_used+length<=sizeof(uart_bytes));memcpy(uart_bytes+uart_used,bytes,length);uart_used+=length;
}
static void feed(const char *text) {feed_bytes((const uint8_t *)text,strlen(text));}
static void drain(void) {
    for(unsigned i=0;uart_cursor<uart_used&&i<200;i++)poll();
    assert(uart_cursor==uart_used);
}
static void hands_off(void) {
    contact(0,40,70);
    for(unsigned i=0;i<10;i++)poll();
    assert(state.normal_zero&&now-state.normal_zero_since_ms>=100);
}
static void info(void) {
    unsigned writes=factory_writes;
    feed("AXS_RAW_INFO\r\n");drain();
    for(unsigned i=0;state.info_stage&&i<10;i++)poll();
    assert(state.info_valid&&!state.info_stage&&state.info_fw==0x0101&&factory_writes==writes);
}
static void start(void) {
    feed("AXS_SHADOW_START 0101\n");drain();
    assert(state.shadow_active&&xiao147_touch_capture_active());
    assert(state.capture.mode==XIAO147_RAW_MODE_FAST_DIFF&&scheduled_ms==4);
}
static void prepare(void) {fixture();hands_off();info();start();}
static void until_phase(enum xiao147_raw_phase target,unsigned limit) {
    for(unsigned i=0;state.capture.phase!=target&&i<limit;i++)poll();
    assert(state.capture.phase==target);
}
static void samples(unsigned count) {
    uint32_t target=state.capture.samples_received+count;
    unsigned normal=ordinary_reads, hid=hid_events;
    for(unsigned i=0;state.capture.samples_received<target&&i<1000;i++)poll();
    assert(state.capture.samples_received==target&&samples_read==target);
    assert(ordinary_reads==normal&&hid_events==hid&&scheduled_ms==4);
}
static void quiet(void) {memset(diff_values,0,sizeof(diff_values));}
static void two_stations(unsigned a,unsigned b,int ae,int ao,int be,int bo) {
    assert(a<b&&b<6);quiet();
    diff_values[2*a]=(int16_t)ae;diff_values[2*a+1]=(int16_t)ao;
    diff_values[2*b]=(int16_t)be;diff_values[2*b+1]=(int16_t)bo;
}
static void two(int ae,int ao,int be,int bo) {two_stations(1,4,ae,ao,be,bo);}
static void arm(void) {
    quiet();
    for(unsigned i=0;!state.baseline.armed&&i<20;i++)samples(1);
    assert(state.baseline.armed&&armed_logs==1&&!hid_events);
    assert(state.baseline.last_ms-state.baseline.first_ms>=100&&state.baseline.quiet_frames>=3);
    for(unsigned i=0;i<12;i++)assert(state.baseline.values[i]==0);
    samples(2);assert(!state.shadow.blocked);
}
static void finish_restore(void) {
    unsigned normal=ordinary_reads,hid=hid_events;
    for(unsigned i=0;xiao147_raw_capture_active(&state.capture)&&i<150;i++)poll();
    assert(!xiao147_touch_capture_active()&&!factory_page&&!state.shadow_active);
    assert(ordinary_reads==normal&&hid_events==hid&&scheduled_ms==16);
    assert(normal_validation_reads==1&&state.swallow_until_release);
}
static void stop(void) {feed("AXS_RAW_STOP\r\n");drain();finish_restore();}
static void parser_and_gates(void) {
    fixture();hands_off();feed("AXS_SHADOW_START 0101\n");drain();
    assert(!factory_writes&&!xiao147_touch_capture_active());
    info();
    const char *invalid[]={"AXS_SHADOW_START 101\n","AXS_SHADOW_START 01010\n",
        "AXS_SHADOW_START 0g01\n","AXS_SHADOW_START 0101 extra\n",
        " AXS_SHADOW_START 0101\n","axs_shadow_start 0101\n"};
    for(size_t i=0;i<sizeof(invalid)/sizeof(invalid[0]);i++){
        feed(invalid[i]);drain();assert(!factory_writes&&!xiao147_touch_capture_active());
    }
    feed("AXS_SHADOW_START 0102\n");drain();assert(!factory_writes);
    uint8_t oversized[100];memset(oversized,'A',sizeof(oversized));feed_bytes(oversized,sizeof(oversized));
    feed("AXS_SHADOW_START 0101\n");drain();assert(!factory_writes);
    const uint8_t nul_command[]="AXS_SHADOW_START\0 0101\n";
    feed_bytes(nul_command,sizeof(nul_command)-1);drain();assert(!factory_writes);
    hands_off();info();feed("AXS_SHADOW_STA");drain();assert(!xiao147_touch_capture_active());
    feed("RT 0101\r");drain();assert(!xiao147_touch_capture_active());
    feed("\n");drain();assert(state.shadow_active&&scheduled_ms==4);
    uint32_t started=state.capture.started_ms;
    feed("AXS_SHADOW_START 0101\n");drain();assert(state.capture.started_ms==started);
    until_phase(XIAO147_RAW_SCAN_START,50);stop();
    feed("AXS_SHADOW_START 0101\n");drain();assert(page_enters==1&&!xiao147_touch_capture_active());

    fixture();poll();info();feed("AXS_SHADOW_START 0101\n");drain();
    assert(!factory_writes); /* 0 exists but has not been stable >=100 ms. */
    fixture();hands_off();info();contact(1,40,70);poll();
    feed("AXS_SHADOW_START 0101\n");drain();assert(!state.normal_zero&&!factory_writes);
    fixture();hands_off();info();now+=101;feed("AXS_SHADOW_START 0101\n");drain();
    assert(!factory_writes); /* Old zero is not a fresh lift observation. */
    fixture();hands_off();info();page=XIAO147_PAGE_SETTINGS;
    feed("AXS_SHADOW_START 0101\n");drain();assert(!factory_writes);
    fixture();hands_off();info();imu_enabled=true;
    feed("AXS_SHADOW_START 0101\n");drain();assert(!factory_writes);
    fixture();hands_off();info();wrong_id=true;
    feed("AXS_SHADOW_START 0101\n");drain();
    assert(!factory_writes&&!xiao147_touch_capture_active());
    fixture();hands_off();info();firmware=0x0102;start();poll();
    assert(!factory_writes&&!xiao147_touch_capture_active());
}
static void baseline_and_candidates(void) {
    prepare();two(280,280,280,280);samples(10);
    assert(!state.baseline.armed&&!armed_logs&&state.baseline.quiet_frames==0);
    assert(state.shadow.blocked&&!state.shadow_taps&&!state.shadow_scrolls&&!hid_events);
    for(unsigned i=0;i<12;i++)assert(state.baseline.values[i]==0); /* Never learn a held contact. */
    arm();unsigned normal=ordinary_reads;
    two(280,280,280,280);samples(2);assert(state.shadow.qualified_two);
    quiet();samples(2);assert(state.shadow_taps==1&&!hid_events&&ordinary_reads==normal);
    assert(strstr(latest_shadow,"tap=1")&&strstr(latest_shadow,"HID=0"));

    two(280,280,280,280);samples(2);
    unsigned scrolls=state.shadow_scrolls;
    two(240,240,320,320);samples(1); /* Same sites, unequal strength, NOT movement. */
    assert(state.shadow_scrolls==scrolls&&!state.shadow.moving&&!hid_events);
    /* Both actual synthetic lobe sites move +1 station. Metadata preserves
     * ordered association (peak/valley shift <=1); equal strength is not used
     * as a substitute for spatial movement. USB-left +X -> negative pan. */
    two_stations(2,5,280,280,280,280);samples(1);
    assert(state.shadow_scrolls>=1&&strstr(latest_shadow,"pan=-")&&!hid_events);
    /* Both lobes keep their sites and coherently shift their column ratio.
     * This synthetic +Y feature -> negative wheel, not hardware calibration. */
    two_stations(2,5,200,360,200,360);samples(1);
    assert(state.shadow_scrolls>=2&&strstr(latest_shadow,"wheel=-")&&!hid_events);
    quiet();samples(2);assert(state.shadow_taps==1); /* Scroll cancels tap. */

    two(120,120,440,440);samples(2);
    assert(state.shadow_last_hypothesis==XIAO147_RAW_ONE_LOBE); /* Weak lobe below 35%, not a count. */
    quiet();samples(2);assert(state.shadow_taps==1&&!hid_events);
    assert(state.baseline.armed);for(unsigned i=0;i<12;i++)assert(state.baseline.values[i]==0);
    for(unsigned i=0;i<120;i++)samples(1);
    assert(rate_logs>0&&state.capture.min_interval_ms>=20&&state.capture.scan_requests==samples_read);
    assert(type_writes==1&&scan_writes==samples_read&&ordinary_reads==normal&&!hid_events);
    stop();
}
static void invalid_unknown_and_gap(void) {
    prepare();arm();two(280,280,280,280);samples(2);
    unsigned taps=state.shadow_taps,scrolls=state.shadow_scrolls;
    quiet();diff_values[0]=-100;samples(1);
    assert(state.shadow_last_hypothesis==XIAO147_RAW_UNKNOWN&&state.shadow.blocked);
    assert(strstr(latest_shadow,"cancel=1"));
    two(280,280,280,280);samples(2);quiet();samples(2);
    assert(state.shadow_taps==taps&&state.shadow_scrolls==scrolls&&!hid_events);

    two(280,280,280,280);samples(2);now+=101;samples(1);
    assert(state.capture.last_interval_ms>100&&state.shadow.blocked);
    quiet();samples(2);assert(state.shadow_taps==taps&&!hid_events);

    two(280,280,280,280);samples(2);
    /* Defensively inject an identical-timestamp consumer delivery. The real
     * engine never emits it; the actual wrapper must still refuse a gesture. */
    int32_t duplicate[12];for(unsigned i=0;i<12;i++)duplicate[i]=diff_values[i];
    axs_raw_sample(&state,XIAO147_RAW_DIFF,12,duplicate,now-state.capture.started_ms);
    assert(state.shadow.blocked&&!hid_events);
    quiet();samples(2);assert(state.shadow_taps==taps);

    two(280,280,280,280);samples(2);fail_sample=true;
    until_phase(XIAO147_RAW_RESTORE_WRITE,30);
    assert(!state.capture.sample_valid&&state.shadow.blocked);finish_restore();
    assert(!hid_events);

    const uint8_t layouts[][3]={{3,4,0},{6,1,6},{7,6,0xf0},{0,2,0}};
    for(size_t i=0;i<sizeof(layouts)/sizeof(layouts[0]);i++){
        fixture();hands_off();info();memcpy(channel_layout,layouts[i],3);start();
        until_phase(XIAO147_RAW_RESTORE_WRITE,60);
        assert(!samples_read&&!scan_writes&&!type_writes&&!hid_events);
        finish_restore();
    }
}
static void interruptions_and_recovery(void) {
    for(unsigned reason=0;reason<3;reason++){
        prepare();arm();two(280,280,280,280);samples(2);
        unsigned normal=ordinary_reads;contact(1,60,90);
        if(reason==0)page=XIAO147_PAGE_SETTINGS;
        if(reason==1)imu_enabled=true;
        if(reason==2)dtr=false;
        poll();assert(state.capture.phase>=XIAO147_RAW_RESTORE_WRITE);
        assert(scheduled_ms==16&&state.shadow.blocked);
        finish_restore();assert(ordinary_reads==normal&&!hid_events);
        page=XIAO147_PAGE_STATUS;imu_enabled=false;dtr=true;
        for(unsigned i=0;i<5;i++){contact(1,60+(int)i,90);poll();}
        assert(!hid_events&&state.swallow_until_release);
        contact(0,60,90);poll();assert(!state.swallow_until_release&&!hid_events);
        contact(1,40,70);poll();contact(0,40,70);poll();
        assert(key_events==2&&last_key==INPUT_BTN_0);
        feed("AXS_SHADOW_START 0101\n");drain();assert(page_enters==1&&!xiao147_touch_capture_active());
    }
    prepare();arm();feed("AXS_SHADOW_START 01");drain();assert(state.control.length!=0);
    dtr=false;poll();assert(!state.control.length&&!state.info_valid);finish_restore();
    dtr=true;feed("01\nAXS_SHADOW_START 0101\n");drain();assert(page_enters==1&&!hid_events);

    prepare();arm();unsigned normal=ordinary_reads;block_restore=true;
    feed("AXS_RAW_STOP\n");drain();until_phase(XIAO147_RAW_STICKY_FAULT,150);
    assert(factory_page&&xiao147_touch_capture_active()&&page_restores==3&&scheduled_ms==16);
    unsigned writes=factory_writes;
    for(unsigned i=0;i<200;i++){contact(1,40+(int)(i%20),70);poll();}
    feed("AXS_RAW_INFO\nAXS_SHADOW_START 0101\nAXS_RAW_STOP\n");drain();
    assert(state.capture.phase==XIAO147_RAW_STICKY_FAULT&&factory_writes==writes);
    assert(ordinary_reads==normal&&!normal_validation_reads&&!hid_events);
}
int main(void) {
    fixture();poll();assert(ordinary_reads==1&&!factory_writes&&!hid_events&&scheduled_ms==16);
    contact(1,40,70);poll();contact(0,40,70);poll();assert(key_events==2&&last_key==INPUT_BTN_0);
    parser_and_gates();baseline_and_candidates();invalid_unknown_and_gap();interruptions_and_recovery();
    puts("PASS: actual SHADOW UART/driver/FAST engine/profile, strict zero/layout gates, candidate tap/both-axis scroll never HID, bounded restoration/held-release/disconnect/sticky isolation");
    return 0;
}
#endif
'''

legacy_test = replace_once(parts["test"], 'int main(void) {', r'''
#if RAW_MODE
static void shadow_disabled(void) {
    fixture();for(unsigned i=0;i<10;i++)poll();info();
    feed("AXS_SHADOW_START 0101\n");drain();
    assert(!factory_writes&&!xiao147_touch_capture_active());
}
#endif
int main(void) {''')
legacy_test = replace_once(legacy_test, 'assert(!factory_writes);parser_tests();command_tests();verify_identity_before_write();',
    'assert(!factory_writes);parser_tests();shadow_disabled();command_tests();verify_identity_before_write();')

with tempfile.TemporaryDirectory(prefix="xiao147-shadow-integration-") as directory:
    for raw, shadow in ((0, 0), (1, 0), (1, 1)):
        c = Path(directory) / f"test-{raw}-{shadow}.c"
        c.write_text(prelude + driver + hardware + (shadow_test if shadow else legacy_test))
        flags = [f"-DRAW_MODE={raw}", f"-DSHADOW_MODE={shadow}"]
        preprocessed = subprocess.run(
            ["cc", "-std=c11", *flags, "-E", "-P", "-I", str(root / "src"), str(c)],
            check=True, capture_output=True, text=True,
        ).stdout
        if not raw:
            for forbidden in ("axs_raw_write", "axs_raw_poll", "capture_console", "uart_poll_in", "AXS_RAW_READY"):
                assert forbidden not in preprocessed, f"factory/UART path leaked into default: {forbidden}"
        if not shadow:
            for forbidden in ("xiao147_raw_profile_evaluate", "xiao147_shadow_gesture_observe", "AXS_SHADOW_READY"):
                assert forbidden not in preprocessed, f"shadow path leaked into non-shadow build: {forbidden}"
        sources = [str(c)]
        if raw:
            sources.append(str(root / "src/xiao147_raw_capture.c"))
        if shadow:
            sources.append(str(root / "src/xiao147_raw_gestures.c"))
        binary = Path(directory) / f"test-{raw}-{shadow}"
        subprocess.run(
            ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
             *flags, "-I", str(root / "src"), *sources, "-o", str(binary)], check=True,
        )
        subprocess.run([str(binary)], check=True)
