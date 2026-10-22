"""Execute actual generated SDK PTX ACK/timeout + event service under fake MMIO.
Hardware timing, interrupt concurrency and RF are explicitly outside this test.
"""
from pathlib import Path
import sys,tempfile,subprocess
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'));from prepare_hog_routing import function
sdk=Path('/work/v51-two-sets/workspace/build-right_set1/modules/zmk-config-LisM/sdk_esb_mpsl.c').read_text()
ack=function(sdk,'on_radio_disabled_tx_wait_for_ack');anchor='\n\t\t} else {\n\t\t\tbool radio_started = true;';assert anchor in ack
# Right hub deliberately sets zero hardware retries. Execute both real paths;
# the unused positive retry-count branch must never be entered in this target.
ack=ack[:ack.index(anchor)]+'\n        } else {assert(!"positive hardware retries not configured");}\n    }\n}'
pre=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#define IS_ENABLED(x) (x)
#define NRF_RADIO 0
#define ESB_RADIO_EVENT_END 0
#define ESB_PROTOCOL_ESB 0
#define ESB_TXMODE_MANUAL 0
#define ESB_STATE_IDLE 0
#define NRF_TIMER_HAS_SHUTDOWN 0
#define NRF_TIMER_TASK_STOP 0
#define NRF_TIMER_TASK_CLEAR 1
#define INT_TX_SUCCESS_MSK 1
#define INT_TX_FAILED_MSK 2
#define INT_RX_DATA_RECEIVED_MSK 4
#define ESB_EVENT_TX_SUCCESS 1
#define ESB_EVENT_TX_FAILED 2
#define ESB_EVENT_RX_RECEIVED 3
#define NRF_TIMER_INT_COMPARE1_MASK 2
#define NRF_TIMER_INT_COMPARE3_MASK 8
#define NRF_TIMER_EVENT_COMPARE1 1
#define NRF_TIMER_EVENT_COMPARE3 3
#define ESB_EVT_IRQ_NUMBER 20
#define __ASSERT_NO_MSG(x) assert(x)
static bool in_slot,crc_ok,packet_end;static unsigned starts,events,timer_calls,clears,rx_acks,interrupt_flags,last_tx_attempts,retransmits_remaining,esb_state;
struct esb_evt{unsigned evt_id,tx_attempts;};static struct {unsigned retransmit_count,protocol,tx_mode;} esb_cfg={.retransmit_count=0,.protocol=1,.tx_mode=1};
struct esb_radio_pdu{union{struct{uint8_t length,pid;}dpl_pdu;}type;};static uint8_t rx_payload_buffer[8];
static struct{unsigned count;}tx_fifo;static struct{int p_reg;}esb_timer;
static void esb_ppi_for_wait_for_ack_clear(void){assert(in_slot);}
static void mpsl_fem_lna_configuration_clear(void){assert(in_slot);}static void mpsl_fem_disable(void){assert(in_slot);}
static bool nrf_radio_event_check(int r,int e){(void)r;(void)e;assert(in_slot);return packet_end;}
static bool nrf_radio_crc_status_check(int r){(void)r;assert(in_slot);return crc_ok;}
static void tx_fifo_remove_last(void){assert(in_slot&&tx_fifo.count);tx_fifo.count--;}
static unsigned nrf_radio_txaddress_get(int r){(void)r;assert(in_slot);return 1;}
static bool rx_fifo_push_rfbuf(unsigned p,unsigned pid){assert(in_slot&&p==1&&pid==4);rx_acks++;return true;}
static void errata216_off(void){assert(in_slot);}
static void set_evt_interrupt(void){assert(in_slot);}
static void start_tx_transaction(void){assert(in_slot);starts++;}
static void nrf_timer_task_trigger(int t,int task){(void)t;(void)task;assert(in_slot);}
static unsigned irq_lock(void){assert(in_slot);return 0;}static void irq_unlock(unsigned k){(void)k;assert(in_slot);}
static void callback(const struct esb_evt *e){assert(in_slot);events|=1U<<e->evt_id;}
static void (*event_handler)(const struct esb_evt*)=callback;
static unsigned timer_events,timer_interrupts;
static bool nrf_timer_int_enable_check(int t,unsigned mask){(void)t;assert(in_slot);return timer_interrupts&mask;}
static bool nrf_timer_event_check(int t,int e){(void)t;assert(in_slot);return timer_events&(1U<<e);}
static void nrf_timer_event_clear(int t,int e){(void)t;assert(in_slot);timer_events&=~(1U<<e);}
static void esb_timer_handler(int e,void*ctx){(void)ctx;assert(in_slot);timer_calls++;timer_events&=~(1U<<e);}
static void NVIC_ClearPendingIRQ(int n){assert(in_slot&&n==20);clears++;}
'''
main=r'''
int main(void){
 in_slot=true;assert(esb_start_tx()==-61);tx_fifo.count=1;assert(!esb_start_tx()&&starts==1);
 packet_end=crc_ok=true;((struct esb_radio_pdu*)rx_payload_buffer)->type.dpl_pdu.length=3;((struct esb_radio_pdu*)rx_payload_buffer)->type.dpl_pdu.pid=4;on_radio_disabled_tx_wait_for_ack();assert(interrupt_flags==(INT_TX_SUCCESS_MSK|INT_RX_DATA_RECEIVED_MSK)&&!tx_fifo.count&&rx_acks==1);lism_esb_slot_service();assert((events&(1U<<ESB_EVENT_TX_SUCCESS))&&(events&(1U<<ESB_EVENT_RX_RECEIVED))&&!interrupt_flags);
 tx_fifo.count=1;retransmits_remaining=0;packet_end=false;crc_ok=false;on_radio_disabled_tx_wait_for_ack();assert(interrupt_flags==INT_TX_FAILED_MSK&&esb_state==ESB_STATE_IDLE&&tx_fifo.count==1);lism_esb_slot_service();assert(events&(1U<<ESB_EVENT_TX_FAILED));
 timer_events=(1U<<1)|(1U<<3);timer_interrupts=NRF_TIMER_INT_COMPARE1_MASK;lism_esb_slot_service();assert(timer_calls==1&&(timer_events&(1U<<3)));timer_interrupts|=NRF_TIMER_INT_COMPARE3_MASK;lism_esb_slot_service();assert(timer_calls==2&&!timer_events&&clears>=4);
 puts("PASS actual generated SDK: explicit PTX start, ACK success+payload, ACK timeout at zero retries, bounded timer/event polling with disabled external IRQs; mocked MMIO only");return 0;}
'''
code=pre+'\n'+function(sdk,'esb_start_tx')+'\n'+ack+'\n'+function(sdk,'get_and_clear_irqs')+'\n'+function(sdk,'esb_evt_irq_handler')+'\n'+function(sdk,'lism_esb_slot_service')+main
code=code.replace('#include <stdio.h>','#include <stdio.h>\n#include <errno.h>')
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'ptx.c';out=Path(t)/'ptx';f.write_text(code);subprocess.run(['cc','-std=gnu11','-fsanitize=address,undefined',str(f),'-o',str(out)],check=True);subprocess.run([str(out)],check=True)
