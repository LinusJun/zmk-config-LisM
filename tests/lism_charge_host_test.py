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
#include "lism_battery_presence.h"
#define IS_ENABLED(x) (x)
#define CONFIG_ZMK_SPLIT_ROLE_CENTRAL CENTRAL
#define CONFIG_LISM_XIAO147_TOUCH CENTRAL
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
#define LOG_MODULE_REGISTER(...)
#define LOG_LEVEL_INF 3
static unsigned diagnostic_logs;
#define LOG_INF(...) do { diagnostic_logs++; if (0) printf(__VA_ARGS__); } while (0)
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
static unsigned adc_requests;
void lism_charge_request_battery_sample(void) { adc_requests++; }
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
#define XIAO147_PERIPHERAL_COUNT 6
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
 assert(c->security>=2&&c->role==0&&c->slot>=0&&c->slot<XIAO147_PERIPHERAL_COUNT);
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
/* Distinct 1s ADC completions; STAT/UI samples cannot manufacture evidence. */
static void sample_mv(int mv) {
 for(int i=0;i<3;i++){now+=250;sample_charge(NULL);}
 now+=250;voltage_mv=mv;battery_fetch(0);sample_charge(NULL);
}
static void reset_local(bool source) {
 local_filter=(struct lism_charge_filter){0};
 presence=(struct lism_battery_presence){0};
 battery_sample_valid=false;power_seen=false;power_settling=false;
 fetch_pending=false;power_epoch=0;last_adc_request_at=0;
 usb=source;stat=0;voltage_error=0;now=1000;
 sample_charge(NULL);
}
static void establish_usb_step(void) {
 for(int i=0;i<5;i++)sample_mv(4150);
 for(int i=0;i<4;i++)sample_mv(3850);
 for(int i=0;i<4;i++){now+=250;sample_charge(NULL);}
 assert(lism_charge_show_bolt(lism_charge_local()));
 assert(lism_charge_local().flags&LISM_CHARGE_BATTERY_INFERRED);
 assert(!(lism_charge_local().flags&LISM_CHARGE_BATTERY_CONFIRMED));
}
static void power_transition_tests(void) {
 reset_local(false);
 /* After minutes on battery, telemetry still requests fresh evidence within
  * 2s; the normal 60s BAS timer is not sufficient for a USB handoff. */
 unsigned requests_before=adc_requests;
 now+=60000;sample_charge(NULL);assert(adc_requests==requests_before+1);
 battery_fetch(0);sample_charge(NULL);
 now+=2000;sample_charge(NULL);battery_fetch(0);sample_charge(NULL);
 usb=true;sample_charge(NULL);
 for(int i=0;i<4;i++)sample_mv(3820);
 assert(lism_charge_show_bolt(lism_charge_local()));
 reset_local(true);
 /* Static 3.8V/CHG LOW is ambiguous, not 80% or charging. */
 for(int i=0;i<8;i++)sample_mv(3800);
 assert(lism_charge_usb_display(lism_charge_local()));
 assert(!lism_charge_show_bolt(lism_charge_local()));
 reset_local(true);establish_usb_step();
 assert(adc_requests);
 stat=1;sample_charge(NULL);assert(!lism_charge_show_bolt(lism_charge_local()));
 stat=-EIO;sample_charge(NULL);assert(!lism_charge_show_bolt(lism_charge_local()));
 stat=0;
 for(int i=0;i<4;i++){now+=250;sample_charge(NULL);}
 assert(lism_charge_show_bolt(lism_charge_local()));
 /* Voltage-derived 100% is NOT charge termination. */
 battery_percent=100;sample_mv(3900);
 assert(lism_charge_show_bolt(lism_charge_local()));
 stat=1;sample_charge(NULL);assert(!lism_charge_show_bolt(lism_charge_local()));
 assert(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID);
 /* OFF/removal jump invalidates even with STAT stuck LOW. */
 stat=0;sample_mv(4150);
 assert(lism_charge_usb_display(lism_charge_local()));
 assert(!lism_charge_show_bolt(lism_charge_local()));
 reset_local(true);establish_usb_step();
 /* An extra timer-driven ADC completion must veto, even within 500 ms. */
 sample_mv(3850);now+=250;voltage_mv=4000;battery_fetch(0);sample_charge(NULL);
 assert(!lism_charge_show_bolt(lism_charge_local()));
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 reset_local(true);establish_usb_step();
 battery_fetch(-EIO);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 sample_mv(3850);assert(!lism_charge_show_bolt(lism_charge_local()));
 reset_local(true);establish_usb_step();
 now+=3501;sample_charge(NULL);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 assert(!lism_charge_show_bolt(lism_charge_local()));
 reset_local(true);establish_usb_step();
 /* USB loss is immediately visible before the sampling worker executes. */
 usb=false;struct lism_charge_state s=lism_charge_local();
 assert(!(s.flags&(LISM_CHARGE_USB|LISM_CHARGE_STAT_ACTIVE|LISM_CHARGE_LEVEL_VALID)));
 now+=999;battery_fetch(0);sample_charge(NULL);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 now++;battery_fetch(0);sample_charge(NULL);
 assert(lism_charge_local().flags&LISM_CHARGE_BATTERY_CONFIRMED);
 assert(!lism_charge_show_bolt(lism_charge_local()));
 /* Handoff is INFERRED only after fresh, settled USB voltage, not cached %. */
 usb=true;(void)lism_charge_local();
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 for(int i=0;i<4;i++)sample_mv(3870);
 assert(lism_charge_show_bolt(lism_charge_local()));
 assert(lism_charge_local().flags&LISM_CHARGE_BATTERY_INFERRED);
 /* A source transition during fetch and settling-started fetch are rejected. */
 lism_charge_begin_battery_sample();usb=false;(void)lism_charge_local();
 now+=2000;lism_charge_note_battery_sample(0);sample_charge(NULL);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 battery_fetch(0);sample_charge(NULL);
 assert(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID);
 usb=true;lism_charge_begin_battery_sample();now+=2000;
 lism_charge_note_battery_sample(0);sample_charge(NULL);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 reset_local(true);establish_usb_step();
 voltage_error=-EIO;battery_fetch(0);
 assert(!lism_charge_show_bolt(lism_charge_local()));
 voltage_error=0;sample_mv(4301);
 assert(!(lism_charge_local().flags&LISM_CHARGE_LEVEL_VALID));
 sample_mv(1600);assert(!lism_charge_show_bolt(lism_charge_local()));
 assert(config_calls==CENTRAL); /* no charger-control writes */
}
static void recovery_tests(void) {
 reset_local(false);sample_mv(3900);
 usb=true;sample_charge(NULL);sample_mv(3920);
 uint32_t deadline=presence.seed_at+7000;
 battery_fetch(-EIO);sample_charge(NULL);
 assert(presence.handoff&&!presence.present&&!presence.candidate_count);
 assert(presence.last_reason==LISM_PRESENCE_SAMPLE_ERROR);
 sample_charge(NULL);assert(presence.last_reason==LISM_PRESENCE_SAMPLE_ERROR);
 assert(!lism_charge_show_bolt(lism_charge_local()));
 for(int i=0;i<3;i++){sample_mv(3920);assert(!lism_charge_show_bolt(lism_charge_local()));}
 sample_mv(3920);assert(lism_charge_show_bolt(lism_charge_local())&&now<=deadline);
 /* A later transient can recover from the previously fresh USB inference,
  * without pretending its old percentage/bolt remained valid during error. */
 for(int i=0;i<30;i++)sample_mv(3920);
 uint32_t good_at=presence.last_at;
 battery_fetch(-EIO);sample_charge(NULL);
 assert(!presence.present&&presence.handoff&&presence.seed_at==good_at);
 for(int i=0;i<3;i++){sample_mv(3930);assert(!lism_charge_show_bolt(lism_charge_local()));}
 sample_mv(3930);assert(lism_charge_show_bolt(lism_charge_local()));
 /* Repeated errors never extend the deadline or splice old votes. */
 battery_fetch(-EIO);uint32_t seed=presence.seed_at;
 for(int i=0;i<8;i++){now+=1000;battery_fetch(-EIO);sample_charge(NULL);assert(presence.seed_at==seed);}
 assert(!presence.handoff&&!presence.present);
 for(int i=0;i<8;i++)sample_mv(3930);
 assert(!lism_charge_show_bolt(lism_charge_local()));
 /* Within-baseline candidate noise restarts all votes, not the seed clock. */
 reset_local(false);sample_mv(3900);usb=true;sample_charge(NULL);
 sample_mv(3910);seed=presence.seed_at;sample_mv(3950);
 assert(presence.handoff&&!presence.candidate&&presence.seed_at==seed);
 for(int i=0;i<3;i++){sample_mv(3950);assert(!presence.present);}
 sample_mv(3950);assert(presence.present);
 /* Late-started candidate may not confirm after the original seven seconds. */
 struct lism_battery_presence p={0};lism_presence_source(&p,false,0);
 lism_presence_sample(&p,1000,true,3900);lism_presence_source(&p,true,1100);
 for(unsigned t=6000;t<=9000;t+=1000)lism_presence_sample(&p,t,true,3920);
 assert(!p.present&&!p.handoff&&p.last_reason==LISM_PRESENCE_TIMEOUT);
 /* Hard voltage veto discards seeds and cannot fall into step-down in frame. */
 p=(struct lism_battery_presence){0};lism_presence_source(&p,false,0);
 lism_presence_sample(&p,1000,true,3900);lism_presence_source(&p,true,1100);
 lism_presence_sample(&p,2100,true,3981);assert(!p.handoff&&!p.present);
 assert(p.last_reason==LISM_PRESENCE_BASELINE);
 for(unsigned t=3100;t<8100;t+=1000)lism_presence_sample(&p,t,true,3981);
 assert(!p.present);
 /* Recovery also handles timestamp wrap with an immutable last-good seed. */
 p=(struct lism_battery_presence){.source_seen=true,.usb=true,.present=true,
     .inferred=true,.have_sample=true,.last_mv=3900,.last_at=UINT32_MAX-1500,
     .changed_at=UINT32_MAX-10000};
 lism_presence_retry(&p,UINT32_MAX-500,LISM_PRESENCE_SAMPLE_ERROR);
 seed=p.seed_at;
 for(unsigned i=1;i<=4;i++)lism_presence_sample(&p,seed+i*1000U,true,3910);
 assert(p.present&&p.inferred);
}
static void presence_tests(void) {
 struct lism_battery_presence p={0};
 lism_presence_source(&p,true,0);
 /* Cached/too-fast repeated readings cannot qualify. */
 for(int i=0;i<10;i++)lism_presence_sample(&p,1000,true,4150);
 assert(p.stable_count==1&&!p.reference_valid);
 for(unsigned t=2000;t<=4000;t+=1000)lism_presence_sample(&p,t,true,4150);
 assert(p.reference_valid&&!p.present);
 lism_presence_sample(&p,5000,true,4001);assert(!p.candidate);
 /* 149mV or a slow ramp is not an insertion step. */
 for(unsigned t=6000;t<=12000;t+=1000)lism_presence_sample(&p,t,true,4001-(t-6000)/100);
 assert(!p.present);
 p=(struct lism_battery_presence){0};lism_presence_source(&p,true,0);
 for(unsigned t=1000;t<=4000;t+=1000)lism_presence_sample(&p,t,true,4150);
 for(unsigned t=5000;t<=8000;t+=1000)lism_presence_sample(&p,t,true,4000);
 assert(p.present&&p.inferred); /* 150mV exactly: inference, not proof */
 for(unsigned t=9000;t<=15000;t+=1000)lism_presence_sample(&p,t,true,4000+(t-8000)/100);
 assert(p.present);
 lism_presence_sample(&p,16000,true,4200);assert(!p.present);
 /* No noisy candidate hold, invalid ADC, no low-voltage recovery shortcut. */
 lism_presence_sample(&p,17000,true,1600);
 for(unsigned t=18000;t<=25000;t+=1000)lism_presence_sample(&p,t,true,3800);
 assert(!p.present);
 lism_presence_sample(&p,26000,false,3800);assert(!p.have_sample);
 p=(struct lism_battery_presence){0};lism_presence_source(&p,true,0);
 for(unsigned t=1000;t<=4000;t+=1000)lism_presence_sample(&p,t,true,4150);
 lism_presence_sample(&p,5000,true,3850);assert(p.candidate);
 lism_presence_sample(&p,5250,true,3950);assert(!p.candidate&&!p.present);
 p=(struct lism_battery_presence){0};lism_presence_source(&p,false,0);
 lism_presence_sample(&p,1000,true,3900);assert(p.present);
 lism_presence_source(&p,true,1100);
 lism_presence_sample(&p,2100,true,3979);assert(p.candidate_handoff);
 lism_presence_sample(&p,3100,true,4008);assert(!p.present&&!p.candidate);
 /* Same logic across uptime wrap. */
 p=(struct lism_battery_presence){0};
 uint32_t base=UINT32_MAX-2500;lism_presence_source(&p,true,base);
 for(unsigned i=1;i<=4;i++)lism_presence_sample(&p,base+i*1000U,true,4150);
 for(unsigned i=5;i<=8;i++)lism_presence_sample(&p,base+i*1000U,true,3850);
 assert(p.present&&p.inferred);
 lism_presence_sample(&p,base+12000U,true,3850);assert(!p.present);
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
 uint8_t wire[]={2,111,80,0};assert(lism_charge_decode(wire,4,&s)&&lism_charge_show_bolt(s));
 for(unsigned n=0;n<4;n++)assert(!lism_charge_decode(wire,n,&s)&&!s.flags);
 assert(!lism_charge_decode(wire,5,&s));wire[0]=1;assert(!lism_charge_decode(wire,4,&s));
 wire[0]=2;wire[1]=128;assert(!lism_charge_decode(wire,4,&s));
 wire[1]=4;assert(!lism_charge_decode(wire,4,&s));wire[1]=111;wire[2]=101;
 assert(!lism_charge_decode(wire,4,&s));wire[2]=80;wire[3]=1;assert(!lism_charge_decode(wire,4,&s));
 wire[3]=0;wire[1]=95;assert(!lism_charge_decode(wire,4,&s)); /* USB cannot be confirmed */
 wire[1]=104;assert(!lism_charge_decode(wire,4,&s)); /* inference requires USB */
 uint8_t level=0;
 assert(lism_charge_level((struct lism_charge_state){0},true,55,&level)&&level==55);
 assert(!lism_charge_level((struct lism_charge_state){.flags=65},true,55,&level));
 assert(lism_charge_usb_display((struct lism_charge_state){.flags=65}));
 assert(!lism_charge_usb_display((struct lism_charge_state){.flags=111,.percent=80}));
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
 uint8_t wire[]={2,111,80,0};reply(0,0,wire,4);reply(3,10,NULL,0); /* old firmware */
 assert(lism_charge_show_bolt(lism_charge_peripheral(0))&&!lism_charge_peripheral(3).flags);
 now+=3000;poll_charges(NULL);assert(read_calls==3&&pending[0]&&!pending[3]);
 /* Timeout expires UI but never recycles an active parameter structure. */
 now+=11000;poll_charges(NULL);assert(read_calls==3&&!(lism_charge_peripheral(0).flags&LISM_CHARGE_LEVEL_VALID));
 connections[0].connected=false;lism_charge_connections.disconnected(&connections[0],1);
 connections[4]=(struct bt_conn){.slot=0,.security=2,.connected=true};
 poll_charges(NULL);assert(!pending[4]&&connections[0].refs==1);
 reply(0,0,wire,4);assert(!lism_charge_peripheral(0).flags);
 poll_charges(NULL);assert(connections[0].refs==0&&connections[4].refs==1&&pending[4]);
 reply(4,0,wire,4);assert(lism_charge_show_bolt(lism_charge_peripheral(0)));
 /* Wrong slot after request, malformed data, query failure all fail closed. */
 now+=3000;poll_charges(NULL);connections[4].slot=1;reply(4,0,wire,4);
 assert(!lism_charge_peripheral(0).flags);poll_charges(NULL);reply(4,0,wire,3);
 assert(!(lism_charge_peripheral(1).flags&LISM_CHARGE_LEVEL_VALID));
 now+=60001;read_error=-ENOMEM;poll_charges(NULL);assert(!peers[1].pending&&!peers[1].valid);
 read_error=0;now+=3000;poll_charges(NULL);reply(4,0,wire,4);
 assert(lism_charge_show_bolt(lism_charge_peripheral(1)));
 /* Fresh reads from a stale server must not manufacture charging. */
 now+=3000;poll_charges(NULL);wire[1]=0;reply(4,0,wire,4);
 assert(!lism_charge_show_bolt(lism_charge_peripheral(1)));
 /* Optional diagnostic reads use a distinct UUID and cannot alter v2 state. */
 if(pending[4])reply(4,0,wire,4);
 now+=60000;poll_charges(NULL);assert(pending[4]);
 wire[1]=65;reply(4,0,wire,4);
 poll_charges(NULL);assert(pending[4]);
 assert(pending[4]->by_uuid.uuid==&diag_uuid.uuid);
 uint8_t diag[12]={1,65,0,32,0xf0,0x0e,0,0,1,0,30,0};
 unsigned logs=diagnostic_logs;reply(4,0,diag,sizeof(diag));
 assert(diagnostic_logs==logs+1&&peers[1].valid&&peers[1].state.flags==65);
 peers[1].diag_at=now;poll_charges(NULL);
 uint8_t diag2[LISM_CHARGE_DIAG_SIZE]={2,65,0,8,0x77,0x0f,0,0,1,0,30,0,
     0x3c,0x0f,LISM_PRESENCE_SAMPLE_ERROR,0,20,0};
 logs=diagnostic_logs;reply(4,0,diag2,sizeof(diag2));
 assert(diagnostic_logs==logs+2&&peers[1].valid&&peers[1].state.flags==65);
 peers[1].diag_at=now;poll_charges(NULL);diag2[0]=1;
 reply(4,0,diag2,sizeof(diag2));assert(peers[1].valid&&peers[1].state.flags==65);
 now+=3000;poll_charges(NULL);reply(4,0,wire,4);
 peers[1].diag_at=now;read_error=-ENOMEM;poll_charges(NULL);
 assert(peers[1].valid&&!peers[1].pending&&peers[1].state.flags==65);
 assert(peers[1].diag_at==now+60000);read_error=0;
 now+=3000;poll_charges(NULL);reply(4,0,wire,4);
 peers[1].diag_at=now;poll_charges(NULL);reply(4,10,NULL,0);
 assert(peers[1].valid&&peers[1].state.flags==65); /* old peripheral supported */
 /* A delayed diagnostic does not extend freshness, and disconnect cannot
  * recycle its params or reattach a late result to a replacement connection. */
 peers[1].diag_at=now;poll_charges(NULL);assert(pending[4]);
 struct bt_gatt_read_params *inflight=pending[4];
 now+=6000;poll_charges(NULL);assert(pending[4]==inflight);
 reply(4,0,diag,sizeof(diag));assert(peers[1].state.flags==65);
 now+=3000;poll_charges(NULL);reply(4,0,wire,4);
 peers[1].diag_at=now;poll_charges(NULL);assert(pending[4]);
 connections[4].connected=false;lism_charge_connections.disconnected(&connections[4],1);
 poll_charges(NULL);assert(connections[4].refs==1&&pending[4]);
 reply(4,0,diag,sizeof(diag));assert(!peers[1].valid);
 poll_charges(NULL);assert(connections[4].refs==0&&!peers[1].conn);
 /* Second-set physical slot5 is independently polled, not aliased to A-R. */
 connections[5]=(struct bt_conn){.slot=5,.security=2,.connected=true};
 now+=3000;poll_charges(NULL);assert(pending[5]);
 wire[1]=111;reply(5,0,wire,4);
 assert(lism_charge_show_bolt(lism_charge_peripheral(5)));
 assert(!lism_charge_show_bolt(lism_charge_peripheral(1)));
}
#endif
int main(void) {
 policy_tests();presence_tests();assert(charge_init()==0);
 power_transition_tests();recovery_tests();
#if CENTRAL
 client_tests();
#else
 reset_local(true);establish_usb_step();
 uint8_t wire[4];assert(lism_charge_service[1]==BT_GATT_PERM_READ_ENCRYPT);
 assert(read_charge(NULL,NULL,wire,4,0)==4&&wire[0]==2);
 struct lism_charge_state s;assert(lism_charge_decode(wire,4,&s)&&lism_charge_show_bolt(s));
 assert(read_charge(NULL,NULL,wire,4,5)<0);
 assert(lism_charge_service[2]==BT_GATT_PERM_READ_ENCRYPT);
 uint8_t diag[LISM_CHARGE_DIAG_SIZE];
 assert(read_charge_diagnostics(NULL,NULL,diag,sizeof(diag),0)==LISM_CHARGE_DIAG_SIZE);
 assert(diag[0]==2&&diag[1]==s.flags&&diag[2]==s.percent);
 assert((diag[3]&7)==7&&(diag[4]|(diag[5]<<8))==3850&&diag[6]==0&&diag[7]==0);
 assert((diag[10]|(diag[11]<<8))==now/1000);
 uint8_t tail[4];assert(read_charge_diagnostics(NULL,NULL,tail,4,8)==4);
 assert(!memcmp(tail,diag+8,4));
 assert(read_charge_diagnostics(NULL,NULL,tail,4,19)<0);
 battery_fetch(-EIO);
 assert(read_charge_diagnostics(NULL,NULL,diag,sizeof(diag),0)==LISM_CHARGE_DIAG_SIZE);
 assert((int8_t)diag[7]==-EIO&&!(diag[3]&1));
 assert(diag[14]==LISM_PRESENCE_SAMPLE_ERROR&&diag[15]==0);
#endif
 puts("PASS: fresh dynamic inference, unknown USB, removal/errors/stale, v2 encrypted peer lifecycle");
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
