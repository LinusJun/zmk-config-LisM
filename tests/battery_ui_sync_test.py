"""Exercise the production display refresh helper, without modeling RF or ADC."""
from pathlib import Path
import argparse, hashlib, json, re, subprocess, tempfile

P = Path(__file__).resolve().parents[2]
a = argparse.ArgumentParser()
a.add_argument('--workspace', type=Path, required=True)
W = a.parse_args().workspace
helper = P / 'source/src/esb_upstream/battery_ui_sync.inc'
generated = W / 'display-dongle/boards/shields/prospector_adapter/src/layouts/operator/battery_circles.c'
text = generated.read_text()
assert text.count(helper.read_text()) == 1
init = text[text.index('int zmk_widget_battery_circles_init('):]
assert init.index('widget->initialized = true;') < init.index('sys_slist_append(&widgets, &widget->node);') < init.index('lism_esb_battery_sync_start();')
# All original widget layout and event code must be unchanged.
old = P / 'reference_v32_battery_circles.c'
restored = text.replace(helper.read_text() + '\n', '', 1).replace('\n    lism_esb_battery_sync_start();', '', 1)
assert restored == old.read_text()

body = re.sub(r'^#include.*$', '', helper.read_text(), flags=re.M)
pre = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stddef.h>
#define PERIPHERAL_COUNT 2
#define BUILD_ASSERT(c,m) _Static_assert(c,m)
#define LOG_MODULE_DECLARE(...)
#define LOG_INF(...) do {} while (0)
#define LOG_WRN(...) do {} while (0)
#define ARG_UNUSED(x) (void)(x)
typedef struct { int unused; } lv_timer_t;
static lv_timer_t timer;
static void (*timer_cb)(lv_timer_t *);
static int created, period, locks, paints[2], errors[2];
static bool cache_connected[2], peripheral_connected[2];
static uint8_t cache_battery[2], peripheral_battery[2], painted_battery[2];
static bool painted_connected[2];
static int64_t now;
static int64_t k_uptime_get(void) { return now; }
static int zmk_split_central_get_peripheral_battery_level(unsigned s,uint8_t *v) {
    assert(s<2); locks++; if(!errors[s]) *v=cache_battery[s]; locks--; return errors[s];
}
bool lism_connected(unsigned s) {
    assert(s<2); locks++; bool v=cache_connected[s]; locks--; return v;
}
static void update_peripheral_display(unsigned s) {
    assert(!locks); paints[s]++; painted_battery[s]=peripheral_battery[s];
    painted_connected[s]=peripheral_connected[s];
}
static lv_timer_t *lv_timer_create(void (*cb)(lv_timer_t *),int ms,void *user) {
    assert(user==NULL); created++; period=ms; timer_cb=cb; return &timer;
}
'''
main = r'''
static void tick(void) { now+=250; timer_cb(&timer); }
int main(void) {
    // Both early events dropped before widget initialization.
    cache_battery[0]=61; cache_battery[1]=83;
    cache_connected[0]=cache_connected[1]=true;
    lism_esb_battery_sync_start();
    assert(created==1 && period==250 && paints[0]==1 && paints[1]==1);
    assert(painted_battery[0]==61 && painted_battery[1]==83);
    assert(painted_connected[0] && painted_connected[1]);
    tick(); assert(paints[0]==1 && paints[1]==1); // No animation restart.
    // Two source events coalesced, only right UI got the latest value.
    cache_battery[0]=62; cache_battery[1]=84; peripheral_battery[1]=84;
    tick(); assert(paints[0]==2 && paints[1]==1 && painted_battery[0]==62);
    // Stale native display work after a poll must be repaired next tick.
    peripheral_battery[0]=7; peripheral_connected[1]=false;
    tick(); assert(peripheral_battery[0]==62 && peripheral_connected[1]);
    assert(paints[0]==3 && paints[1]==2);
    cache_connected[1]=false; tick();
    assert(!painted_connected[1] && painted_battery[1]==84);
    assert(paints[0]==3 && paints[1]==3);
    cache_connected[1]=true; tick();
    assert(painted_connected[1] && painted_battery[1]==84 && paints[1]==4);
    // Successful zero is authoritative; read errors are not zero.
    cache_battery[0]=0; cache_battery[1]=0; errors[1]=-5; tick();
    assert(peripheral_battery[0]==0 && painted_battery[0]==0);
    assert(peripheral_battery[1]==84 && paints[1]==4);
    errors[1]=0; tick(); assert(peripheral_battery[1]==0 && paints[1]==5);
    // Recreated widget needs initial painting even with unchanged cache.
    int l=paints[0],r=paints[1]; lism_esb_battery_sync_start();
    assert(created==1 && paints[0]==l+1 && paints[1]==r+1);
    tick(); assert(paints[0]==l+1 && paints[1]==r+1);
    assert(!locks); puts("PASS: early/coalesced/stale events, reconnect, zero/error, initial draw, unchanged polls");
}
'''
with tempfile.TemporaryDirectory() as d:
    c = Path(d)/'ui.c'; exe=Path(d)/'ui'; c.write_text(pre+body+main)
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(c),'-o',str(exe)],check=True)
    r=subprocess.run([str(exe)],capture_output=True,text=True,check=True)
report={'passed':True,'actual_production_helper_compiled':True,'generated_widget_integration_verified':True,
        'original_layout_and_event_code_unchanged':old.exists(),'sanitizers':['address','undefined'],
        'helper_sha256':hashlib.sha256(helper.read_bytes()).hexdigest(),'stdout':r.stdout,
        'scope':'UI helper with mocked authoritative getters; no RF, ADC, LVGL rendering or hardware emulation'}
(W/'battery-ui-sync-test.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
