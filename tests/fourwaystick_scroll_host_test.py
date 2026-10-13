"""Exercise pinned ZMK's real scroll behavior with this keymap's Base bindings.

Fixture: zmkfirmware/zmk 9ebbeff0a8b69a42f14aec022cdf16c7a107b9e0,
app/src/behaviors/behavior_input_two_axis.c (MIT, unmodified).
This tests emitted HID-axis signs; host OS page interpretation is a hardware test.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / "tests/fixtures/zmk_input_two_axis_9ebbeff.c").read_text()
assert hashlib.sha256(source.encode()).hexdigest() == "bb191add309a68c96e333b3b0ff590349a567e897da683d703eb099f62561840"
assert "revision: 9ebbeff0a8b69a42f14aec022cdf16c7a107b9e0" in (root/"config/west.yml").read_text()
keymap = (root / "config/lism.keymap").read_text()
speed = int(re.search(r"#define LISM_4WAY_SCROLL_SPEED (\d+)", keymap)[1])
base = re.search(r"default_layer\s*\{.*?bindings\s*=\s*<(.*?)>;", keymap, re.S)[1]
moves = re.findall(r"&stick_scroll MOVE_([XY])\((-?LISM_4WAY_SCROLL_SPEED)\)", base)
assert moves == [("Y", "-LISM_4WAY_SCROLL_SPEED"), ("X", "-LISM_4WAY_SCROLL_SPEED"),
                 ("Y", "LISM_4WAY_SCROLL_SPEED"), ("X", "LISM_4WAY_SCROLL_SPEED")]
assert "acceleration-exponent = <0>" in keymap and "time-to-max-speed-ms = <0>" in keymap
params = []
for axis, value in moves:
    v = -speed if value.startswith("-") else speed
    params.append((v & 0xffff) << (16 if axis == "X" else 0))
source = re.sub(r"^#include .*$", "", source, flags=re.M)
source = source[:source.index("#define ITA_INST(n)")]
prelude = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <math.h>
#define CONFIG_MINIMAL_LIBC 0
#define CONFIG_ZMK_POINTING_SMOOTH_SCROLLING 0
#define CONFIG_ZMK_LOG_LEVEL 0
#define CONFIG_SYS_CLOCK_TICKS_PER_SEC 1000
#define IS_ENABLED(x) (x)
#define LOG_MODULE_DECLARE(...)
#define LOG_DBG(...)
#define CLAMP(v, lo, hi) ((v)<(lo)?(lo):((v)>(hi)?(hi):(v)))
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
#define MOVE_X_DECODE(v) ((int16_t)(((uint32_t)(v)>>16)&0xffff))
#define MOVE_Y_DECODE(v) ((int16_t)((uint32_t)(v)&0xffff))
struct k_work { int dummy; };
struct k_work_delayable { struct k_work work; bool scheduled; unsigned delay; };
#define CONTAINER_OF(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
static struct k_work_delayable *k_work_delayable_from_work(struct k_work *p) {
    return CONTAINER_OF(p,struct k_work_delayable,work);
}
struct device { void *data; const void *config; };
struct zmk_behavior_binding { const char *behavior_dev; uint32_t param1; };
struct zmk_behavior_binding_event { int position; };
struct behavior_driver_api {
    int (*binding_pressed)(struct zmk_behavior_binding *,struct zmk_behavior_binding_event);
    int (*binding_released)(struct zmk_behavior_binding *,struct zmk_behavior_binding_event);
};
#define INPUT_REL_WHEEL 8
#define INPUT_REL_HWHEEL 6
static uint64_t ticks=1;
static int output_x,output_y,event_count;
static const struct device *selected_dev;
static uint64_t k_uptime_ticks(void) { return ticks; }
static void k_work_init_delayable(struct k_work_delayable *w,void (*cb)(struct k_work *)) {
    (void)cb; w->scheduled=false;
}
static void k_work_schedule(struct k_work_delayable *w,unsigned delay) {
    w->scheduled=true; w->delay=delay;
}
static void k_work_cancel_delayable(struct k_work_delayable *w) { w->scheduled=false; }
static const struct device *zmk_behavior_get_binding(const char *name) {
    (void)name; return selected_dev;
}
static int input_report_rel(const struct device *d,int code,int16_t value,bool sync,int wait) {
    (void)d;(void)sync;(void)wait;
    assert(code==INPUT_REL_WHEEL || code==INPUT_REL_HWHEEL);
    if(code==INPUT_REL_HWHEEL) output_x+=value; else output_y+=value;
    event_count++; return 0;
}
"""
test = r"""
static struct behavior_input_two_axis_data data;
static const struct behavior_input_two_axis_config config={
    .x_code=INPUT_REL_HWHEEL,.y_code=INPUT_REL_WHEEL,.delay_ms=0,
    .time_to_max_speed_ms=0,.trigger_period_ms=10,.acceleration_exponent=0,
};
static struct device dev={.data=&data,.config=&config};
static void start(void) {
    data=(struct behavior_input_two_axis_data){0}; selected_dev=&dev;
    ticks=1; output_x=output_y=event_count=0;
    assert(behavior_input_two_axis_init(&dev)==0);
}
static void tick(unsigned n) {
    for(unsigned i=0;i<n;i++) {
        ticks+=10; data.tick_work.scheduled=false;
        tick_work_cb(&data.tick_work.work);
    }
}
static struct zmk_behavior_binding binding(unsigned direction) {
    return (struct zmk_behavior_binding){.behavior_dev="stick_scroll",.param1=params[direction]};
}
static void press(unsigned i) {
    struct zmk_behavior_binding b=binding(i);
    assert(behavior_input_two_axis_driver_api.binding_pressed(&b,(struct zmk_behavior_binding_event){0})==0);
}
static void release(unsigned i) {
    struct zmk_behavior_binding b=binding(i);
    assert(behavior_input_two_axis_driver_api.binding_released(&b,(struct zmk_behavior_binding_event){0})==0);
}
int main(void) {
    for(unsigned dir=0;dir<4;dir++) {
        start(); press(dir);
        assert(data.tick_work.scheduled && data.tick_work.delay==10);
        tick(100);
        int want_x=dir==1?-scroll_speed:(dir==3?scroll_speed:0);
        int want_y=dir==0?-scroll_speed:(dir==2?scroll_speed:0);
        assert(output_x==want_x && output_y==want_y && event_count>1);
        int x=output_x,y=output_y;
        tick(100); assert(output_x==2*x && output_y==2*y); /* sustained, no ramp */
        release(dir); assert(!data.tick_work.scheduled && !data.state.x.speed && !data.state.y.speed);
        int count=event_count; tick(100);
        assert(event_count==count && output_x==2*x && output_y==2*y);
    }
    start(); press(0); press(3); tick(100); /* Up+Left: down+right wheel signs */
    assert(output_x==scroll_speed && output_y==-scroll_speed);
    release(0); assert(data.tick_work.scheduled); tick(100);
    assert(output_x==2*scroll_speed && output_y==-scroll_speed);
    release(3); assert(!data.tick_work.scheduled);
    start(); press(0); press(2); tick(100); /* opposing directions cancel */
    assert(!event_count && !data.tick_work.scheduled);
    release(0); tick(100);
    assert(output_y==scroll_speed && output_x==0);
    release(2); assert(!data.tick_work.scheduled);
    puts("PASS: actual pinned ZMK scroll press/hold/release, all four signs, constant repeat, diagonals, one-axis release, opposing cancellation");
}
"""
constants = "static const uint32_t params[]={"+",".join(f"{p}U" for p in params)+"};\n"
constants += f"static const int scroll_speed={speed};\n"
with tempfile.TemporaryDirectory(prefix="fourway-scroll-test-") as directory:
    folder = Path(directory)
    c = folder/"test.c"
    c.write_text(prelude + constants + source + test)
    binary = folder/"test"
    subprocess.run(["cc","-std=c11","-Wall","-Wextra","-Werror",
                    "-Wno-unused-parameter","-Wno-unused-but-set-variable",
                    "-fsanitize=address,undefined",str(c),"-lm","-o",str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
