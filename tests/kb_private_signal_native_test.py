"""Compile actual private helper, hub receive/getter and framing admission.
Mock only transport ABI fixture, clock, mutex, queue callback and uplink command.
"""
from pathlib import Path
import subprocess,tempfile,json,hashlib
P=Path(__file__).resolve().parents[1]
ns={"__file__":str(P/'tests/kb_route_native_test.py')}
exec((P/'tests/kb_route_native_test.py').read_text().split('route_pre=')[0],ns)
pre=ns['pre'];function=ns['function'];radio=(P/'src/esb_upstream/hub_radio.inc').read_text()
common=Path('/work/workspace/module-right/src/split/esb/common.h').read_text()
pre+=common[common.index('#define ZMK_SPLIT_ESB_ENVELOPE_MAGIC_PREFIX'):common.index('typedef void (*zmk_split_esb_process')]
pre+=r'''
#include "/work/source/src/kb_route/private_signal.h"
#include "/work/source/src/kb_route/mirror.h"
static uint32_t k_uptime_get_32(void){return (uint32_t)now;}
struct esb_payload {uint8_t pipe,length,rssi;bool noack;uint8_t data[64];};
typedef struct {int evt_type;unsigned pipe;uint8_t *buf;size_t data_length;bool accepted;} app_esb_event_t;
#define APP_ESB_EVT_RX 0
struct hub_tx {struct esb_payload p;};
struct hub_link {uint32_t boot,last_boot[2],previous_boot[2],last_seq[2],ack_boot[2],ack_seq[2];struct hub_tx tx;bool have;atomic_t result;};
static struct hub_link links[2];
struct hub_rx {struct esb_payload p;uint8_t link,station;uint32_t boot,sampled_ms;};
static struct hub_rx rx[64];static atomic_t head,tail;static int tx_lock;
static uint32_t left_route_epoch,control_last_seq,left_route_receiver_boot;static uint8_t left_route_phase,left_route_target,left_route_station;static int64_t next_left_control;
static struct kb_private_signal_cache private_left_signal;
static bool reject;static unsigned accepted;
static void cb(app_esb_event_t *e){e->accepted=!reject;if(e->accepted)accepted++;}
static void (*callback)(app_esb_event_t*)=cb;
static uint8_t kb_uplink_station_current(void){return 0;}
static bool kb_uplink_is_probe(const uint8_t*p,size_t n){(void)p;(void)n;return false;}
static uint32_t kb_route_command_epoch(void){return 0;}
static bool kb_uplink_command(unsigned pipe,const uint8_t*p,size_t n,uint32_t ep,uint32_t boot){(void)pipe;(void)p;(void)n;(void)ep;(void)boot;return false;}
static bool kb_uplink_valid(unsigned p,const uint8_t*b,size_t n){(void)p;(void)b;(void)n;return false;}
static uint32_t crc32_ieee(const void*data,size_t n){uint32_t c=~0U;const uint8_t*p=data;for(size_t i=0;i<n;i++){c^=p[i];for(int b=0;b<8;b++)c=(c>>1)^((c&1)?0xedb88320:0);}return ~c;}
'''
code=pre+'\n'+'\n'.join(function(t,n) for t,n in [
(radio,'private_signal_tuple'),(radio,'kb_private_left_signal'),(radio,'kb_left_set_route'),(radio,'get32'),(radio,'put32'),(ns['link'],'lism_valid_event'),((P/'src/esb_upstream/common.c').read_text(),'lism_wire_valid'),(radio,'valid_body'),(radio,'receive')])
main=r'''
static struct kb_left_control ctl(void){return (struct kb_left_control){.physical={.authority=100,.epoch=10,.keys={1},.buttons=3,.battery=80,.station=255},.phase=KB_LEFT_APPLIED,.target=1,.prepared=1};}
static struct esb_payload frame(struct kb_left_control c,uint32_t peer,uint32_t seq,unsigned mag){
 struct esb_payload p={.pipe=0,.rssi=mag};
 struct esb_event_envelope e={.prefix={.magic_prefix="ZmKe",.payload_size=1+sizeof(e.payload.event.type)+sizeof(c)},.payload={.source=0,.event={.type=KB_LEFT_CONTROL}}};
 memcpy(&e.payload.event.data,&c,sizeof(c));size_t n=sizeof(e.prefix)+e.prefix.payload_size;
 memcpy(p.data,"LsE3",4);put32(p.data+4,peer);put32(p.data+8,seq);memcpy(p.data+20,&e,n);
 struct esb_msg_postfix crc={.crc=crc32_ieee(p.data+20,n)};memcpy(p.data+20+n,&crc,sizeof(crc));p.length=20+n+sizeof(crc);return p;
}
static void feed(struct esb_payload p,uint32_t boot,uint32_t sampled){rx[head]=(struct hub_rx){.p=p,.boot=boot,.sampled_ms=sampled};head=(head+1)%64;receive();}
static int8_t dbm;static bool get(void){return kb_private_left_signal(&dbm);}
static uint32_t seq;
static void sample(unsigned mag){feed(frame(ctl(),300,++seq,mag),100,(uint32_t)now);}
static void reset(void){memset(links,0,sizeof(links));memset(&private_left_signal,0,sizeof(private_left_signal));links[0].boot=100;seq=0;head=tail=0;reject=false;now=5000;kb_left_set_route(10,KB_LEFT_COMMIT,1,255,0);}
int main(void){
 reset();sample(1);assert(get()&&dbm==-1&&private_left_signal.raw_dbm==-1);now+=1000;sample(126);assert(get()&&dbm==-1&&private_left_signal.raw_dbm==-126);now+=1000;sample(80);now+=1000;sample(70);assert(get()&&dbm==-70);
 now+=1499;assert(get());now++;assert(!get());sample(45);assert(get()&&dbm==-45);
 /* Invalid magnitudes never create a sample or refresh TTL. */
 for(int mag=0;mag<=127;mag+=127){reset();sample(mag);assert(!get());sample(50);now+=1499;sample(mag);now++;assert(!get());}
 /* All semantic mismatches are rejected, even when callback accepts them. */
 for(int bad=0;bad<12;bad++){
  reset();struct kb_left_control c=ctl();switch(bad){case 0:c.physical.epoch=9;break;case 1:c.physical.epoch=11;break;case 2:c.physical.authority=101;break;case 3:c.target=0;break;case 4:c.physical.station=0;c.physical.receiver_boot=77;break;case 5:c.physical.receiver_boot=77;break;case 6:c.prepared=0;break;case 7:c.phase=KB_LEFT_COMMIT;break;case 8:c.physical.buttons=32;break;case 9:c.physical.battery=101;break;case 10:c.physical.keys[5]=0x80;break;case 11:c.prepared=2;break;}
  feed(frame(c,300,++seq,50),100,now);assert(!get());
  sample(60);assert(get());now+=1499;feed(frame(c,300,++seq,50),100,now);now++;assert(!get());
 }
 reset();struct esb_payload p=frame(ctl(),300,++seq,50);p.data[p.length-1]^=1;feed(p,100,now);assert(!get());
 reset();p=frame(ctl(),300,++seq,50);feed(p,99,now);assert(!get());reject=true;feed(p,100,now);assert(!get());reject=false;feed(p,100,now);assert(get());now+=1499;feed(p,100,now);now++;assert(!get());
 reset();sample(50);now+=1499;p=frame(ctl(),301,1,70);feed(p,100,now);assert(get()&&dbm==-70);p=frame(ctl(),300,100,80);feed(p,100,now);assert(get()&&dbm==-70);now+=1500;assert(!get());
 reset();sample(50);now+=1499;p=frame(ctl(),300,++seq,70);p.data[p.length-1]^=1;feed(p,100,now);now++;assert(!get());
 reset();sample(50);now+=1499;reject=true;sample(70);now++;assert(!get());
 /* Request delay is conservatively counted; queue residence cannot renew. */
 reset();feed(frame(ctl(),300,++seq,50),100,now-1499);assert(get());now++;assert(!get());
 reset();feed(frame(ctl(),300,++seq,50),100,now-1500);assert(!get());
 reset();now=UINT32_MAX-100;sample(50);now+=1499;assert(get());now++;assert(!get());
 reset();now=UINT32_MAX-100;sample(50);for(int i=0;i<3;i++){now+=1000;sample(60+i);}assert(get()&&dbm==-62);
 /* Route setter invalidates even an A->B->A transition with no getter in B. */
 for(int change=0;change<5;change++){
  reset();sample(50);assert(get());switch(change){case 0:kb_left_set_route(10,0,1,255,0);break;case 1:kb_left_set_route(11,1,1,255,0);break;case 2:kb_left_set_route(10,1,0,0,77);break;case 3:kb_left_set_route(10,1,1,0,77);break;case 4:kb_left_set_route(10,1,1,255,77);break;}
  kb_left_set_route(10,1,1,255,0);assert(!get());sample(80);assert(get()&&dbm==-80);
 }
 reset();sample(50);links[0].boot=101;assert(!get());links[0].boot=100;assert(!get());
 reset();sample(50);links[0].last_boot[0]=301;assert(!get());links[0].last_boot[0]=300;assert(!get());
 reset();sample(50);struct kb_left_control future=ctl();future.physical.epoch=11;feed(frame(future,300,++seq,70),100,now);kb_left_set_route(11,1,1,255,0);assert(!get());
 printf("private RSSI actual receive/helper/getter: PASS; cache=%zu tuple=%zu rx=%zu\n",sizeof(private_left_signal),sizeof(private_left_signal.tuple),sizeof(struct hub_rx));
}
'''
# Static scheduling guard: single requester writes time only after idle check and
# idle=0. collect only copies atomic time; terminal signals alone release idle.
collect=function(radio,'collect');thread=function(radio,'radio_thread');slot=function(radio,'slot')
assert 'k_uptime' not in collect and 'k_mutex' not in collect
assert 'sampled_ms=(uint32_t)atomic_get(&grant_requested_ms)' in collect
assert thread.count('mpsl_timeslot_request(session,&request)')==1
assert 'atomic_set(&idle,0);atomic_set(&grant_requested_ms,k_uptime_get_32());' in thread
assert thread.index('if(atomic_get(&idle))')<thread.index('atomic_set(&grant_requested_ms')
assert 'MPSL_TIMESLOT_SIGNAL_CANCELLED:atomic_set(&idle,1)' in slot
with tempfile.TemporaryDirectory() as d:
 c=Path(d)/'test.c';exe=Path(d)/'test';c.write_text(code+'\n'+main)
 subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Wno-unused-function','-Wno-unused-variable','-Werror','-Wno-type-limits','-fsanitize=address,undefined','-I'+str(P/'src'),str(c),'-o',str(exe)],check=True)
 run=subprocess.run([str(exe)],check=True,capture_output=True,text=True)
 print(run.stdout,end='')
 out=P.parent/'verification';out.mkdir(exist_ok=True)
 (out/'private-rssi-v50417.log').write_text(run.stdout+'Static grant lifetime and IRQ0 timestamp checks: PASS\nMock scope: clock, mutex, callback queue acceptance, uplink calls; actual pinned ABI, wire validator, CRC, hub receive/getter, helper, route setter. No RF/MPSL runtime or firmware build.\n')
 (out/'private-rssi-v50417-result.json').write_text(json.dumps({'passed':True,'test':'actual C receive/helper/getter; ASan+UBSan','result':run.stdout.strip(),'mock_scope':['clock','mutex','queue callback','uplink calls'],'limitations':['MPSL lifecycle checked statically; no RF hardware test or firmware build'],'sha256':{str(f.relative_to(P)):hashlib.sha256(f.read_bytes()).hexdigest() for f in [P/'src/kb_route/private_signal.h',P/'src/esb_upstream/hub_radio.inc']}},indent=2)+'\n')
