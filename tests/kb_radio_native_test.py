"""Execute actual two-link framing, deduplication, ACK admission and slot setup."""
from pathlib import Path
import runpy,sys,tempfile,subprocess,re
P=Path(__file__).resolve().parents[1]
# Shared fixture uses the actual pinned transport ABI.
b=runpy.run_path(str(P/'tests/kb_route_native_test.py'));pre=b['pre'];function=b['function'];radio=b['radio'];hub=b['hub'];link=b['link']
common=Path('/work/workspace/module-right/src/split/esb/common.h').read_text();common=common[common.index('#define ZMK_SPLIT_ESB_ENVELOPE_MAGIC_PREFIX'):common.index('typedef void (*zmk_split_esb_process')]
pre+=common+r'''
#include "/work/source/src/kb_route/private_signal.h"
static struct kb_private_signal_cache private_left_signal;
static uint32_t k_uptime_get_32(void){return (uint32_t)now;}
#include "/work/source/src/kb_route/mirror.h"
struct esb_payload {uint8_t pipe,length,rssi;bool noack;uint8_t data[64];};
typedef struct {uint8_t pipe;const uint8_t *data;size_t len;} app_esb_data_t;
typedef struct {int evt_type;unsigned pipe;uint8_t *buf;size_t data_length;bool accepted;} app_esb_event_t;
#define APP_ESB_EVT_RX 0
#define APP_ESB_EVT_TX_SUCCESS 1
#define APP_ESB_EVT_TX_FAIL 2
struct hub_tx {struct esb_payload p;uint32_t peer,generation,local_boot;uint8_t station;int64_t since;};
struct hub_link {uint32_t boot,next_seq,last_boot[2],previous_boot[2],last_seq[2],ack_boot[2],ack_seq[2];struct hub_tx tx;bool have;atomic_t result;uint32_t success,fail;};
static struct hub_link links[2];static struct hub_tx latest_mirror;static bool mirror_pending;
struct hub_rx {struct esb_payload p;uint8_t link,station;uint32_t boot,sampled_ms;};
static struct hub_rx rx[64];static atomic_t head,tail,uplink_generation;static int tx_lock;static uint32_t left_route_epoch,control_last_seq;static uint8_t left_route_phase,left_route_target,left_route_station;static uint32_t left_route_receiver_boot;
static atomic_t station_selected,station_requested;static int64_t uplink_seen;
struct queue;static void k_msgq_purge(struct queue*q);
static uint32_t sys_rand32_get(void){static uint32_t n=900;return ++n;}
static int atomic_inc(atomic_t*p);static uint8_t kb_uplink_station_current(void){return station_selected;}
static bool kb_uplink_is_probe(const uint8_t*p,size_t n){(void)p;(void)n;return false;}static int64_t next_left_control;
struct queue {struct hub_tx items[64];unsigned size,head,count;};static struct queue left_tx={.size=16},dongle_tx={.size=64};
static int k_msgq_put(struct queue *q,const struct hub_tx*t,int wait){(void)wait;if(q->count==q->size)return -ENOSPC;q->items[(q->head+q->count++)%64]=*t;return 0;}
static int k_msgq_get(struct queue *q,struct hub_tx*t,int wait){(void)wait;if(!q->count)return -ENOMSG;*t=q->items[q->head++%64];q->count--;return 0;}
static void k_msgq_purge(struct queue*q){q->head=q->count=0;}
static uint32_t crc32_ieee(const void*data,size_t n){uint32_t c=~0U;const uint8_t*p=data;for(size_t i=0;i<n;i++){c^=p[i];for(int b=0;b<8;b++)c=(c>>1)^((c&1)?0xedb88320:0);}return ~c;}
static unsigned deliveries[2];static bool reject;
static void native_callback(app_esb_event_t *e){if(!reject){deliveries[0]++;e->accepted=true;}}
static void (*callback)(app_esb_event_t*)=native_callback;
static bool kb_uplink_command(uint8_t pipe,const uint8_t*p,size_t n,uint32_t ep,uint32_t peer){(void)ep;(void)peer;(void)pipe;(void)p;(void)n;if(reject)return false;deliveries[1]++;return true;}
'''
pre+='\nstatic uint32_t kb_route_command_epoch(void){return 0;}\n'
receiver=function((P/'src/esb_upstream/radio.c').read_text(),'receive_frames')
# Compile the actual dongle CRC/source/session/dedup receiver in a separate
# modeled device context, then drive it with the real hub next_tx output.
for name in ['receive_frames','receive_ring','head','tail','diag_rx','diag_valid','diag_accepted','last_boot','previous_boot','last_seq','mode','have_tx','current','tx_result','boot_id','command_ack_boot','command_ack_seq','send_mutex','callback']:
    receiver=re.sub(r'\b'+name+r'\b','d_'+name,receiver)
