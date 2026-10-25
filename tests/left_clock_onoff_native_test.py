"""Real pinned Zephyr onoff state machine with actual patched clock completion."""
from pathlib import Path
import ast,sys,subprocess,os
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_left_clock_handoff import prepare
from prepare_hog_routing import function
base=Path(os.environ.get('LISM_ZEPHYR_BASE','/sdk/zephyr'))
D=P.parent/'native-clock-onoff';D.mkdir(exist_ok=True)
# Reuse only the hardware stubs; compile the real SDK manager and notify library.
a=ast.parse((P/'tests/left_clock_handoff_test.py').read_text());pre=next(ast.literal_eval(n.value) for n in a.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='pre' for t in n.targets))
pre='#include <stdlib.h>\n#include <zephyr/kernel.h>\n#include <zephyr/sys/onoff.h>\n'+pre
for line in ['#define ONOFF_STATE_MASK 0x7\n','#define ONOFF_STATE_TO_ON 6\n','struct onoff_manager{unsigned flags,refs;};\n','struct device{int unused;};\n','#define __ASSERT_NO_MSG(x) assert(x)\n']:
 pre=pre.replace(line,'')
pre=pre.replace('#define IS_ENABLED(x) 0','#define TEST_IS_ENABLED(x) 0')
pre=pre.replace('static unsigned irq_lock(void){unsigned old=locked;locked=1;return old;}','static unsigned fake_irq_lock(void){unsigned old=irq_lock();locked=1;return old;}')
pre=pre.replace('static void irq_unlock(unsigned key){locked=key;if(!key&&inject){inject=0;clock_event_handler(NRFX_CLOCK_EVT_HFCLK_STARTED);}}','static void fake_irq_unlock(unsigned key){locked=0;irq_unlock(key);if(inject){inject=0;clock_event_handler(NRFX_CLOCK_EVT_HFCLK_STARTED);}}')
copy=D/'clock.c';prepare(base/'drivers/clock_control/clock_control_nrf.c',copy);s=copy.read_text()
funcs='\n'.join(function(s,n) for n in ['set_on_state','clkstarted_handle','clock_event_handler','onoff_started_callback','lism_left_hfclk_finish_pending'])
funcs=funcs.replace('irq_lock(', 'fake_irq_lock(').replace('irq_unlock(', 'fake_irq_unlock(').replace('IS_ENABLED(', 'TEST_IS_ENABLED(')
main=r'''
static struct onoff_client usb,left;
static unsigned usb_completed,left_completed,stopped,started;
static void client_callback(struct onoff_manager*m,struct onoff_client*c,uint32_t state,int res){assert(m==&manager&&state==ONOFF_STATE_ON&&res==0);if(c==&usb)usb_completed++;else{assert(c==&left);left_completed++;}}
static void init_client(struct onoff_client*c){*c=(struct onoff_client){0};sys_notify_init_callback(&c->notify,client_callback);}
static void start(struct onoff_manager*m,onoff_notify_fn notify){assert(m==&manager);started++;data[0]=(struct nrf_clock_control_sub_data){.flags=CLOCK_CONTROL_STATUS_STARTING|CTX_ONOFF,.cb=onoff_started_callback,.user_data=notify};hfclk_users=HF_USER_GENERIC;}
static void stop(struct onoff_manager*m,onoff_notify_fn notify){stopped++;data[0].flags=0;hfclk_users=0;notify(m,0);}
int main(void){
 const struct onoff_transitions t={.start=start,.stop=stop};assert(!onoff_manager_init(&manager,&t));
 /* USB starts first; left joins TO_ON. Both complete exactly once. */
 init_client(&usb);init_client(&left);assert(onoff_request(&manager,&usb)==ONOFF_STATE_OFF);assert(onoff_request(&manager,&left)==ONOFF_STATE_TO_ON);assert(started==1);
 running=true;source=1;inject=1;assert(lism_left_hfclk_finish_pending()==1);assert(usb_completed==1&&left_completed==1&&manager.refs==2);
 clock_event_handler(NRFX_CLOCK_EVT_HFCLK_STARTED);assert(!lism_left_hfclk_finish_pending());assert(usb_completed==1&&left_completed==1);
 assert(onoff_release(&manager)==ONOFF_STATE_ON);assert(manager.refs==1&&stopped==0);assert(onoff_release(&manager)==ONOFF_STATE_ON);assert(manager.refs==0&&stopped==1);
 /* Cancel USB before completion; only the left is notified. */
 init_client(&usb);init_client(&left);assert(onoff_request(&manager,&usb)==ONOFF_STATE_OFF);assert(onoff_request(&manager,&left)==ONOFF_STATE_TO_ON);assert(onoff_cancel(&manager,&usb)==ONOFF_STATE_TO_ON);
 assert(lism_left_hfclk_finish_pending());assert(usb_completed==1&&left_completed==2&&manager.refs==1);onoff_release(&manager);assert(stopped==2);
 /* Ordinary IRQ wins before recovery, followed by release/re-request. */
 init_client(&left);assert(onoff_request(&manager,&left)==ONOFF_STATE_OFF);clock_event_handler(NRFX_CLOCK_EVT_HFCLK_STARTED);assert(!lism_left_hfclk_finish_pending());assert(left_completed==3&&manager.refs==1);onoff_release(&manager);assert(stopped==3);
 /* All waiting clients cancel: completion releases the unused service. */
 init_client(&usb);assert(onoff_request(&manager,&usb)==ONOFF_STATE_OFF);assert(onoff_cancel(&manager,&usb)==ONOFF_STATE_TO_ON);assert(lism_left_hfclk_finish_pending());assert(manager.refs==0&&stopped==4&&usb_completed==1);
 puts("PASS real Zephyr onoff: two clients, cancellation, IRQ ordering, release/re-request and all-cancelled cleanup");fflush(stdout);exit(0);
}
'''
(D/'main.c').write_text(pre+funcs+main)
(D/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.20.0)\nfind_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\nproject(left_clock_onoff)\ntarget_sources(app PRIVATE main.c)\n')
(D/'prj.conf').write_text('CONFIG_ONOFF=y\nCONFIG_ASSERT=y\nCONFIG_POLL=y\n')
env=dict(os.environ,ZEPHYR_BASE=str(base),ZEPHYR_TOOLCHAIN_VARIANT='host')
for cmd in [['cmake','-S',str(D),'-B',str(D/'build'),'-GNinja','-DBOARD=native_sim/native/64'],['cmake','--build',str(D/'build'),'-j4'],[str(D/'build/zephyr/zephyr.exe')]]:
 r=subprocess.run(cmd,capture_output=True,text=True,env=env,timeout=180)
 if r.returncode:print(r.stdout+r.stderr);raise SystemExit(r.returncode)
 if cmd[0].endswith('zephyr.exe'):
  output=r.stdout+r.stderr
  print(output)
  assert 'PASS real Zephyr onoff:' in output, 'Native test did not reach success marker'
