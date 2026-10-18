"""Exercise the actual central receiver/codec/CCM with a bounded fake ZMK queue.

No RF simulation: these tests verify admission, retry and cleanup policy only.
Pass --tinycrypt /path/to/tinycrypt (the pinned west module).
"""
from pathlib import Path
import argparse
import os
import subprocess
import tempfile
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

ROOT = Path(__file__).resolve().parents[1]
COMMON = r'''
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <sys/types.h>
#include <errno.h>
#define CONFIG_LISM_SPLIT_ESB_CHANNEL 37
#define CONFIG_ZMK_LOG_LEVEL 3
#define LOG_MODULE_REGISTER(...)
#define LOG_ERR(...) ((void)0)
#define LOG_WRN(...) ((void)0)
#define LOG_INF(...) ((void)0)
#define K_FOREVER 0
#define K_MUTEX_DEFINE(name) int name
#define ARG_UNUSED(x) (void)(x)
#define BIT(x) (1u << (x))
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
static inline void k_mutex_lock(int *p, int timeout) {(void)p;(void)timeout;}
static inline void k_mutex_unlock(int *p) {(void)p;}
int64_t k_uptime_get(void);
typedef int32_t atomic_t;
static inline int atomic_get(const atomic_t *p) {return *p;}
static inline int atomic_set(atomic_t *p, int v) {int old=*p;*p=v;return old;}
static inline int atomic_clear(atomic_t *p) {return atomic_set(p,0);}
typedef struct {uint8_t val[6];} bt_addr_le_t;
struct bt_conn {bt_addr_le_t addr;int security;};
static inline const bt_addr_le_t *bt_conn_get_dst(struct bt_conn *c) {return &c->addr;}
static inline int bt_conn_get_security(struct bt_conn *c) {return c->security;}
#define BT_SECURITY_L2 2
struct bt_uuid {int unused;};
struct bt_uuid_128 {struct bt_uuid uuid;};
#define BT_UUID_128_ENCODE(...) 0
#define BT_UUID_INIT_128(...) {{0}}
struct bt_gatt_attr {void *user_data;};
#define BT_GATT_SERVICE_DEFINE(...)
#define BT_GATT_ERR(x) (-(x))
#define BT_ATT_ERR_AUTHORIZATION 8
#define BT_ATT_ERR_UNLIKELY 14
ssize_t bt_gatt_attr_read(struct bt_conn *, const struct bt_gatt_attr *, void *, uint16_t,
                          uint16_t, const void *, uint16_t);
int bt_rand(void *,size_t);
struct esb_payload {uint8_t length,pipe;bool noack;uint8_t data[68];};
struct zmk_sensor_channel_data {struct {int32_t val1,val2;} value;uint32_t channel;};
enum zmk_split_transport_peripheral_event_type {
 ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,
 ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_SENSOR_EVENT,
 ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT,
 ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT};
struct zmk_split_transport_peripheral_event {int type;union {
 struct {uint8_t position,pressed;} key_position_event;
 struct {struct zmk_sensor_channel_data channel_data;uint8_t sensor_index;} sensor_event;
 struct {uint8_t reg,sync,type;uint16_t code;int32_t value;} input_event;
 struct {uint8_t level;} battery_event;} data;};
enum zmk_split_transport_central_command_type {
 ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_POLL_EVENTS,
 ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR,
 ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_PHYSICAL_LAYOUT,
 ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_HID_INDICATORS};
struct zmk_split_transport_central_command {int type;union {
 struct {char behavior_dev[16];uint32_t param1,param2,position;uint8_t event_source,state;} invoke_behavior;
 struct {uint8_t layout_idx;} set_physical_layout;
 struct {uint8_t indicators;} set_hid_indicators;} data;};
enum {ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_DISCONNECTED,
 ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_SOME_CONNECTED,
 ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_ALL_CONNECTED};
struct zmk_split_transport_status {bool available,enabled;int connections;};
struct zmk_split_transport_central_api {
 int (*send_command)(uint8_t,struct zmk_split_transport_central_command);
 int (*get_available_source_ids)(uint8_t *);
 int (*set_enabled)(bool);
 struct zmk_split_transport_status (*get_status)(void);};
struct zmk_split_transport_central {const struct zmk_split_transport_central_api *api;};
#define ZMK_SPLIT_TRANSPORT_CENTRAL_REGISTER(name,api,prio) \
 struct zmk_split_transport_central name = {api}
'''
TEST = r'''
#include <assert.h>
#include <stdio.h>
#include "central.c"
static int64_t mock_now;
static bool mock_faulted[6];
static uint32_t mock_generation[6] = {1,1,1,1,1,1};
static unsigned mock_capacity, mock_count, mock_fault_count;
static struct zmk_split_transport_peripheral_event mock_events[512];
int64_t k_uptime_get(void) {return mock_now;}
int bt_rand(void *p,size_t n) {static uint8_t v=1;for(size_t i=0;i<n;i++)((uint8_t*)p)[i]=v++;return 0;}
ssize_t bt_gatt_attr_read(struct bt_conn *c,const struct bt_gatt_attr *a,void *out,uint16_t len,
 uint16_t off,const void *data,uint16_t size) {
 (void)c;(void)a;if(off>size)return -1;size-=off;if(size>len)size=len;
 memcpy(out,(const uint8_t*)data+off,size);return size;
}
int lism_split_sets_find_addr(const bt_addr_le_t *a) {return a->val[0];}
uint32_t lism_split_sets_generation(uint8_t s) {return mock_generation[s];}
bool lism_split_sets_dispatch(uint8_t s,uint32_t g) {
 return s<2 && !mock_faulted[s] && g==mock_generation[s];
}
void lism_esb_control_start(void) {}
int lism_esb_control_sources(uint8_t *s) {(void)s;return 0;}
int lism_esb_control_command(uint8_t s,struct zmk_split_transport_central_command c) {(void)s;(void)c;return 0;}
void lism_esb_queue_fault(uint8_t s) {mock_faulted[s]=true;mock_fault_count++;}
int lism_esb_queue_event(uint8_t s,uint32_t g,const struct zmk_split_transport_peripheral_event *ev) {
 if(!lism_split_sets_dispatch(s,g))return -ESTALE;
 if(!mock_capacity)return -EAGAIN;
 mock_capacity--;assert(mock_count<512);mock_events[mock_count++]=*ev;return 0;
}
static struct esb_payload packet_for(struct lism_esb_session *s,struct lism_esb_message *m) {
 struct esb_payload p={.pipe=s->slot};int n=lism_esb_seal(s,m,p.data);assert(n>0);p.length=n;return p;
}
static void crypto_tests(void) {
 struct lism_esb_session s={.slot=0,.id=0x12345678,.channel=37};
 for(unsigned i=0;i<16;i++)s.key[i]=i;
 struct lism_esb_message m={.kind=LISM_ESB_EVENT,.length=8,.sequence=7},decoded;
 for(unsigned i=0;i<8;i++)m.body[i]=i+1;
 uint8_t reference[] = {@REFERENCE@};
 struct esb_payload p=packet_for(&s,&m);
 assert(sizeof(reference)==p.length && !memcmp(p.data,reference,p.length));
 for(unsigned slot=0;slot<6;slot++) {
  if(!lism_esb_keyboard_slot(slot))continue;s.slot=slot;
  for(unsigned n=0;n<=LISM_ESB_BODY;n++) {
   m.length=n;m.sequence++;
   for(unsigned i=0;i<n;i++)m.body[i]=(uint8_t)(i+n);
   p=packet_for(&s,&m);assert(!lism_esb_open(&s,p.data,p.length,&decoded));
   assert(decoded.length==n && !memcmp(decoded.body,m.body,n));
   for(unsigned i=0;i<p.length;i++) {
    p.data[i]^=0x80;assert(lism_esb_open(&s,p.data,p.length,&decoded)<0);p.data[i]^=0x80;
   }
   struct lism_esb_session other=s;other.key[0]^=1;
   assert(lism_esb_open(&other,p.data,p.length,&decoded)<0);
   other=s;other.id++;assert(lism_esb_open(&other,p.data,p.length,&decoded)<0);
  }
 }
 for(size_t n=0;n<LISM_ESB_HEADER+LISM_ESB_TAG;n++)assert(lism_esb_open(&s,p.data,n,&decoded)<0);
}
int main(void) {
 crypto_tests();enabled=true;
 struct bt_conn conn={.security=2};uint8_t wire[31];
 assert(read_config(&conn,NULL,wire,20,0)<0); /* not READY */
 lism_esb_peer_ready(0);conn.security=1;assert(read_config(&conn,NULL,wire,20,0)<0);
 conn.security=2;assert(read_config(&conn,NULL,wire,20,0)==20);
 assert(read_config(&conn,NULL,wire+20,11,20)==11);
 struct lism_esb_session s;assert(!lism_esb_config_decode(&s,wire,sizeof(wire)));
 struct lism_esb_message m={.kind=LISM_ESB_SNAPSHOT,.length=20,.sequence=1};
 m.body[4]=BIT(5);m.body[5]=BIT(2);m.body[6]=BIT(4);
 struct esb_payload p=packet_for(&s,&m);
 for(unsigned i=0;i<3;i++) {mock_capacity=1;lism_esb_transport_receive(&p,10+i);assert(mock_count==i+1);}
 assert(peers[0].received==1 && peers[0].synced);
 assert(mock_events[0].data.key_position_event.position==5);
 assert(mock_events[1].data.key_position_event.position==10);
 assert(mock_events[2].data.key_position_event.position==20);
 lism_esb_transport_receive(&p,999);assert(mock_count==3 && peers[0].last_seen==12);
 struct zmk_split_transport_peripheral_event event={
  .type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,
  .data.key_position_event={.position=30,.pressed=1}};
 m=(struct lism_esb_message){.kind=LISM_ESB_EVENT,.length=4,.sequence=2};
 int n=lism_esb_encode_event(&event,m.body+5);m.body[4]=n;m.length+=n+1;
 event=(struct zmk_split_transport_peripheral_event){
  .type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT,
  .data.input_event={.reg=0,.sync=1,.type=2,.code=0,.value=-12}};
 n=lism_esb_encode_event(&event,m.body+m.length+1);m.body[m.length]=n;m.length+=n+1;
 p=packet_for(&s,&m);mock_capacity=1;lism_esb_transport_receive(&p,20);
 assert(mock_count==4 && peers[0].received==1);
 mock_capacity=2;lism_esb_transport_receive(&p,21);assert(mock_count==5 && peers[0].received==2);
 assert(mock_events[4].data.input_event.value==-12);
 lism_esb_transport_receive(&p,30);assert(mock_count==5); /* lost application ACK */
 struct zmk_split_transport_central_command cmd={
  .type=ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_HID_INDICATORS,
  .data.set_hid_indicators.indicators=3};
 assert(!send_command(0,cmd));struct lism_esb_radio_plan plan;
 assert(lism_esb_transport_plan(&plan,40) && plan.count==1);
 struct lism_esb_message ack;assert(!lism_esb_open(&s,plan.packets[0].data,plan.packets[0].length,&ack));
 assert(ack.kind==LISM_ESB_ACK && lism_esb_u32(ack.body)==2 && lism_esb_u32(ack.body+4)==1);
 assert(!lism_esb_decode_command(ack.body+9,ack.body[8],&cmd));
 assert(cmd.data.set_hid_indicators.indicators==3);
 m=(struct lism_esb_message){.kind=LISM_ESB_HEARTBEAT,.length=4,.sequence=3};
 lism_esb_put_u32(m.body,1);p=packet_for(&s,&m);lism_esb_transport_receive(&p,50);
 assert(!peers[0].command_count && peers[0].received==3);
 struct esb_payload tampered=p;tampered.data[tampered.length-1]^=1;
 lism_esb_transport_receive(&tampered,100);assert(peers[0].last_seen==50);
 m.sequence=99;p=packet_for(&s,&m);lism_esb_transport_receive(&p,100);assert(peers[0].received==3);
 conn.addr.val[0]=4;lism_esb_peer_ready(4);assert(read_config(&conn,NULL,wire,31,0)<0);
 assert(!mock_fault_count);lism_esb_transport_poll(1550);
 assert(mock_fault_count==1 && !peers[0].provisioned); /* lease -> existing cleanup worker */
 lism_esb_transport_receive(&p,1600);assert(mock_count==5);
 /* A queued packet from an older BLE generation cannot revive the source. */
 conn.addr.val[0]=0;mock_faulted[0]=false;mock_generation[0]++;lism_esb_peer_ready(0);
 assert(read_config(&conn,NULL,wire,31,0)==31);
 assert(peers[0].generation==2 && peers[0].session.id!=s.id);
 lism_esb_transport_receive(&p,1601);assert(!peers[0].received && mock_count==5);
 puts("PASS actual ESB central: CCM reference/tamper, encrypted admission, partial queue retry, exactly-once batch, command ACK, stale session, A/B exclusion, watchdog");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tinycrypt', type=Path, required=True)
    args = parser.parse_args()
    tc = args.tinycrypt.resolve()/'lib'
    header = bytes([1,1,0,8,0x78,0x56,0x34,0x12,7,0,0,0])
    expected = header + AESCCM(bytes(range(16)), tag_length=8).encrypt(
        header+b'\x4c',bytes(range(1,9)),header)
    with tempfile.TemporaryDirectory() as tmp:
        temp = Path(tmp)
        (temp/'common.h').write_text(COMMON)
        for name in ['zephyr/kernel.h','zephyr/sys/atomic.h','zephyr/bluetooth/bluetooth.h',
                     'zephyr/bluetooth/conn.h','zephyr/bluetooth/gatt.h','zephyr/bluetooth/uuid.h',
                     'zephyr/bluetooth/addr.h','zmk/split/transport/central.h',
                     'zephyr/logging/log.h',
                     'zmk/split/transport/types.h','esb.h']:
            file=temp/name;file.parent.mkdir(parents=True,exist_ok=True)
            file.write_text('#include "common.h"\n')
        source=temp/'test.c'
        source.write_text(TEST.replace('@REFERENCE@',','.join(hex(x) for x in expected)))
        binary=temp/'test'
        subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror',
            '-Wno-unused-function','-Wno-unused-variable','-Wno-misleading-indentation',
            '-fsanitize=address,undefined','-fno-omit-frame-pointer','-g',
            '-I'+str(temp),'-I'+str(ROOT/'src/esb'),'-I'+str(tc/'include'),
            str(source),str(ROOT/'src/esb/protocol.c'),str(ROOT/'src/esb/codec.c'),
            *[str(tc/'source'/x) for x in ['aes_encrypt.c','ccm_mode.c','utils.c']],
            '-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))


if __name__ == '__main__':
    main()
