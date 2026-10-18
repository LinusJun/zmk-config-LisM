"""Run the actual peripheral TX/session code with fake queues and BLE I/O."""
from pathlib import Path
import argparse
import os
import subprocess
import tempfile
from lism_esb_transport_test import COMMON, ROOT

EXTRA = r'''
#define IS_ENABLED(x) 0
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
#define MIN(a,b) ((a)<(b)?(a):(b))
#define SYS_INIT(...)
struct k_work {int unused;};
struct k_work_delayable {struct k_work work;};
#define K_WORK_DELAYABLE_DEFINE(name,fn) struct k_work_delayable name
static inline int k_work_reschedule(struct k_work_delayable *w,int delay) {(void)w;(void)delay;return 0;}
struct mock_msgq {size_t size,capacity,head,count;uint8_t data[4096];};
#define K_MSGQ_DEFINE(name,sz,n,align) struct mock_msgq name = {.size=sz,.capacity=n}
static inline void k_msgq_purge(struct mock_msgq *q) {q->head=q->count=0;}
static inline int k_msgq_put(struct mock_msgq *q,const void *data,int timeout) {
 (void)timeout;if(q->count==q->capacity)return -ENOMSG;
 memcpy(q->data+((q->head+q->count)%q->capacity)*q->size,data,q->size);q->count++;return 0;
}
static inline int k_msgq_get(struct mock_msgq *q,void *data,int timeout) {
 (void)timeout;if(!q->count)return -ENOMSG;
 memcpy(data,q->data+q->head*q->size,q->size);q->head=(q->head+1)%q->capacity;q->count--;return 0;
}
#define INPUT_EV_KEY 1
#define INPUT_EV_REL 2
#define BT_GATT_ITER_STOP 0
#define BT_GATT_ITER_CONTINUE 1
#define BT_ATT_FIRST_ATTRIBUTE_HANDLE 1
#define BT_ATT_LAST_ATTRIBUTE_HANDLE 65535
#define BT_GATT_DISCOVER_CHARACTERISTIC 1
#define BT_CONN_STATE_CONNECTED 1
#define BT_CONN_ROLE_PERIPHERAL 1
#define BT_CONN_TYPE_LE 1
#define BT_HCI_ERR_REMOTE_USER_TERM_CONN 0x13
struct bt_conn_info {int state,role;};
struct bt_gatt_discover_params {struct bt_uuid *uuid;
 uint8_t (*func)(struct bt_conn *,const struct bt_gatt_attr *,struct bt_gatt_discover_params *);
 uint16_t start_handle,end_handle;int type;};
struct bt_gatt_read_params {
 uint8_t (*func)(struct bt_conn *,uint8_t,struct bt_gatt_read_params *,const void *,uint16_t);
 int handle_count;struct {uint16_t handle,offset;} single;};
struct bt_gatt_chrc {uint16_t value_handle;};
struct bt_conn_cb {void (*disconnected)(struct bt_conn *,uint8_t);};
#define BT_CONN_CB_DEFINE(name) struct bt_conn_cb name
static inline struct bt_conn *bt_conn_ref(struct bt_conn *c) {return c;}
static inline void bt_conn_unref(struct bt_conn *c) {(void)c;}
static inline void bt_gatt_cancel(struct bt_conn *c,void *p) {(void)c;(void)p;}
static inline int bt_conn_get_info(struct bt_conn *c,struct bt_conn_info *i) {(void)c;(void)i;return -1;}
static inline int bt_gatt_read(struct bt_conn *c,struct bt_gatt_read_params *p) {(void)c;(void)p;return 0;}
static inline int bt_gatt_discover(struct bt_conn *c,struct bt_gatt_discover_params *p) {(void)c;(void)p;return 0;}
void bt_conn_foreach(int type,void (*fn)(struct bt_conn *,void *),void *data);
int bt_conn_disconnect(struct bt_conn *,uint8_t);
struct zmk_split_transport_peripheral_api {
 int (*report_event)(const struct zmk_split_transport_peripheral_event *);
 int (*set_enabled)(bool);
 struct zmk_split_transport_status (*get_status)(void);};
struct zmk_split_transport_peripheral {const struct zmk_split_transport_peripheral_api *api;};
#define ZMK_SPLIT_TRANSPORT_PERIPHERAL_REGISTER(name,api,priority) \
 struct zmk_split_transport_peripheral name = {api}
struct zmk_split_peripheral_status_changed {bool connected;};
int raise_zmk_split_peripheral_status_changed(struct zmk_split_peripheral_status_changed);
int zmk_split_transport_peripheral_command_handler(const struct zmk_split_transport_peripheral *,
 struct zmk_split_transport_central_command);
'''
TEST = r'''
#include <assert.h>
#include <stdio.h>
#include "peripheral.c"
static int64_t mock_now;
static unsigned mock_commands,mock_disconnects;
static struct bt_conn mock_conn;
static struct zmk_split_transport_status mock_ble_status(void) {
 return (struct zmk_split_transport_status){.available=true,.enabled=true};
}
static int mock_enable(bool e) {(void)e;return 0;}
static struct zmk_split_transport_peripheral_api mock_api={.get_status=mock_ble_status,.set_enabled=mock_enable};
static struct zmk_split_transport_peripheral mock_ble={.api=&mock_api};
struct zmk_split_transport_peripheral *zmk_split_transport_peripheral_bt(void) {return &mock_ble;}
int64_t k_uptime_get(void) {return mock_now;}
void bt_conn_foreach(int type,void (*fn)(struct bt_conn *,void *),void *data) {(void)type;fn(&mock_conn,data);}
int bt_conn_disconnect(struct bt_conn *c,uint8_t reason) {(void)c;(void)reason;mock_disconnects++;return 0;}
int raise_zmk_split_peripheral_status_changed(struct zmk_split_peripheral_status_changed s) {(void)s;return 0;}
int zmk_split_transport_peripheral_command_handler(const struct zmk_split_transport_peripheral *p,
 struct zmk_split_transport_central_command c) {(void)p;(void)c;mock_commands++;return 0;}
static struct esb_payload ack_for(struct lism_esb_session *s,uint32_t serial,uint32_t rx,uint32_t cmd) {
 struct lism_esb_message m={.kind=LISM_ESB_ACK,.length=9,.sequence=serial};
 lism_esb_put_u32(m.body,rx);lism_esb_put_u32(m.body+4,cmd);
 if(cmd) {struct zmk_split_transport_central_command c={
  .type=ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_HID_INDICATORS,
  .data.set_hid_indicators.indicators=1};
  int n=lism_esb_encode_command(&c,m.body+9);m.body[8]=n;m.length+=n;}
 struct esb_payload p={.pipe=s->slot};int n=lism_esb_seal(s,&m,p.data);assert(n>0);p.length=n;return p;
}
int main(void) {
 enabled=true;
 struct zmk_split_transport_peripheral_event key={
  .type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,
  .data.key_position_event={.position=5,.pressed=1}};
 assert(!report_event(&key) && !events.count && held[0]==BIT(5));
 struct lism_esb_session s={.slot=0,.id=17,.channel=37};
 for(unsigned i=0;i<16;i++)s.key[i]=i;
 start_session(&s,100);assert(waiting && !synced);
 struct lism_esb_radio_plan plan;assert(lism_esb_transport_plan(&plan,100));
 struct esb_payload first=plan.packets[0];struct lism_esb_message decoded;
 assert(!lism_esb_open(&s,first.data,first.length,&decoded));
 assert(decoded.kind==LISM_ESB_SNAPSHOT && decoded.sequence==1 && decoded.body[4]==BIT(5));
 key.data.key_position_event.position=6;mock_now=101;assert(!report_event(&key));
 assert(lism_esb_transport_plan(&plan,104));
 assert(first.length==plan.packets[0].length && !memcmp(first.data,plan.packets[0].data,first.length));
 struct esb_payload ack=ack_for(&s,10,1,1);lism_esb_transport_receive(&ack,105);
 assert(!waiting && synced && mock_commands==1 && command_ack==1);
 lism_esb_transport_receive(&ack,106);assert(mock_commands==1 && last_received==105);
 assert(lism_esb_transport_plan(&plan,109));
 assert(!lism_esb_open(&s,plan.packets[0].data,plan.packets[0].length,&decoded));
 assert(decoded.kind==LISM_ESB_EVENT && decoded.sequence==2 && lism_esb_u32(decoded.body)==1);
 struct zmk_split_transport_peripheral_event e;
 unsigned offset=4,seen=0;
 while(offset<decoded.length) {uint8_t n=decoded.body[offset++];
  assert(!lism_esb_decode_event(decoded.body+offset,n,&e));offset+=n;seen++;
  if(e.type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT)
   assert(e.data.key_position_event.position==6);}
 assert(seen==2); /* battery snapshot and queued new edge */
 lism_esb_transport_receive(&ack,110);assert(waiting); /* old ACK cannot retire sequence 2 */
 ack=ack_for(&s,11,2,0);lism_esb_transport_receive(&ack,111);assert(!waiting);
 assert(!lism_esb_transport_plan(&plan,120));
 assert(lism_esb_transport_plan(&plan,210));
 assert(!lism_esb_open(&s,plan.packets[0].data,plan.packets[0].length,&decoded));
 assert(decoded.kind==LISM_ESB_HEARTBEAT && decoded.sequence==3);
 ack=ack_for(&s,12,3,0);lism_esb_transport_receive(&ack,211);
 for(unsigned i=0;i<49;i++) {key.data.key_position_event.position=10+i;report_event(&key);}
 assert(atomic_get(&fault));lism_esb_transport_poll(212);
 assert(!online && !waiting && !events.count && mock_disconnects==1);
 s.id++;start_session(&s,300);
 ack=ack_for(&s,1,1,0);lism_esb_transport_receive(&ack,301);
 /* Consume startup battery, then queue an already expired relative event. */
 k_msgq_purge(&events);
 e=(struct zmk_split_transport_peripheral_event){
  .type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT,
  .data.input_event={.type=INPUT_EV_REL,.reg=0,.sync=1,.value=50}};
 mock_now=302;assert(!report_event(&e));
 assert(!lism_esb_transport_plan(&plan,400) && atomic_get(&fault));
 lism_esb_transport_poll(400);assert(!online && mock_disconnects==2);
 /* A short encrypted GATT read must NOT install a partial session key. */
 reading=true;reading_conn=&mock_conn;config_length=0;
 uint8_t wire[LISM_ESB_CONFIG_SIZE];assert(lism_esb_config_encode(&s,wire)==31);
 assert(read_result(&mock_conn,0,&reader,wire,20)==BT_GATT_ITER_CONTINUE);
 assert(!atomic_get(&config_pending));
 assert(read_result(&mock_conn,0,&reader,wire+20,11)==BT_GATT_ITER_CONTINUE);
 assert(!atomic_get(&config_pending));
 assert(read_result(&mock_conn,0,&reader,NULL,0)==BT_GATT_ITER_STOP);
 assert(atomic_get(&config_pending) && proposed.id==s.id);
 lism_esb_transport_poll(500);assert(online && waiting && outgoing.sequence==1);
 disconnected(&mock_conn,0);lism_esb_transport_poll(501);assert(!online && !atomic_get(&config_pending));
 puts("PASS actual ESB peripheral: held-key snapshot, immutable retry, batch TX, duplicate command ACK, stale ACK, queue overflow fence, pointer expiry, fragmented commissioning, BLE loss");
}
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tinycrypt',type=Path,required=True)
    args=parser.parse_args();tc=args.tinycrypt.resolve()/'lib'
    with tempfile.TemporaryDirectory() as tmp:
        temp=Path(tmp);(temp/'common.h').write_text(COMMON+EXTRA)
        for name in ['zephyr/kernel.h','zephyr/sys/atomic.h','zephyr/bluetooth/bluetooth.h',
            'zephyr/bluetooth/conn.h','zephyr/bluetooth/gatt.h','zephyr/bluetooth/uuid.h',
            'zephyr/logging/log.h','zephyr/dt-bindings/input/input-event-codes.h',
            'zmk/split/transport/types.h','zmk/split/transport/peripheral.h',
            'zmk/events/split_peripheral_status_changed.h','esb.h']:
            path=temp/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('#include "common.h"\n')
        source=temp/'test.c';source.write_text(TEST);binary=temp/'test'
        subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror',
            '-Wno-unused-function','-Wno-unused-variable','-Wno-misleading-indentation',
            '-fsanitize=address,undefined','-fno-omit-frame-pointer','-g',
            '-I'+str(temp),'-I'+str(ROOT/'src/esb'),'-I'+str(tc/'include'),str(source),
            str(ROOT/'src/esb/protocol.c'),str(ROOT/'src/esb/codec.c'),
            *[str(tc/'source'/x) for x in ['aes_encrypt.c','ccm_mode.c','utils.c']],
            '-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))


if __name__=='__main__':main()
