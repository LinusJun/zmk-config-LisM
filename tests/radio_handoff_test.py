"""Actual SDK cleanup + old/new end_slot with modeled RADIO registers.
Verifies handoff state, not the behavior of the closed-source SDC scheduler.
"""
from pathlib import Path
import argparse,ast,hashlib,json,re,subprocess,tempfile
P=Path(__file__).resolve().parents[2]
a=argparse.ArgumentParser();a.add_argument('--workspace',type=Path,required=True);W=a.parse_args().workspace
def function(t,name):
    m=re.search(r'(?m)^(?:static )?[^\n;]+\b'+name+r'\([^;]*?\)\s*\{',t);assert m,name
    i=t.index('{',m.start())+1;depth=1
    while depth:depth+=(t[i]=='{')-(t[i]=='}');i+=1
    return t[m.start():i]
sdk_path=next((W/'build-dongle').rglob('sdk_esb_mpsl.c'))
sdk=sdk_path.read_text();ppi=Path(str(sdk_path)+'.ppi.c').read_text()
radio=(P/'source/src/esb_upstream/radio.c').read_text()
old=(P/'reference_v41_radio.c').read_text()
tree=ast.parse((P/'source/tests/timeslot_sdk_test.py').read_text())
pre=next(n.value.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='pre' for t in n.targets))
pre=pre.replace('#define NRF_RADIO 0','''static struct {unsigned POWER,STATE,SHORTS,INTENSET;} fake_radio;
#define NRF_RADIO (&fake_radio)
#define NRF_TIMER0 0
#define NRF_TIMER_INT_COMPARE0_MASK 1
#define NRF_TIMER_EVENT_COMPARE0 0
#define RADIO_IRQn 1
#define __DSB() ((void)0)''')
for name in ['base0_set','base1_set','prefix0_set','prefix1_set','fast_ramp_up_enable_set','shorts_disable','int_disable','power_set','event_clear','crc_status_check']:
    pre=pre.replace('nrf_radio_'+name+'(int r,','nrf_radio_'+name+'(void*r,').replace('nrf_radio_'+name+'(int r)','nrf_radio_'+name+'(void*r)')
pre=pre.replace('static void NVIC_ClearPendingIRQ(int i){assert(i==10 || i==20);}', 'static void NVIC_ClearPendingIRQ(int i){assert(i==1 || i==10 || i==20);}')
pre=pre.replace('static void nrf_radio_shorts_disable(void*r,unsigned mask){assert(in_slot);}', 'static void nrf_radio_shorts_disable(void*r,unsigned mask){assert(in_slot);fake_radio.SHORTS&=~mask;}')
pre=pre.replace('static void nrf_radio_int_disable(void*r,unsigned mask){assert(in_slot);}', 'static void nrf_radio_int_disable(void*r,unsigned mask){assert(in_slot);fake_radio.INTENSET&=~mask;}')
pre=pre.replace('assert(in_slot && !v);power_off=++sequence;assert(ppi_stopped<power_off);', 'assert(in_slot);if(!v){power_off=++sequence;assert(ppi_stopped<power_off);}fake_radio.POWER=v;fake_radio.STATE=0;fake_radio.SHORTS=0;fake_radio.INTENSET=0;')
extra=r'''
typedef int atomic_t;
static bool initialized;
static atomic_t diag_power,diag_state,diag_shorts,diag_interrupts,diag_handoffs;
static int atomic_set(atomic_t*p,int v){int old=*p;*p=v;return old;}
static void atomic_inc(atomic_t*p){++*p;}
static unsigned collected;
static void collect_rx(void){assert(in_slot);collected++;}
static void nrf_timer_event_clear(int t,int e){assert(in_slot);}
'''
rx=function(sdk,'on_radio_disabled_rx')
rx_errors=rx[:rx.index('\n\tpipe_info = &rx_pipe_info')]+'\n assert(!"normal RX outside handoff test");\n}'
main=r'''
int main(void){
 assert(!lism_esb_resources_init());in_slot=true;
 struct esb_config cfg={.use_fast_ramp_up=true};
 assert(!esb_init(&cfg));initialized=true;fake_radio.POWER=1;
 old_end_slot();assert(!initialized && fake_radio.POWER==0);
 for(int error=0;error<3;error++) {
  assert(!esb_init(&cfg));initialized=true;fake_radio.POWER=1;
  if(error){crc_ok=(error==2);rx_fifo.count=error==2?16:0;on_radio_disabled_rx();assert(fake_radio.POWER==0);}
  end_slot();assert(!initialized && fake_radio.POWER==1 && fake_radio.STATE==0 && fake_radio.SHORTS==0 && fake_radio.INTENSET==0);
 }
 // No initialized ESB: same cleanup used by failed-config and disabled START.
 end_slot();assert(fake_radio.POWER==1 && !fake_radio.STATE);
 assert(diag_handoffs==4 && diag_power==1 && !diag_state && !diag_shorts && !diag_interrupts);
 assert(timer_allocs==1 && ppi_allocs==1 && collected==4);
 in_slot=false;
 puts("PASS: actual old end_slot leaves POWER=0; new end_slot returns POWER=1/DISABLED/shorts0/ints0, including real bad-CRC/full-FIFO checks and uninitialized ESB path");
}
'''
code=pre+'\n'+extra+'\n'+function(ppi,'esb_ppi_disable_all')+'\n'+'\n'.join(function(sdk,n) for n in ['esb_irq_disable','esb_init','esb_disable','lism_esb_resources_init','clear_events_restart_rx'])+'\n'+rx_errors+'\n'+function(old,'end_slot').replace('end_slot(', 'old_end_slot(',1)+'\n'+function(radio,'end_slot')+'\n'+main
with tempfile.TemporaryDirectory() as d:
    c=Path(d)/'test.c';exe=Path(d)/'test';c.write_text(code)
    subprocess.run(['cc','-std=c11','-fsanitize=address,undefined','-fno-omit-frame-pointer','-Werror=implicit-function-declaration',str(c),'-o',str(exe)],check=True)
    r=subprocess.run([str(exe)],capture_output=True,text=True,check=True)
report={'passed':True,'actual_SDK_and_old_new_end_slot':True,'production_radio_sha256':hashlib.sha256((P/'source/src/esb_upstream/radio.c').read_bytes()).hexdigest(),
        'normal_CRC_FIFO_uninitialized_paths':True,'stdout':r.stdout,
        'scope':'Modeled MMIO and FIFO copy; proves handoff state only, not SDC scheduler behavior, RF timing or real cause of slot stall'}
(W/'radio-handoff-test.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
