"""Pinned SDK PTX retry identity joined to its real PRX ACK FIFO decisions.

Run in lism-esb-v50: python3 /work/source/tests/left_retry_pid_sdk_test.py.
Full SDK functions execute natively with MMIO mocked; PRX receives captured PTX
PDUs and an explicitly supplied CRC. No RF, interrupt concurrency or elapsed-time
model: this does not establish delivery, a 350 ms pause or the 1500 ms root cause.
The bounded application retry loop below is a policy fixture, not production code.
"""
from pathlib import Path
import hashlib
import subprocess
import sys
import tempfile

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P / 'tools'))
from prepare_hog_routing import function
from prepare_timeslot_sdk import SDK_SHA

source = Path('/sdk/nrf/subsys/esb/esb.c').read_bytes()
assert hashlib.sha256(source).hexdigest() == SDK_SHA
sdk = source.decode()
rx = function(sdk, 'on_radio_disabled_rx')
# Exactly the actual PID+CRC decision/state-update block (the surrounding RADIO
# checks are outside this native event fixture, as in prx_ack_sdk_test.py).
decision = rx[rx.index('\tpipe_info = &rx_pipe_info'):rx.index('\n\t/* Check if an ack should be sent */')]
pre = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#define CONFIG_ESB_TX_FIFO_SIZE 4
#define CONFIG_ESB_RX_FIFO_SIZE 4
#define CONFIG_ESB_MAX_PAYLOAD_LENGTH 64
#define CONFIG_ESB_PIPE_COUNT 2
#define CONFIG_ESB_NEVER_DISABLE_TX 0
#define PID_MAX 3
#define ESB_PROTOCOL_ESB 0
#define ESB_PROTOCOL_ESB_DPL 1
#define ESB_MODE_PTX 0
#define ESB_TXMODE_AUTO 0
#define ESB_TXMODE_MANUAL 1
#define ESB_STATE_IDLE 0
#define ESB_STATE_PTX_TXIDLE 1
#define ESB_STATE_PTX_TX_ACK 2
#define ESB_STATE_PTX_TX 3
#define IS_ENABLED(x) (x)
#define BIT(n) (1U<<(n))
#define NRF_RADIO 0
#define ESB_RADIO_EVENT_END 1
#define NRF_RADIO_EVENT_READY 2
#define NRF_RADIO_EVENT_ADDRESS 3
#define NRF_RADIO_EVENT_PAYLOAD 4
#define NRF_RADIO_EVENT_DISABLED 5
#define NRF_RADIO_SHORT_DISABLED_RXEN_MASK 1
#define NRF_RADIO_INT_DISABLED_MASK 2
#define ESB_RADIO_INT_END_MASK 4
#define ESB_SHORT_DISABLE_MASK 8
#define NRF_RADIO_TASK_START 0
#define NRF_RADIO_STATE_TXRU 1
#define NRF_TIMER_HAS_SHUTDOWN 0
#define NRF_TIMER_TASK_STOP 0
#define NRF_TIMER_TASK_CLEAR 1
#define NRF_TIMER_TASK_START 2
#define NRF_TIMER_EVENT_COMPARE1 0
#define NRF_TIMER_SHORT_COMPARE1_STOP_MASK 1
#define NRF_TIMER_SHORT_COMPARE1_CLEAR_MASK 2
#define NRF_TIMER_CC_CHANNEL1 1
#define INT_TX_SUCCESS_MSK 1
#define INT_TX_FAILED_MSK 2
#define INT_RX_DATA_RECEIVED_MSK 4
#define ESB_RADIO_IRQ_NUMBER 1
#define RADIO_BASE_FREQUENCY 2400
#define RF_CHANNEL_UPDATE_FLAG 0
#define ERRATA_216_PRESENT 0
#define TX_FAST_RAMP_UP_TIME_US 40
#define TX_RAMP_UP_TIME_US 130
#define ADDR_EVENT_LATENCY_US 10
#define ERRATA_216_MIN_TIME_TO_DISABLE_US 20
struct esb_payload {unsigned pipe,length,pid;bool noack;uint8_t data[64];};
struct esb_radio_pdu {union {struct {unsigned length,pid,no_ack;} dpl_pdu;
 struct {unsigned pid;} fixed_pdu;} type;uint8_t data[64];};
