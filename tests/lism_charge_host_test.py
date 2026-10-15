"""Execute charge policy + actual asynchronous client/server C using fault injection."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = re.sub(r'^#include .*$', '', (root/'src/lism_charge.c').read_text(), flags=re.M)
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
#include "lism_charge.h"
#define IS_ENABLED(x) (x)
#define CONFIG_ZMK_SPLIT_ROLE_CENTRAL CENTRAL
#define CONFIG_LISM_XIAO147_TOUCH CENTRAL
#define INSTALLED 0 /* unified firmware cannot prove USB battery presence */
#define CONFIG_APPLICATION_INIT_PRIORITY 90
#define CONFIG_ZMK_BATTERY_REPORT_INTERVAL 60
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define ARG_UNUSED(x) (void)(x)
#define CONTAINER_OF(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define K_FOREVER -1
#define K_MSEC(x) (x)
#define K_SECONDS(x) ((x)*1000)
#define GPIO_INPUT 1
#define GPIO_PULL_UP 2
#define DT_NODELABEL(x) 0
#define DT_CHOSEN(x) 0
#define DEVICE_DT_GET(x) (&dev)
#define SYS_INIT(...)
#define K_MUTEX_DEFINE(x) static int x
static void k_mutex_lock(int *m,int t) {(void)m;(void)t;}
static void k_mutex_unlock(int *m) {(void)m;}
struct device {int unused;};static struct device dev;
struct k_work {int unused;};
struct k_work_delayable {struct k_work work;};
struct k_work_q {int unused;};
#define K_THREAD_STACK_DEFINE(n,s) static char n[s]
#define K_THREAD_STACK_SIZEOF(n) sizeof(n)
#define K_PRIO_PREEMPT(n) (n)
static void k_work_queue_start(struct k_work_q*q,char*s,unsigned size,int prio,void*cfg) {
 (void)q;(void)s;(void)cfg;assert(size==1536&&prio==10);
}
static int k_work_reschedule_for_queue(struct k_work_q*q,struct k_work_delayable*w,int t) {
 (void)q;(void)w;assert(t==1000);return 0;
}
static int k_work_schedule_for_queue(struct k_work_q*q,struct k_work_delayable*w,int t) {
 (void)q;(void)w;assert(t==3000);return 0;
}
#define K_WORK_DELAYABLE_DEFINE(n,f) static struct k_work_delayable n
static int k_work_reschedule(struct k_work_delayable *w,int t) {(void)w;(void)t;return 0;}
static int k_work_schedule(struct k_work_delayable *w,int t) {(void)w;(void)t;return 0;}
static uint32_t now;
static uint32_t k_uptime_get_32(void) {return now;}
static bool usb=true,ready=true;
static int stat=1,config_calls,voltage_mv=3800,voltage_error,battery_percent=80;
static bool device_is_ready(const struct device*d) {(void)d;return ready;}
static int gpio_pin_get_raw(const struct device*d,int p) {assert(d==&dev&&p==17);return stat;}
static int gpio_pin_configure(const struct device*d,int p,int flags) {
 assert(d==&dev&&p==17&&flags==(GPIO_INPUT|GPIO_PULL_UP));config_calls++;return 0;
}
#define NRF_POWER (&dev)
static bool nrf_power_usbregstatus_vbusdet_get(const struct device*d) {
 assert(d==&dev);return usb;
}
static uint8_t zmk_battery_state_of_charge(void) {return battery_percent;}
#define SENSOR_CHAN_GAUGE_VOLTAGE 1
struct sensor_value {int val1,val2;};
static int sensor_channel_get(const struct device*d,int c,struct sensor_value*v) {
 assert(d==&dev&&c==1);v->val1=voltage_mv/1000;v->val2=voltage_mv%1000*1000;return voltage_error;
}
struct bt_uuid {int value;}; struct bt_uuid_128 {struct bt_uuid uuid;};
#define BT_UUID_128_ENCODE(...) 1
#define BT_UUID_INIT_128(x) {{x}}
#define BT_UUID_DECLARE_128(x) (&state_uuid.uuid)
#define BT_GATT_ITER_STOP 0
#define BT_GATT_PERM_READ_ENCRYPT 1
#define BT_GATT_CHRC_READ 2
#define BT_GATT_PRIMARY_SERVICE(...) 0
#define BT_GATT_CHARACTERISTIC(uuid,prop,perm,fn,wr,ud) perm
#define BT_GATT_SERVICE_DEFINE(n,...) static const int n[]={__VA_ARGS__}
struct bt_gatt_attr {int unused;};
struct bt_conn {int slot,role,security,refs;bool connected;};
static struct bt_conn connections[6];
struct bt_conn_info {int type,state,role;};
#define BT_CONN_TYPE_LE 1
#define BT_CONN_STATE_CONNECTED 1
#define BT_CONN_ROLE_CENTRAL 0
#define BT_SECURITY_L2 2
#define XIAO147_PERIPHERAL_COUNT 4
struct bt_gatt_read_params;
typedef uint8_t (*read_fn)(struct bt_conn*,uint8_t,struct bt_gatt_read_params*,const void*,uint16_t);
struct bt_gatt_read_params {read_fn func;unsigned handle_count;
 struct {uint16_t start_handle,end_handle;const struct bt_uuid *uuid;} by_uuid;};
struct bt_conn_cb {void(*disconnected)(struct bt_conn*,uint8_t);};
#define BT_CONN_CB_DEFINE(n) static struct bt_conn_cb n
int peripheral_slot_index_for_conn(struct bt_conn*c) {return c->slot;}
static int bt_conn_get_info(struct bt_conn*c,struct bt_conn_info*i) {
 *i=(struct bt_conn_info){1,c->connected?1:0,c->role};return 0;
}
static struct bt_conn *bt_conn_ref(struct bt_conn*c) {c->refs++;return c;}
static void bt_conn_unref(struct bt_conn*c) {assert(c->refs>0);c->refs--;}
static int bt_conn_get_security(struct bt_conn*c) {return c->security;}
static void bt_conn_foreach(int t,void(*fn)(struct bt_conn*,void*),void*ctx) {
 assert(t==1);for(unsigned i=0;i<ARRAY_SIZE(connections);i++)if(connections[i].connected)fn(&connections[i],ctx);
}
static int read_error,read_calls;
static struct bt_gatt_read_params *pending[6];
static int bt_gatt_read(struct bt_conn*c,struct bt_gatt_read_params*p) {
 assert(c->security>=2&&c->role==0&&c->slot>=0&&c->slot<4);
 assert(!p->handle_count&&p->by_uuid.start_handle==1&&p->by_uuid.end_handle==65535);
 read_calls++;if(read_error)return read_error;
 assert(!pending[c-connections]);pending[c-connections]=p;return 0;
}
static ssize_t bt_gatt_attr_read(struct bt_conn*c,const struct bt_gatt_attr*a,void*b,
 uint16_t len,uint16_t off,const void*data,uint16_t size) {
 (void)c;(void)a;if(off>size)return -1;if(len>size-off)len=size-off;
 memcpy(b,(const uint8_t*)data+off,len);return len;
}
'''
tests = r'''
static void battery_fetch(int result) {
 lism_charge_begin_battery_sample();lism_charge_note_battery_sample(result);
}
static void power_transition_tests(void) {
 now=20000;usb=true;stat=0;voltage_error=0;voltage_mv=3800;
 battery_fetch(0);
 for(unsigned i=0;i<4;i++){sample_charge(NULL);now+=250;}
 assert((bool)lism_charge_show_bolt(lism_charge_local())==(bool)INSTALLED);
 /* A read must suppress stale charging before the sampler gets CPU time. */
 usb=false;struct lism_charge_state s=lism_charge_local();
 assert(!(s.flags&(LISM_CHARGE_USB|LISM_CHARGE_STAT_ACTIVE|LISM_CHARGE_LEVEL_VALID)));
 sample_charge(NULL);battery_fetch(0);sample_charge(NULL);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 now+=999;battery_fetch(0);sample_charge(NULL);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 now++;battery_fetch(0);sample_charge(NULL);
 assert(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID);
 assert(!lism_charge_show_bolt(lism_charge_local()));
 /* The battery-only ADC result must not be reused after USB insertion. */
 usb=true;s=lism_charge_local();
 assert((s.flags&LISM_CHARGE_USB)&&!(s.flags&LISM_CHARGE_LEVEL_VALID));
 now+=1001;sample_charge(NULL);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 battery_fetch(0);
 for(unsigned i=0;i<4;i++){sample_charge(NULL);now+=250;}
 assert((bool)lism_charge_show_bolt(lism_charge_local())==(bool)INSTALLED);
 /* Unplug/replug observed entirely between ADC fetches still invalidates. */
 usb=false;(void)lism_charge_local();usb=true;(void)lism_charge_local();
 now+=1001;sample_charge(NULL);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 /* 100% is only an estimate: it neither changes pins nor overrides STAT. */
 battery_percent=100;battery_fetch(0);
 for(unsigned i=0;i<4;i++){sample_charge(NULL);now+=250;}
 assert((bool)lism_charge_show_bolt(lism_charge_local())==(bool)INSTALLED);
 stat=1;sample_charge(NULL);assert(!lism_charge_show_bolt(lism_charge_local()));
 assert(config_calls==CENTRAL); /* no runtime charger-control writes */
 /* Bad voltage is unknown, not an automatic 'full' or 'battery inserted'. */
 stat=0;voltage_mv=4351;battery_fetch(0);sample_charge(NULL);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 voltage_mv=1600;battery_fetch(0);sample_charge(NULL);
 assert(!lism_charge_show_bolt(lism_charge_local()));
 voltage_mv=3800;battery_percent=80;
 /* Old-source fetch completes after a long preemption and must stay invalid. */
 lism_charge_begin_battery_sample();usb=false;(void)lism_charge_local();
 now+=2000;lism_charge_note_battery_sample(0);sample_charge(NULL);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 battery_fetch(0);sample_charge(NULL);
 assert(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID);
 /* A fetch started during settling cannot become valid just by completing late. */
 usb=true;lism_charge_begin_battery_sample();now+=2000;
 lism_charge_note_battery_sample(0);sample_charge(NULL);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 battery_fetch(0);sample_charge(NULL);
 assert((bool)(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID)==(bool)INSTALLED);
 now+=2000;
 assert(lism_charge_local().flags == (usb ? LISM_CHARGE_USB : 0));
}
static void policy_tests(void) {
 struct lism_charge_filter f={0};struct lism_charge_state s;
 for(unsigned i=1;i<=4;i++)lism_charge_observe(&f,i*250,true,0,false,false,80);
 assert(f.state.flags&LISM_CHARGE_STAT_ACTIVE);assert(!lism_charge_show_bolt(f.state));
 lism_charge_observe(&f,1250,true,0,true,true,80);assert(lism_charge_show_bolt(f.state));
 lism_charge_observe(&f,1500,true,-EIO,true,true,80);assert(!lism_charge_show_bolt(f.state));
 lism_charge_observe(&f,1750,true,0,true,true,80);assert(!lism_charge_show_bolt(f.state));
 for(unsigned i=8;i<=10;i++)lism_charge_observe(&f,i*250,true,0,true,true,80);
 assert(lism_charge_show_bolt(f.state));
 lism_charge_observe(&f,4000,true,0,true,true,80);assert(!lism_charge_show_bolt(f.state));
 lism_charge_observe(&f,4250,false,0,true,true,80);assert(!(f.state.flags&LISM_CHARGE_STAT_ACTIVE));
 uint8_t wire[]={1,31,80,0};assert(lism_charge_decode(wire,4,&s)&&lism_charge_show_bolt(s));
 for(unsigned n=0;n<4;n++)assert(!lism_charge_decode(wire,n,&s)&&!s.flags);
 assert(!lism_charge_decode(wire,5,&s));wire[0]=2;assert(!lism_charge_decode(wire,4,&s));
 wire[0]=1;wire[1]=128;assert(!lism_charge_decode(wire,4,&s));
 wire[1]=4;assert(!lism_charge_decode(wire,4,&s));wire[1]=31;wire[2]=101;
 assert(!lism_charge_decode(wire,4,&s));wire[2]=80;wire[3]=1;assert(!lism_charge_decode(wire,4,&s));
}
#if CENTRAL
static void reply(unsigned index,uint8_t err,const void *wire,unsigned len) {
 struct bt_gatt_read_params*p=pending[index];assert(p);pending[index]=NULL;
 assert(p->func(&connections[index],err,p,wire,len)==BT_GATT_ITER_STOP);
}
static void client_tests(void) {
 for(unsigned i=0;i<6;i++)connections[i]=(struct bt_conn){.slot=-1,.security=2};
 connections[0]=(struct bt_conn){.slot=0,.security=2,.connected=true};
 connections[1]=(struct bt_conn){.slot=1,.security=1,.connected=true};
 connections[2]=(struct bt_conn){.slot=2,.role=1,.security=2,.connected=true};
 connections[3]=(struct bt_conn){.slot=3,.security=2,.connected=true};
 now=5000;poll_charges(NULL);assert(read_calls==2&&pending[0]&&pending[3]);
 uint8_t wire[]={1,31,80,0};reply(0,0,wire,4);reply(3,10,NULL,0); /* old firmware */
 assert(lism_charge_show_bolt(lism_charge_peripheral(0))&&!lism_charge_peripheral(3).flags);
 now+=3000;poll_charges(NULL);assert(read_calls==3&&pending[0]&&!pending[3]);
 /* Timeout expires UI but never recycles an active parameter structure. */
 now+=11000;poll_charges(NULL);assert(read_calls==3&&!lism_charge_peripheral(0).flags);
 connections[0].connected=false;lism_charge_connections.disconnected(&connections[0],1);
 connections[4]=(struct bt_conn){.slot=0,.security=2,.connected=true};
 poll_charges(NULL);assert(!pending[4]&&connections[0].refs==1);
 reply(0,0,wire,4);assert(!lism_charge_peripheral(0).flags);
 poll_charges(NULL);assert(connections[0].refs==0&&connections[4].refs==1&&pending[4]);
 reply(4,0,wire,4);assert(lism_charge_show_bolt(lism_charge_peripheral(0)));
 /* Wrong slot after request, malformed data, query failure all fail closed. */
 now+=3000;poll_charges(NULL);connections[4].slot=1;reply(4,0,wire,4);
 assert(!lism_charge_peripheral(0).flags);poll_charges(NULL);reply(4,0,wire,3);
 assert(!lism_charge_peripheral(1).flags);
 now+=60001;read_error=-ENOMEM;poll_charges(NULL);assert(!peers[1].pending&&!peers[1].valid);
 read_error=0;now+=3000;poll_charges(NULL);reply(4,0,wire,4);
 assert(lism_charge_show_bolt(lism_charge_peripheral(1)));
 /* Fresh reads from a stale server must not manufacture charging. */
 now+=3000;poll_charges(NULL);wire[1]=0;reply(4,0,wire,4);
 assert(!lism_charge_show_bolt(lism_charge_peripheral(1)));
}
#endif
int main(void) {
 policy_tests();assert(charge_init()==0);assert(config_calls==CENTRAL);
 stat=0;now=5000;battery_fetch(0);
 for(unsigned i=0;i<4;i++){sample_charge(NULL);now+=250;}
 struct lism_charge_state s=lism_charge_local();
 assert((bool)lism_charge_show_bolt(s)==(bool)INSTALLED);
 /* Real upstream failure leaves cached voltage intact: observer still clears it. */
 battery_fetch(-EIO);sample_charge(NULL);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 battery_fetch(0);sample_charge(NULL);
 assert((bool)lism_charge_show_bolt(lism_charge_local())==(bool)INSTALLED);
 now+=65001;sample_charge(NULL);assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 voltage_error=-EIO;battery_fetch(0);sample_charge(NULL);
 assert(!lism_charge_show_bolt(lism_charge_local()));
 voltage_error=0;now+=2000;assert(lism_charge_local().flags == (usb ? LISM_CHARGE_USB : 0));
 power_transition_tests();
#if CENTRAL
 client_tests();
#else
 uint8_t wire[4];assert(lism_charge_service[1]==BT_GATT_PERM_READ_ENCRYPT);
 assert(read_charge(NULL,NULL,wire,4,0)==4&&wire[0]==1&&wire[1]==LISM_CHARGE_USB);
 assert(read_charge(NULL,NULL,wire,4,5)<0);
#endif
 puts("PASS: charge filtering, no-battery policy, stale server, encrypted service and asynchronous peer lifecycle");
}
'''
with tempfile.TemporaryDirectory(prefix='lism-charge-') as tmp:
    for central in (0, 1):
        for installed in (0,):
            c=Path(tmp)/f'test-{central}-{installed}.c'; exe=c.with_suffix('')
            c.write_text(prelude+source+tests)
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',
                            '-Wno-unused-function','-Wno-unused-variable','-Wno-sign-compare',
                            '-fsanitize=address,undefined','-fno-omit-frame-pointer','-g',
                            f'-DCENTRAL={central}',
                            '-I'+str(root/'src'),str(c),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)
