"""Exercise actual local mode/chord code under ASan/UBSan, without a radio.

Includes the production C source; stubs model I/O and explicit asynchronous
completion. These checks cannot validate Nordic controller timing or RF.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from prepare_standalone import generate

COMMON=r'''
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <sys/types.h>
#include <string.h>
#include <errno.h>
#include <stdio.h>
#include <assert.h>
#define ARG_UNUSED(x) (void)(x)
#define BIT(x) (1u<<(x))
#define MAX(a,b) ((a)>(b)?(a):(b))
#define K_FOREVER -1
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
#define LOG_MODULE_REGISTER(...)
#define LOG_WRN(...) ((void)0)
#define LOG_INF(...) ((void)0)
#define ZMK_LISTENER(...)
#define ZMK_SUBSCRIPTION(...)
#define ZMK_EV_EVENT_BUBBLE 0
#define SETTINGS_STATIC_HANDLER_DEFINE(...)
#define INPUT_CALLBACK_DEFINE(...)
#define ZMK_POSITION_STATE_CHANGE_SOURCE_LOCAL 255
#define ZMK_TRANSPORT_BLE 2
#define BT_CONN_STATE_DISCONNECTED 0
#define BT_HCI_ERR_REMOTE_USER_TERM_CONN 0x13
#define INPUT_EV_MSC 4
#define INPUT_EV_REL 2
#define INPUT_REL_X 0
#define INPUT_REL_Y 1
struct bt_conn {int state;};
struct bt_conn_info {int state;};
struct device {const char *name;};
struct input_event {const struct device *dev;uint8_t type;uint16_t code;int32_t value;bool sync;};
typedef int atomic_t;
static void atomic_set(atomic_t *a,int v){*a=v;}
static void atomic_clear(atomic_t *a){*a=0;}
static int atomic_get(atomic_t *a){return *a;}
struct k_mutex {int unused;};
#define K_MUTEX_DEFINE(name) struct k_mutex name
static void k_mutex_lock(struct k_mutex *m,int t){(void)m;(void)t;}
static void k_mutex_unlock(struct k_mutex *m){(void)m;}
struct k_work {void (*fn)(struct k_work *);};
struct k_work_delayable {struct k_work work;};
struct k_work_q {int unused;};
#define K_WORK_DELAYABLE_DEFINE(name,f) struct k_work_delayable name={.work={.fn=f}}
#define K_WORK_DEFINE(name,f) struct k_work name={.fn=f}
static int k_work_reschedule(struct k_work_delayable *w,int t){(void)w;(void)t;return 0;}
static int k_work_cancel_delayable(struct k_work_delayable *w){(void)w;return 0;}
static int k_work_submit(struct k_work *w){(void)w;return 0;}
static int k_work_submit_to_queue(struct k_work_q *q,struct k_work *w){(void)q;w->fn(w);return 0;}
struct zmk_position_state_changed {uint8_t source;uint32_t position;bool state;int64_t timestamp;};
typedef struct {int type;struct zmk_position_state_changed pos;} zmk_event_t;
static struct zmk_position_state_changed *as_zmk_position_state_changed(zmk_event_t *e){return e->type==1?&e->pos:NULL;}
struct zmk_battery_state_changed {uint8_t state_of_charge;};
static struct zmk_battery_state_changed *as_zmk_battery_state_changed(const zmk_event_t *e){(void)e;return NULL;}
typedef int (*settings_read_cb)(void *,void *,size_t);
#define BT_CONN_TYPE_LE 1
#define ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT 1
#define ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT 2
#define ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT 3
struct zmk_split_transport_peripheral_event {int type;union {
 struct {uint8_t position;bool pressed;} key_position_event;
 struct {uint8_t level;} battery_event;
 struct {uint8_t reg,type;uint16_t code;int32_t value;bool sync;} input_event;
} data;};
typedef struct {uint8_t type,a[6];} bt_addr_le_t;
static int64_t now;
static int64_t k_uptime_get(void){return now;}
static unsigned saves,clears,disconnections,esb_events,replays,mouse_events,marker_requests;
static bool esb_enabled,central_enabled,marker_full;
static unsigned selected_profile;
static struct zmk_position_state_changed last_replay;
static struct bt_conn connection;
static int settings_save_one(const char *n,const void *v,size_t l){(void)n;(void)v;(void)l;saves++;return 0;}
static void zmk_hid_keyboard_clear(void){clears++;}
static void zmk_hid_consumer_clear(void){}
static void zmk_hid_mouse_clear(void){}
static int zmk_ble_prof_select(unsigned i){selected_profile=i;return 0;}
static int zmk_endpoint_set_preferred_transport(int t){assert(t==2);return 0;}
static int bt_le_adv_stop(void){return 0;}
static int bt_conn_disconnect(struct bt_conn *c,uint8_t r){(void)c;(void)r;disconnections++;return 0;}
static int bt_conn_get_info(struct bt_conn *c,struct bt_conn_info *info){info->state=c->state;return 0;}
static void bt_conn_foreach(int t,void (*f)(struct bt_conn *,void *),void *d){(void)t;f(&connection,d);}
static int input_report(const struct device *d,uint8_t t,uint16_t c,int32_t v,bool s,int timeout){
 (void)d;(void)t;(void)c;(void)v;(void)s;(void)timeout;marker_requests++;return marker_full?-EAGAIN:0;}
static int zmk_input_split_report_peripheral_event(uint8_t reg,uint8_t t,uint16_t c,int32_t v,bool s){
 (void)t;(void)c;(void)v;(void)s;assert(reg==1);mouse_events++;return 0;}
'''
TEST=r'''
#include "mode.c"
struct k_work_q hog_work_q;
void lism_standalone_replay(const struct zmk_position_state_changed *p){last_replay=*p;replays++;}
int lism_standalone_esb_report(const struct zmk_split_transport_peripheral_event *e){(void)e;esb_events++;return 0;}
void lism_standalone_esb_enable(bool e){esb_enabled=e;}
void lism_standalone_central_enable(bool e){central_enabled=e;}
bool lism_standalone_central_drained(void){return true;}
void lism_standalone_host_update(void){}
void lism_standalone_link_wake(void){}
void lism_standalone_hog_purge(void){}
void lism_standalone_pointer_reset(void){}
void lism_standalone_listener_reset(void){}
void lism_standalone_note_activity(void){}
static bool companion_ready=true;
bool lism_standalone_companion_ready(void){return companion_ready;}
static void key(uint8_t source,unsigned p,bool down){
 zmk_event_t e={.type=1,.pos={.source=source,.position=p,.state=down,.timestamp=now}};
 assert(lism_standalone_event(&e));
}
static void complete(void){
 now+=310;mode_work_fn(NULL);assert(transition && fence_started);
 input_fence(NULL,NULL);mode_work_fn(NULL);assert(!transition);
}
int main(void){
 /* Early scan before settings/boot must not call uninitialized chord hooks. */
 key(255,5,true);key(255,5,false);assert(!esb_events);
 boot_fn(NULL);assert(ready && !mode && esb_enabled && !central_enabled);
 key(255,5,true);key(255,5,false);assert(esb_events==2 && !replays);
 key(0,1,true);assert(!replays);
 companion_ready=false;request(NULL,1);assert(!target);companion_ready=true;
 key(255,15,true);request(NULL,1);mode_work_fn(NULL);assert(!transition);
 key(255,15,false);mode_work_fn(NULL);assert(transition && !esb_enabled);
 unsigned old=esb_events;key(255,6,true);key(255,6,false);assert(esb_events==old);
 now+=310;marker_full=true;mode_work_fn(NULL);assert(!fence_started && !mode);
 marker_full=false;mode_work_fn(NULL);assert(fence_started && !mode);
 input_fence(NULL,NULL);mode_work_fn(NULL);
 assert(mode==1 && central_enabled && !esb_enabled && selected_profile==0 && saves==1);
 key(0,31,true);assert(last_replay.source==0 && last_replay.state);
 key(255,15,true);key(255,15,false);
 struct input_event p={.type=INPUT_EV_REL,.code=INPUT_REL_X,.value=5,.sync=true};
 lism_standalone_route_input(&p);assert(mouse_events==1);
 connection.state=1;request(NULL,4);mode_work_fn(NULL);
 assert(transition && disconnections && last_replay.position==31 && last_replay.source==0 && !last_replay.state);
 old=mouse_events;lism_standalone_route_input(&p);assert(mouse_events==old);
 now+=400;mode_work_fn(NULL);assert(transition && mode==1); /* actual disconnect barrier */
 connection.state=0;complete();assert(mode==4 && selected_profile==3);
 assert(lism_standalone_profile_select(4)==-ERANGE && target==4);
 assert(!lism_standalone_profile_select(1));mode_work_fn(NULL);complete();assert(mode==2);
 request(NULL,0);mode_work_fn(NULL);complete();assert(!mode && esb_enabled && !central_enabled);
 old=esb_events;lism_standalone_route_input(&p);assert(esb_events==old+1 && clears==4);
 puts("PASS actual right mode: early boot, physical release barrier, five targets, input queue retry/fence, BLE disconnect barrier, source-preserving release, pointer/output route isolation, profile admission");
}
'''


def main():
    app=Path(os.environ.get('LISM_ZMK_APP',ROOT.parent/'zmk-core/app'))
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);generated=tmp/'generated';generate(app,generated,ROOT)
        # Pinned generation must fail closed after upstream anchor drift.
        from prepare_standalone import replace
        try: replace('changed upstream','old anchor','new')
        except ValueError: pass
        else: raise AssertionError('unchecked upstream drift')
        source=(ROOT/'boards/shields/lism/lism_dongle.overlay').read_text()
        for line in ['#include "lism.dtsi"','#include "split_input_right.dtsi"','#include "lism-layouts.dtsi"','#include <input/processors/xyz.dtsi>']:
            source=source.replace(line,'')
        source=source.replace('#include "split_input.dtsi"','#include "../../boards/shields/lism/split_input.dtsi"')
        assert (ROOT/'experimental/standalone/right.overlay').read_text()==('/* Routing copied verbatim from lism_dongle.overlay; checked by standalone tests. */\n'+source+'\n/ {chosen {zmk,kscan = &kscan0;};};\n')
        headers=['zephyr/bluetooth/conn.h','zmk/event_manager.h','zmk/split/transport/peripheral.h',
            'zmk/events/position_state_changed.h','zephyr/input/input.h','zmk/events/battery_state_changed.h',
            'zmk/ble.h','zmk/hid.h','zmk/endpoints.h','zmk/pointing/input_split.h',
            'zephyr/bluetooth/bluetooth.h','zephyr/bluetooth/hci.h','zephyr/settings/settings.h',
            'zephyr/sys/atomic.h','zephyr/logging/log.h']
        (tmp/'common.h').write_text(COMMON)
        for name in headers:
            p=tmp/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('#include "common.h"\n')
        (tmp/'test.c').write_text(TEST)
        flags=['gcc','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable',
            '-fsanitize=address,undefined','-fno-omit-frame-pointer','-g','-I'+str(tmp),'-I'+str(ROOT/'src/standalone')]
        subprocess.run(flags+[str(tmp/'test.c'),str(ROOT/'src/standalone/chord.c'),'-o',str(tmp/'test')],check=True)
        env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0')
        subprocess.run([str(tmp/'test')],check=True,env=env)
        subprocess.run(flags+[str(ROOT/'tests/lism_mode_chord_test.c'),str(ROOT/'src/standalone/chord.c'),'-o',str(tmp/'chord')],check=True)
        subprocess.run([str(tmp/'chord')],check=True,env=env)
    print('PASS pinned source generation and exact trackball-layer routing preservation')

if __name__=='__main__':main()
