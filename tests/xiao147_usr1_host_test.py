"""Resolve real Seeed aliases; actual pinned gpio-keys polling -> actual UI callback.

Seeed BSP 5948eafd5a6f93dbb0fe100b0c697f9f27c95a33, not stale .cpp comments.
Zephyr 10ba6d0cb38b input_gpio_keys.c (Apache-2.0), unchanged fixture.
These mocks test wiring/config and software, not the user's physical switch.
"""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
header = (root / "tests/fixtures/seeed_plus_variant.h").read_text()
cpp = (root / "tests/fixtures/seeed_plus_variant.cpp").read_text()
cpp = re.sub(r"//[^\n]*", "", cpp)
array = re.search(r"g_ADigitalPinMap\[\]\s*=\s*\{(.*?)\}", cpp, re.S)[1]
pins = list(map(int, re.findall(r"\d+", array)))
aliases = {name: int(index) for name, index in re.findall(r"uint8_t\s+(D\d+)\s*=\s*(\d+)\s*;", header)}
assert len(pins) == 39
resolved = {name: divmod(pins[aliases[name]], 32) for name in ("D15", "D17", "D18", "D19")}
assert resolved == {"D15": (0, 10), "D17": (1, 3), "D18": (1, 5), "D19": (1, 7)}
overlay = (root / "boards/shields/xiao_plus_147_touch/xiao_plus_147_touch.overlay").read_text()
reset = tuple(map(int, re.search(r"reset-gpios\s*=\s*<&gpio(\d+)\s+(\d+)\s+GPIO_ACTIVE_LOW>", overlay).groups()))
button = overlay[overlay.index("xiao_touch_left_button:"):overlay.index("xiao_touch_menu_button:")]
usr1 = tuple(map(int, re.search(r"gpios\s*=\s*<&gpio(\d+)\s+(\d+)\s+\(GPIO_ACTIVE_LOW\s*\|\s*GPIO_PULL_UP\)>", button).groups()))
assert usr1 == resolved["D19"] and reset == resolved["D17"] and usr1 != reset
assert 'compatible = "gpio-keys"' in button and "polling-mode;" in button
assert "debounce-interval-ms = <20>" in button
assert "gpios = <&gpio0 10 (GPIO_ACTIVE_LOW | GPIO_PULL_UP)>" in overlay

source = (root / "tests/fixtures/zephyr_input_gpio_keys.c").read_text()
source = re.sub(r"^#include .*$", "", source, flags=re.M)
ui = (root / "src/xiao147_ui.c").read_text()
def function(signature):
    start = ui.index(signature + "\n{")
    return ui[start:ui.index("\n}\n", start)+3]
callbacks = function("void xiao147_ui_toggle_imu(void)")
callbacks += function("static void imu_button_input(struct input_event *evt, void *user_data)")
assert "INPUT_CALLBACK_DEFINE(DEVICE_DT_GET(DT_NODELABEL(xiao_touch_left_button)), imu_button_input, NULL);" in ui
assert "zephyr,code = <INPUT_BTN_0>;" in button

prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#define LOG_MODULE_REGISTER(...)
#define LOG_DBG(...) ((void)0)
#define LOG_INF(...) ((void)0)
#define LOG_ERR(...) ((void)0)
#define __maybe_unused __attribute__((unused))
#define DT_INST_FOREACH_STATUS_OKAY(x)
#define CONTAINER_OF(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define BIT(x) (1U<<(x))
#define ARG_UNUSED(x) ((void)(x))
#define K_FOREVER -1
#define K_MSEC(x) (x)
#define GPIO_INPUT 1
#define GPIO_PULL_UP 16
#define GPIO_ACTIVE_LOW 32
#define GPIO_INT_EDGE_BOTH 3
#define INPUT_EV_KEY 1
#define INPUT_BTN_0 256
struct device {const char *name;const void *config;void *data;};
struct gpio_callback {int unused;};
struct gpio_dt_spec {const struct device *port;unsigned pin,dt_flags;};
struct k_work {int unused;};
typedef void (*k_work_handler_t)(struct k_work *);
struct k_work_delayable {struct k_work work;k_work_handler_t handler;bool pending;uint32_t due;};
struct input_event {const struct device *dev;uint8_t type;uint16_t code;int value;bool sync;};
typedef int atomic_val_t;
static int imu_mode,auto_rotate_mode,restore_landscape,sleeping;
static uint32_t now,last_activity_ms;
static struct k_work redraw_work;
static int gpio_raw[32],read_error,configured,configured_pin,submitted,events;
static int atomic_get(int *p) {return *p;}
static int atomic_set(int *p,int v) {int old=*p;*p=v;return old;}
static bool atomic_cas(int *p,int old,int v) {if(*p!=old)return false;*p=v;return true;}
static uint32_t k_uptime_get_32(void) {return now;}
static bool zmk_display_is_initialized(void) {return true;}
static void *zmk_display_work_q(void) {return &redraw_work;}
static void k_work_submit_to_queue(void *q,struct k_work *w) {assert(q==&redraw_work&&w==&redraw_work);submitted++;}
static struct k_work_delayable *k_work_delayable_from_work(struct k_work *w) {return (void *)w;}
static void k_work_init_delayable(struct k_work_delayable *w,k_work_handler_t h) {w->handler=h;w->pending=false;}
static int k_work_reschedule(struct k_work_delayable *w,int interval) {assert(interval==20);w->pending=true;w->due=now+interval;return 0;}
static bool gpio_is_ready_dt(const struct gpio_dt_spec *g) {return g->port!=NULL;}
static int gpio_pin_configure_dt(const struct gpio_dt_spec *g,int mode) {
    assert(g->pin==7&&mode==GPIO_INPUT&&g->dt_flags==(GPIO_ACTIVE_LOW|GPIO_PULL_UP));
    configured++;configured_pin=g->pin;return 0;
}
static int gpio_pin_get_dt(const struct gpio_dt_spec *g) {
    if(read_error)return read_error;
    return gpio_raw[g->pin]^!!(g->dt_flags&GPIO_ACTIVE_LOW);
}
static void gpio_init_callback(struct gpio_callback *cb,
    void(*h)(const struct device *,struct gpio_callback *,uint32_t),unsigned mask) {(void)cb;(void)h;(void)mask;}
