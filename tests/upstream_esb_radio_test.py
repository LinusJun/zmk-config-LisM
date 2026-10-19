"""Execute production radio framing and MPSL callback bodies with fake SDK HW.
This checks queue ownership, dedup/backpressure and IRQ-context boundaries, not RF.
"""
from pathlib import Path
import argparse,re,tempfile,subprocess
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);a=p.parse_args();W=a.workspace
source=(ROOT/'src/esb_upstream/radio.c').read_text()
def function(name):
    m=re.search(r'(?m)^(?:static )?[^\n;]+\b'+name+r'\([^;]*?\)\s*\{',source)
    assert m,name
    start=m.start();pos=source.index('{',m.start());depth=1;i=pos+1
    while depth:
        if source[i]=='{':depth+=1
        elif source[i]=='}':depth-=1
        i+=1
    return source[start:i]
def stripped(path):return re.sub(r'^\s*#\s*(?:include|pragma once).*$', '',Path(path).read_text(),flags=re.M)
pre=r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <errno.h>
#include <assert.h>
#include <stdio.h>
#define IS_ENABLED(x) (x)
#define CONFIG_ZMK_BLE 1
#define CONFIG_ESB_MAX_PAYLOAD_LENGTH 64
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define BIT(x) (1U<<(x))
#define ARG_UNUSED(x) (void)(x)
#define K_NO_WAIT 0
#define K_FOREVER 0
#define SLOT_US 4000
#define WIRE_HEADER 20
typedef int atomic_t;
static int atomic_get(atomic_t *p){return *p;}
static int atomic_set(atomic_t *p,int v){int old=*p;*p=v;return old;}
static void atomic_inc(atomic_t *p){++*p;}
static bool fake_irq;
struct k_mutex {int unused;};static struct k_mutex send_mutex;
static void k_mutex_lock(struct k_mutex *m,int t){assert(!fake_irq);}
static void k_mutex_unlock(struct k_mutex *m){assert(!fake_irq);}
struct esb_payload {uint8_t length,pipe;bool noack;uint8_t data[64];};
struct tx_item {struct esb_payload payload;uint8_t retries;uint32_t target_boot;};
static struct {struct tx_item items[32];unsigned count;} tx_queue;
static int k_msgq_put(void *q,const void *p,int timeout){assert(!fake_irq);if(tx_queue.count==32)return -EAGAIN;tx_queue.items[tx_queue.count++]=*(const struct tx_item*)p;return 0;}
struct esb_evt {int evt_id;};
enum {ESB_EVENT_TX_SUCCESS=1,ESB_EVENT_TX_FAILED,ESB_EVENT_RX_RECEIVED};
enum {ESB_PROTOCOL_ESB_DPL,ESB_BITRATE_2MBPS,ESB_MODE_PRX,ESB_MODE_PTX,ESB_TXMODE_MANUAL_START,ESB_TX_POWER_8DBM};
struct esb_config {int protocol,bitrate,mode,tx_mode,retransmit_delay,retransmit_count,tx_output_power;bool selective_auto_ack,use_fast_ramp_up;void(*event_handler)(const struct esb_evt*);};
#define ESB_DEFAULT_CONFIG {0}
static struct esb_payload incoming[70],written[10];static unsigned incoming_n,incoming_cursor,written_n;
static int init_error;
static int esb_init(const struct esb_config *c){return init_error;}
static int esb_set_base_address_0(const uint8_t *p){return 0;}
static int esb_set_base_address_1(const uint8_t *p){return 0;}
static int esb_set_prefixes(const uint8_t *p,size_t n){return 0;}
static int esb_enable_pipes(unsigned mask){assert(mask==3);return 0;}
static int esb_set_rf_channel(int c){assert(c==37);return 0;}
static int esb_write_payload(const struct esb_payload *p){assert(written_n<10);written[written_n++]=*p;return 0;}
static int esb_start_rx(void){return 0;}
static void esb_disable(void){}
static int esb_read_rx_payload(struct esb_payload *p){if(incoming_cursor==incoming_n)return -ENODATA;*p=incoming[incoming_cursor++];return 0;}
#define NRF_RADIO 0
#define NRF_TIMER0 0
#define NRF_TIMER_INT_COMPARE0_MASK 1
#define NRF_TIMER_BIT_WIDTH_32 32
#define NRF_TIMER_CC_CHANNEL0 0
#define NRF_TIMER_EVENT_COMPARE0 0
#define RADIO_IRQn 1
static void NVIC_ClearPendingIRQ(int i){}
static void irq_enable(int i){}
static void nrf_radio_power_set(int r,bool p){}
static void nrf_timer_int_disable(int t,int m){}
static void nrf_timer_int_enable(int t,int m){}
static void nrf_timer_bit_width_set(int t,int b){}
static void nrf_timer_cc_set(int t,int c,int v){}
static void nrf_timer_event_clear(int t,int e){}
static bool nrf_timer_event_check(int t,int e){return true;}
typedef int mpsl_timeslot_session_id_t;
typedef struct {int callback_action;} mpsl_timeslot_signal_return_param_t;
enum {MPSL_TIMESLOT_SIGNAL_ACTION_NONE,MPSL_TIMESLOT_SIGNAL_ACTION_END};
enum {MPSL_TIMESLOT_SIGNAL_START,MPSL_TIMESLOT_SIGNAL_RADIO,MPSL_TIMESLOT_SIGNAL_TIMER0,MPSL_TIMESLOT_SIGNAL_SESSION_IDLE,MPSL_TIMESLOT_SIGNAL_BLOCKED,MPSL_TIMESLOT_SIGNAL_CANCELLED,MPSL_TIMESLOT_SIGNAL_INVALID_RETURN,MPSL_TIMESLOT_SIGNAL_OVERSTAYED};
static unsigned dispatched;
static void fake_radio_irq(const void *p){dispatched++;}
static void *__ptr__radio_dynamic_irq_handler=(void*)fake_radio_irq;
static const uint8_t base0[4]={1},base1[4]={2},prefixes[8]={3};
static atomic_t enabled,ready,irq_error,tx_result,ring_overflow,head,tail,idle=1;
static bool initialized,have_tx;
static struct tx_item current;
static uint32_t boot_id,next_seq=1,previous_boot[2],last_seq[2];
static atomic_t last_boot[2],command_ack_boot,command_ack_seq;
static struct esb_payload receive_ring[64];
static mpsl_timeslot_signal_return_param_t response;
struct esb_msg_prefix {uint8_t magic_prefix[4],payload_size;} __attribute__((packed));
struct esb_msg_postfix {uint32_t crc;} __attribute__((packed));
uint32_t crc32_ieee_update(uint32_t,const uint8_t*,size_t);
'''
driver=W/'module-dongle/src/split/esb'
app=stripped(driver/'app_esb.h')
globals=r'''
static app_esb_callback_t callback;static app_esb_mode_t mode=APP_ESB_MODE_PRX;
static bool accept=true;static unsigned delivered;
static void receive_callback(app_esb_event_t *e){assert(!fake_irq);delivered++;e->accepted=accept;}
bool lism_wire_valid(uint8_t pipe,const uint8_t *p,size_t len){return pipe<2 && len==12;}
'''
main=r'''
static struct esb_payload frame(unsigned pipe,uint32_t boot,uint32_t seq) {
 struct esb_payload p={.pipe=pipe,.length=WIRE_HEADER+12};memcpy(p.data,"LsE2",4);put32(p.data+4,boot);put32(p.data+8,seq);
 struct esb_msg_prefix prefix={.magic_prefix={'Z','m','K','e'},.payload_size=3};
 memcpy(p.data+WIRE_HEADER,&prefix,sizeof(prefix));p.data[WIRE_HEADER+5]=pipe;p.data[WIRE_HEADER+6]=0;p.data[WIRE_HEADER+7]=1;
 struct esb_msg_postfix crc={.crc=crc32_ieee(p.data+WIRE_HEADER,8)};memcpy(p.data+WIRE_HEADER+8,&crc,4);return p;
}
int main(void) {
 callback=receive_callback;boot_id=123;
 uint8_t a[]={1,2,3},b[]={4,5,6};app_esb_data_t p={.pipe=0,.data=a,.len=3,.max_retry=3};
 assert(!zmk_split_esb_send(&p));assert(tx_queue.count==1);
 current=tx_queue.items[0];have_tx=true;p.pipe=1;p.data=b;p.max_retry=0;assert(!zmk_split_esb_send(&p));
 assert(current.payload.pipe==0 && current.retries==3 && tx_queue.items[1].payload.pipe==1);
 assert(get32(current.payload.data+8)==1 && get32(tx_queue.items[1].payload.data+8)==2);
 /* Configuration and the actual MPSL callback must never enter kernel queues/mutexes. */
 fake_irq=true;assert(slot(0,MPSL_TIMESLOT_SIGNAL_START)->callback_action==MPSL_TIMESLOT_SIGNAL_ACTION_NONE);
 slot(0,MPSL_TIMESLOT_SIGNAL_RADIO);assert(dispatched==1);
 incoming[0]=frame(0,100,1);incoming_n=1;
 assert(slot(0,MPSL_TIMESLOT_SIGNAL_TIMER0)->callback_action==MPSL_TIMESLOT_SIGNAL_ACTION_END);
 slot(0,MPSL_TIMESLOT_SIGNAL_SESSION_IDLE);fake_irq=false;
 assert(!initialized && atomic_get(&idle));receive_frames();assert(delivered==1 && last_seq[0]==1);
 /* Retrying a command across slots keeps exactly the same immutable payload. */
 fake_irq=true;slot(0,MPSL_TIMESLOT_SIGNAL_START);slot(0,MPSL_TIMESLOT_SIGNAL_TIMER0);fake_irq=false;
 assert(written_n==2 && !memcmp(&written[0],&written[1],sizeof(struct esb_payload)));
 receive_ring[head]=frame(0,100,1);head=(head+1)%64;receive_frames();assert(delivered==1);
 accept=false;receive_ring[head]=frame(0,100,2);head=(head+1)%64;receive_frames();assert(last_seq[0]==1);
 accept=true;receive_ring[head]=frame(0,100,2);head=(head+1)%64;receive_frames();assert(last_seq[0]==2 && delivered==3);
 receive_ring[head]=frame(0,200,1);head=(head+1)%64;receive_frames();assert(last_boot[0]==200 && last_seq[0]==1);
 receive_ring[head]=frame(0,100,99);head=(head+1)%64;receive_frames();assert(last_boot[0]==200);
 struct esb_payload bad=frame(0,300,100);bad.data[bad.length-1]^=1;receive_ring[head]=bad;head=(head+1)%64;receive_frames();assert(last_boot[0]==200);
 receive_ring[head]=frame(1,400,1);head=(head+1)%64;receive_frames();assert(last_boot[1]==400);
 struct esb_evt hardware_ack={.evt_id=ESB_EVENT_TX_SUCCESS};tx_result=0;radio_event(&hardware_ack);assert(!tx_result);
 struct esb_payload confirm=frame(0,200,2);put32(confirm.data+12,boot_id);put32(confirm.data+16,get32(current.payload.data+8));
 receive_ring[head]=confirm;head=(head+1)%64;receive_frames();assert(tx_result==1);
 /* Half rejects a command targeted at its previous boot before queueing it. */
 mode=APP_ESB_MODE_PTX;unsigned before=delivered;
 struct esb_payload command=frame(0,600,1);put32(command.data+12,boot_id-1);
 receive_ring[head]=command;head=(head+1)%64;receive_frames();assert(delivered==before);
 put32(command.data+12,boot_id);receive_ring[head]=command;head=(head+1)%64;receive_frames();
 assert(delivered==before+1 && (uint32_t)command_ack_boot==600 && command_ack_seq==1);
 assert(!zmk_split_esb_send(&p));assert(get32(tx_queue.items[2].payload.data+12)==600 && get32(tx_queue.items[2].payload.data+16)==1);
 fake_irq=true;init_error=-EIO;assert(slot(0,MPSL_TIMESLOT_SIGNAL_START)->callback_action==MPSL_TIMESLOT_SIGNAL_ACTION_END);fake_irq=false;assert(irq_error==-EIO);
 next_seq=0;assert(zmk_split_esb_send(&p)==-EAGAIN);
 puts("PASS: production radio API immutable TX ownership, 2 pipes, MPSL IRQ0 no kernel queues, slot stop/retry, dedup, RX-backpressure retry, application command ACK, old-boot command rejection, CRC before sequence and wrap guard");
}
'''
names=['put32','get32','collect_rx','radio_event','configure_radio','end_slot','slot','receive_frames','zmk_split_esb_send']
code='\n'.join([pre,app,globals,stripped(W/'zephyr/lib/crc/crc32_sw.c')]+[function(n) for n in names]+[main])
with tempfile.TemporaryDirectory() as d:
 d=Path(d);(d/'test.c').write_text(code)
 subprocess.run(['cc','-std=c11','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-Werror=implicit-function-declaration',str(d/'test.c'),'-o',str(d/'test')],check=True)
 subprocess.run([str(d/'test')],check=True)
