"""Compile actual generated SDK init/teardown/PPI masks with fake MMIO.

Checks isolation and bounded structure, not microsecond timing or RF behavior.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,re,subprocess,tempfile
P=Path(__file__).resolve().parents[2]
a=argparse.ArgumentParser();a.add_argument('--workspace',type=Path,required=True)
a.add_argument('--target',default='dongle',choices=['dongle','right','dongle_a','dongle_b'])
args=a.parse_args();W=args.workspace
generated=W/('build-'+args.target)/'modules/zmk-config-LisM/sdk_esb_mpsl.c'
sdk=generated.read_text();ppi=Path(str(generated)+'.ppi.c').read_text()
def function(t,name):
    m=re.search(r'(?m)^(?:static )?[^\n;]+\b'+name+r'\([^;]*?\)\s*\{',t);assert m,name
    i=t.index('{',m.start())+1;depth=1
    while depth:
        depth+=(t[i]=='{')-(t[i]=='}');i+=1
    return t[m.start():i]
spec=importlib.util.spec_from_file_location('sdk_prepare',P/'source/tools/prepare_timeslot_sdk.py')
import sys;sys.path.insert(0,str(P/'source/tools'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as d:
    output=Path(d)/'sdk.c';output_ppi=Path(d)/'ppi.c'
    module.prepare(W/'nrf/subsys/esb/esb.c',output)
    module.prepare_ppi(W/'nrf/subsys/esb/esb_ppi.c',output_ppi)
    assert output.read_text()==sdk and output_ppi.read_text()==ppi
init=function(sdk,'esb_init');stop=function(sdk,'esb_disable')
recover=function(sdk,'clear_events_restart_rx')
assert 'while (' not in recover and 'nrf_radio_power_set(NRF_RADIO, false)' in recover
rx=function(sdk,'on_radio_disabled_rx')
# Execute the real two error checks/returns; valid packet handling is outside
# this MMIO test. Fail if a test unexpectedly reaches that normal path.
rx_errors=rx[:rx.index('\n\tpipe_info = &rx_pipe_info')]+ '\n assert(!"valid RX path outside this test");\n}'
assert not any(s in init for s in ['sys_timer_init(', 'esb_ppi_init(', 'irq_connect', 'IRQ_DIRECT_CONNECT','irq_enable('])
assert not any(s in stop for s in ['while (','sys_timer_deinit(', 'esb_ppi_deinit('])
assert stop.index('esb_ppi_disable_all();')<stop.index('nrf_radio_power_set(NRF_RADIO, false);')
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>
#define IS_ENABLED(x) (x)
#define CONFIG_ESB_NEVER_DISABLE_TX 0
#define CONFIG_SOC_SERIES_NRF54HX 0
#undef CONFIG_SOC_SERIES_NRF54HX
#define BIT(x) (1U<<(x))
#define NRF_RADIO 0
#define NRF_PPI 0
#define ESB_NRF_TIMER_INSTANCE 2
#define NRF_TIMER_TASK_STOP 0
#define NRF_TIMER_TASK_CLEAR 1
#define NRF_RADIO_EVENT_DISABLED 0
#define ESB_EVT_IRQ_NUMBER 20
#define ESB_TIMER_IRQ 10
#define CONFIG_ESB_RX_FIFO_SIZE 16
#define LOG_ERR(...) do {} while(0)
#define LISM_PRX_TRACE(...) do {} while(0)
enum {ESB_STATE_IDLE};
struct esb_config {bool use_fast_ramp_up;void (*event_handler)(void);};
static struct esb_config esb_cfg;
static void (*event_handler)(void),(*on_radio_disabled)(void);
static bool fast_switching,esb_initialized,in_slot;
static int interrupt_flags,esb_state,rx_pipe_info[8],pids[8];
static struct {struct {struct {uint32_t event;} generic;} event;} disable_event;
static int timer_allocs,ppi_allocs,radio_calls,dispatches,reset_calls,sequence,ppi_stopped,power_off;
static unsigned disabled_mask;
static unsigned egu_ramp_up=2,disabled_egu=3,egu_timer_start=4,radio_address_timer_stop=5;
static unsigned timer_compare0_radio_disable=6,timer_compare1_radio_txen=7,radio_end_timer_start=0;
static void nrf_ppi_channels_disable(int p,unsigned mask){assert(in_slot);disabled_mask=mask;ppi_stopped=++sequence;}
static int sys_timer_init(void){assert(!in_slot);timer_allocs++;return 0;}
static int esb_ppi_init(void){assert(!in_slot);ppi_allocs++;return 0;}
static void irq_disable(int i){assert(i==10 || i==20);}
static void NVIC_ClearPendingIRQ(int i){assert(i==10 || i==20);}
static void update_radio_parameters(void){assert(in_slot);radio_calls++;}
static void initialize_fifos(void){assert(in_slot);}
static void reset_fifos(void){assert(in_slot);reset_calls++;}
static unsigned esb_ppi_radio_disabled_get(void){assert(in_slot);return 123;}
static void nrf_radio_base0_set(int r,unsigned v){assert(in_slot);}
static void nrf_radio_base1_set(int r,unsigned v){assert(in_slot);}
static void nrf_radio_prefix0_set(int r,unsigned v){assert(in_slot);}
static void nrf_radio_prefix1_set(int r,unsigned v){assert(in_slot);}
static void nrf_radio_fast_ramp_up_enable_set(int r,bool v){assert(in_slot);}
static bool nrf52_errata_182(void){return false;}
static void nrf_radio_shorts_disable(int r,unsigned mask){assert(in_slot);}
static void nrf_radio_int_disable(int r,unsigned mask){assert(in_slot);}
static void nrf_radio_power_set(int r,bool v){assert(in_slot && !v);power_off=++sequence;assert(ppi_stopped<power_off);}
static void nrf_radio_event_clear(int r,int e){assert(in_slot);}
static void esb_fem_reset(void){assert(in_slot);}
static void nrf_timer_task_trigger(int t,int task){assert(in_slot);}
static void nrf_timer_int_disable(int t,unsigned mask){assert(in_slot);}
static void errata216_off(void){assert(in_slot);}
static void radio_irq_handler(void){assert(in_slot);dispatches++;}
struct pipe_info;
struct esb_radio_pdu;
static unsigned char rx_payload_buffer[64],tx_payload_buffer[64];
static struct {int count;} rx_fifo;
static bool crc_ok;
static bool nrf_radio_crc_status_check(int r){assert(in_slot);return crc_ok;}
void esb_disable(void);
'''
main=r'''
int main(void) {
 assert(!lism_esb_resources_init());assert(timer_allocs==1 && ppi_allocs==1);
 struct esb_config cfg={.use_fast_ramp_up=true};
 for(int i=0;i<1000;i++) {
  in_slot=true;assert(!esb_init(&cfg));assert(esb_initialized);
  lism_esb_radio_irq();esb_disable();assert(!esb_initialized);
  assert(disabled_mask==0xfc); // Only allocated channels 2..7; never default channel 0.
  in_slot=false;
 }
 assert(timer_allocs==1 && ppi_allocs==1 && radio_calls==1000 && dispatches==1000 && reset_calls==1000);
 in_slot=true;assert(!esb_init(&cfg));crc_ok=false;rx_fifo.count=0;
 int before=power_off;on_radio_disabled_rx();assert(power_off>before && disabled_mask==0xfc);
 esb_disable();assert(!esb_initialized);
 assert(!esb_init(&cfg));crc_ok=true;rx_fifo.count=16;
 before=power_off;on_radio_disabled_rx();assert(power_off>before && disabled_mask==0xfc);
 esb_disable();assert(!esb_initialized);
 // A subsequent grant can initialize without reallocating TIMER2/PPI.
 assert(!esb_init(&cfg));esb_disable();in_slot=false;
 assert(timer_allocs==1 && ppi_allocs==1);
 puts("PASS: 1000 SDK init/stop cycles plus actual bad-CRC/full-FIFO RX checks and bounded slot-abandon helper; resources allocated outside slots, only owned PPI disabled");
}
'''
code=pre+'\n'+function(ppi,'esb_ppi_disable_all')+'\n'+function(sdk,'esb_irq_disable')+'\n'+init+'\n'+stop+'\n'+function(sdk,'lism_esb_resources_init')+'\n'+function(sdk,'lism_esb_radio_irq')+'\n'+recover+'\n'+rx_errors+'\n'+main
with tempfile.TemporaryDirectory() as d:
    c=Path(d)/'test.c';exe=Path(d)/'test';c.write_text(code)
    subprocess.run(['cc','-std=c11','-fsanitize=address,undefined','-fno-omit-frame-pointer','-Werror=implicit-function-declaration',str(c),'-o',str(exe)],check=True)
    r=subprocess.run([str(exe)],capture_output=True,text=True,check=True)
report={'passed':True,'cycles':1000,'actual_generated_SDK_functions':True,'generated_copy_reproduced':True,
        'generated_SDK_sha256':hashlib.sha256(generated.read_bytes()).hexdigest(),
        'bad_CRC_and_full_FIFO_production_checks_executed':True,
        'stdout':r.stdout,'scope':'Mocked MMIO; normal RX IRQ handler remains mocked, actual error-check prefix and recovery helper executed; no RF, interrupt concurrency, FEM internals or microsecond timing simulation'}
(W/'timeslot-sdk-test.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