static int gpio_add_callback(const struct device *d,struct gpio_callback *cb) {(void)d;(void)cb;assert(false);return 0;}
static int gpio_pin_interrupt_configure_dt(const struct gpio_dt_spec *g,int mode) {(void)g;(void)mode;assert(false);return 0;}
static int pm_device_runtime_enable(const struct device *d) {(void)d;return 0;}
static void imu_button_input(struct input_event *evt,void *data);
static int input_report_key(const struct device *dev,int code,int value,bool sync,int wait) {
    assert(code==INPUT_BTN_0&&sync&&wait==K_FOREVER&&dev->config&&
           strcmp(dev->name,"xiao_touch_left_button")==0);events++;
    struct input_event e={dev,INPUT_EV_KEY,code,value,sync};imu_button_input(&e,NULL);return 0;
}
'''
test = r'''
static void run_poll(struct gpio_keys_pin_data *d) {
    assert(d->work.pending);now=d->work.due;d->work.pending=false;
    d->work.handler(&d->work.work);assert(d->work.pending&&d->work.due==now+20);
}
int main(void) {
    struct device port={.name="gpio1"};
    const struct gpio_keys_pin_config pin={.spec={&port,7,GPIO_ACTIVE_LOW|GPIO_PULL_UP},.zephyr_code=INPUT_BTN_0};
    struct gpio_keys_pin_data state={0};struct gpio_keys_data runtime={};
    const struct gpio_keys_config cfg={.debounce_interval_ms=20,.num_keys=1,.pin_cfg=&pin,
        .pin_data=&state,.handler=gpio_keys_poll_pins,.polling_mode=true};
    struct device key={"xiao_touch_left_button",&cfg,&runtime};
    gpio_raw[3]=gpio_raw[7]=1;
    assert(gpio_keys_init(&key)==0&&configured==1&&configured_pin==7);
    run_poll(&state);assert(!events&&!imu_mode);
    /* Reset transitions must never be mistaken for USR1. */
    gpio_raw[3]=0;run_poll(&state);gpio_raw[3]=1;run_poll(&state);assert(!events);
    /* Short bounce entirely between 20ms samples is not a press. */
    gpio_raw[7]=0;now+=3;gpio_raw[7]=1;now+=3;gpio_raw[7]=0;now+=3;gpio_raw[7]=1;
    run_poll(&state);assert(!events);
    auto_rotate_mode=1;sleeping=1;gpio_raw[7]=0;run_poll(&state);
    assert(events==1&&imu_mode==1&&!auto_rotate_mode&&!sleeping&&submitted==1);
    for(int i=0;i<150;i++){run_poll(&state);}assert(events==1&&submitted==1);
    gpio_raw[7]=1;run_poll(&state);assert(events==2&&imu_mode==1&&submitted==1);
    read_error=-EIO;gpio_raw[7]=0;run_poll(&state);run_poll(&state);assert(events==2&&imu_mode==1);
    read_error=0;run_poll(&state);assert(events==3&&!imu_mode&&restore_landscape&&submitted==2);
    gpio_raw[7]=1;run_poll(&state);assert(events==4&&!imu_mode);
    /* Repeated fresh boot with key already held: one press, no repeats. */
    memset(&state,0,sizeof(state));imu_mode=events=submitted=0;gpio_raw[7]=0;
    assert(gpio_keys_init(&key)==0);run_poll(&state);assert(events==1&&imu_mode==1);
    for(int i=0;i<100;i++){run_poll(&state);}assert(events==1&&submitted==1);
    gpio_raw[7]=1;run_poll(&state);assert(events==2&&imu_mode==1);
    gpio_raw[7]=0;run_poll(&state);assert(events==3&&!imu_mode&&submitted==2);
    puts("PASS: corrected D17/D19 aliases, separate reset/input owners, actual gpio-keys polling -> real USR1 callback, held/release/repress/errors/boot-held");
}
'''
with tempfile.TemporaryDirectory(prefix="xiao147-usr1-") as directory:
    c = Path(directory) / "test.c"
    c.write_text(prelude+source+callbacks+test)
    binary = Path(directory) / "test"
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", "-fsanitize=address,undefined",
                    str(c), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
