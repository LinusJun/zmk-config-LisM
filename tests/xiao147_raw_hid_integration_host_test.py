"""Actual driver/engine/profile: separate explicit, bounded experimental HID.

Compile default, RAW, SHADOW and the opt-in functional-test configuration.
Only Zephyr/I2C/UART/input queue boundaries are mocked; real C producers and
cleanup run. A successful mocked input API means queue acceptance, NOT an OS
acknowledgment or physically validated two-finger/coordinate recognition.
No device access, automatic activation or firmware writes occur here.
"""
import ast
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
boundary_path = root / "tests/xiao147_shadow_integration_host_test.py"
tree = ast.parse(boundary_path.read_text())
cutoff = next(index for index, node in enumerate(tree.body) if isinstance(node, ast.With))
prefix = tree.body[:cutoff]
for node in ast.walk(ast.Module(body=prefix, type_ignores=[])):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        assert not (isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"), \
            "Boundary assembly unexpectedly executes a compiler/process"
# Evaluate only source assembly, never import/execute its compiler/run block.
boundary = {"__file__": str(boundary_path)}
exec(compile(ast.Module(body=prefix, type_ignores=[]), str(boundary_path), "exec"), boundary)
prelude, hardware = boundary["prelude"], boundary["hardware"]
driver = re.sub(r"^#include .*$", "", (root / "src/axs5106l_touchpad.c").read_text(), flags=re.M)


def replace_once(text, old, new):
    assert text.count(old) == 1, f"HID integration boundary hook changed: {old[:100]}"
    return text.replace(old, new, 1)


prelude = replace_once(prelude, '#include "xiao147_raw_gestures.h"', '''#include "xiao147_raw_gestures.h"
#if HID_MODE
#define CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST 1
#endif''')
prelude = replace_once(prelude, 'static char latest_shadow[512];', '''static bool host_hid_log_enabled(void);
static char latest_shadow[512];''')
prelude = replace_once(prelude, 'assert(strstr(latest_shadow,"HID=0"));',
    '''assert(strstr(latest_shadow,host_hid_log_enabled()?"HID=1":"HID=0"));''')
prelude = replace_once(prelude, 'static int uart_error;', 'static int uart_error, uart_query_error;')
prelude = replace_once(prelude,
    'assert(dev==&host_console&&control==UART_LINE_CTRL_DTR);*value=dtr;return 0;',
    'assert(dev==&host_console&&control==UART_LINE_CTRL_DTR);if(uart_query_error)return uart_query_error;*value=dtr;return 0;')
prelude = replace_once(prelude, '''static int input_report_key(const struct device *dev,int code,int value,bool sync,int wait) {
    (void)dev;(void)value;assert(sync&&wait==K_NO_WAIT&&!input_must_be_blocked());
    hid_events++;key_events++;last_key=code;return 0;
}
static int input_report_rel(const struct device *dev,int code,int value,bool sync,int wait) {
    (void)dev;(void)code;(void)value;(void)sync;
    assert(wait==K_NO_WAIT&&!input_must_be_blocked());hid_events++;return 0;
}''', '''static int host_input_key(const struct device *dev,int code,int value,bool sync,int wait);
static int host_input_rel(const struct device *dev,int code,int value,bool sync,int wait);
static int input_report_key(const struct device *dev,int code,int value,bool sync,int wait) {
    return host_input_key(dev,code,value,sync,wait);
}
static int input_report_rel(const struct device *dev,int code,int value,bool sync,int wait) {
    return host_input_rel(dev,code,value,sync,wait);
}''')
prelude += r'''
struct host_input_event {bool relative;int code,value,result;bool sync;uint32_t timestamp;};
static struct host_input_event input_events[4096];
static unsigned input_used, release_attempts, press_attempts, scroll_attempts;
static unsigned release_attempts_this_poll;
static bool queue_right_down, explicit_window_requested;
static unsigned fail_press, fail_release, fail_scroll;
static int fail_scroll_code=-1;
'''
hardware = replace_once(hardware, 'static struct device touch_device={&state,&config};',
    '''static struct device touch_device={&state,&config};
static bool host_hid_log_enabled(void) {
#if HID_MODE
    return state.experimental_hid_active;
#else
    return false;
#endif
}''')
hardware = replace_once(hardware, 'return xiao147_raw_capture_active(&state.capture)||factory_page;',
    '''return xiao147_raw_capture_active(&state.capture)||factory_page
#if HID_MODE
        ||state.experimental_button_down
#endif
        ;''')
hardware = replace_once(hardware, '''static void poll(void) {
    now+=scheduled_ms;''', '''static void poll(void) {
    now+=scheduled_ms;release_attempts_this_poll=0;''')
hardware = replace_once(hardware, 'uint32_t calls=state.capture.io_calls;',
    'uint32_t calls=state.capture.io_calls,started=state.capture.started_ms;')
hardware = replace_once(hardware,
    'assert(state.capture.io_calls>=calls&&state.capture.io_calls-calls<=1);',
    '''if(state.capture.started_ms!=started){assert(state.capture.io_calls<=1);}
    else {assert(state.capture.io_calls>=calls&&state.capture.io_calls-calls<=1);}''')
hardware = replace_once(hardware,
    'scheduled_ms=16;shadow_logs=armed_logs=rate_logs=rejected_logs=0;latest_shadow[0]=0;',
    '''scheduled_ms=16;shadow_logs=armed_logs=rate_logs=rejected_logs=0;latest_shadow[0]=0;
    input_used=release_attempts=press_attempts=scroll_attempts=release_attempts_this_poll=0;
    queue_right_down=explicit_window_requested=false;
    fail_press=fail_release=fail_scroll=0;fail_scroll_code=-1;''')
hardware = replace_once(hardware,
    'atomic_set(&capture_gate,0);dtr=console_ready=true;uart_error=0;',
    'atomic_set(&capture_gate,0);dtr=console_ready=true;uart_error=uart_query_error=0;')
hardware += r'''
static void record_input(bool relative,int code,int value,int result,bool sync) {
    assert(input_used<sizeof(input_events)/sizeof(input_events[0]));
    input_events[input_used++]=(struct host_input_event){relative,code,value,result,sync,now};
}
static int host_input_key(const struct device *dev,int code,int value,bool sync,int wait) {
    assert(dev==&touch_device&&sync&&wait==K_NO_WAIT&&(value==0||value==1));
    int result=0;
    if(input_must_be_blocked()){
#if HID_MODE
        assert(code==INPUT_BTN_1&&explicit_window_requested);
        if(value==1){
            assert(state.experimental_hid_active&&state.baseline.armed&&
                   state.capture.phase==XIAO147_RAW_READ_SAMPLE&&page==XIAO147_PAGE_STATUS&&
                   !imu_enabled&&dtr&&!queue_right_down);
            press_attempts++;if(fail_press){fail_press--;result=-EIO;}
        } else {
            /* Recovery releases are permitted even after disabling the
             * experiment, losing DTR, entering sticky fault or normal page. */
            assert(queue_right_down&&state.experimental_button_down);
            release_attempts++;assert(++release_attempts_this_poll==1);
            if(fail_release){fail_release--;result=-EIO;}
        }
#else
        assert(false);
#endif
    } else {assert(code==INPUT_BTN_0||code==INPUT_BTN_1);}
    record_input(false,code,value,result,sync);
    if(result==0){
        hid_events++;key_events++;last_key=code;
        if(code==INPUT_BTN_1){queue_right_down=value!=0;}
    }
    return result;
}
static int host_input_rel(const struct device *dev,int code,int value,bool sync,int wait) {
    assert(dev==&touch_device&&wait==K_NO_WAIT);
    int result=0;
    if(input_must_be_blocked()){
#if HID_MODE
        assert(explicit_window_requested&&state.experimental_hid_active&&state.baseline.armed&&
               state.capture.phase==XIAO147_RAW_READ_SAMPLE&&page==XIAO147_PAGE_STATUS&&
               !imu_enabled&&dtr&&(code==INPUT_REL_WHEEL||code==INPUT_REL_HWHEEL)&&sync);
        assert(value>=-127&&value<=127&&value!=0);
        scroll_attempts++;if(fail_scroll&&code==fail_scroll_code){fail_scroll--;result=-EIO;}
#else
        assert(false);
#endif
    } else {assert(code==INPUT_REL_X||code==INPUT_REL_Y||code==INPUT_REL_WHEEL||code==INPUT_REL_HWHEEL);}
    record_input(true,code,value,result,sync);if(result==0){hid_events++;}return result;
}
'''

tests = r'''
static void normal_boot(void) {
    fixture();poll();assert(ordinary_reads==1&&!hid_events&&scheduled_ms==16);
#if RAW_MODE
    assert(!factory_writes&&!xiao147_touch_capture_active());
#endif
#if HID_MODE
    assert(!state.experimental_hid_active&&!state.experimental_button_down);
#endif
    contact(1,40,70);poll();contact(0,40,70);poll();
    assert(key_events==2&&last_key==INPUT_BTN_0);
    contact(1,40,70);poll();contact(1,42,74);poll();
    assert(hid_events>2);contact(0,42,74);poll();
#if RAW_MODE
    assert(!factory_writes);
#endif
}
#if RAW_MODE
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
    contact(0,40,70);for(unsigned i=0;i<10;i++)poll();
    assert(state.normal_zero&&now-state.normal_zero_since_ms>=100);
}
static void info(void) {
    unsigned writes=factory_writes;feed("AXS_RAW_INFO\r\n");drain();
    for(unsigned i=0;state.info_stage&&i<10;i++)poll();
    assert(state.info_valid&&!state.info_stage&&state.info_fw==0x0101&&writes==factory_writes);
}
#if !SHADOW_MODE || HID_MODE
static void until_phase(enum xiao147_raw_phase phase,unsigned limit) {
    for(unsigned i=0;state.capture.phase!=phase&&i<limit;i++)poll();
    assert(state.capture.phase==phase);
}
#endif
static void restore_engine(void) {
    unsigned reads=ordinary_reads;
    for(unsigned i=0;xiao147_raw_capture_active(&state.capture)&&i<200;i++)poll();
    assert(!xiao147_raw_capture_active(&state.capture)&&!factory_page&&ordinary_reads==reads);
    assert(normal_validation_reads==1&&scheduled_ms==16&&state.swallow_until_release);
}
static void request_rejected(void) {
    assert(state.capture.phase==XIAO147_RAW_IDLE);
    assert(!xiao147_raw_capture_active(&state.capture)&&!xiao147_touch_capture_active());
#if HID_MODE
    assert(!state.experimental_hid_active);
#endif
}
static void command_disabled(void) {
    fixture();hands_off();info();feed("AXS_GESTURE_START 0101\n");drain();
#if !HID_MODE
    assert(!factory_writes&&!xiao147_touch_capture_active());
    request_rejected();
#else
    assert(state.experimental_hid_active&&xiao147_touch_capture_active());
    until_phase(XIAO147_RAW_SCAN_START,60);assert(factory_page&&page_enters==1);
    feed("AXS_RAW_STOP\n");drain();restore_engine();
#endif
}
#if !SHADOW_MODE
static void legacy_raw(void) {
    fixture();hands_off();info();feed("AXS_RAW_START 0101\n");drain();
    until_phase(XIAO147_RAW_PAIR_WAIT,60);assert(samples_read==2&&!hid_events);
    feed("AXS_RAW_STOP\n");drain();restore_engine();assert(!xiao147_touch_capture_active());
}
#endif
#endif

#if SHADOW_MODE
static void request(bool functional) {
    explicit_window_requested=functional;
    feed(functional?"AXS_GESTURE_START 0101\n":"AXS_SHADOW_START 0101\n");drain();
    assert(state.shadow_active&&xiao147_touch_capture_active()&&scheduled_ms==4);
#if HID_MODE
    assert(state.experimental_hid_active==functional);
#else
    assert(!functional);
#endif
}
static void prepare(bool functional) {fixture();hands_off();info();request(functional);}
static void samples(unsigned count) {
    uint32_t target=state.capture.samples_received+count;
    unsigned reads=ordinary_reads;
    for(unsigned i=0;state.capture.samples_received<target&&i<1000;i++){
        assert(xiao147_raw_capture_active(&state.capture));poll();
    }
    assert(state.capture.samples_received==target&&samples_read==target&&ordinary_reads==reads);
}
static void quiet(void) {memset(diff_values,0,sizeof(diff_values));}
static void two(unsigned first,unsigned second,int even,int odd) {
    assert(first<second&&second<6);quiet();
    diff_values[2*first]=diff_values[2*second]=(int16_t)even;
    diff_values[2*first+1]=diff_values[2*second+1]=(int16_t)odd;
}
static void arm(void) {
    quiet();for(unsigned i=0;!state.baseline.armed&&i<20;i++)samples(1);
    assert(state.baseline.armed&&armed_logs==1&&!hid_events);samples(2);
    assert(!state.shadow.blocked);
}
static void tap(void) {two(1,4,280,280);samples(2);quiet();samples(2);}
static void stop(void) {feed("AXS_RAW_STOP\n");drain();restore_engine();}
static void shadow_never_hid(void) {
    prepare(false);arm();tap();assert(state.shadow_taps==1&&!hid_events);
    two(1,4,280,280);samples(2);two(2,5,280,280);samples(1);
    unsigned scrolls=state.shadow_scrolls;
    two(2,5,200,360);samples(1);
    assert(state.shadow_scrolls==scrolls&&state.shadow.scroll_axis==XIAO147_SCROLL_HORIZONTAL&&!hid_events);
    quiet();samples(2);two(2,5,280,280);samples(2);two(2,5,200,360);samples(1);
    assert(state.shadow_scrolls>scrolls&&state.shadow.scroll_axis==XIAO147_SCROLL_VERTICAL&&!hid_events);
#if HID_MODE
    uint32_t started=state.capture.started_ms;
    feed("AXS_GESTURE_START 0101\n");drain();
    assert(!state.experimental_hid_active&&state.capture.started_ms==started&&!hid_events);
#endif
    stop();assert(!queue_right_down&&!hid_events&&strstr(latest_shadow,"HID=0"));
}
#endif

#if HID_MODE
static unsigned accepted(int code,int value) {
    unsigned result=0;for(unsigned i=0;i<input_used;i++){
        result+=input_events[i].code==code&&input_events[i].value==value&&input_events[i].result==0;
    }return result;
}
static void gates_and_parser(void) {
    fixture();hands_off();feed("AXS_GESTURE_START 0101\n");drain();assert(!factory_writes);
    request_rejected();
    info();const char *bad[]={"AXS_GESTURE_START 101\n","AXS_GESTURE_START 01010\n",
        "AXS_GESTURE_START 0g01\n","AXS_GESTURE_START 0101 extra\n",
        " AXS_GESTURE_START 0101\n","axs_gesture_start 0101\n","AXS_GESTURE_START\n"};
    for(size_t i=0;i<sizeof(bad)/sizeof(bad[0]);i++){
        feed(bad[i]);drain();assert(!factory_writes);request_rejected();
    }
    feed("AXS_GESTURE_START 0102\n");drain();assert(!factory_writes);
    request_rejected();
    uint8_t oversized[100];memset(oversized,'A',sizeof(oversized));feed_bytes(oversized,sizeof(oversized));
    feed("AXS_GESTURE_START 0101\n");drain();assert(!factory_writes);
    request_rejected();
    const uint8_t nul[]="AXS_GESTURE_START\0 0101\n";feed_bytes(nul,sizeof(nul)-1);drain();assert(!factory_writes);
    request_rejected();
    hands_off();info();explicit_window_requested=true;
    feed("AXS_GESTURE_STA");drain();assert(!state.experimental_hid_active);
    request_rejected();
    feed("RT 0101\r");drain();assert(!state.experimental_hid_active);
    request_rejected();
    feed("\n");drain();assert(state.experimental_hid_active&&scheduled_ms==4);
    uint32_t started=state.capture.started_ms;
    feed("AXS_GESTURE_START 0101\nAXS_SHADOW_START 0101\nAXS_RAW_INFO\n");drain();
    assert(state.experimental_hid_active&&state.capture.started_ms==started&&!state.info_stage);
    stop();feed("AXS_GESTURE_START 0101\n");drain();assert(page_enters==1&&!state.experimental_hid_active);
    request_rejected();

    fixture();poll();info();feed("AXS_GESTURE_START 0101\n");drain();assert(!factory_writes);
    request_rejected();
    fixture();hands_off();info();contact(1,40,70);poll();
    feed("AXS_GESTURE_START 0101\n");drain();assert(!factory_writes);
    request_rejected();
    fixture();hands_off();info();now+=101;
    feed("AXS_GESTURE_START 0101\n");drain();assert(!factory_writes);
    request_rejected();
    fixture();hands_off();info();page=XIAO147_PAGE_SETTINGS;
    feed("AXS_GESTURE_START 0101\n");drain();assert(!factory_writes);
    request_rejected();
    fixture();hands_off();info();imu_enabled=true;
    feed("AXS_GESTURE_START 0101\n");drain();assert(!factory_writes);
    request_rejected();
    fixture();hands_off();info();wrong_id=true;
    feed("AXS_GESTURE_START 0101\n");drain();assert(!factory_writes&&!xiao147_touch_capture_active());
    request_rejected();
    fixture();hands_off();info();firmware=0x0102;request(true);poll();
    assert(!factory_writes&&!xiao147_touch_capture_active()&&!state.experimental_hid_active);
    request_rejected();
}
static void exact_tap_and_natural_scroll(void) {
    prepare(true);arm();contact(1,40,70);tap();
    assert(press_attempts==1&&release_attempts==1&&key_events==2&&hid_events==2);
    assert(accepted(INPUT_BTN_1,1)==1&&accepted(INPUT_BTN_1,0)==1&&!queue_right_down);
    assert(!state.experimental_button_down&&!accepted(INPUT_BTN_0,1));
    samples(8);assert(hid_events==2);stop();assert(hid_events==2);

    for(unsigned axis=0;axis<2;axis++)for(int sign=-1;sign<=1;sign+=2){
        prepare(true);arm();
        unsigned a=axis==0&&sign<0?2:1,b=axis==0&&sign<0?5:4;
        two(a,b,280,280);samples(2);
        if(axis==0){two(sign>0?2:1,sign>0?5:4,280,280);}
        else {two(a,b,sign>0?200:360,sign>0?360:200);}
        samples(1);assert(input_used==1&&scroll_attempts==1&&hid_events==1);
        assert(input_events[0].relative&&input_events[0].sync&&input_events[0].result==0);
        assert(input_events[0].code==(axis==0?INPUT_REL_HWHEEL:INPUT_REL_WHEEL));
        assert(input_events[0].value*sign<0); /* Negated logical feature delta. */
        quiet();samples(2);assert(!key_events);stop();assert(hid_events==1);
    }
    /* Comparable net movement on both axes stays diagonal, unlike64/26
     * which deliberately locks horizontal at the2:1 threshold. */
    prepare(true);arm();two(1,4,280,280);samples(2);two(2,5,80,480);samples(1);
    assert(input_used==2&&input_events[0].code==INPUT_REL_HWHEEL&&input_events[1].code==INPUT_REL_WHEEL);
    assert(input_events[0].sync&&input_events[1].sync&&input_events[0].value<0&&input_events[1].value<0);
    unsigned emitted=hid_events;stop();for(unsigned i=0;i<10;i++)poll();assert(hid_events==emitted);
}

static void release_fault_command_quarantine(void) {
    prepare(true);arm();fail_release=100;tap();
    assert(state.experimental_button_down&&state.experimental_uart_quarantine);
    feed("AXS_RAW_INFO\n");for(unsigned i=0;i<64;i++)feed("\n");
    feed("AXS_GESTURE_START 0101\n");
    restore_engine();assert(state.experimental_button_down&&queue_right_down);
    fail_release=0;poll();
    assert(!state.experimental_button_down&&!queue_right_down&&!xiao147_touch_capture_active());
    assert(state.experimental_uart_quarantine&&!state.info_valid&&page_enters==1);
    unsigned reads=ordinary_reads;
    for(unsigned i=0;i<6;i++)poll();
    assert(ordinary_reads>reads); /* UART isolation does not block ordinary touch. */

    /* Empty polls between old USB fragments cannot end quarantine. */
    feed("AXS_RAW_IN");drain();for(unsigned i=0;i<6;i++)poll();
    feed("FO\n");drain();for(unsigned i=0;i<8;i++)poll();
    feed("AXS_GESTURE_STA");drain();for(unsigned i=0;i<6;i++)poll();
    feed("RT 0101\n");drain();for(unsigned i=0;i<6;i++)poll();
    assert(state.experimental_uart_quarantine&&!state.experimental_hid_active&&page_enters==1);
    assert(!state.info_valid&&!state.control.length&&!xiao147_touch_capture_active());

    /* A failed DTR query is not a verified low transition. */
    uart_query_error=-EIO;dtr=false;poll();
    assert(state.experimental_uart_quarantine&&!state.experimental_uart_saw_low);
    uart_query_error=0;dtr=true;poll();assert(state.experimental_uart_quarantine);
    /* RX error also cannot prove the queue drained during low. */
    dtr=false;uart_error=-EIO;poll();
    assert(state.experimental_uart_saw_low&&!state.experimental_uart_low_drained);
    uart_error=0;dtr=true;poll();assert(state.experimental_uart_quarantine);

    dtr=false;feed("AXS_RAW_INFO\n");for(unsigned i=0;i<64;i++)feed("\n");
    feed("AXS_GESTURE_START 0101\n");drain();poll();
    assert(state.experimental_uart_saw_low&&state.experimental_uart_low_drained);
    dtr=true;poll();assert(!state.experimental_uart_quarantine&&!state.info_valid);
    feed("AXS_GESTURE_START 0101\n");drain();request_rejected();
    hands_off();info();request(true);until_phase(XIAO147_RAW_SCAN_START,50);
    assert(page_enters==2&&state.experimental_hid_active);
    feed("AXS_RAW_STOP\n");drain();
    for(unsigned i=0;xiao147_raw_capture_active(&state.capture)&&i<200;i++)poll();
    assert(!xiao147_touch_capture_active()&&!factory_page&&normal_validation_reads==2);
    assert(!state.experimental_hid_active&&!state.experimental_uart_quarantine);
    assert(accepted(INPUT_BTN_1,1)==1&&accepted(INPUT_BTN_1,0)==1);
}
static void emission_bounds_and_conflicts(void) {
    prepare(true);arm();until_phase(XIAO147_RAW_READ_SAMPLE,30);
    axs_experimental_emit(&state,(struct xiao147_shadow_result){.candidate_wheel=127,.candidate_hwheel=-127});
    assert(input_used==2&&input_events[0].value==-127&&input_events[1].value==127);
    stop();
    for(unsigned which=0;which<3;which++){
        prepare(true);arm();until_phase(XIAO147_RAW_READ_SAMPLE,30);
        struct xiao147_shadow_result result={0};
        if(which==0)result.candidate_wheel=128;
        if(which==1)result.candidate_hwheel=-128;
        if(which==2){result.candidate_right_tap=true;result.candidate_wheel=1;}
        axs_experimental_emit(&state,result);
        assert(!hid_events&&!state.experimental_hid_active&&state.shadow.blocked);
        assert(state.capture.phase==XIAO147_RAW_RESTORE_WRITE);restore_engine();
    }
}
static void normal_after_release(void) {
    assert(!state.experimental_button_down&&!queue_right_down&&!xiao147_touch_capture_active());
    unsigned before=hid_events;contact(1,50,80);poll();contact(1,52,84);poll();
    assert(hid_events==before&&state.swallow_until_release);
    contact(0,52,84);poll();assert(!state.swallow_until_release&&hid_events==before);
    contact(1,40,70);poll();contact(0,40,70);poll();
    assert(accepted(INPUT_BTN_0,1)==1&&accepted(INPUT_BTN_0,0)==1&&hid_events==before+2);
}
static void press_and_release_failures(void) {
    prepare(true);arm();fail_press=1;tap();
    assert(press_attempts==1&&!release_attempts&&!key_events&&!queue_right_down);
    assert(!state.experimental_hid_active&&state.shadow.blocked&&state.input_errors==1);
    assert(!state.shadow.remainder_x&&!state.shadow.remainder_y);
    assert(state.capture.phase==XIAO147_RAW_RESTORE_WRITE);restore_engine();normal_after_release();

    for(unsigned failures=1;failures<=12;failures+=11){
        prepare(true);arm();contact(1,50,80);fail_release=failures;tap();
        assert(press_attempts==1&&release_attempts==1&&key_events==1&&queue_right_down);
        assert(state.experimental_button_down&&!state.experimental_hid_active&&state.shadow.blocked);
        unsigned reads=ordinary_reads;
        for(unsigned i=0;xiao147_raw_capture_active(&state.capture)&&i<200;i++){
            bool down=state.experimental_button_down;unsigned attempts=release_attempts;
            poll();if(down){assert(release_attempts==attempts+1);}
        }
        assert(!xiao147_raw_capture_active(&state.capture)&&ordinary_reads==reads&&!factory_page);
        if(failures>1){
            assert(state.experimental_button_down&&queue_right_down&&xiao147_touch_capture_active());
            fail_release=10;dtr=false;feed("AXS_GESTURE_START 0101\n");
            for(unsigned i=0;i<6;i++){
                unsigned attempts=release_attempts;poll();
                assert(release_attempts==attempts+1&&ordinary_reads==reads&&xiao147_touch_capture_active());
            }
            assert(state.experimental_button_down&&queue_right_down);
            fail_release=0;poll();assert(!state.experimental_button_down&&!queue_right_down);
            dtr=true;drain();assert(!state.experimental_hid_active&&page_enters==1);
            request_rejected();
        }
        assert(accepted(INPUT_BTN_1,1)==1&&accepted(INPUT_BTN_1,0)==1);
        assert(!state.experimental_button_down&&!queue_right_down&&!xiao147_touch_capture_active());
        normal_after_release();
    }

    prepare(true);arm();fail_release=100;block_restore=true;tap();
    until_phase(XIAO147_RAW_STICKY_FAULT,150);
    assert(state.experimental_button_down&&queue_right_down&&xiao147_touch_capture_active());
    unsigned reads=ordinary_reads;fail_release=0;poll();
    assert(!queue_right_down&&!state.experimental_button_down&&xiao147_touch_capture_active());
    assert(state.capture.phase==XIAO147_RAW_STICKY_FAULT&&ordinary_reads==reads);
    assert(accepted(INPUT_BTN_1,1)==1&&accepted(INPUT_BTN_1,0)==1);
}
static void scroll_failures(void) {
    for(unsigned axis=0;axis<2;axis++){
        prepare(true);arm();two(1,4,280,280);samples(2);
        fail_scroll=1;fail_scroll_code=axis==0?INPUT_REL_HWHEEL:INPUT_REL_WHEEL;
        two(2,5,80,480);samples(1);
        assert(!state.experimental_hid_active&&state.shadow.blocked&&state.input_errors==1);
        assert(!state.shadow.remainder_x&&!state.shadow.remainder_y);
        assert(state.capture.phase==XIAO147_RAW_RESTORE_WRITE);
        assert(input_used==(axis==0?1U:2U)&&hid_events==(axis==0?0U:1U));
        assert(input_events[input_used-1].result==-EIO);
        for(unsigned i=0;i<input_used;i++)assert(input_events[i].sync);
        unsigned emitted=hid_events;restore_engine();for(unsigned i=0;i<5;i++)poll();
        assert(hid_events==emitted&&!queue_right_down);
    }
}
static void interruption_and_deadline(void) {
    for(unsigned reason=0;reason<7;reason++){
        prepare(true);arm();two(1,4,280,280);samples(2);
        if(reason==0)page=XIAO147_PAGE_SETTINGS;
        if(reason==1)imu_enabled=true;
        if(reason==2)dtr=false;
        if(reason==3)uart_error=-EIO;
        if(reason<4){poll();assert(!state.experimental_hid_active);uart_error=0;restore_engine();}
        else {
            if(reason==4)pose=XIAO147_BUTTONS_BOTTOM;
            if(reason==5){quiet();diff_values[0]=-100;}
            if(reason==6)now+=101;
            samples(1);assert(state.shadow.blocked&&!hid_events);
            quiet();samples(2);assert(!hid_events);stop();
        }
        assert(!hid_events&&!queue_right_down);
    }
    prepare(true);arm();two(1,4,280,280);samples(2);fail_sample=true;
    until_phase(XIAO147_RAW_RESTORE_WRITE,30);
    assert(!state.experimental_hid_active&&!hid_events);restore_engine();
    const uint8_t bad_layouts[][3]={{3,4,0},{6,1,6},{7,6,0xf0},{0,2,0}};
    for(size_t i=0;i<sizeof(bad_layouts)/sizeof(bad_layouts[0]);i++){
        fixture();hands_off();info();memcpy(channel_layout,bad_layouts[i],3);request(true);
        until_phase(XIAO147_RAW_RESTORE_WRITE,60);
        assert(!state.experimental_hid_active&&!hid_events&&!samples_read);restore_engine();
    }
    prepare(true);arm();uint32_t started=state.capture.started_ms;
    for(unsigned i=0;i<3;i++){
        feed("AXS_GESTURE_START 0101\n");drain();assert(state.capture.started_ms==started);
    }
    now=started+XIAO147_RAW_SESSION_MS-scheduled_ms;poll();
    assert(state.capture.phase==XIAO147_RAW_RESTORE_WRITE&&!state.experimental_hid_active);
    assert(state.capture.last_error==XIAO147_RAW_SESSION_TIMEOUT);restore_engine();
    for(unsigned i=0;i<100;i++)poll();
    feed("AXS_GESTURE_START 0101\n");drain();assert(page_enters==1&&!state.experimental_hid_active&&!hid_events);
    request_rejected();
}
#endif

int main(void) {
    normal_boot();
#if RAW_MODE
    command_disabled();
#if !SHADOW_MODE
    legacy_raw();
#endif
#endif
#if SHADOW_MODE
    shadow_never_hid();
#endif
#if HID_MODE
    gates_and_parser();exact_tap_and_natural_scroll();emission_bounds_and_conflicts();
    press_and_release_failures();release_fault_command_quarantine();scroll_failures();interruption_and_deadline();
    puts("PASS: explicit bounded experimental GESTURE_START actual C, right press/release retries, both natural-scroll axes, error/disconnect/deadline isolation; queue acceptance only, NOT OS/hardware validation");
#else
    puts("PASS: non-functional configuration cannot activate GESTURE_START; normal behavior preserved and SHADOW never emits HID");
#endif
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="xiao147-raw-hid-integration-") as directory:
    for raw, shadow, hid in ((0, 0, 0), (1, 0, 0), (1, 1, 0), (1, 1, 1)):
        c = Path(directory) / f"test-{raw}-{shadow}-{hid}.c"
        c.write_text(prelude + driver + hardware + tests)
        flags = [f"-DRAW_MODE={raw}", f"-DSHADOW_MODE={shadow}", f"-DHID_MODE={hid}"]
        preprocessed = subprocess.run(
            ["cc", "-std=c11", *flags, "-E", "-P", "-I", str(root / "src"), str(c)],
            check=True, capture_output=True, text=True,
        ).stdout
        if not raw:
            for forbidden in ("axs_raw_write", "axs_raw_poll", "capture_console", "uart_poll_in"):
                assert forbidden not in preprocessed, f"factory/UART path leaked into default: {forbidden}"
        if not shadow:
            assert "xiao147_shadow_gesture_observe_profile" not in preprocessed
        if not hid:
            for forbidden in ("axs_experimental_emit", "axs_experimental_release", "experimental_hid_active"):
                assert forbidden not in preprocessed, f"live experimental path leaked into disabled build: {forbidden}"
        sources = [str(c)]
        if raw:
            sources.append(str(root / "src/xiao147_raw_capture.c"))
        if shadow:
            sources.append(str(root / "src/xiao147_raw_gestures.c"))
        binary = Path(directory) / f"test-{raw}-{shadow}-{hid}"
        subprocess.run(
            ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
             *flags, "-I", str(root / "src"), *sources, "-o", str(binary)], check=True,
        )
        subprocess.run([str(binary)], check=True)