pre+=r'''
#define WIRE_HEADER LISM_WIRE_HEADER
#define CONFIG_LISM_DONGLE_STATION 0
#define APP_ESB_MODE_PRX 0
#define APP_ESB_MODE_PTX 1
static int atomic_inc(atomic_t*p){return (*p)++;}
static atomic_t d_head,d_tail,d_diag_rx,d_diag_valid,d_diag_accepted,d_last_boot[2],d_tx_result,d_command_ack_boot,d_command_ack_seq;
static uint32_t d_previous_boot[2],d_last_seq[2],d_boot_id;
static struct esb_payload d_receive_ring[64];static int d_mode=APP_ESB_MODE_PRX,d_send_mutex;static bool d_have_tx;
static struct {struct esb_payload payload;} d_current;
static struct k_spinlock signal_lock;static struct {int8_t dbm;int64_t seen;} signals[2];
static unsigned d_mirrors,d_events;
static void d_callback(app_esb_event_t*e){struct esb_event_envelope env={0};memcpy(&env,e->buf,e->data_length-sizeof(struct esb_msg_postfix));d_events++;if(env.payload.event.type==KB_MIRROR_EVENT)d_mirrors++;e->accepted=true;}
'''
code=pre+'\n'+(P/'src/esb_upstream/hub_motion.inc').read_text()+'\n'+ '\n'.join(function(t,n) for t,n in [(radio,'get32'),(radio,'put32'),(link,'lism_valid_event'),((P/'src/esb_upstream/common.c').read_text(),'lism_wire_valid'),(hub,'kb_uplink_valid'),(radio,'private_signal_tuple'),(radio,'valid_body'),(radio,'receive'),(radio,'enqueue'),(radio,'kb_uplink_send'),(radio,'next_tx'),(radio,'tx_live'),(radio,'apply_station_request')])+'\n'+receiver
main=r'''
static struct esb_payload frame(unsigned net,unsigned source,uint32_t peer_boot,uint32_t seq,uint32_t target,bool command){
 struct esb_payload p={.pipe=source};uint8_t body[64]={0};size_t n;
 if(command){struct esb_command_envelope e={.prefix={.magic_prefix="ZmKe",.payload_size=1+sizeof(e.payload.cmd.type)},.payload={.source=source,.cmd={.type=ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_POLL_EVENTS}}};n=sizeof(e.prefix)+e.prefix.payload_size;memcpy(body,&e,n);}
 else {struct esb_event_envelope e={.prefix={.magic_prefix="ZmKe",.payload_size=1+sizeof(e.payload.event.type)+sizeof(struct lism_snapshot)},.payload={.source=source,.event={.type=127}}};struct lism_snapshot s={.keys=1,.battery=81};memcpy(&e.payload.event.data,&s,sizeof(s));n=sizeof(e.prefix)+e.prefix.payload_size;memcpy(body,&e,n);}
 struct esb_msg_postfix crc={.crc=crc32_ieee(body,n)};memcpy(body+n,&crc,sizeof(crc));n+=sizeof(crc);
 p.length=20+n;memcpy(p.data,"LsE3",4);put32(p.data+4,peer_boot);put32(p.data+8,seq);put32(p.data+12,target);memcpy(p.data+20,body,n);return p;
}
static void feed(unsigned net,struct esb_payload p){unsigned h=head;rx[h]=(struct hub_rx){.p=p,.link=net,.boot=links[net].boot};head=(h+1)%64;receive();}
int main(void){
 links[0].boot=100;links[1].boot=200;d_boot_id=400;links[0].next_seq=links[1].next_seq=1;
 struct esb_payload p=frame(0,0,300,1,0,false);feed(0,p);assert(deliveries[0]==1&&links[0].last_boot[0]==300&&!links[1].last_boot[0]);feed(0,p);assert(deliveries[0]==1);
 p.data[25]^=1;feed(0,p);assert(deliveries[0]==1);p.data[25]^=1;
 p=frame(0,0,301,1,0,false);feed(0,p);assert(deliveries[0]==2);p=frame(0,0,300,2,0,false);feed(0,p);assert(deliveries[0]==2); /* previous boot replay */
 p=frame(1,1,400,1,999,true);feed(1,p);assert(!deliveries[1]);put32(p.data+12,200);
 reject=true;feed(1,p);assert(!deliveries[1]&&!links[1].last_seq[1]&&!links[1].ack_seq[1]);reject=false;feed(1,p);assert(deliveries[1]==1&&links[1].ack_boot[1]==400&&links[1].ack_seq[1]==1);feed(1,p);assert(deliveries[1]==1);
 /* Same bytes on a different link never enter the wrong native callback. */
 p=frame(0,1,400,2,200,true);feed(0,p);assert(deliveries[0]==2);
 uint8_t bytes[2]={1,2};app_esb_data_t d={.pipe=1,.data=bytes,.len=2};assert(!enqueue(1,&d,false));assert(links[1].next_seq==1&&links[0].next_seq==1);struct hub_tx tx;assert(next_tx(1,&tx));assert(links[1].next_seq==2);assert(get32(tx.p.data+12)==400&&get32(tx.p.data+16)==1);
 uplink_generation=1;assert(!tx_live(1,&tx,now));tx.generation=1;assert(tx_live(1,&tx,now));assert(!tx_live(1,&tx,now+501));
 /* Telemetry is a single replaceable slot and never consumes the reliable queue. */
 struct zmk_split_transport_peripheral_event e={.type=KB_MIRROR_EVENT};for(int i=0;i<1000;i++){e.data.input_event.value=i;assert(!kb_uplink_send(1,&e));}assert(!dongle_tx.count&&mirror_pending);assert(next_tx(1,&tx)&&!mirror_pending);assert(!next_tx(1,&tx));assert(tx.p.length<=64);
 struct hub_tx ordinary={.p.pipe=1,.generation=1,.station=0,.local_boot=200,.since=now};assert(!k_msgq_put(&dongle_tx,&ordinary,0));assert(!enqueue(1,&d,true));assert(next_tx(1,&tx)&&tx.p.length==0);assert(next_tx(1,&tx)&&tx.p.length>0); /* releases/control first */
 dongle_tx.size=1;assert(!enqueue(1,&d,false));uint32_t seq=links[1].next_seq;assert(enqueue(1,&d,false)==-ENOSPC&&links[1].next_seq==seq);

 /* Actual publish -> mirror -> snapshots order must survive the dongle's
  * real per-pipe de-duplication; retrying the selected frame stays duplicate. */
 assert(next_tx(1,&tx)); /* drain reliable queue from full-queue check */
 dongle_tx.size=64;
 uint32_t previous=0;
 for(int cycle=0;cycle<100;cycle++){
  e=(struct zmk_split_transport_peripheral_event){.type=KB_ROUTE_EVENT};assert(!kb_uplink_send(1,&e));
  e.type=KB_MIRROR_EVENT;assert(!kb_uplink_send(1,&e));
  e.type=LISM_SNAPSHOT_TYPE;assert(!kb_uplink_send(1,&e));assert(!kb_uplink_send(1,&e));
  unsigned selected=0;
  while(next_tx(1,&tx)){
   uint32_t wire=get32(tx.p.data+8);assert(wire>previous);previous=wire;selected++;
   d_receive_ring[d_head]=tx.p;d_head=(d_head+1)%64;d_receive_frames();
   unsigned accepted=d_events;
   d_receive_ring[d_head]=tx.p;d_head=(d_head+1)%64;d_receive_frames();assert(d_events==accepted);
   if(selected==4){ /* producer races after mirror selection, before its TX */
    e.type=LISM_SNAPSHOT_TYPE;assert(!kb_uplink_send(1,&e));
   }
  }
  assert(selected==5&&d_mirrors==(unsigned)cycle+1);
 }
 assert(d_events==500);
 /* Fresh application ACK must not wait behind enqueue-time metadata. */
 assert(!enqueue(1,&d,false));links[1].ack_seq[1]=77;assert(next_tx(1,&tx));assert(get32(tx.p.data+16)==77);
 /* Continuous packed motion coalesces, but never across key/button barriers. */
 struct zmk_split_transport_peripheral_event move={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT,.data.input_event={.reg=1,.type=INPUT_EV_REL,.code=INPUT_REL_Z,.sync=1,.value=(uint32_t)(uint16_t)-2<<16 | 3}};
 for(unsigned i=0;i<100;i++)assert(!kb_uplink_send(1,&move));assert(motion_pending&&!dongle_tx.count);assert(next_tx(1,&tx));
 struct esb_event_envelope decoded;assert(motion_event(&tx,&decoded));assert((int16_t)((uint32_t)decoded.payload.event.data.input_event.value>>16)==-200 && (int16_t)decoded.payload.event.data.input_event.value==300);assert(!next_tx(1,&tx));
 assert(!kb_uplink_send(1,&move));e=(struct zmk_split_transport_peripheral_event){.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,.data.key_position_event={.position=3,.pressed=1}};assert(!kb_uplink_send(1,&e));assert(!kb_uplink_send(1,&move));
 assert(next_tx(1,&tx)&&motion_event(&tx,&decoded));assert(next_tx(1,&tx)&&!motion_event(&tx,&decoded));assert(next_tx(1,&tx)&&motion_event(&tx,&decoded));assert(!next_tx(1,&tx));
 /* Saturated barrier retry cannot duplicate the already-flushed motion. */
 dongle_tx.size=1;assert(!kb_uplink_send(1,&move));assert(kb_uplink_send(1,&e)==-ENOSPC&&!motion_pending);assert(kb_uplink_send(1,&e)==-ENOSPC);assert(next_tx(1,&tx)&&motion_event(&tx,&decoded));assert(!kb_uplink_send(1,&e));assert(next_tx(1,&tx)&&!motion_event(&tx,&decoded));assert(!next_tx(1,&tx));dongle_tx.size=64;
 /* Overflow splits rather than truncating signed16; only unselected motion expires. */
 move.data.input_event.value=(uint32_t)30000<<16;assert(!kb_uplink_send(1,&move));assert(!kb_uplink_send(1,&move));assert(dongle_tx.count==1&&motion_pending);assert(next_tx(1,&tx));assert(next_tx(1,&tx));
 assert(!kb_uplink_send(1,&move));now+=41;uint32_t seq_before=links[1].next_seq;assert(!next_tx(1,&tx)&&links[1].next_seq==seq_before);
 assert(!kb_uplink_send(1,&move));uplink_generation++;assert(!next_tx(1,&tx));

 /* A station mutation invalidates queued motion, mirror, retry and RX origin. */
 struct hub_tx old=tx;old.generation=uplink_generation;old.station=0;old.local_boot=200;old.since=now;
 assert(tx_live(1,&old,now));
 assert(!kb_uplink_send(1,&move));e.type=KB_MIRROR_EVENT;assert(!kb_uplink_send(1,&e));
 links[1].have=true;station_requested=1;apply_station_request();
 assert(station_selected==1 && links[1].boot!=200 && !links[1].have && !motion_pending && !mirror_pending && !dongle_tx.count);
 assert(!tx_live(1,&old,now));
 unsigned delivered=deliveries[1];struct esb_payload stale=frame(1,1,400,99,links[1].boot,true);
 rx[head]=(struct hub_rx){.p=stale,.link=1,.station=0,.boot=links[1].boot};head=(head+1)%64;receive();assert(deliveries[1]==delivered);
 puts("PASS actual two-link wire functions: isolated boot/seq/ACK, duplicate+replay rejection, CRC/source/direction checks, full-queue retry admission, stale epoch/age, telemetry coalescing, release priority, actual hub-to-dongle priority interleaving/dedup and producer-after-selection race");return 0;}
'''
with tempfile.TemporaryDirectory() as tmp:
 f=Path(tmp)/'wire.c';out=Path(tmp)/'wire';f.write_text(code+main);subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',str(f),'-o',str(out)],check=True);subprocess.run([str(out)],check=True)
