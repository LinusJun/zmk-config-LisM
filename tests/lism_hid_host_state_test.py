"""Compile the actual coordinator/GATT callbacks with bounded Zephyr mocks.

No BLE hardware and no copied state-machine implementation. Callback context
assertions prohibit GATT, settings and blocking locks in simulated BT RX.
"""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
runtime = (root / "src/lism_hid_host.c").read_text()
runtime = re.sub(r'^#include .*$', '', runtime, flags=re.M)
header = re.sub(r'^#include <zephyr/.*$', '', (root / "src/lism_hid_host.h").read_text(), flags=re.M)
header = header.replace("#pragma once", "")
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stdarg.h>
#include "lism_hid_parser.h"
#define ARG_UNUSED(x) (void)(x)
#define MIN(a,b) ((a)<(b)?(a):(b))
#define CLAMP(a,l,h) ((a)<(l)?(l):((a)>(h)?(h):(a)))
#define BIT(x) (1U<<(x))
#define BIT_MASK(x) (BIT(x)-1)
#define CONTAINER_OF(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define K_NO_WAIT 0
#define K_FOREVER -1
#define K_MSEC(x) (x)
#define LOG_MODULE_REGISTER(...)
#define LOG_INF(...) mock_log(__VA_ARGS__)
#define LOG_WRN(...)
#define INPUT_EV_MSC 4
#define ZMK_HID_MOUSE_NUM_BUTTONS 5
#define IS_ENABLED(x) 1
#define ZMK_LISTENER(...)
#define ZMK_SUBSCRIPTION(...)
#define ZMK_EV_EVENT_BUBBLE 0
typedef struct {int unused;} zmk_event_t;
static const zmk_event_t *as_zmk_endpoint_changed(const zmk_event_t *event){return event;}
#define CONFIG_INPUT_INIT_PRIORITY 0
#define CONFIG_ZMK_BLE_INIT_PRIORITY 0
#define POST_KERNEL 0
#define APPLICATION 0
#define DEVICE_DEFINE(...)
#define DEVICE_GET(x) NULL
#define INPUT_CALLBACK_DEFINE(...)
#define SETTINGS_STATIC_HANDLER_DEFINE(...)
#define SYS_INIT(...)
static int rx;
static unsigned diagnostic_logs;
static char last_diagnostic_gates[512],last_diagnostic_counts[512];
static void mock_log(const char *format,...){
 assert(!rx);char line[768];va_list args;va_start(args,format);
 vsnprintf(line,sizeof(line),format,args);va_end(args);
 if(strstr(line,"BLE scan diag v1 counts")){
   diagnostic_logs++;snprintf(last_diagnostic_counts,sizeof(last_diagnostic_counts),"%.511s",line);
 }else if(strstr(line,"BLE scan diag v1")){
   diagnostic_logs++;snprintf(last_diagnostic_gates,sizeof(last_diagnostic_gates),"%.511s",line);
 }
}
static uint32_t now;
typedef int atomic_t;
static int atomic_get(atomic_t *a){return *a;}
static int atomic_set(atomic_t *a,int v){int o=*a;*a=v;return o;}
static void atomic_clear(atomic_t *a){*a=0;}
static void atomic_inc(atomic_t *a){++*a;}
static bool atomic_cas(atomic_t *a,int old,int v){if(*a!=old)return false;*a=v;return true;}
static void atomic_set_bit(atomic_t *a,int b){*a|=1<<b;}
struct k_work {int unused;};
#define K_WORK_DEFINE(n,f) struct k_work n
#define K_WORK_DELAYABLE_DEFINE(n,f) struct k_work n
#define K_MUTEX_DEFINE(n) int n
struct k_spinlock {int unused;};
typedef int k_spinlock_key_t;
static int k_spin_lock(struct k_spinlock *l){(void)l;return 0;}
static void k_spin_unlock(struct k_spinlock *l,int k){(void)l;(void)k;}
static void k_mutex_lock(int *p,int t){(void)p;(void)t;assert(!rx);}
static void k_mutex_unlock(int *p){(void)p;assert(!rx);}
struct queue {unsigned size,cap,head,count; unsigned char *bytes;};
#define K_MSGQ_DEFINE(n,s,c,a) static unsigned char n##_storage[(s)*(c)]; static struct queue n={s,c,0,0,n##_storage}
static int k_msgq_put(struct queue *q,const void *p,int t){(void)t;if(q->count==q->cap)return -1;memcpy(q->bytes+((q->head+q->count++)%q->cap)*q->size,p,q->size);return 0;}
static int k_msgq_get(struct queue *q,void *p,int t){(void)t;if(!q->count)return -1;memcpy(p,q->bytes+(q->head++%q->cap)*q->size,q->size);q->count--;return 0;}
static void k_msgq_purge(struct queue *q){q->head=q->count=0;}
static unsigned k_msgq_num_used_get(struct queue *q){return q->count;}
static int k_work_submit(struct k_work *w){(void)w;return 0;}
static int k_work_reschedule(struct k_work *w,int t){(void)w;(void)t;return 0;}
static uint32_t k_uptime_get_32(void){return now;}
struct input_event {int type,code;};
static int kicks,failed_kicks;
static int input_report(void *d,int t,int c,int v,bool s,int to){(void)d;(void)t;(void)c;(void)v;(void)s;(void)to;kicks++;if(failed_kicks){failed_kicks--;return -ENOMSG;}return 0;}
static int xiao147_pointer_endpoint(void){return 1;}
static int moves,sends;
static void zmk_hid_mouse_button_release(int b){(void)b;assert(!rx);}
static void zmk_hid_mouse_button_press(int b){(void)b;assert(!rx);}
static void zmk_hid_mouse_movement_set(int x,int y){assert(!rx);moves+=x+y;}
static void zmk_hid_mouse_scroll_set(int x,int y){assert(!rx);(void)x;(void)y;}
static int zmk_endpoint_send_mouse_report(void){assert(!rx);sends++;return 0;}
typedef struct {uint8_t val[6];} bt_addr_t;
typedef struct {uint8_t type;bt_addr_t a;} bt_addr_le_t;
static const bt_addr_le_t any={0};
#define BT_ADDR_LE_ANY (&any)
#define BT_ID_DEFAULT 0
static int bt_addr_le_cmp(const bt_addr_le_t *a,const bt_addr_le_t *b){return memcmp(a,b,sizeof(*a));}
static void bt_addr_le_copy(bt_addr_le_t *a,const bt_addr_le_t *b){*a=*b;}
static int bt_addr_to_str(const bt_addr_t *a,char *s,size_t n){return snprintf(s,n,"%02x",a->val[0]);}
typedef uint8_t bt_security_t;
enum bt_security_err {SEC_OK};
#define BT_SECURITY_L2 2
#define BT_HCI_ERR_REMOTE_USER_TERM_CONN 19
struct bt_conn {int refs,index;bt_addr_le_t addr;};
static struct bt_conn connection={.index=3};
static int creates,disconnects,cancels,unpairs,saves,deletes,discover_calls,read_calls,subscribe_calls;
static bt_addr_le_t forgotten;
static struct bt_conn *bt_conn_ref(struct bt_conn *c){c->refs++;return c;}
static void bt_conn_unref(struct bt_conn *c){assert(c->refs>0);c->refs--;}
static int bt_conn_index(struct bt_conn *c){return c->index;}
static const bt_addr_le_t *bt_conn_get_dst(struct bt_conn *c){return &c->addr;}
static int bt_conn_disconnect(struct bt_conn *c,int r){(void)c;(void)r;assert(!rx);disconnects++;return 0;}
static int bt_conn_le_create_cancel(void){assert(!rx);cancels++;return 0;}
#define BT_CONN_LE_CREATE_CONN NULL
#define BT_LE_CONN_PARAM(...) NULL
static int bt_conn_le_create(const bt_addr_le_t *a,void *p,void *q,struct bt_conn **out){(void)p;(void)q;assert(!rx);creates++;connection.addr=*a;connection.refs++;*out=&connection;return 0;}
static int bt_conn_get_security(struct bt_conn *c){(void)c;assert(!rx);return 2;}
static int bt_conn_set_security(struct bt_conn *c,int l){(void)c;(void)l;assert(!rx);return 0;}
struct bt_bond_info {bt_addr_le_t addr;};
static void bt_foreach_bond(int id,void (*f)(const struct bt_bond_info *,void *),void *p){(void)id;assert(!rx);struct bt_bond_info b={connection.addr};f(&b,p);}
static int bt_unpair(int id,const bt_addr_le_t *a){(void)id;assert(!rx&&a!=BT_ADDR_LE_ANY);forgotten=*a;unpairs++;return 0;}
typedef int (*settings_read_cb)(void *,void *,size_t);
static int settings_save_one(const char *s,void *p,size_t n){(void)s;(void)p;(void)n;assert(!rx);saves++;return 0;}
static int settings_delete(const char *s){assert(!rx&&!strcmp(s,"lism_hid/peer"));deletes++;return 0;}
struct bt_uuid {uint8_t type;};
struct bt_uuid_16 {struct bt_uuid uuid;uint16_t val;};
#define BT_UUID_TYPE_16 1
#define BT_UUID_16(p) ((const struct bt_uuid_16 *)(p))
#define BT_UUID_DECLARE_16(v) ((struct bt_uuid *)&(struct bt_uuid_16){{1},v})
#define BT_UUID_INIT_16(v) {{1},v}
#define BT_GATT_ITER_STOP 0
#define BT_GATT_ITER_CONTINUE 1
#define BT_GATT_DISCOVER_PRIMARY 1
#define BT_GATT_DISCOVER_CHARACTERISTIC 2
#define BT_GATT_DISCOVER_DESCRIPTOR 3
#define BT_GATT_CHRC_NOTIFY 16
#define BT_GATT_CHRC_WRITE_WITHOUT_RESP 4
#define BT_GATT_CCC_NOTIFY 1
#define BT_GATT_SUBSCRIBE_FLAG_VOLATILE 0
struct bt_gatt_attr {uint16_t handle;const struct bt_uuid *uuid;void *user_data;};
struct bt_gatt_service_val {uint16_t end_handle;};
struct bt_gatt_chrc {const struct bt_uuid *uuid;uint16_t value_handle;uint8_t properties;};
struct bt_gatt_discover_params {uint8_t type;uint16_t start_handle,end_handle;const struct bt_uuid *uuid;uint8_t (*func)(struct bt_conn *,const struct bt_gatt_attr *,struct bt_gatt_discover_params *);};
struct bt_gatt_read_params {uint8_t (*func)(struct bt_conn *,uint8_t,struct bt_gatt_read_params *,const void *,uint16_t);uint8_t handle_count;struct {uint16_t handle;} single;};
struct bt_gatt_subscribe_params {uint8_t (*notify)(struct bt_conn *,struct bt_gatt_subscribe_params *,const void *,uint16_t);void (*subscribe)(struct bt_conn *,uint8_t,struct bt_gatt_subscribe_params *);uint16_t value_handle,ccc_handle,value;uint8_t min_security;atomic_t flags[1];};
static struct bt_gatt_discover_params *dp;
static struct bt_gatt_read_params *rp;
static struct bt_gatt_subscribe_params *sp;
static int subscribe_error;
static int bt_gatt_discover(struct bt_conn *c,struct bt_gatt_discover_params *p){(void)c;assert(!rx);dp=p;discover_calls++;return 0;}
static int bt_gatt_read(struct bt_conn *c,struct bt_gatt_read_params *p){(void)c;assert(!rx);rp=p;read_calls++;return 0;}
static int bt_gatt_subscribe(struct bt_conn *c,struct bt_gatt_subscribe_params *p){(void)c;assert(!rx);sp=p;subscribe_calls++;return subscribe_error;}
static void bt_gatt_cancel(struct bt_conn *c,void *p){(void)c;(void)p;assert(!rx);}
static int bt_gatt_write_without_response(struct bt_conn *c,uint16_t h,const void *p,uint16_t n,bool s){(void)c;(void)h;(void)p;(void)n;(void)s;assert(!rx);return 0;}
struct callbacks {void (*connected)(struct bt_conn *,uint8_t);void (*disconnected)(struct bt_conn *,uint8_t);void (*security_changed)(struct bt_conn *,bt_security_t,enum bt_security_err);};
#define BT_CONN_CB_DEFINE(n) struct callbacks n
#define BT_DATA_NAME_COMPLETE 9
#define BT_DATA_NAME_SHORTENED 8
#define BT_DATA_UUID16_ALL 3
#define BT_DATA_UUID16_SOME 2
#define BT_DATA_UUID128_ALL 7
#define BT_DATA_UUID128_SOME 6
#define BT_DATA_SVC_DATA16 0x16
#define BT_DATA_SVC_DATA128 0x21
#define BT_DATA_GAP_APPEARANCE 25
#define BT_GAP_ADV_PROP_CONNECTABLE 1
struct bt_data {uint8_t type,data_len;const uint8_t *data;};
struct net_buf_simple {uint8_t *data;size_t len;};
struct bt_le_scan_recv_info {const bt_addr_le_t *addr;int8_t rssi;uint16_t adv_props;};
struct bt_le_scan_cb {void (*recv)(const struct bt_le_scan_recv_info *,struct net_buf_simple *);};
static uint16_t sys_get_le16(const uint8_t *p){return p[0]|p[1]<<8;}
static void bt_data_parse(struct net_buf_simple *b,bool (*f)(struct bt_data *,void *),void *p){for(size_t i=0;i<b->len;){unsigned n=b->data[i++];if(!n||i+n>b->len)return;struct bt_data d={b->data[i],n-1,b->data+i+1};if(!f(&d,p))return;i+=n;}}
static int registration_attempts,registration_result=-EALREADY;
static bool bluetooth_ready;
static int bt_le_scan_cb_register(struct bt_le_scan_cb *c){(void)c;assert(!rx);registration_attempts++;return registration_result;}
static bool bt_is_ready(void){assert(!rx);return bluetooth_ready;}
static int resumes,pauses;
void lism_hid_scan_resume(void){assert(!rx);resumes++;}
int lism_hid_scan_pause(void){assert(!rx);pauses++;return 0;}
static unsigned owner_diagnostics;
void lism_hid_scan_diagnostics(void){assert(!rx);owner_diagnostics++;}
'''
checks = r'''
static const uint8_t mouse_map[]={
 0x05,1,0x09,2,0xa1,1,0x09,1,0xa1,0,0x05,9,0x19,1,0x29,3,
 0x15,0,0x25,1,0x75,1,0x95,3,0x81,2,0x75,5,0x95,1,0x81,1,
 0x05,1,0x09,0x30,0x09,0x31,0x09,0x38,0x15,0x81,0x25,0x7f,
 0x75,8,0x95,3,0x81,6,0xc0,0xc0};