struct pipe_info {uint32_t crc;uint8_t pid;bool ack_payload;};
struct payload_wrap {struct esb_payload *p_payload;bool in_use;struct payload_wrap *p_next;};
static struct payload_wrap ack_pl_wrap[4],*ack_pl_wrap_pipe[2];
static struct {unsigned back,front,count;struct esb_payload *payload[4];} tx_fifo,rx_fifo;
static struct pipe_info rx_pipe_info[2];
static unsigned pids[2];
static struct {unsigned protocol,mode,tx_mode,payload_length,retransmit_count,retransmit_delay;
 bool selective_auto_ack,use_fast_ramp_up;} esb_cfg;
static bool esb_initialized=true,fast_switching=false,packet_end,crc_ok;
static unsigned interrupt_flags,esb_state,retransmits_remaining,last_tx_attempts;
static unsigned radio_shorts_common,wait_for_ack_timeout_us,rx_crc,starts,mmio_calls;
static struct {unsigned rf_channel,rf_channel_flags;} esb_addr;
static struct {unsigned p_reg;} esb_timer;
static uint8_t tx_payload_buffer[sizeof(struct esb_radio_pdu)],rx_payload_buffer[sizeof(struct esb_radio_pdu)];
static struct esb_payload *current_payload;
static void (*on_radio_disabled)(void),(*on_timer_compare1)(void);
static void start_tx_transaction(void);
static unsigned irq_lock(void){return 0;} static void irq_unlock(unsigned k){(void)k;}
#define STUB0(n) static void n(void){mmio_calls++;}
#define STUB1(n,T) static void n(T a){(void)a;mmio_calls++;}
#define STUB2(n,T,U) static void n(T a,U b){(void)a;(void)b;mmio_calls++;}
STUB0(esb_ppi_for_wait_for_ack_clear)
STUB0(mpsl_fem_lna_configuration_clear)
STUB0(mpsl_fem_disable)
STUB0(errata216_off)
STUB0(set_evt_interrupt)
STUB0(update_radio_tx_power)
STUB0(esb_fem_for_tx_retry)
STUB0(esb_ppi_for_retransmission_set)
STUB0(esb_fem_for_tx_retry_clear)
STUB0(esb_ppi_for_retransmission_clear)
STUB0(esb_ppi_for_wait_for_rx_set)
STUB0(on_radio_disabled_tx)
STUB0(on_radio_disabled_tx_noack)
STUB0(on_radio_end_tx_noack)
STUB1(esb_fem_for_tx_set,bool)
STUB1(NVIC_ClearPendingIRQ,unsigned)
STUB1(irq_enable,unsigned)
STUB1(nrfx_timer_clear,void*)
STUB2(nrf_radio_shorts_set,unsigned,unsigned)
STUB2(nrf_radio_int_enable,unsigned,unsigned)
STUB2(nrf_radio_txaddress_set,unsigned,unsigned)
STUB2(nrf_radio_rxaddresses_set,unsigned,unsigned)
STUB2(nrf_radio_frequency_set,unsigned,unsigned)
STUB2(atomic_clear_bit,unsigned*,unsigned)
STUB2(nrf_radio_packetptr_set,unsigned,void*)
STUB2(nrf_radio_event_clear,unsigned,unsigned)
STUB2(nrf_radio_task_trigger,unsigned,unsigned)
STUB2(nrf_timer_task_trigger,unsigned,unsigned)
STUB2(nrf_timer_shorts_set,unsigned,unsigned)
STUB2(esb_ppi_for_txrx_set,bool,bool)
static void nrfx_timer_compare(void*a,unsigned b,unsigned c,bool d){(void)a;(void)b;(void)c;(void)d;mmio_calls++;}
static void update_rf_payload_format(unsigned n){assert(n<=64);}
static void radio_start(void){starts++;}
static bool nrf_radio_event_check(unsigned r,unsigned e){(void)r;return e==ESB_RADIO_EVENT_END&&packet_end;}
static bool nrf_radio_crc_status_check(unsigned r){(void)r;return crc_ok;}
static unsigned nrf_radio_txaddress_get(unsigned r){(void)r;return current_payload->pipe;}
static unsigned nrf_radio_rxmatch_get(unsigned r){(void)r;return 0;}
static unsigned nrf_radio_rxcrc_get(unsigned r){(void)r;return rx_crc;}
static bool nrf_timer_event_check(unsigned t,unsigned e){(void)t;(void)e;return false;}
static unsigned nrf_radio_state_get(unsigned r){(void)r;return NRF_RADIO_STATE_TXRU;}
static bool rx_fifo_push_rfbuf(unsigned pipe,unsigned pid){assert(pipe==0);(void)pid;return true;}
'''
names = ['reset_fifos', 'initialize_fifos', 'find_free_payload_cont',
         'tx_fifo_remove_last', 'esb_write_payload', 'esb_flush_tx',
         'start_tx_transaction', 'esb_start_tx',
         'on_radio_disabled_tx_wait_for_ack', 'on_radio_disabled_rx_dpl']
body = '\n'.join(function(sdk, name) for name in names)
helper = r'''
static void reset(unsigned mode){
 initialize_fifos();memset(pids,0,sizeof(pids));memset(rx_pipe_info,0,sizeof(rx_pipe_info));
 memset(&esb_cfg,0,sizeof(esb_cfg));esb_cfg.protocol=ESB_PROTOCOL_ESB_DPL;
 esb_cfg.mode=mode;esb_cfg.tx_mode=ESB_TXMODE_MANUAL;esb_cfg.payload_length=64;
 interrupt_flags=0;esb_state=ESB_STATE_IDLE;starts=0;packet_end=crc_ok=false;
}
static void failed(void){
 packet_end=crc_ok=false;interrupt_flags=0;on_radio_disabled_tx_wait_for_ack();
 assert(interrupt_flags==INT_TX_FAILED_MSK&&esb_state==ESB_STATE_IDLE);
 assert(last_tx_attempts==1&&tx_fifo.count==1);
}
static struct esb_radio_pdu frame(void){return *(struct esb_radio_pdu*)tx_payload_buffer;}
static void same_wire(struct esb_radio_pdu a,struct esb_radio_pdu b){
 assert(a.type.dpl_pdu.length==b.type.dpl_pdu.length);
 assert(a.type.dpl_pdu.no_ack==b.type.dpl_pdu.no_ack);
 assert(!memcmp(a.data,b.data,a.type.dpl_pdu.length));
}
static unsigned receive(struct esb_radio_pdu frame,unsigned crc){
 bool retransmit_payload=false,send_rx_event=true;struct pipe_info *pipe_info;
 memcpy(rx_payload_buffer,&frame,sizeof(frame));rx_crc=crc;
 struct esb_radio_pdu *rx_pdu=(struct esb_radio_pdu*)rx_payload_buffer;
''' + decision + r'''
 (void)send_rx_event;on_radio_disabled_rx_dpl(retransmit_payload,pipe_info);
 return ((struct esb_radio_pdu*)tx_payload_buffer)->type.dpl_pdu.length;
}
static void ack_pair(struct esb_radio_pdu a,struct esb_radio_pdu b,bool retain){
 reset(1);struct esb_payload ack={.pipe=0,.length=63};memcpy(ack.data,"LsE3",4);
 assert(!esb_write_payload(&ack));assert(receive(a,0x123456)==63&&tx_fifo.count==1);
 // Equal CRC is deliberately supplied even for changed PID: PID alone suffices
 // to enter the real nonduplicate branch; this needs no fabricated CRC model.
 assert(receive(b,0x123456)==(retain?63:0));
 assert(tx_fifo.count==(retain?1:0));
 assert(interrupt_flags==(retain?0:INT_TX_SUCCESS_MSK));
 if(retain){assert(receive(b,0x654321)==0&&tx_fifo.count==0);}
}
'''
main = r'''
int main(void){
 struct esb_payload p={.pipe=0,.length=32};memcpy(p.data,"same-wire-71961",15);
 struct esb_radio_pdu old_first,old_retry,new_first,new_retry;
 reset(ESB_MODE_PTX);assert(esb_start_tx()==-ENODATA);
 assert(!esb_write_payload(&p)&&tx_fifo.count==1);assert(!esb_start_tx());old_first=frame();
 assert(old_first.type.dpl_pdu.pid==1);failed();
 assert(!esb_flush_tx()&&tx_fifo.count==0);
 assert(!esb_write_payload(&p)&&tx_fifo.count==1);assert(!esb_start_tx());old_retry=frame();
 same_wire(old_first,old_retry);assert(old_retry.type.dpl_pdu.pid==2);
 failed();
 reset(ESB_MODE_PTX);assert(!esb_write_payload(&p));assert(!esb_start_tx());new_first=frame();
 struct esb_payload *queued=tx_fifo.payload[tx_fifo.front];unsigned front=tx_fifo.front,back=tx_fifo.back;
 // SDK zero hardware retries does not impose an application retry limit.
 // The fixture retries the retained head up to its own budget (two starts).
 unsigned app_budget=2,app_attempts=1;
 while(app_attempts<app_budget){
  failed();assert(tx_fifo.payload[tx_fifo.front]==queued&&tx_fifo.front==front&&tx_fifo.back==back);
  assert(!esb_start_tx());app_attempts++;new_retry=frame();same_wire(new_first,new_retry);
  assert(new_retry.type.dpl_pdu.pid==new_first.type.dpl_pdu.pid&&tx_fifo.count==1);
 }
 failed();assert(starts==app_budget&&app_attempts==app_budget&&tx_fifo.count==1);
 // An additional application start remains legal: budget belongs to caller.
 assert(!esb_start_tx()&&starts==app_budget+1);new_retry=frame();
 same_wire(new_first,new_retry);assert(new_retry.type.dpl_pdu.pid==new_first.type.dpl_pdu.pid);
 packet_end=crc_ok=true;interrupt_flags=0;
 ((struct esb_radio_pdu*)rx_payload_buffer)->type.dpl_pdu.length=0;
 on_radio_disabled_tx_wait_for_ack();
 assert(interrupt_flags==INT_TX_SUCCESS_MSK&&tx_fifo.count==0);
 assert(tx_fifo.front==(front+1)%CONFIG_ESB_TX_FIFO_SIZE&&esb_state==ESB_STATE_IDLE);
 assert(esb_start_tx()==-ENODATA&&mmio_calls>0);
 ack_pair(old_first,old_retry,false);ack_pair(new_first,new_retry,true);
 puts("PASS pinned SDK: flush/write/start same wire PID 1->2; retained FIFO start keeps payload/PID/count; only ACK success pops PTX; caller retry budget; actual PRX PID+CRC/ACKFIFO gives 63->0 vs 63->63; same PID/new CRC consumes. MMIO mocked; no RF, pause-duration or 1500ms root-cause claim.");
}
'''
with tempfile.TemporaryDirectory() as directory:
    c = Path(directory) / 'left_retry_pid.c'
    binary = Path(directory) / 'left_retry_pid'
    c.write_text(pre + body + helper + main)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror=implicit-function-declaration',
                    '-fsanitize=address,undefined', str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
