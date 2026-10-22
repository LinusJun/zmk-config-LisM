"""Execute actual left radio loop with fake ESB IRQs/clock, never real RF."""
from pathlib import Path
import runpy,tempfile,subprocess
P=Path(__file__).resolve().parents[1];b=runpy.run_path(str(P/'tests/kb_route_native_test.py'));function=b['function'];s=(P/'src/esb_upstream/left_radio.inc').read_text()
common=Path('/work/workspace/module-left/src/split/esb/common.h').read_text();common=common[common.index('#define ZMK_SPLIT_ESB_ENVELOPE_MAGIC_PREFIX'):common.index('typedef void (*zmk_split_esb_process')]
pre=b['pre']+r'''
#include <setjmp.h>
#include <stdlib.h>
#define K_MSEC(x) (x)
#define ESB_EVENT_TX_SUCCESS 1
#define ESB_EVENT_TX_FAILED 2
#define ESB_EVENT_RX_RECEIVED 3
#define APP_ESB_EVT_TX_SUCCESS 1
#define APP_ESB_EVT_RX 2
struct esb_payload{uint8_t pipe,length;bool noack;uint8_t data[64];};
struct esb_evt{int evt_id;};
typedef struct{int evt_type;uint8_t pipe;uint8_t*buf;size_t data_length;bool accepted;}app_esb_event_t;
typedef void(*app_esb_callback_t)(app_esb_event_t*);
struct queue{unsigned size,n,head;uint8_t bytes[64][128];};
#define K_MSGQ_DEFINE(name,bytes,count,align) static struct queue name={.size=bytes}
static int k_msgq_put(struct queue*q,const void*p,int w){(void)w;if(q->n==64)return -ENOSPC;memcpy(q->bytes[(q->head+q->n++)%64],p,q->size);return 0;}
static int k_msgq_get(struct queue*q,void*p,int w){(void)w;if(!q->n)return -ENOMSG;memcpy(p,q->bytes[q->head++%64],q->size);q->n--;return 0;}
static void k_msgq_purge(struct queue*q){q->n=q->head=0;}
static unsigned k_msgq_num_used_get(struct queue*q){return q->n;}
static void atomic_clear(atomic_t*p){*p=0;}
static uint32_t crc32_ieee(const void*d,size_t n){uint32_t v=~0U;const uint8_t*p=d;while(n--){v^=*p++;for(int k=0;k<8;k++)v=(v>>1)^((v&1)?0xedb88320:0);}return ~v;}
static unsigned warnings;
#undef LOG_WRN
#define LOG_WRN(...) warnings++
#define LOG_ERR(...) ((void)0)
static uint8_t dongle_base0[4],dongle_base1[4],private_base0[4],private_base1[4],prefixes[8];
#define ESB_DEFAULT_CONFIG {0}
#define ESB_PROTOCOL_ESB_DPL 0
#define ESB_BITRATE_2MBPS 0
#define ESB_MODE_PTX 0
#define ESB_TXMODE_MANUAL_START 0
#define ESB_TX_POWER_8DBM 0
struct esb_config{unsigned protocol,bitrate,mode,tx_mode,retransmit_delay,retransmit_count,tx_output_power;bool selective_auto_ack,use_fast_ramp_up;void(*event_handler)(const struct esb_evt*);};
static int esb_init(struct esb_config*c){assert(!c->retransmit_count);return 0;}
static int esb_set_base_address_0(const void*p){(void)p;return 0;}static int esb_set_base_address_1(const void*p){(void)p;return 0;}
static int esb_set_prefixes(const void*p,unsigned n){(void)p;assert(n==8);return 0;}static int esb_enable_pipes(unsigned n){assert(n==1);return 0;}
static int esb_set_rf_channel(unsigned n){assert(n==37);return 0;}static void esb_disable(void){}
static struct esb_payload fifo[8];static unsigned fn,fh;static int esb_read_rx_payload(struct esb_payload*p){if(!fn)return -ENOMSG;*p=fifo[fh++%8];fn--;return 0;}
static int clocks_start(void){return 0;}static unsigned rng=23;static uint32_t sys_rand32_get(void){return rng++;}
static void lism_notify_status(void){}static void lism_ack_seen(void){}
static bool lism_wire_valid(unsigned pipe,const void*p,size_t n){(void)p;return !pipe&&n>=9;}
static struct esb_payload sent;static bool active_tx;static int esb_write_payload(const struct esb_payload*p){sent=*p;return 0;}
static int esb_start_tx(void){active_tx=true;return 0;}static void esb_flush_tx(void){active_tx=false;}
static void received_callback(app_esb_event_t*e){e->accepted=true;}
static jmp_buf stop;static unsigned budget,steps,failed;static bool phase_mode,missing_control_callbacks;static unsigned phase_offset,phase_period,control_success;static bool fail_forever;static uint32_t first_seq,last_seq_seen;static unsigned successes,control_between;static bool press_delivered,release_delivered;
static void k_sleep(int ms);
'''+common
# Take real state, policy, producer, SPSC IRQ receive, decode, formatter and radio loop.
defs=s[s.index('static app_esb_callback_t callback;'):s.index('static void event(')]
code=pre+defs+'\n'.join(function(s,n) for n in ['event','configure','receive','frame','radio_thread'])
main=r'''
static void k_sleep(int ms){
 now+=ms;if(steps++>=budget)longjmp(stop,1);if(!active_tx)return;active_tx=false;
 struct esb_event_envelope env={0};memcpy(&env,sent.data+20,sent.length-24);
 if((int)env.payload.event.type==KB_LEFT_CONTROL){if(phase_mode){unsigned phase=(now+phase_offset)%phase_period;if(phase<1 || phase>3){event(&(struct esb_evt){ESB_EVENT_TX_FAILED});return;}control_success++;}if(failed && !press_delivered)control_between++;if(missing_control_callbacks)return;event(&(struct esb_evt){ESB_EVENT_TX_SUCCESS});return;}
 assert((int)env.payload.event.type==KB_LEFT_DATA);
 struct kb_left_packet p;memcpy(&p,&env.payload.event.data,sizeof(p));uint32_t seq=get32(sent.data+8);
 if(kb_left_keys(&p)){
  if(!first_seq)first_seq=seq;assert(seq==first_seq);
  if(fail_forever || failed++<28){event(&(struct esb_evt){ESB_EVENT_TX_FAILED});return;}
  press_delivered=true;
 }else if(press_delivered){release_delivered=true;assert(seq>first_seq);}
 last_seq_seen=seq;successes++;event(&(struct esb_evt){ESB_EVENT_TX_SUCCESS});
}
static struct esb_payload command(uint32_t authority,uint32_t seq,uint32_t epoch,unsigned phase,unsigned target){
 struct esb_payload p={.pipe=0};struct esb_command_envelope env={.prefix={.magic_prefix="ZmKe"},.payload={.source=0,.cmd={.type=ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR}}};
 __typeof__(env.payload.cmd.data.invoke_behavior) b={0};strcpy(b.behavior_dev,"KB_LEFT_ROUTE3");b.param1=(phase<<4)|(target<<2)|(target?2:0);b.param2=epoch;b.position=target?0:777;b.state=1;memcpy(&env.payload.cmd.data.invoke_behavior,&b,sizeof(b));
 env.prefix.payload_size=1+sizeof(env.payload.cmd.type)+sizeof(b);size_t n=sizeof(env.prefix)+env.prefix.payload_size;
 p.length=20+n+4;memcpy(p.data,"LsE3",4);put32(p.data+4,authority);put32(p.data+8,seq);put32(p.data+12,boot_id);put32(p.data+16,epoch);memcpy(p.data+20,&env,n);struct esb_msg_postfix crc={.crc=crc32_ieee(p.data+20,n)};memcpy(p.data+20+n,&crc,sizeof(crc));return p;
}
int main(int argc,char**argv){
 if(argc==4){phase_mode=true;phase_period=atoi(argv[1]);phase_offset=atoi(argv[2]);rng=atoi(argv[3]);callback=received_callback;enabled=1;budget=1500;now=1;if(!setjmp(stop))radio_thread(NULL,NULL,NULL);assert(control_success);return 0;}
 if(argc==2)missing_control_callbacks=true;
 callback=received_callback;enabled=1;policy=(struct kb_left_policy){.authority=99,.epoch=1,.phase=KB_LEFT_APPLIED,.target=0,.station=0,.receiver_boot=777,.prepared=true};route_generation=1;
 struct zmk_split_transport_peripheral_event key={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,.data.key_position_event={.position=4,.pressed=1}};
 kb_left_track(&key);key.data.key_position_event.pressed=0;kb_left_track(&key);
 budget=600;now=1;coordinator_seen=1;if(!setjmp(stop))radio_thread(NULL,NULL,NULL);
 assert(press_delivered&&release_delivered&&control_between&&successes==2&&!warnings&&last_seq_seen>first_seq);
 /* Capture the ACK's provenance at IRQ enqueue, then change the active net. */
 fifo[0]=command(88,1,2,0,0);fn=1;fh=0;current_net=0;event(&(struct esb_evt){ESB_EVENT_RX_RECEIVED});current_net=1;receive();
 assert(command_boot[0]==88&&!command_boot[1]&&policy.authority==99);
 fifo[0]=command(100,1,1,0,1);fn=1;fh=0;current_net=1;event(&(struct esb_evt){ESB_EVENT_RX_RECEIVED});current_net=0;receive();
 assert(command_boot[1]==100&&policy.authority==100&&policy.phase==KB_LEFT_FREEZE);
 puts("PASS actual left radio loop: immutable unacknowledged press retained across 28 failures and control probes, release ordered after ACK; actual IRQ RX provenance survives switching both ways");
}
'''
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'left.c';o=Path(t)/'left';f.write_text(code+main)
 subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True);subprocess.run([str(o)],check=True)
 subprocess.run([str(o),'missing-control-callbacks'],check=True)
 for period in range(19,26):
  for offset in range(period):
   for seed in [1,11,23]:
    subprocess.run([str(o),str(period),str(offset),str(seed)],check=True,capture_output=True)
 print('PASS actual left control scheduler: 462 deterministic clock-phase/jitter cases find 4ms private window within modeled 1.5s; no physical RF/timing proof')
