"""Execute actual old/new HOG producers and workers with delayed consumption.

Connection objects, queue contention and notifications are modeled. This
checks routing and reference ownership, not over-the-air delivery or latency.
"""
from pathlib import Path
import argparse, hashlib, json, re, subprocess, sys, tempfile
P=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);W=p.parse_args().workspace
sys.path.insert(0,str(P/'source/tools'))
from prepare_hog_routing import function
generated=next((W/'build-dongle').rglob('lism_hog_routing.c'))
new=generated.read_text();old=(W/'zmk/app/src/hog.c').read_text()
ep=next((W/'build-dongle').rglob('lism_endpoints.c')).read_text()
for kind in ['keyboard','consumer','mouse']:
    assert f'zmk_hog_send_{kind}_report_to_profile(&{kind}_report->body, current_instance.ble.profile_index)' in ep
# The entire GATT service and descriptors remain byte-for-byte upstream.
service=lambda t:t[t.index('BT_GATT_SERVICE_DEFINE('):t.index('K_THREAD_STACK_DEFINE(hog_q_stack')]
assert service(new)==service(old)
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#define ZMK_BLE_PROFILE_COUNT 5
#define CONFIG_ZMK_BLE_KEYBOARD_REPORT_QUEUE_SIZE 20
#define CONFIG_ZMK_BLE_CONSUMER_REPORT_QUEUE_SIZE 5
#define CONFIG_ZMK_BLE_MOUSE_REPORT_QUEUE_SIZE 20
#define K_NO_WAIT 0
#define K_MSEC(n) (n)
#define BT_ID_DEFAULT 0
#define BT_SECURITY_L2 2
#define LOG_WRN(...) ((void)0)
#define LOG_DBG(...) ((void)0)
struct zmk_hid_keyboard_report_body {uint8_t data[8];};
struct zmk_hid_consumer_report_body {uint8_t data[4];};
struct zmk_hid_mouse_report_body {uint8_t data[6];};
typedef struct {int peer;} bt_addr_le_t;
static bt_addr_le_t addresses[5]={{1},{2},{3},{4},{5}},any={0};
#define BT_ADDR_LE_ANY (&any)
static int bt_addr_le_cmp(const bt_addr_le_t*a,const bt_addr_le_t*b){return a->peer-b->peer;}
static uint8_t active;
static uint32_t epoch=1;
static uint32_t lism_input_merge_generation(void){return epoch;}
static int zmk_ble_active_profile_index(void){return active;}
static bt_addr_le_t*zmk_ble_profile_address(uint8_t p){assert(p<5);return &addresses[p];}
struct bt_conn {int id,refs;bool connected;};
static struct bt_conn connections[5];
static struct bt_conn*bt_conn_lookup_addr_le(int id,const bt_addr_le_t*a){
 assert(id==0);for(int i=0;i<5;i++)if(connections[i].id==a->peer && connections[i].connected){connections[i].refs++;return &connections[i];}return NULL;
}
static struct bt_conn*zmk_ble_active_profile_conn(void){return bt_conn_lookup_addr_le(0,&addresses[active]);}
static void bt_conn_unref(struct bt_conn*c){assert(c && c->refs>0);c->refs--;}
static int security_requests;
static int bt_conn_set_security(struct bt_conn*c,int level){assert(c->refs>0 && level==2);security_requests++;return 0;}
struct k_work {int unused;};struct k_work_q{int unused;};
static struct k_work_q hog_work_q;
#define K_WORK_DEFINE(name,fn) static struct k_work name
static int k_work_submit_to_queue(struct k_work_q*q,struct k_work*w){return 1;}
struct fake_queue {unsigned count,capacity,size;unsigned char data[24][64];};
#define K_MSGQ_DEFINE(name,sz,num,align) static struct fake_queue name={.capacity=num,.size=sz}
static int forced_errors[2],forced_count,forced_index;
static bool race_drain;
static int k_msgq_put(struct fake_queue*q,const void*p,int timeout){
 assert(q->size<=64);
 if(forced_index<forced_count)return forced_errors[forced_index++];
 if(q->count==q->capacity)return -EAGAIN;
 memcpy(q->data[q->count++],p,q->size);return 0;
}
static int k_msgq_get(struct fake_queue*q,void*p,int timeout){
 if(race_drain){
  // Model a consumer winning the race between failed put and overflow pop.
  for(unsigned i=0;i<q->count;i++){struct bt_conn*c;memcpy(&c,q->data[i],sizeof(c));bt_conn_unref(c);}
  q->count=0;race_drain=false;return -ENOMSG;
 }
 if(!q->count)return -ENOMSG;
 memcpy(p,q->data[0],q->size);q->count--;
 memmove(q->data[0],q->data[1],q->count*64);return 0;
}
static struct {int attrs[20];} hog_svc;
struct bt_gatt_notify_params {void*attr;const void*data;unsigned len;};
static struct {int peer,value;} sent[128];static unsigned sent_count;
static int notify_error;
static int bt_gatt_notify_cb(struct bt_conn*c,struct bt_gatt_notify_params*n){
 assert(c->refs>0);
 if(!c->connected)return -ENOTCONN;
 if(notify_error){int e=notify_error;notify_error=0;return e;}
 sent[sent_count].peer=c->id;sent[sent_count++].value=*(const uint8_t*)n->data;return 0;
}
static void reset(void){
 for(int i=0;i<5;i++){assert(!connections[i].refs);connections[i]=(struct bt_conn){.id=i+1,.connected=true};}
 active=0;sent_count=0;forced_count=forced_index=0;race_drain=false;notify_error=0;security_requests=0;
}
static void balanced(void){for(int i=0;i<5;i++)assert(!connections[i].refs);}
'''
def declarations(t,kind,modern):
    queue='zmk_hog_'+kind+'_msgq'
    m=re.search(r'K_MSGQ_DEFINE\('+queue+r',.*?\);',t,re.S);assert m
    out=m.group(0)+'\n'
    if modern:
        m=re.search(r'struct lism_hog_'+kind+r'_item \{.*?\};',t,re.S);assert m
        out=m.group(0)+'\n'+out
    return out

results=[]
for kind in ['keyboard','consumer','mouse']:
    name='zmk_hog_send_'+kind+'_report'
    worker='send_'+kind+'_report_callback'
    queue='zmk_hog_'+kind+'_msgq'
    body='struct zmk_hid_'+kind+'_report_body'
    common=declarations(new,kind,True)+'\n'+function(new,worker)+'\nK_WORK_DEFINE(hog_'+kind+'_work,'+worker+');\n'
    common+=function(new,'lism_queue_'+kind)+'\n'+function(new,name+'_to_profile')+'\n'+function(new,name)+'\n'
    main=r'''
