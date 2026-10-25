"""Exercise the actual patched driver start/completion path with HFXO states."""
from pathlib import Path
import tempfile,subprocess,sys
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_left_clock_handoff import prepare
from prepare_hog_routing import function
src=Path(sys.argv[1])
pre=r'''
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#include <stdio.h>
#define HF_USER_GENERIC 2
#define HF_USER_BT 1
#define NRF_CLOCK_DOMAIN_HFCLK 0
#define NRF_CLOCK_HFCLK_HIGH_ACCURACY 1
#define CLOCK_CONTROL_STATUS_STARTING 0
#define CLOCK_CONTROL_STATUS_OFF 1
#define CLOCK_CONTROL_STATUS_ON 2
#define CLOCK_CONTROL_NRF_TYPE_HFCLK 0
#define CLOCK_CONTROL_NRF_TYPE_LFCLK 1
#define CLOCK_CONTROL_NRF_TYPE_HFCLK192M 2
#define CLOCK_CONTROL_NRF_TYPE_HFCLKAUDIO 3
#define NRF_CLOCK_HAS_HFCLK192M 0
#define NRF_CLOCK_HAS_HFCLKAUDIO 0
#define NRF_CLOCK_HAS_XO_TUNE 0
#define GET_CTX(x) ((x)&0xc0)
#define GET_STATUS(x) ((x)&0x7)
#define DBG(...) ((void)0)
#define LOG_WRN(...) ((void)0)
#define CTX_ONOFF 0x40
#define ONOFF_STATE_MASK 0x7
#define ONOFF_STATE_TO_ON 6
#define IS_ENABLED(x) 0
#define __ASSERT_NO_MSG(x) assert(x)
#define CONFIG_CLOCK_CONTROL_NRF_DRIVER_CALIBRATION 0
struct device{int unused;};
static struct device fake_device;
#define CLOCK_DEVICE (&fake_device)
typedef unsigned nrf_clock_hfclk_t;
typedef void *clock_control_subsys_t;
typedef void (*clock_control_cb_t)(const struct device*,clock_control_subsys_t,void*);
enum clock_control_nrf_type{DUMMY};
typedef enum{NRFX_CLOCK_EVT_HFCLK_STARTED,NRFX_CLOCK_EVT_LFCLK_STARTED,NRFX_CLOCK_EVT_CAL_DONE,NRFX_CLOCK_EVT_PLL_STARTED} nrfx_clock_evt_type_t;
struct nrf_clock_control_sub_data{uint32_t flags;clock_control_cb_t cb;void*user_data;};
static struct nrf_clock_control_sub_data data[4];
static unsigned lism_handoff[5];
static unsigned lism_hf_starts,lism_hf_events,lism_hf_completed,lism_hf_entry_flags,lism_hf_entry_running,lism_hf_entry_type;
static unsigned hfclk_users,locked,starts,completions,inject;static bool running;static unsigned source;
static unsigned irq_lock(void){unsigned old=locked;locked=1;return old;}
static void clock_event_handler(nrfx_clock_evt_type_t);
static void irq_unlock(unsigned key){locked=key;if(!key&&inject){inject=0;clock_event_handler(NRFX_CLOCK_EVT_HFCLK_STARTED);}}
static bool nrfx_clock_is_running(unsigned domain,nrf_clock_hfclk_t*t){assert(locked&&domain==0);*t=source;return running;}
static uint32_t*get_hf_flags(void){return &data[0].flags;}
static struct nrf_clock_control_sub_data*get_sub_data(const struct device*d,unsigned n){assert(d==&fake_device);return &data[n];}
struct onoff_manager{unsigned flags,refs;};
static struct onoff_manager manager;
static struct onoff_manager*get_onoff_manager(const struct device*d,unsigned n){assert(d==CLOCK_DEVICE&&n==0);return &manager;}
static void onoff_started_callback(const struct device*,clock_control_subsys_t,void*);
static void hfclk_start(void){starts++;}
static void z_nrf_clock_calibration_lfclk_started(void){}
static void z_nrf_clock_calibration_done_handler(void){}
'''
main=r'''
static void complete(const struct device*d,clock_control_subsys_t s,void*u){assert(!locked&&d==&fake_device&&s==NULL&&u==&fake_device);completions++;}
static void onoff_started_callback(const struct device*d,clock_control_subsys_t s,void*u){manager.flags=2;complete(d,s,u);}
static void wait_state(void){data[0]=(struct nrf_clock_control_sub_data){.flags=CLOCK_CONTROL_STATUS_STARTING|CTX_ONOFF,.cb=onoff_started_callback,.user_data=&fake_device};manager.flags=ONOFF_STATE_TO_ON;hfclk_users=HF_USER_GENERIC;running=true;source=1;inject=0;}
static void request(bool on,unsigned type,unsigned bt,bool pending){
 data[0]=(struct nrf_clock_control_sub_data){.flags=CLOCK_CONTROL_STATUS_STARTING|CTX_ONOFF,.cb=complete,.user_data=&fake_device};
 running=on;source=type;hfclk_users=bt;inject=pending;generic_hfclk_start();
 assert((hfclk_users&HF_USER_GENERIC)&&!locked);
}
int main(void){
 /* Running HFXO without BT: no STARTED event needed. */
 request(true,1,0,false);assert(completions==1&&starts==0&&data[0].flags==(CTX_ONOFF|CLOCK_CONTROL_STATUS_ON));
 clock_event_handler(NRFX_CLOCK_EVT_HFCLK_STARTED);assert(completions==1);
 /* An IRQ at unlock must not complete the same request twice. */
 request(true,1,0,true);assert(completions==2&&starts==0);
 /* Repeat after release/off remains valid; existing BT case is preserved. */
 data[0].flags=CLOCK_CONTROL_STATUS_OFF;hfclk_users=0;request(true,1,HF_USER_BT,true);assert(completions==3&&starts==0);
 /* Stopped crystal source selection is not sufficient. */
 request(false,1,0,false);assert(completions==3&&starts==1);
 clock_event_handler(NRFX_CLOCK_EVT_HFCLK_STARTED);assert(completions==4&&data[0].flags==(CTX_ONOFF|CLOCK_CONTROL_STATUS_ON));
 clock_event_handler(NRFX_CLOCK_EVT_HFCLK_STARTED);assert(completions==4);
 /* RC running retains normal asynchronous crystal start. */
 request(true,0,0,false);assert(completions==4&&starts==2);
 clock_event_handler(NRFX_CLOCK_EVT_HFCLK_STARTED);assert(completions==5);
 /* A prior USB transition can be pending before this request joins it. */
 wait_state();inject=1;assert(lism_left_hfclk_finish_pending()==1);assert(completions==6&&manager.flags==2);
 assert(lism_left_hfclk_finish_pending()==0&&completions==6);
 clock_event_handler(NRFX_CLOCK_EVT_HFCLK_STARTED);assert(completions==6);
 /* Completion wins first via IRQ: recovery must become a no-op. */
 wait_state();clock_event_handler(NRFX_CLOCK_EVT_HFCLK_STARTED);assert(completions==7);assert(!lism_left_hfclk_finish_pending());
 /* Cancelled/off transition, foreign API, missing callback, RC or stopped
  * hardware may never be completed by this startup-only helper. */
 wait_state();manager.flags=0;assert(!lism_left_hfclk_finish_pending());
 wait_state();data[0].flags=CLOCK_CONTROL_STATUS_OFF;assert(!lism_left_hfclk_finish_pending());
 wait_state();data[0].flags=0x80|CLOCK_CONTROL_STATUS_STARTING;assert(!lism_left_hfclk_finish_pending());
 wait_state();data[0].cb=NULL;assert(!lism_left_hfclk_finish_pending());
 wait_state();source=0;assert(!lism_left_hfclk_finish_pending());
 wait_state();running=false;assert(!lism_left_hfclk_finish_pending());
 wait_state();hfclk_users=0;assert(!lism_left_hfclk_finish_pending());
 assert(completions==7);
 puts("PASS gated pending-transition recovery, already-completed/cancelled/API/null-callback/RC/stopped cases and IRQ ordering");
 puts("PASS actual HFXO start/completion: inherited crystal, stopped-source, RC, BT, repeat request and pending/duplicate IRQ");
}
'''
with tempfile.TemporaryDirectory() as d:
 copy=Path(d)/'driver.c';prepare(src,copy);s=copy.read_text();c=Path(d)/'test.c';o=Path(d)/'test'
 c.write_text(pre+'\n'.join(function(s,n) for n in ['set_on_state','clkstarted_handle','generic_hfclk_start','clock_event_handler','lism_left_hfclk_finish_pending'])+main)
 subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',str(c),'-o',str(o)],check=True);subprocess.run([str(o)],check=True)
 # Never silently patch a different SDK revision.
 copy.write_text(s+'\n');
 try:prepare(copy,Path(d)/'bad.c')
 except ValueError:pass
 else:raise AssertionError('Unpinned SDK accepted')
 print('PASS pinned driver validation')
