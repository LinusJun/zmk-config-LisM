"""Execute production framing/link C with pinned Zephyr ring buffer and CRC.
Fake clocks, transport sink, mutexes and scheduling do not model RF hardware.
"""
from pathlib import Path
import argparse,re,subprocess,tempfile
ROOT=Path(__file__).resolve().parents[1]
a=argparse.ArgumentParser();a.add_argument('--workspace',type=Path,required=True);args=a.parse_args();W=args.workspace
def c(path):return re.sub(r'^\s*#\s*(?:include|pragma once).*$', '',Path(path).read_text(),flags=re.M)
pre=r'''
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>
#include <assert.h>
#include <stdio.h>
#define __packed __attribute__((packed))
#define __noinit
#define BUILD_ASSERT(x,...) _Static_assert(x,#x)
#define ARG_UNUSED(x) (void)(x)
#define __ASSERT(x,...) assert(x)
#define __ASSERT_NO_MSG(x) assert(x)
#define unlikely(x) (x)
#define MIN(a,b) ((a)<(b)?(a):(b))
#define BIT(n) (1UL<<(n))
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define IS_ENABLED(x) (x)
#define CONFIG_ZMK_SPLIT_ROLE_CENTRAL 1
#define CONFIG_ESB_MAX_PAYLOAD_LENGTH 64
#define CONFIG_ZMK_SPLIT_ESB_PERIPHERAL_ID 0
#define CONFIG_ZMK_SPLIT_ESB_MSG_POSTFIX_CRC 1
#define INPUT_EV_KEY 1
#define INPUT_EV_REL 2
#define INPUT_BTN_LEFT 272
#define INPUT_REL_X 0
#define INPUT_REL_Y 1
#define INPUT_REL_WHEEL 8
#define INPUT_REL_HWHEEL 6
#define ZMK_KEYMAP_HAS_SENSORS 0
#define ZMK_KEYMAP_SENSORS_LEN 0
#define LOG_MODULE_REGISTER(...)
#define LOG_INF(...)
#define K_FOREVER 0
#define K_MSEC(x) (x)
struct k_mutex {int count;};
#define K_MUTEX_DEFINE(n) struct k_mutex n
static int k_mutex_lock(struct k_mutex *m,int timeout) {m->count++;return 0;}
static int k_mutex_unlock(struct k_mutex *m) {assert(m->count>0);m->count--;return 0;}
struct k_work {int unused;};
struct k_work_delayable {int unused;};
#define K_WORK_DELAYABLE_DEFINE(n,f) struct k_work_delayable n
static int k_work_reschedule(struct k_work_delayable *w,int delay) {return 0;}
#define SYS_INIT(...)
typedef int atomic_t;
static int atomic_set(atomic_t *p,int x) {int old=*p;*p=x;return old;}
static int64_t now;
static int64_t k_uptime_get(void) {return now;}
typedef uint8_t zmk_hid_indicators_t;
struct zmk_sensor_channel_data {int32_t v1,v2;uint32_t channel;} __packed;
uint32_t crc32_ieee_update(uint32_t,const uint8_t*,size_t);
'''
sink=r'''
struct zmk_split_transport_central {int unused;};
const struct zmk_split_transport_central esb_central={0};
struct zmk_split_central_status_changed {uint8_t slot;bool connected;};
static unsigned status_count,emitted;
static uint64_t actual_keys[2];
static uint8_t actual_buttons[2];
static uint8_t actual_battery[2];
static int forced_return;
static int raise_zmk_split_central_status_changed(struct zmk_split_central_status_changed e) {status_count++;return 0;}
static int zmk_split_transport_central_peripheral_event_handler(const struct zmk_split_transport_central *t,uint8_t source,struct zmk_split_transport_peripheral_event e) {
 assert(source<2);if(forced_return==-EINVAL)return forced_return;emitted++;
 if(e.type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT) {
  assert(e.data.key_position_event.position<46);uint64_t m=UINT64_C(1)<<e.data.key_position_event.position;
  if(e.data.key_position_event.pressed)actual_keys[source]|=m;else actual_keys[source]&=~m;
 }
 if(e.type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT && e.data.input_event.type==INPUT_EV_KEY) {
  assert(e.data.input_event.reg==source);unsigned m=BIT(e.data.input_event.code-INPUT_BTN_LEFT);
  if(e.data.input_event.value)actual_buttons[source]|=m;else actual_buttons[source]&=~m;
 }
 if(e.type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT)actual_battery[source]=e.data.battery_event.level;
 return e.type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT?forced_return:0;
}
bool lism_radio_ready(void) {return true;}
void lism_notify_status(void) {}
static unsigned sent,allow=1000;
static uint8_t sent_data[100][64],sent_pipe[100];static unsigned sent_len[100];
int zmk_split_esb_send(app_esb_data_t *p) {
 if(sent>=allow)return -EAGAIN;
 assert(p->pipe<2 && p->len<=52);assert(sent<100);
 memcpy(sent_data[sent],p->data,p->len);sent_pipe[sent]=p->pipe;sent_len[sent++]=p->len;return 0;
}
'''
main=r'''
static size_t frame(uint8_t *out,uint8_t source,int value) {
 struct esb_event_envelope e={.prefix={.magic_prefix={'Z','m','K','e'},.payload_size=1+sizeof(e.payload.event.type)+sizeof(e.payload.event.data.key_position_event)},.payload={.source=source,.event={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,.data.key_position_event={.position=value,.pressed=1}}}};
 size_t n=sizeof(e.prefix)+e.prefix.payload_size;memcpy(out,&e,n);
 struct esb_msg_postfix p={.crc=crc32_ieee(out,n)};memcpy(out+n,&p,sizeof(p));return n+sizeof(p);
}
int main(void) {
 RING_BUF_DECLARE(tx,4096);struct zmk_split_esb_state state={.tx_pipe=1,.tx_buf=&tx};
 uint8_t bytes[128];size_t n=0;
 for(int i=0;i<20;i++) {
  n=frame(bytes,i%2,i);struct esb_msg_meta m={.msg_id=i+1,.max_retry=3};
  ring_buf_put(&tx,bytes,n);ring_buf_put(&tx,(void *)&m,sizeof(m));
 }
 assert(ring_buf_size_get(&tx)>64);allow=2;zmk_split_esb_tx(&state);
 assert(sent==2 && ring_buf_size_get(&tx)==18*(n+sizeof(struct esb_msg_meta)));
 allow=100;zmk_split_esb_tx(&state);assert(sent==20 && ring_buf_is_empty(&tx));
 for(int i=0;i<20;i++){struct esb_event_envelope e;memcpy(&e,sent_data[i],sent_len[i]-4);assert(e.payload.event.data.key_position_event.position==i);assert(sent_pipe[i]==i%2);}
 RING_BUF_DECLARE(rx,512);struct esb_event_envelope out;
 n=frame(bytes,1,45);ring_buf_put(&rx,bytes,n-1);
 assert(zmk_split_esb_get_item(&rx,(void *)&out,sizeof(out))==-EAGAIN);
 ring_buf_put(&rx,bytes+n-1,1);assert(!zmk_split_esb_get_item(&rx,(void *)&out,sizeof(out)));
 assert(lism_valid_event(1,&out));assert(!lism_valid_event(0,&out));assert(!lism_valid_event(8,&out));
 out.prefix.payload_size--;assert(!lism_valid_event(1,&out));
 n=frame(bytes,1,46);ring_buf_put(&rx,bytes,n);assert(!zmk_split_esb_get_item(&rx,(void *)&out,sizeof(out)));assert(!lism_valid_event(1,&out));
 n=frame(bytes,1,2);bytes[n-1]^=1;ring_buf_put(&rx,bytes,n);assert(zmk_split_esb_get_item(&rx,(void *)&out,sizeof(out))==-EINVAL);
 struct zmk_split_transport_peripheral_event snap={.type=LISM_SNAPSHOT_TYPE};
 struct zmk_split_transport_peripheral_event early={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,.data.key_position_event={.position=4,.pressed=1}};
 now=1;assert(!lism_handle_event(0,&early));assert(online[0] && actual_keys[0]);
 now=1601;tick(NULL);assert(!online[0] && !actual_keys[0]);
 struct lism_snapshot l={.keys=UINT64_C(1)<<45,.battery=87},r={.keys=UINT64_C(1)<<7,.buttons=1,.battery=52};
 memcpy(&snap.data,&l,sizeof(l));now=100;assert(!lism_handle_event(0,&snap));
 memcpy(&snap.data,&r,sizeof(r));now=200;assert(!lism_handle_event(1,&snap));
 assert(actual_keys[0]==l.keys && actual_keys[1]==r.keys && actual_buttons[1]==1);
 assert(actual_battery[0]==87 && actual_battery[1]==52);
 uint8_t ids[2];assert(split_central_esb_get_available_source_ids(ids)==2 && ids[0]==0 && ids[1]==1);
 /* Heartbeats preserve a long hold. Only the right source is allowed to expire. */
 for(now=300;now<=1700;now+=200) {memcpy(&snap.data,&l,sizeof(l));lism_handle_event(0,&snap);tick(NULL);}
 assert(lism_connected(0) && !lism_connected(1));
 assert(actual_keys[0]==l.keys && !actual_keys[1] && !actual_buttons[1]);
 assert(actual_battery[1]==52);
 assert(split_central_esb_get_available_source_ids(ids)==1 && ids[0]==0);
 /* Reconnect restores a still-held key/button; the next snapshot repairs a lost release. */
 memcpy(&snap.data,&r,sizeof(r));assert(!lism_handle_event(1,&snap));assert(actual_buttons[1]==1);
 memset(&snap.data,0,sizeof(snap.data));assert(!lism_handle_event(1,&snap));
 assert(!actual_keys[1] && !actual_buttons[1]);
 /* Bad bitmap must not refresh the lease. */
 r.keys=UINT64_C(1)<<63;memcpy(&snap.data,&r,sizeof(r));int64_t old=seen[1];now+=100;
 assert(lism_handle_event(1,&snap)==-EINVAL && seen[1]==old);
 /* A valid output-behavior release can be consumed with -ENOTSUP. */
 r.keys=3;r.buttons=0;forced_return=-ENOTSUP;memcpy(&snap.data,&r,sizeof(r));
 assert(!lism_handle_event(1,&snap));assert(actual_keys[1]==3);
 memset(&snap.data,0,sizeof(snap.data));assert(!lism_handle_event(1,&snap));assert(!held[1].keys);
 /* Inactive transport errors must not be accepted or renew the lease. */
 forced_return=-EINVAL;r.keys=1;memcpy(&snap.data,&r,sizeof(r));old=seen[1];now+=100;
 assert(lism_handle_event(1,&snap)==-EINVAL && !held[1].keys && seen[1]==old);
 puts("PASS: real Zephyr ring/CRC, queued bursts, retained backpressure, partial/corrupt frames, exact source/length, 46 keys, independent leases, long holds, reconnect, consumed -ENOTSUP key releases and button-release healing");
}
'''
driver=W/'module-dongle/src/split/esb'
central=(driver/'central.c').read_text();begin=central.index('static int split_central_esb_get_available_source_ids(');end=central.index('\n}',begin)+2
parts=[pre,c(W/'zephyr/include/zephyr/sys/ring_buffer.h'),c(W/'zephyr/lib/utils/ring_buffer.c'),c(W/'zephyr/lib/crc/crc32_sw.c'),c(W/'zmk/app/include/zmk/split/transport/types.h'),c(driver/'app_esb.h'),c(driver/'common.h'),c(ROOT/'src/esb_upstream/link.h'),sink,c(ROOT/'src/esb_upstream/common.c'),c(ROOT/'src/esb_upstream/link.c'),central[begin:end],main]
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.c').write_text('\n'.join(parts))
 subprocess.run(['cc','-std=c11','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-Werror=implicit-function-declaration',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
