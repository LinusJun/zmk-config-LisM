"""Real pinned SDK FIFO/PID branches, with manually advanced RX/ACK events.

No RF, MPSL IRQ timing or clock model: in particular local completion does not
prove delivery. A new nonduplicate uplink can consume an undelivered payload.
"""
from pathlib import Path
import sys,tempfile,subprocess
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
from prepare_timeslot_sdk import prepare
with tempfile.TemporaryDirectory() as d:
 generated=Path(d)/'sdk.c';prepare('/sdk/nrf/subsys/esb/esb.c',generated);sdk=generated.read_text()
 assert 'memset(rx_pipe_info, 0, sizeof(rx_pipe_info));' in function(sdk,'esb_init')
 assert 'LISM_PRX_TRACE(2' in function(sdk,'on_radio_disabled_rx_ack')
 assert 'LISM_PRX_TRACE(3' in function(sdk,'lism_esb_prx_slot_end_trace')
 hub=(P/'src/esb_upstream/hub_radio.inc').read_text();end=function(hub,'end_slot')
 assert end.index('lism_esb_prx_slot_end_trace()')<end.index('esb_disable()')
 rx=function(sdk,'on_radio_disabled_rx')
 # Run the real duplicate PID+CRC decision and its state update. CRC-error and
 # full-FIFO recovery are already exercised by timeslot_sdk_test.
 decision=rx[rx.index('\tpipe_info = &rx_pipe_info'):rx.index('\n\t/* Check if an ack should be sent */')]
 pre=r'''
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
#define PID_MAX 3
#define ESB_PROTOCOL_ESB 0
#define ESB_MODE_PTX 0
#define ESB_TXMODE_AUTO 0
#define ESB_STATE_IDLE 0
#define ESB_STATE_PTX_TXIDLE 1
#define CONFIG_ESB_NEVER_DISABLE_TX 0
#define IS_ENABLED(x) (x)
#define INT_TX_SUCCESS_MSK 1
#define NRF_RADIO 0
struct esb_payload {unsigned pipe,length,pid;uint8_t data[64];};
struct esb_radio_pdu {union{struct{unsigned length,pid,no_ack;}dpl_pdu;}type;uint8_t data[64];};
struct pipe_info {uint32_t crc;uint8_t pid;bool ack_payload;};
struct payload_wrap {struct esb_payload*p_payload;bool in_use;struct payload_wrap*p_next;};
static struct payload_wrap ack_pl_wrap[4],*ack_pl_wrap_pipe[2];
static struct {unsigned back,front,count;struct esb_payload*payload[4];}tx_fifo,rx_fifo;
static struct pipe_info rx_pipe_info[2];static unsigned pids[2];
static struct {unsigned protocol,mode,tx_mode,payload_length;}esb_cfg={1,1,1,64};
static bool esb_initialized=true;static unsigned interrupt_flags,esb_state,rx_crc,prepared,empty;
static uint8_t tx_payload_buffer[sizeof(struct esb_radio_pdu)],rx_payload_buffer[sizeof(struct esb_radio_pdu)];
static struct esb_payload*current_payload;
static unsigned irq_lock(void){return 0;}static void irq_unlock(unsigned k){(void)k;}
static void start_tx_transaction(void){assert(!"PRX test must not start PTX");}
static unsigned nrf_radio_rxmatch_get(unsigned r){(void)r;return 0;}
static unsigned nrf_radio_rxcrc_get(unsigned r){(void)r;return rx_crc;}
static void update_rf_payload_format(unsigned n){assert(n<=64);}
static void trace(unsigned ev,const uint8_t*in,unsigned ni,const uint8_t*out,unsigned no,unsigned detail){
 (void)in;(void)out;(void)detail;assert(ev==1&&ni<=64&&no<=64);if(no)prepared++;else empty++;
}
#define LISM_PRX_TRACE(...) trace(__VA_ARGS__)
'''
 body='\n'.join(function(sdk,n) for n in ['reset_fifos','initialize_fifos','find_free_payload_cont','esb_write_payload','on_radio_disabled_rx_dpl'])
 helper='''
static void rx_event(unsigned pid,unsigned crc){
 bool retransmit_payload=false,send_rx_event=true;struct pipe_info*pipe_info;
 struct esb_radio_pdu*rx_pdu=(struct esb_radio_pdu*)rx_payload_buffer;
 rx_pdu->type.dpl_pdu.pid=pid;rx_pdu->type.dpl_pdu.length=32;rx_crc=crc;
'''+decision+'''
 (void)send_rx_event;on_radio_disabled_rx_dpl(retransmit_payload,pipe_info);
}
static unsigned ack_len(void){return ((struct esb_radio_pdu*)tx_payload_buffer)->type.dpl_pdu.length;}
static void grant_init(void){initialize_fifos();memset(rx_pipe_info,0,sizeof(rx_pipe_info));memset(pids,0,sizeof(pids));}
'''
 main=r'''
int main(void){
 struct esb_payload p={.pipe=0,.length=63};memcpy(p.data,"LsE3",4);
 grant_init();assert(!esb_write_payload(&p));rx_event(1,101);
 assert(ack_len()==63&&tx_fifo.count==1&&!interrupt_flags); // first ACK is still pending
 rx_event(1,101);assert(ack_len()==63&&tx_fifo.count==1&&!interrupt_flags); // same CRC+PID retry
 rx_event(2,102);assert(!ack_len()&&!tx_fifo.count&&interrupt_flags==INT_TX_SUCCESS_MSK);
 // The next NEW uplink consumes the sole ACK payload even if the previous
 // downlink was lost. It is already empty on the second distinct uplink.
 grant_init();assert(!esb_write_payload(&p));rx_event(1,101);assert(ack_len()==63);
 // Ending a grant before ACK completion is not remote delivery. Reinit resets
 // SDK PID/FIFO state, then reloading the same application command restores it.
 grant_init();assert(!esb_write_payload(&p));rx_event(1,101);assert(ack_len()==63);
 // Same PID with changed CRC is also a new uplink and consumes the payload.
 rx_event(1,999);assert(!ack_len()&&!tx_fifo.count);
 grant_init();assert(!esb_write_payload(&p));assert(!esb_write_payload(&p));
 rx_event(0,42);assert(ack_len()==63&&tx_fifo.count==2);
 rx_event(1,43);assert(ack_len()==63&&tx_fifo.count==1);
 rx_event(2,44);assert(!ack_len()&&!tx_fifo.count);
 assert(prepared==6&&empty==3);
 puts("PASS actual SDK: first payload pending, identical PID+CRC retry retains, next distinct uplink consumes to empty, same PID/new CRC consumes, two ACK FIFO entries, grant reset/reload; no on-air delivery or timing claim");
}
'''
 f=Path(d)/'prx.c';o=Path(d)/'prx';f.write_text(pre+body+helper+main)
 subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True)
 subprocess.run([str(o)],check=True)