static void pump(void){assert(!rx);control(NULL);}
static void done(void){rx=1;disconnected(&connection,19);rx=0;pump();assert(!peer&&!connection.refs);}
static void boot(void){hid_settings_commit();pump();assert(settings_ready);}
static void ad(unsigned identity,bool connectable,bool hint){
 bt_addr_le_t a={.a={{identity}}};uint8_t bytes[]={3,3,0x12,0x18};
 struct net_buf_simple buf={bytes,hint?sizeof(bytes):0};
 struct bt_le_scan_recv_info info={&a,-42,connectable?1:0};
 rx=1;scan_received(&info,&buf);rx=0;pump();
}
static void start(void){
 lism_hid_host_pair();pump();assert(status.pairing);
 ad(7,true,false);assert(!candidates[0].public.valid);
 ad(7,false,true);assert(candidates[0].public.valid);
 int n=creates;lism_hid_host_select(0,candidates[0].public.token+1);pump();assert(creates==n);
 lism_hid_host_select(0,candidates[0].public.token);pump();assert(peer&&phase==CONNECTING&&creates==n+1);
}
static void connect_ok(void){rx=1;connected(&connection,0);rx=0;pump();assert(phase==SERVICE&&dp->type==BT_GATT_DISCOVER_PRIMARY);}
static void discover_attr(uint16_t handle,uint16_t uuid,uint16_t value,uint8_t props,uint16_t end){
 struct bt_uuid_16 u={{1},uuid};struct bt_gatt_chrc ch={(struct bt_uuid *)&u,value,props};
 struct bt_gatt_service_val sv={end};struct bt_gatt_attr a={handle,(struct bt_uuid *)&u,dp->type==BT_GATT_DISCOVER_PRIMARY?(void *)&sv:(void *)&ch};
 rx=1;dp->func(&connection,&a,dp);rx=0;
}
static void discovery_end(void){rx=1;dp->func(&connection,NULL,dp);rx=0;pump();}
static void to_map(void){
 connect_ok();discover_attr(1,0x1812,0,0,30);pump();assert(phase==CHARS);
 discover_attr(2,0x2a4b,3,2,0);
 discover_attr(4,0x2a4d,5,BT_GATT_CHRC_NOTIFY,0);
 /* Boot mouse is a different characteristic: CCC search must end at 9. */
 discover_attr(10,0x2a33,11,BT_GATT_CHRC_NOTIFY,0);discovery_end();
 assert(phase==MAP&&reports[0].end==9&&rp->single.handle==3);
}
static void read_data(const uint8_t *bytes,size_t n){rx=1;rp->func(&connection,0,rp,bytes,n);rp->func(&connection,0,rp,NULL,0);rx=0;pump();}
static void to_reference(void){
 to_map();read_data(mouse_map,sizeof(mouse_map));assert(phase==DESCRIPTORS);
 assert(dp->start_handle==6&&dp->end_handle==9);
 discover_attr(6,0x2908,0,0,0);discover_attr(7,0x2902,0,0,0);discovery_end();
 assert(phase==REFERENCE&&rp->single.handle==6);
}
static void to_subscribe(void){
 to_reference();
 uint8_t ref[]={0,1};read_data(ref,2);assert(phase==SUBSCRIBE&&sp&&sp->ccc_handle==7);
}
static void scan_start(void){
 assert(!peer&&!have_saved);lism_hid_host_pair();pump();assert(status.pairing);
}
static void raw_ad(unsigned identity,unsigned address_type,bool connectable,
                   unsigned field,const uint8_t *value,unsigned length){
 assert(length<=60);bt_addr_le_t a={.type=address_type,.a={{identity}}};
 uint8_t bytes[64]={length+1,field};if(length)memcpy(bytes+2,value,length);
 struct net_buf_simple b={bytes,field?length+2:0};
 struct bt_le_scan_recv_info i={&a,-43,connectable?1:0};
 rx=1;scan_received(&i,&b);rx=0;pump();
}
static void named(unsigned id,unsigned type,bool connectable,bool complete,const char *name){
 raw_ad(id,type,connectable,complete?BT_DATA_NAME_COMPLETE:BT_DATA_NAME_SHORTENED,
        (const uint8_t *)name,strlen(name));
}
static unsigned candidate_count(void){unsigned n=0;for(unsigned i=0;i<LISM_HID_CANDIDATES;i++){n+=candidates[i].public.valid;}return n;}
static void advertisement_regression(void){
 int initial_creates=creates,initial_saves=saves,initial_unpairs=unpairs;
 scan_start();named(20,0,true,true,"Logitech G705");
 assert(candidate_count()==1&&!peer&&creates==initial_creates);
 assert(!strcmp(candidates[0].public.name,"Logitech G705"));
 /* Real name permits discovery, never bypasses HIDS validation. */
 lism_hid_host_select(0,candidates[0].public.token);pump();assert(peer);connect_ok();
 discovery_end();assert(phase==DISCONNECTING&&!status.ready&&!input_ready);
 done();assert(saves==initial_saves&&unpairs==initial_unpairs);

 /* ADV and SCAN_RSP may arrive in either order. A response itself is not
  * connectable: only the same address/type's recent ADV supplies that bit. */
 scan_start();raw_ad(21,0,true,0,NULL,0);assert(!candidate_count());
 named(21,0,false,true,"Mouse response");assert(candidate_count()==1&&!peer);
 scan_start();named(22,0,false,true,"Response first");assert(!candidate_count());
 raw_ad(22,0,true,0,NULL,0);assert(candidate_count()==1&&!peer);
 /* Expire the entire cache entry, not just its connectable/HID flags. */
 scan_start();named(23,0,false,true,"Expired name");now+=2001;
 raw_ad(23,0,true,0,NULL,0);assert(!candidate_count());
 scan_start();raw_ad(24,0,true,0,NULL,0);now+=2001;
 named(24,0,false,true,"Late response");assert(!candidate_count());
 scan_start();ad(24,false,true);now+=2001;
 raw_ad(24,0,true,0,NULL,0);assert(!candidate_count());

 scan_start();named(25,0,true,true,"Public mouse");
 named(25,1,true,true,"Random mouse");assert(candidate_count()==2);
 assert(candidates[0].addr.type!=candidates[1].addr.type);
 named(25,0,false,false,"Pub");
 assert(!strcmp(candidates[0].public.name,"Public mouse"));
 named(25,0,false,true,"Public full replacement");
 assert(!strcmp(candidates[0].public.name,"Public full replacement"));

 static const uint8_t hids128[]={0xfb,0x34,0x9b,0x5f,0x80,0,0,0x80,
                              0,0x10,0,0,0x12,0x18,0,0};
 scan_start();raw_ad(26,0,true,BT_DATA_UUID128_ALL,hids128,sizeof(hids128));
 assert(candidate_count()==1&&!peer);
 uint8_t lookalike[16];memcpy(lookalike,hids128,16);lookalike[15]=1;
 raw_ad(27,0,true,BT_DATA_UUID128_SOME,lookalike,16);assert(candidate_count()==1);
 raw_ad(28,0,true,BT_DATA_UUID128_ALL,hids128,15);assert(candidate_count()==1);
 uint8_t reversed[16];for(unsigned i=0;i<16;i++){reversed[i]=hids128[15-i];}
 raw_ad(29,0,true,BT_DATA_UUID128_ALL,reversed,16);assert(candidate_count()==1);
 uint8_t trailing[17];memcpy(trailing,hids128,16);trailing[16]=0;
 raw_ad(30,0,true,BT_DATA_UUID128_SOME,trailing,17);assert(candidate_count()==1);
 raw_ad(31,0,true,BT_DATA_SVC_DATA128,hids128,16);assert(candidate_count()==2);
 uint8_t svc16[]={0x12,0x18,0xff};
 raw_ad(32,0,true,BT_DATA_SVC_DATA16,svc16,3);assert(candidate_count()==3);
 scan_start();named(33,0,true,true,"   ");assert(!candidate_count());
 /* Complete-name priority also applies to duplicate fields in one packet. */
 struct ad_fields fields={0};
 struct bt_data complete={BT_DATA_NAME_COMPLETE,9,(const uint8_t *)"Full name"};
 struct bt_data short_name={BT_DATA_NAME_SHORTENED,4,(const uint8_t *)"Full"};
 parse_ad(&complete,&fields);parse_ad(&short_name,&fields);
 assert(fields.name_complete&&!strcmp(fields.name,"Full name"));

 /* Queue pressure remains observable without replacing control events or
  * automatically connecting any newly visible device. */
 scan_start();bt_addr_le_t flood_addr={.a={{34}}};
 struct net_buf_simple empty_payload={NULL,0};
 struct bt_le_scan_recv_info flood_info={&flood_addr,-45,1};
 unsigned received=advertisements.cap+2;
 rx=1;for(unsigned i=0;i<received;i++){scan_received(&flood_info,&empty_payload);}rx=0;
 struct lism_hid_status snapshot;lism_hid_host_snapshot(&snapshot);
 assert(snapshot.advertisements_seen==received&&snapshot.advertisements_dropped==2);
 pump();assert(!peer&&!candidate_count());

 /* Twelve stable slots, no RSSI resorting/replacement, old scan tokens cannot
  * select a new identity at the same visual index. */
 scan_start();uint32_t tokens[LISM_HID_CANDIDATES];
 for(unsigned i=0;i<LISM_HID_CANDIDATES;i++){
   char name[24];snprintf(name,sizeof(name),"Mouse %u",i);
   named(40+i,0,true,true,name);tokens[i]=candidates[i].public.token;
   assert(tokens[i]&&candidates[i].addr.a.val[0]==40+i);
 }
 assert(LISM_HID_CANDIDATES==12&&candidate_count()==12&&!peer);
 named(90,0,true,true,"Thirteenth device");assert(candidate_count()==12);
 for(unsigned i=0;i<LISM_HID_CANDIDATES;i++){
   assert(candidates[i].public.token==tokens[i]&&candidates[i].addr.a.val[0]==40+i);
 }
 named(40,0,true,false,"M");assert(candidates[0].public.token==tokens[0]);
 assert(!strcmp(candidates[0].public.name,"Mouse 0"));
 scan_start();named(91,0,true,true,"New generation");int n=creates;
 lism_hid_host_select(0,tokens[0]);pump();assert(creates==n&&!peer);
 uint32_t fresh=candidates[0].public.token;now+=2001;
 lism_hid_host_select(0,fresh);pump();assert(creates==n&&!peer);
 named(91,0,true,true,"New generation");
 lism_hid_host_select(0,fresh);pump();assert(peer&&connection.addr.a.val[0]==91);
 lism_hid_host_cancel();pump();done();
 assert(saves==initial_saves&&unpairs==initial_unpairs);
}
static void connectable_lease_regression(void){
 /* Non-connectable SCAN_RSP/name traffic may keep a candidate visible, but
  * must never renew the independently aged connectable-ADV evidence. */
 for(unsigned wrap=0;wrap<2;wrap++){
   uint32_t first=wrap?UINT32_MAX-1000:0;now=first;scan_start();
   named(95,0,true,true,"Fresh ADV mouse");
   assert(candidate_count()==1&&candidates[0].last_connectable_seen==first);
   uint32_t token=candidates[0].public.token;
   for(unsigned response=0;response<3;response++){
     now+=750;named(95,0,false,true,"Fresh ADV mouse");
     assert(candidates[0].seen==now&&candidates[0].last_connectable_seen==first);
   }
   int before=creates;lism_hid_host_select(0,token);pump();
   assert(creates==before&&!peer&&status.pairing);
   /* A genuinely new connectable ADV restores eligibility at the same slot
    * and token; timestamp zero and wrap are values, not sentinel states. */
   raw_ad(95,0,true,0,NULL,0);
   assert(candidates[0].last_connectable_seen==now&&candidates[0].public.token==token);
   lism_hid_host_select(0,token);pump();assert(creates==before+1&&peer);
   lism_hid_host_cancel();pump();done();
 }
 /* Selection at uptime zero is legal before any lease has elapsed. */
 now=0;scan_start();named(96,0,true,true,"Zero timestamp mouse");
 int before=creates;lism_hid_host_select(0,candidates[0].public.token);pump();
 assert(peer&&creates==before+1&&selected_seen==0);
 lism_hid_host_cancel();pump();done();
}
static void preboot_diagnostics(void){
 assert(!settings_ready&&!scan_register_calls&&!registration_attempts);
 /* An uninvoked registration is explicitly represented by calls=0, rather
  * than interpreting a zero-initialized return code as successful setup. */
 scan_diagnostic_snapshot("preboot");
 assert(strstr(last_diagnostic_counts,"register_calls=0"));
 ad(1,true,true);assert(raw_callbacks==1&&!advertisements_seen&&!advertisements.count);
 assert(!scan_needed&&!advertisements_enabled&&!creates&&!unpairs&&!saves);
 init();assert(scan_register_calls==1&&registration_attempts==1);
 assert(scan_register_result==-EALREADY); /* real return, not a synthesized 0 */
 assert(!scan_needed&&!advertisements_enabled&&!creates&&!unpairs&&!saves);
}
static void diagnostic_regression(void){
 assert(!peer&&!have_saved);now=5000;bluetooth_ready=false;
 uint32_t raw_before=(uint32_t)atomic_get(&raw_callbacks);
 unsigned owner_before=owner_diagnostics;scan_start();
 assert(diagnostics_raw_baseline==raw_before&&scan_raw_delta()==0);
 assert(owner_diagnostics==owner_before+1&&strstr(last_diagnostic_gates,"v1 start"));
 assert(strstr(last_diagnostic_gates,"settings=1 peer=0 suspended=0 paused=0 needed=1 adv=1 bt_ready=0"));
 char expected_registration[64];snprintf(expected_registration,sizeof(expected_registration),
       "register_calls=1 register_rc=%d",-EALREADY);
 assert(strstr(last_diagnostic_counts,expected_registration));
 /* The Bluetooth-not-ready diagnostic must not change existing scan policy. */
 assert(scan_needed&&advertisements_enabled);
 raw_ad(97,0,true,0,NULL,0);assert(scan_raw_delta()==1&&advertisements_seen==1);
 atomic_clear(&advertisements_enabled);uint32_t accepted=advertisements_seen;
 raw_ad(97,0,true,0,NULL,0);
 assert(scan_raw_delta()==2&&(uint32_t)advertisements_seen==accepted);
 atomic_clear(&advertisements_enabled);
 /* Direct read-only dump itself cannot scan, connect, mutate settings/bonds
  * or re-enable gates. The normal coordinator remains the sole policy owner. */
 int old_resume=resumes,old_pause=pauses,old_create=creates;
 int old_save=saves,old_delete=deletes,old_unpair=unpairs;
 scan_diagnostic_snapshot("read-only");
 assert(resumes==old_resume&&pauses==old_pause&&creates==old_create);
 assert(saves==old_save&&deletes==old_delete&&unpairs==old_unpair&&!advertisements_enabled);
 pump();bluetooth_ready=true;now+=9999;owner_before=owner_diagnostics;pump();
 assert(owner_diagnostics==owner_before);now++;pump();
 assert(owner_diagnostics==owner_before+1&&strstr(last_diagnostic_gates,"v1 periodic"));
 assert(strstr(last_diagnostic_gates,"bt_ready=1")&&strstr(last_diagnostic_counts,"delta=2 seen=1"));
 now+=50000;pump();assert(!status.pairing&&!scan_needed&&!advertisements_enabled);
 assert(strstr(last_diagnostic_gates,"v1 end")&&strstr(last_diagnostic_gates,"needed=0 adv=0"));
 assert(saves==old_save&&deletes==old_delete&&unpairs==old_unpair&&creates==old_create);
 /* Lifetime counter is never reset at pair. Its window subtraction is
  * unsigned, including callbacks crossing UINT32_MAX while gate is off. */
 atomic_set(&raw_callbacks,-2);scan_start();assert(diagnostics_raw_baseline==UINT32_MAX-1);
 atomic_clear(&advertisements_enabled);
 for(unsigned i=0;i<3;i++){raw_ad(98,0,true,0,NULL,0);atomic_clear(&advertisements_enabled);}
 assert((uint32_t)atomic_get(&raw_callbacks)==1&&scan_raw_delta()==3&&!advertisements_seen);
 scan_diagnostic_snapshot("wrap");assert(strstr(last_diagnostic_counts,"raw=1 delta=3 seen=0"));
 lism_hid_host_cancel();pump();assert(strstr(last_diagnostic_gates,"v1 end"));
 registration_result=0;init();assert(scan_register_calls==2&&registration_attempts==2&&scan_register_result==0);
 scan_diagnostic_snapshot("registered");assert(strstr(last_diagnostic_counts,"register_calls=2 register_rc=0"));
 assert(saves==old_save&&deletes==old_delete&&unpairs==old_unpair&&creates==old_create);
}
int main(void){
 preboot_diagnostics();boot();start();to_subscribe();assert(!saves&&!status.ready);
 rx=1;subscribed_cb(&connection,0,sp);rx=0;assert(!status.ready);pump();
 assert(phase==READY&&status.ready&&input_ready&&saves==1&&have_saved);
 struct input_event kick={INPUT_EV_MSC,INPUT_KICK};bridge_input(&kick,NULL);
 uint8_t release[]={0,0,0,0},move[]={0,3,4,1};
 rx=1;notification(&connection,sp,release,4);notification(&connection,sp,move,4);rx=0;
 bridge_input(&kick,NULL);assert(sends==2&&moves==7);
 /* A full Zephyr input queue may reject a wake while retaining the raw frame.
  * The coordinator tick must issue a fresh wake without a new BLE report. */
 failed_kicks=1;rx=1;notification(&connection,sp,release,4);rx=0;
 assert(!failed_kicks&&frames.count==1);int before_kicks=kicks;
 tick(NULL);pump();assert(kicks>before_kicks&&frames.count==1);
 bridge_input(&kick,NULL);assert(!frames.count&&sends==3&&moves==7);
 /* Endpoint/profile/USB observers only latch input-thread cleanup. Queued
  * motion is purged even if the selected endpoint numeric value is unchanged. */
 rx=1;notification(&connection,sp,move,4);bridge_endpoint_event(NULL);rx=0;
 assert(phase==READY&&input_ready&&frames.count==1);bridge_input(&kick,NULL);
 assert(!frames.count&&sends==3&&require_release&&phase==READY);
 /* Cancel and forget are coordinator commands, never immediate RX writes. */
 bt_addr_le_t identity=saved.addr;lism_hid_host_forget();assert(!unpairs);pump();
 assert(unpairs==1&&deletes==1&&!bt_addr_le_cmp(&forgotten,&identity)&&!have_saved);done();
 start();int dis=disconnects;lism_hid_host_cancel();pump();assert(phase==DISCONNECTING);
 rx=1;connected(&connection,0);rx=0;pump();assert(phase==DISCONNECTING&&disconnects>dis&&!status.ready);done();
 /* Cancellation revokes queued GATT callbacks by epoch, including params
  * pointers retained by the stack until disconnect completion. */
 start();to_map();struct bt_gatt_read_params *old=rp;
 lism_hid_host_cancel();pump();size_t before=map_len;
 rx=1;assert(old->func(&connection,0,old,mouse_map,sizeof(mouse_map))==BT_GATT_ITER_STOP);rx=0;pump();assert(map_len==before);done();
 start();to_subscribe();rx=1;subscribed_cb(&connection,5,sp);rx=0;pump();assert(phase==DISCONNECTING&&!input_ready);done();
 start();to_reference();subscribe_error=-5;uint8_t valid_ref[]={0,1};read_data(valid_ref,2);
 assert(phase==DISCONNECTING&&!input_ready);subscribe_error=0;done();
 start();to_map();read_data(mouse_map,sizeof(mouse_map));
 discover_attr(12,0x2902,0,0,0);pump();assert(phase==DISCONNECTING);done();
 start();to_map();uint8_t malformed[]={0x75};read_data(malformed,1);assert(phase==DISCONNECTING&&status.unsupported);done();
 start();to_map();uint8_t huge[LISM_HID_MAX_MAP_BYTES+1]={0};rx=1;
 assert(rp->func(&connection,0,rp,huge,sizeof(huge))==BT_GATT_ITER_STOP);rx=0;pump();assert(phase==DISCONNECTING);done();
 assert(unpairs==1&&deletes==1); /* Unsupported probes must not erase any bond. */
 /* Token from a prior scan cannot select an identically numbered slot. */
 lism_hid_host_pair();pump();ad(8,true,true);uint32_t stale=candidates[0].public.token;
 lism_hid_host_cancel();pump();lism_hid_host_pair();pump();ad(9,true,true);
 int n=creates;lism_hid_host_select(0,stale);pump();assert(creates==n&&!peer);
 /* Reconnect requires fresh connectable advertisements of the exact saved
  * identity, not unrelated peers or name-only scan responses. */
 saved=(struct saved_peer){.version=1,.addr={.a={{12}}}};have_saved=true;publish_identities();
 lism_hid_host_cancel();pump();lism_hid_host_pair();pump();retry_at=now;
 ad(13,true,true);ad(12,false,true);assert(!peer);ad(12,true,false);assert(peer&&connection.addr.a.val[0]==12);done();
 /* Event queue overflow latches teardown rather than dispatching stale data. */
 reconnect_suspended=false;retry_at=now;ad(12,true,false);assert(peer);
 rx=1;for(unsigned i=0;i<40;i++)security_changed(&connection,2,SEC_OK);rx=0;
 pump();assert(phase==DISCONNECTING&&!input_ready&&event_fault==0);done();
 /* Full command queue cannot discard cancellation or forget. */
 for(unsigned i=0;i<10;i++){lism_hid_host_pair();}lism_hid_host_forget();pump();
 assert(!have_saved&&unpairs==2&&!status.pairing);
 /* Wrap-safe pairing timeout and session deadline. */
 now=UINT32_MAX-100;lism_hid_host_pair();pump();assert(status.pairing);
 now+=59999;pump();assert(status.pairing);now++;pump();assert(!status.pairing);
 assert(elapsed(4,UINT32_MAX-2)&&!elapsed(UINT32_MAX-2,4));
 now=UINT32_MAX-100;start();now+=SESSION_TIMEOUT_MS-1;pump();assert(phase==CONNECTING);
 now++;pump();assert(phase==DISCONNECTING);done();
 assert(!connection.refs);
 advertisement_regression();assert(!connection.refs);
 connectable_lease_regression();assert(!connection.refs);
 diagnostic_regression();assert(!connection.refs);
 puts("PASS actual HID coordinator: callback context, discovery/map/reference/CCC/READY, mouse, cancellation/late callbacks, subscribe/map rejection, named discovery/ADV aggregation/cache expiry/name priority/address type/exact UUIDs/12 stable slots/tokens/diagnostics, exact fresh reconnect, target-only forget, overflow and clock wrap");
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="lism-hid-state-") as directory:
    source = Path(directory) / "test.c"
    binary = Path(directory) / "test"
    source.write_text(prelude + header + runtime + checks)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-function", "-Wno-unused-variable",
                    "-fsanitize=address,undefined", "-I", str(root / "src"),
                    str(source), str(root / "src/lism_hid_parser.c"), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