int main(void){
 reset();BODY report={{42}};
 assert(!SEND(&report));report.data[0]=99;active=4;
 BODY zero={{0}};assert(!TO(&zero,0));report.data[0]=7;assert(!SEND(&report));
 WORK(NULL);assert(sent_count==3);
 assert(sent[0].peer==1 && sent[0].value==42);
 assert(sent[1].peer==1 && sent[1].value==0);
 assert(sent[2].peer==5 && sent[2].value==7);balanced();
 // Rapid return: queued snapshots never look up the new active profile.
 reset();for(int i=0;i<3;i++){active=i==1?4:0;report.data[0]=i+1;assert(!SEND(&report));}
 WORK(NULL);assert(sent_count==3 && sent[0].peer==1 && sent[1].peer==5 && sent[2].peer==1);balanced();
 // A disconnected old object remains referenced, and does not stop new work.
 reset();assert(!SEND(&report));connections[0].connected=false;active=4;assert(!SEND(&report));
 WORK(NULL);assert(sent_count==1 && sent[0].peer==5);balanced();
 reset();QUEUE.capacity=2;
 for(int i=0;i<3;i++){report.data[0]=i;assert(!SEND(&report));}
 assert(connections[0].refs==2);WORK(NULL);assert(sent_count==2 && sent[0].value==1 && sent[1].value==2);balanced();
 // Consumer wins overflow pop; the producer neither leaks nor double-unrefs.
 reset();assert(!SEND(&report));assert(!SEND(&report));race_drain=true;
 assert(!SEND(&report));WORK(NULL);balanced();
 reset();forced_errors[0]=-EAGAIN;forced_errors[1]=-ENOMEM;forced_count=2;
 assert(SEND(&report)==-ENOMEM);balanced();
 reset();forced_errors[0]=-EIO;forced_count=1;assert(SEND(&report)==-EIO);balanced();
 reset();notify_error=-EPERM;assert(!SEND(&report));WORK(NULL);assert(security_requests==1);balanced();
 reset();notify_error=-ENOMEM;assert(!SEND(&report));WORK(NULL);balanced();
 reset();assert(TO(&report,5)==-ERANGE);addresses[4].peer=0;assert(TO(&report,4)==-ENOTCONN);addresses[4].peer=5;
 connections[4].connected=false;assert(TO(&report,4)==-ENOTCONN);balanced();
 reset();epoch=1;report.data[0]=42;assert(!SEND(&report));
 memset(&zero,0,sizeof(zero));assert(!TO(&zero,0));
 epoch=2;active=4;report.data[0]=7;assert(!SEND(&report));WORK(NULL);
 assert(sent_count==2 && sent[0].peer==1 && sent[0].value==0 && sent[1].peer==5 && sent[1].value==7);balanced();
 assert(lism_queue_KIND(&report,4,1)==-ESTALE);
 puts("PASS: exact target, copied payload, old zero, rapid switch, disconnected items, overflow races/errors and balanced refs");
}
'''
    for a,b in [('BODY',body),('SEND',name),('TO',name+'_to_profile'),('WORK',worker),('QUEUE',queue),('KIND',kind)]:main=main.replace(a,b)
    oldcode=declarations(old,kind,False)+'\n'+function(old,worker)+'\nK_WORK_DEFINE(hog_'+kind+'_work,'+worker+');\n'+function(old,name)
    oldmain=f'''int main(void){{reset();{body} report={{{{42}}}};assert(!{name}(&report));active=4;{worker}(NULL);assert(sent_count==1 && sent[0].peer==5);balanced();puts("OLD negative control: queued profile1 report incorrectly sent to profile5");}}'''
    with tempfile.TemporaryDirectory() as d:
        outputs=[]
        for version,code in [('old',oldcode+oldmain),('new',common+main)]:
            c,exe=Path(d)/(version+'.c'),Path(d)/version;c.write_text(pre+'\n'+code)
            subprocess.run(['cc','-std=c11','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-Werror=implicit-function-declaration',str(c),'-o',str(exe)],check=True)
            r=subprocess.run([str(exe)],capture_output=True,text=True,check=True);outputs.append(r.stdout)
        results.append({'kind':kind,'passed':True,'output':outputs})
report={'passed':True,'actual_old_and_new_workers_executed':True,'kinds':results,'GATT_service_and_descriptor_unchanged':True,
        'generated_HOG_sha256':hashlib.sha256(generated.read_bytes()).hexdigest(),
        'explicit_endpoint_profile_for_all_three_kinds':True,
        'scope':'Modeled queue/BT objects/notify API; actual producers and workers. Does not guarantee congested queue release delivery, radio latency, host UI or pairing.'}
(W/'hog-routing-test.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
