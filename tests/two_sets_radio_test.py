"""Execute actual receiver parsing/admission/offer/TX functions for four pipes.
Queues, scheduler and physical MMIO are modeled; RF/hardware not established.
"""
from pathlib import Path
import runpy,tempfile,subprocess
P=Path(__file__).resolve().parents[1]
b=runpy.run_path(str(P/'tests/kb_route_native_test.py'));function=b['function']
r=(P/'src/esb_upstream/radio.c').read_text()
common=(P.parent/'workspace/module-dongle_a/src/split/esb/common.h').read_text();common=common[common.index('#define ZMK_SPLIT_ESB_ENVELOPE_MAGIC_PREFIX'):common.index('typedef void (*zmk_split_esb_process')]
pre=b['pre']+common+r'''
#include "kb_route/owner_policy.h"
#undef CONFIG_LISM_KB_HUB
#define CONFIG_LISM_KB_HUB 0
#define KB_MIRROR_EVENT 125
struct kb_mirror {uint32_t epoch;uint8_t version,wpm,layer,mods,indicators,profile,host_connected,left_battery,left_online;} __packed;
#define CONFIG_LISM_DONGLE_STATION STATION
#define WIRE_HEADER LISM_WIRE_HEADER
#define APP_ESB_MODE_PRX 0
#define APP_ESB_MODE_PTX 1
#define APP_ESB_EVT_RX 0
struct esb_payload {uint8_t pipe,length,rssi;bool noack;uint8_t data[64];};
struct tx_item {struct esb_payload payload;uint8_t retries;uint32_t target_boot,generation;int64_t expires;bool discovery;};
typedef struct {uint8_t pipe;const uint8_t *data;size_t len;unsigned max_retry;} app_esb_data_t;
typedef struct {unsigned evt_type,pipe;uint8_t*buf;size_t data_length;bool accepted;}app_esb_event_t;
static atomic_t command_generation,retiring,published_owner,diag_rx,diag_valid,diag_accepted,head,tail,last_boot[4],command_ack_boot,command_ack_seq,tx_result;
static uint32_t previous_boot[4],last_seq[4],boot_id=400,next_seq=1;
static struct kb_owner_policy owner;static int owner_mutex,send_mutex;static int64_t retired_at;
static struct esb_payload receive_ring[64];static struct tx_item current;static bool have_tx;static int mode=APP_ESB_MODE_PRX;
static struct k_spinlock signal_lock;static struct {int8_t dbm;int64_t seen;}signals[2];
static int atomic_inc(atomic_t*p){return (*p)++;}static bool atomic_cas(atomic_t*p,int a,int b){if(*p!=a)return false;*p=b;return true;}
static uint32_t crc32_ieee(const void*d,size_t n){uint32_t v=~0U;const uint8_t*p=d;while(n--){v^=*p++;for(int i=0;i<8;i++)v=(v>>1)^((v&1)?0xedb88320:0);}return ~v;}
static struct {unsigned count;struct tx_item items[32];}probe_queue,tx_queue;
static int k_msgq_get(void*q,struct tx_item*t,int w){(void)w;__typeof__(probe_queue)*s=q;if(!s->count)return -ENOMSG;*t=s->items[0];memmove(s->items,s->items+1,(--s->count)*sizeof(*t));return 0;}
static unsigned k_msgq_num_used_get(void*q){return ((__typeof__(probe_queue)*)q)->count;}
static int k_msgq_put(void*q,const struct tx_item*t,int w){(void)w;__typeof__(probe_queue)*s=q;if(s->count>=32)return -ENOSPC;s->items[s->count++]=*t;return 0;}
static void kb_receiver_fence_commands(void){command_generation++;}
static bool kb_receiver_admitting(void){return !retiring;}
static uint32_t kb_receiver_route_epoch(void){return owner.epoch;}
static unsigned deliveries[2];static bool reject;static struct zmk_split_transport_peripheral_event delivered[2];
static void callback(app_esb_event_t*e){assert(e->pipe<2);if(reject)return;struct esb_event_envelope env={0};memcpy(&env,e->buf,e->data_length-sizeof(struct esb_msg_postfix));deliveries[e->pipe]++;delivered[e->pipe]=env.payload.event;e->accepted=true;}
'''
code=pre+'\n'.join(function(t,n) for t,n in [(r,'get32'),(r,'put32'),(b['link'],'lism_valid_event'),((P/'src/esb_upstream/common.c').read_text(),'lism_wire_valid'),(r,'retire_owner'),(r,'tx_owner_live'),(r,'take_next_tx'),(r,'probe_preemptible'),(r,'offer_probe'),(r,'receive_frames'),(r,'zmk_split_esb_send')])
main=r'''
static struct esb_payload frame(unsigned set,unsigned hand,unsigned type,uint32_t sender,uint32_t seq,uint32_t target){
 struct esb_event_envelope e={.prefix={.magic_prefix="ZmKe"},.payload={.source=hand,.event={.type=type}}};size_t bytes;
 if(type==KB_PROBE_EVENT){struct kb_discovery d={.version=3,.cookie=91,.authority=100+set,.station=STATION};memcpy(&e.payload.event.data,&d,sizeof(d));bytes=sizeof(d);}
 else if(type==KB_ROUTE_EVENT){struct kb_route_status s={.version=3,.mode=2,.profile=0,.epoch=7,.authority=100+set,.phase=0,.target=0,.station=STATION,.owner_station=STATION,.receiver_boot=target};memcpy(&e.payload.event.data,&s,sizeof(s));bytes=sizeof(s);}
 else {struct lism_snapshot s={.keys=1,.battery=80};memcpy(&e.payload.event.data,&s,sizeof(s));bytes=sizeof(s);}
 e.prefix.payload_size=1+sizeof(e.payload.event.type)+bytes;size_t n=sizeof(e.prefix)+e.prefix.payload_size;
 struct esb_payload p={.pipe=set*2+hand,.length=20+n+sizeof(struct esb_msg_postfix)};
 memcpy(p.data,kb_set_magic(set),4);put32(p.data+4,sender);put32(p.data+8,seq);put32(p.data+12,target);memcpy(p.data+20,&e,n);
 struct esb_msg_postfix crc={.crc=crc32_ieee(p.data+20,n)};memcpy(p.data+20+n,&crc,sizeof(crc));return p;
}
static void feed(struct esb_payload p){receive_ring[head]=p;head=(head+1)%64;receive_frames();}
static void reset_device(void){owner=(struct kb_owner_policy){0};memset(last_boot,0,sizeof(last_boot));memset(last_seq,0,sizeof(last_seq));memset(previous_boot,0,sizeof(previous_boot));retiring=0;published_owner=0;head=tail=0;probe_queue.count=tx_queue.count=0;deliveries[0]=deliveries[1]=0;boot_id++;now+=10000;}
int main(void){
 for(unsigned winner=0;winner<2;winner++){
  reset_device();
  struct esb_payload p=frame(0,1,KB_PROBE_EVENT,10,1,0);feed(p);p=frame(1,1,KB_PROBE_EVENT,11,1,0);feed(p);
  assert(!owner.bound && probe_queue.count==2 && !last_boot[1]&&!last_boot[3]&&!deliveries[1]);
  for(unsigned i=0;i<2;i++){struct tx_item*t=&probe_queue.items[i];assert(t->discovery&&t->payload.pipe==i*2+1);assert(!memcmp(t->payload.data,kb_set_magic(i),4));assert(get32(t->payload.data+12)==10+i);assert(tx_owner_live(t));struct esb_command_envelope e={0};memcpy(&e,t->payload.data+20,t->payload.length-20-sizeof(struct esb_msg_postfix));assert(e.payload.source==1&&!strcmp(e.payload.cmd.data.invoke_behavior.behavior_dev,"KB_PROBE3")&&e.payload.cmd.data.invoke_behavior.param1==91&&e.payload.cmd.data.invoke_behavior.param2==boot_id);}
  /* Wrong magic on a valid physical pipe and stale receiver boot cannot claim. */
  p=frame(winner,1,KB_ROUTE_EVENT,10+winner,1,boot_id);p.data[3]^=3;feed(p);assert(!owner.bound);p.data[3]^=3;put32(p.data+12,boot_id-1);feed(p);assert(!owner.bound);
  feed(frame(winner,1,KB_ROUTE_EVENT,10+winner,1,boot_id));assert(owner.bound&&owner.set==winner&&deliveries[1]==1);
  assert(!tx_owner_live(&probe_queue.items[winner^1]));
  int64_t seen=owner.seen;uint32_t seq=last_seq[winner*2+1];
  for(unsigned n=0;n<100;n++){now++;feed(frame(winner^1,1,KB_PROBE_EVENT,11-winner,n+1,0));feed(frame(winner^1,1,KB_ROUTE_EVENT,11-winner,n+1,boot_id));feed(frame(winner^1,0,127,90,n+1,boot_id));}
  assert(owner.seen==seen&&last_seq[winner*2+1]==seq&&deliveries[1]==1&&!deliveries[0]);
  feed(frame(winner,0,127,80,1,boot_id));assert(deliveries[0]==1&&last_boot[winner*2]==80); /* both sets use same seq=1 safely on separate pipes */
  p=frame(winner,1,127,10+winner,2,boot_id);reject=true;feed(p);assert(last_seq[winner*2+1]==1);reject=false;feed(p);assert(deliveries[1]==2);feed(p);assert(deliveries[1]==2);
  /* Physical source must match logical source, even with valid magic + CRC. */
  p=frame(winner,1,127,10+winner,3,boot_id);p.pipe=winner*2;feed(p);assert(deliveries[0]==1&&deliveries[1]==2);
  uint8_t data[2]={1,2};app_esb_data_t d={.pipe=1,.data=data,.len=2};assert(!zmk_split_esb_send(&d));struct tx_item *t=&tx_queue.items[0];assert(t->payload.pipe==winner*2+1&&!memcmp(t->payload.data,kb_set_magic(winner),4));assert(get32(t->payload.data+12)==10+winner);
  struct tx_item normal=*t,selected;
  current=probe_queue.items[winner];have_tx=true;assert(probe_preemptible());
  assert(take_next_tx(&selected)&&!selected.discovery&&selected.payload.pipe==winner*2+1);have_tx=false;
  assert(take_next_tx(&selected)&&selected.discovery&&selected.payload.pipe==winner*2+1);
  assert(!take_next_tx(&selected));
  current=selected;have_tx=true;assert(!probe_preemptible());have_tx=false;
  /* A discovery reply expires independently of the 3s normal command age. */
  probe_queue.items[0]=(struct tx_item){.discovery=true,.generation=command_generation,.target_boot=10+winner,.expires=now+250,.payload.pipe=winner*2+1};probe_queue.count=1;
  now+=251;assert(!take_next_tx(&selected));t=&normal;
  now=seen+2500;feed(frame(winner^1,1,KB_PROBE_EVENT,11-winner,999,0));assert(retiring&&owner.retired&&!kb_receiver_admitting()&&!tx_owner_live(t));assert(zmk_split_esb_send(&d)==-EAGAIN);
  unsigned count=deliveries[1];feed(frame(winner^1,1,KB_ROUTE_EVENT,11-winner,1000,boot_id));feed(frame(winner,1,127,10+winner,1000,boot_id));assert(deliveries[1]==count);
  uint32_t old_boot=boot_id;reset_device();feed(frame(winner^1,1,KB_ROUTE_EVENT,11-winner,1001,old_boot));assert(!owner.bound);feed(frame(winner^1,1,KB_ROUTE_EVENT,11-winner,1002,boot_id));assert(owner.bound&&owner.set==(winner^1));
 }
 /* Free receivers reject every unsolicited BLE display status; no preview
  * can race a new claim after reset or create a restart loop. */
 reset_device();
 struct esb_payload preview=frame(0,1,KB_ROUTE_EVENT,10,1,boot_id);
 struct kb_route_status bs={.version=3,.mode=1,.phase=1,.target=1,.station=STATION,.owner_station=255,.receiver_boot=boot_id,.authority=100,.epoch=7};
 memcpy(preview.data+20+sizeof(struct esb_msg_prefix)+1+sizeof(enum zmk_split_transport_peripheral_event_type),&bs,sizeof(bs));
 struct esb_msg_postfix pc={.crc=crc32_ieee(preview.data+20,preview.length-20-sizeof(pc))};memcpy(preview.data+preview.length-sizeof(pc),&pc,sizeof(pc));
 for(unsigned n=0;n<100;n++){feed(preview);feed(frame(1,1,KB_PROBE_EVENT,11,n+1,0));assert(!owner.bound&&!retiring);}
 feed(frame(1,1,KB_ROUTE_EVENT,11,101,boot_id));assert(owner.bound&&owner.set==1&&!retiring);
 /* Existing owner switches to direct BLE; an input-seeking other set gets
  * NO old-boot offer before retirement, so its coordinator stays in HUNT. */
 owner.target=1;unsigned old_offers=probe_queue.count;
 feed(frame(0,1,KB_PROBE_EVENT,10,1,0));assert(retiring&&probe_queue.count==old_offers);
 reset_device();feed(frame(0,1,KB_PROBE_EVENT,10,2,0));assert(!retiring&&!owner.bound&&probe_queue.count==1&&get32(probe_queue.items[0].payload.data+4)==boot_id);
 puts("PASS actual four-pipe receiver: directed set/boot/cookie offers; sole A/B accepts either set; atomic winner; busy traffic cannot renew/evict; physical/logical/magic/CRC/boot isolation; native queue backpressure+dedup; physical TX binding; irreversible retire/new-boot-only takeover");
}
'''
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'radio.c';out=Path(t)/'radio';f.write_text(code+main)
 for slot in [0,1]:
  subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',f'-DSTATION={slot}','-I',str(P/'src'),str(f),'-o',str(out)],check=True)
  subprocess.run([str(out)],check=True)

# Negative controls demonstrate these assertions detect removal of the actual
# physical-set wire gate and the actual short discovery expiry, respectively.
with tempfile.TemporaryDirectory() as t:
 for label,old,new in [('wrong-set-magic','memcmp(p.data,kb_set_magic(p.pipe/2),4)','0'),('stale-offer','k_uptime_get()>t->expires','false')]:
  assert old in code
  f=Path(t)/(label+'.c');out=Path(t)/label;f.write_text(code.replace(old,new)+main)
  subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined','-DSTATION=0','-I',str(P/'src'),str(f),'-o',str(out)],check=True)
  result=subprocess.run([str(out)],capture_output=True,text=True,timeout=20)
  assert result.returncode!=0 and 'Assertion' in result.stderr,(label,result.stdout,result.stderr)
  print('PASS negative control detects',label)
