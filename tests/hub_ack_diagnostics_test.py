"""Actual private setup: distinguish ACK loading from radio start/delivery."""
from pathlib import Path
import tempfile,subprocess
P=Path(__file__).resolve().parent
base=P/'kb_radio_native_test.py';t=base.read_text();g={'__file__':str(base)}
exec(compile(t[:t.index('code=pre+')],str(base),'exec'),g)
s=g['radio'];function=g['function']
defs=s[s.index('/* DIAG41_BEGIN'):s.index('/* DIAG41_END */')]
pre=g['pre']+r'''
#define ESB_DEFAULT_CONFIG {0}
#define ESB_PROTOCOL_ESB_DPL 0
#define ESB_BITRATE_2MBPS 0
#define ESB_MODE_PTX 0
#define ESB_MODE_PRX 1
#define ESB_TXMODE_MANUAL_START 0
#define ESB_TX_POWER_8DBM 0
struct esb_config{unsigned protocol,bitrate,mode,tx_mode,retransmit_delay,retransmit_count,tx_output_power;bool selective_auto_ack,use_fast_ramp_up;void(*event_handler)(const void*);};
static bool sdk_ready;static unsigned grant_link;static atomic_t probe_transmitted;
static uint32_t prx_grant_cycle;
static uint32_t k_cycle_get_32(void){return 0;}
static uint8_t keyboard_base0[4],keyboard_base1[4],prefixes[8];
static int init_rc,write_rc,rx_rc;static unsigned writes,rx_calls,tx_calls;
static struct esb_payload loaded;
static void esb_event(const void*e){(void)e;}
static int esb_init(struct esb_config*c){assert(c->retransmit_count==0);return init_rc;}
static int esb_set_base_address_0(const void*p){(void)p;return 0;}
static int esb_set_base_address_1(const void*p){(void)p;return 0;}
static int esb_set_prefixes(const void*p,unsigned n){(void)p;assert(n==8);return 0;}
static int esb_enable_pipes(unsigned n){assert(n==(grant_link?3:1));return 0;}
static int esb_set_rf_channel(unsigned n){assert(n==37);return 0;}
static int esb_write_payload(const struct esb_payload*p){writes++;loaded=*p;return write_rc;}
static int esb_start_rx(void){rx_calls++;return rx_rc;}
static int esb_start_tx(void){tx_calls++;return 0;}
'''
main=r'''
int main(void){
 left_route_epoch=7;links[0].boot=100;
 assert(!setup());assert(diag_private_start==1&&diag_private_selected==0&&diag_ack_load==0&&diag_rx_start==1&&rx_calls==1&&!writes);
 links[0].have=true;links[0].tx=(struct hub_tx){.peer=300,.generation=7,.p={.pipe=0,.length=32}};
 put32(links[0].tx.p.data+8,42);struct hub_tx original=links[0].tx;
 assert(!setup());assert(diag_private_start==2&&diag_private_selected==1&&diag_ack_load==1&&diag_rx_start==2);
 assert(diag_loaded_peer==300&&diag_loaded_generation==7&&diag_loaded_sequence==42&&diag_loaded_bytes==32);
 assert(!memcmp(&links[0].tx,&original,sizeof(original))&&!memcmp(&loaded,&original.p,sizeof(loaded)));
 links[0].tx.generation=6;assert(!setup());assert(writes==1&&diag_private_selected==1&&rx_calls==3);
 links[0].tx.generation=7;write_rc=-ENOSPC;assert(setup()==-ENOSPC);
 assert(diag_ack_fail==1&&diag_load_rc==-ENOSPC&&diag_ack_load==1&&diag_rx_fail==0&&rx_calls==3);
 write_rc=0;rx_rc=-EIO;assert(setup()==-EIO);assert(diag_ack_load==2&&diag_rx_fail==1&&rx_calls==4);
 rx_rc=0;init_rc=-EBUSY;assert(setup()==-EBUSY);assert(diag_setup_fail==1&&writes==3&&rx_calls==4);
 init_rc=0;grant_link=1;assert(!setup());assert(diag_private_start==6&&diag_private_selected==4&&diag_ack_load==2&&diag_ack_fail==1);
 assert(diag_rx_start==3&&diag_rx_fail==1&&diag_setup_fail==1&&!tx_calls);
 puts("PASS actual private setup: empty/selected/stale ACK, exact payload preservation, load and RX-start failures retain return codes, uplink excluded; loading does not prove on-air delivery");
}
'''
code=pre+defs+function(s,'get32')+function(s,'put32')+function(s,'setup')+main
with tempfile.TemporaryDirectory() as d:
 f=Path(d)/'ack.c';o=Path(d)/'ack';f.write_text(code)
 subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True)
 subprocess.run([str(o)],check=True)
