"""Replay actual nrfx anomaly-201 HF IRQ branch and pinned Zephyr clock path."""
from pathlib import Path
import ast,re,sys,tempfile,subprocess,os
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
from prepare_left_clock_handoff import prepare
base=Path(os.environ.get('LISM_ZEPHYR_BASE','/sdk/zephyr'))
driver=base/'drivers/clock_control/clock_control_nrf.c'
nrfx=Path('/deps/modules/hal/nordic/nrfx/drivers/src/nrfx_clock.c')
# Fail on a changed platform ABI rather than silently testing abstract values.
h=(base/'include/zephyr/drivers/clock_control.h').read_text()
constants=re.search(r'enum clock_control_status\s*\{([^}]+)',h).group(1)
assert [x.strip() for x in constants.strip().rstrip(',').split(',')]==['CLOCK_CONTROL_STATUS_STARTING','CLOCK_CONTROL_STATUS_OFF','CLOCK_CONTROL_STATUS_ON','CLOCK_CONTROL_STATUS_UNKNOWN']
assert re.search(r'#define CTX_ONOFF\s+BIT\(6\)',driver.read_text())
a=ast.parse((P/'tests/left_clock_handoff_test.py').read_text());pre=next(ast.literal_eval(n.value) for n in a.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='pre' for t in n.targets))
raw=nrfx.read_text();whole=function(raw,'nrfx_clock_irq_handler');start=whole.index('    if (nrf_clock_event_check(NRF_CLOCK, NRF_CLOCK_EVENT_HFCLKSTARTED))');i=whole.index('{',start)+1;depth=1
while depth:depth+=(whole[i]=='{')-(whole[i]=='}');i+=1
fragment='static void nrfx_hf_irq_fragment(void)\n{\n'+whole[start:i]+'\n}\n'
extra=r'''
#define NRFX_CHECK(x) (x)
#define USE_WORKAROUND_FOR_ANOMALY_201 1
#define NRFX_LOG_DEBUG(...) ((void)0)
#define NRF_CLOCK_EVENT_HFCLKSTARTED 1
#define NRF_CLOCK_INT_HF_STARTED_MASK 1
static struct{unsigned hf,lf,inten;}hw;
#define NRF_CLOCK (&hw)
static struct{bool hfclk_started;void(*event_handler)(nrfx_clock_evt_type_t);}m_clock_cb;
static bool nrf_clock_event_check(void*p,unsigned ev){assert(p==&hw&&ev==1);return hw.hf;}
static void nrf_clock_event_clear(void*p,unsigned ev){assert(p==&hw&&ev==1);hw.hf=0;}
static void nrf_clock_int_disable(void*p,unsigned mask){assert(p==&hw);hw.inten&=~mask;}
'''
main=r'''
static void onoff_started_callback(const struct device*d,clock_control_subsys_t s,void*u){assert(d==CLOCK_DEVICE&&s==NULL&&u==&manager);manager.flags=2;completions++;}
static void request_pending(void){data[0]=(struct nrf_clock_control_sub_data){.flags=CTX_ONOFF|CLOCK_CONTROL_STATUS_STARTING,.cb=onoff_started_callback,.user_data=&manager};manager.flags=ONOFF_STATE_TO_ON;hfclk_users=HF_USER_GENERIC;}
int main(void){
 m_clock_cb.event_handler=clock_event_handler;data[0].flags=CLOCK_CONTROL_STATUS_OFF;
 /* Original path: stale event is consumed by an early shared IRQ; then the
  * genuine application event is suppressed by the actual nrfx guard. */
 hw.hf=1;hw.lf=1;hw.inten=0x380;nrfx_hf_irq_fragment();assert(m_clock_cb.hfclk_started&&lism_hf_events==1&&lism_hf_completed==0);
 request_pending();hw.hf=1;nrfx_hf_irq_fragment();assert(!completions&&data[0].flags==(CTX_ONOFF|CLOCK_CONTROL_STATUS_STARTING));
 /* Patched init discards only the old HF event before IRQ enable. */
 m_clock_cb.hfclk_started=false;data[0].flags=CLOCK_CONTROL_STATUS_OFF;hw.hf=1;hw.inten=0x380;
 lism_left_clock_discard_boot_event();assert(!hw.hf&&hw.lf==1&&hw.inten==0x380);
 nrfx_hf_irq_fragment();assert(!m_clock_cb.hfclk_started);request_pending();hw.hf=1;nrfx_hf_irq_fragment();assert(completions==1&&manager.flags==2&&data[0].flags==(CTX_ONOFF|CLOCK_CONTROL_STATUS_ON));
 hw.hf=1;nrfx_hf_irq_fragment();assert(completions==1);
 /* HFXO already running still follows the normal synchronous adoption. */
 m_clock_cb.hfclk_started=false;hw.hf=1;lism_left_clock_discard_boot_event();request_pending();running=true;source=NRF_CLOCK_HFCLK_HIGH_ACCURACY;generic_hfclk_start();assert(completions==2&&starts==0);
 hw.hf=1;nrfx_hf_irq_fragment();assert(completions==2);
 /* Normal Reset / RC start with no old event completes on the new event. */
 m_clock_cb.hfclk_started=false;hw.hf=0;lism_left_clock_discard_boot_event();request_pending();running=true;source=0;generic_hfclk_start();assert(starts==1&&completions==2);hw.hf=1;nrfx_hf_irq_fragment();assert(completions==3);
 puts("PASS actual nrfx anomaly201 replay: stale-event path stalls; init clear allows genuine event exactly once, HFXO adoption and normal RC start preserved");
}
'''
with tempfile.TemporaryDirectory() as d:
 copy=Path(d)/'driver.c';prepare(driver,copy);s=copy.read_text();init=function(s,'clk_init');assert init.index('nrfx_clock_init(')<init.index('lism_left_clock_discard_boot_event();')<init.index('nrfx_clock_enable();')
 f=Path(d)/'replay.c';o=Path(d)/'replay';f.write_text(pre+extra+'\n'.join(function(s,n) for n in ['set_on_state','clkstarted_handle','generic_hfclk_start','clock_event_handler','lism_left_clock_discard_boot_event'])+fragment+main)
 subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True);subprocess.run([str(o)],check=True)
