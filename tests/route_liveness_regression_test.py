"""Actual receiver command_work and coordinator route_work liveness regressions.

Default mode asserts repaired behavior. --expect-old records the known defects
against the unmodified sources as explicit XFAIL observations (not model proof).
Both binaries execute extracted production C; only scheduling/I/O are mocked.
No RF, USB delivery, real concurrency or physical timeout claim is made.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import sys
import tempfile

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P / 'tools'))
from prepare_hog_routing import function
old = '--expect-old' in sys.argv[1:]
status = (P / 'src/kb_route/status.c').read_text()
hub = (P / 'src/kb_route/hub.c').read_text()
for name, source in [('status.c', status), ('hub.c', hub)]:
    print(f'SOURCE {name} SHA256 {hashlib.sha256(source.encode()).hexdigest()}', flush=True)

receiver_pre = r'''
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "/work/source/src/kb_route/left_protocol.h"
#include "/work/source/src/kb_route/view.h"
#define KB_ROUTE_VERSION 3
#define CONFIG_LISM_DONGLE_STATION 0
#define ARG_UNUSED(x) (void)(x)
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
#define ATOMIC_INIT(x) (x)
typedef int atomic_t;
static int atomic_get(atomic_t*p){return *p;}
static int atomic_set(atomic_t*p,int v){int old=*p;*p=v;return old;}
static bool atomic_cas(atomic_t*p,int old,int v){if(*p!=old)return false;*p=v;return true;}
static void atomic_clear(atomic_t*p){*p=0;}
struct k_spinlock{int x;};typedef int k_spinlock_key_t;
static int k_spin_lock(void*p){(void)p;return 0;}static void k_spin_unlock(void*p,int k){(void)p;(void)k;}
struct k_work {int x;};
#define K_WORK_DELAYABLE_DEFINE(n,f) static int n
static int64_t now=1000;static int64_t k_uptime_get(void){return now;}
static uint32_t boot=123,peer=456;
static uint32_t kb_receiver_boot(void){return boot;}
static uint32_t kb_receiver_peer_boot(unsigned n){assert(n==1);return peer;}
struct kb_route_status{uint8_t version,mode,profile;uint32_t epoch,authority;uint8_t phase,target,station,owner_station;uint32_t receiver_boot;} __attribute__((packed));
static bool queue_idle=true,holdtap_idle=true,combo_idle=true;
static bool kb_route_queue_idle(void){return queue_idle;}
static bool kb_route_holdtap_idle(void){return holdtap_idle;}
static bool kb_route_combo_idle(void){return combo_idle;}
static unsigned schedules,flushes,invokes;static int invoke_rc;
static uint32_t last_action,last_epoch;static void (*during_invoke)(void);
static int k_work_reschedule(void*p,int n){(void)p;(void)n;schedules++;return 0;}
static void kb_route_flush_commands(void){flushes++;}
struct zmk_behavior_binding {const char*behavior_dev;uint32_t param1,param2;};
struct zmk_behavior_binding_event {unsigned source,position;int64_t timestamp;};
static int zmk_split_central_invoke_behavior(unsigned n,const struct zmk_behavior_binding*b,struct zmk_behavior_binding_event e,bool down){
 assert(n==1&&e.source==1&&e.position==0&&down&&(!strcmp(b->behavior_dev,"KB_ROUTE")||!strcmp(b->behavior_dev,"KB_PROBE3")));
 invokes++;last_action=b->param1;last_epoch=b->param2;
 if(during_invoke){void(*hook)(void)=during_invoke;during_invoke=NULL;hook();}
 return invoke_rc;
}
'''
receiver_body = re.sub(r'^#include.*$', '', status.split('#include <zmk/hid.h>', 1)[0], flags=re.M)
receiver_main = r'''
static struct kb_route_status interleave_status;
static void tuple_aba(void){
 struct kb_route_status b=interleave_status;b.authority++;
 kb_route_status_received(&b);kb_route_status_received(&interleave_status);
}
static void busy(unsigned which){queue_idle=which!=1;holdtap_idle=which!=2;combo_idle=which!=3;}
static void check(bool condition,bool known_old,const char*name){
 if(OLD && known_old){assert(!condition);printf("XFAIL actual receiver old source: %s\n",name);}
 else{assert(condition);printf("PASS actual receiver: %s\n",name);}
}
int main(int argc,char**argv){
 assert(argc==2);unsigned test=atoi(argv[1]);
 struct kb_route_status t={.version=3,.mode=KB_ROUTE_DONGLE,.profile=0,.epoch=7,.authority=900,.phase=KB_LEFT_COMMIT,.target=KB_ROUTE_DONGLE,.station=0,.owner_station=0,.receiver_boot=boot};
 if(test==15){
  interleave_status=t;during_invoke=tuple_aba;
  kb_route_status_received(&t);command_work(NULL);
  assert(invokes==1&&atomic_get(&echo_epoch)==7);
  busy(1);command_work(NULL);assert(invokes==1&&atomic_get(&echo_epoch)==7);
  busy(0);command_work(NULL);assert(invokes==2&&atomic_get(&echo_epoch)==0);
  busy(1);kb_route_status_received(&t);command_work(NULL);assert(invokes==3);
  puts("PASS actual receiver: enqueue tuple A->B->A cannot establish old completion or clear new echo; fresh idle completion restores qualification");return 0;
 }
 if(test==4){busy(1);kb_route_status_received(&t);command_work(NULL);assert(!invokes);puts("PASS actual receiver: first COMMIT waits for idle");return 0;}
 if(test==10){
  invoke_rc=-EAGAIN;kb_route_status_received(&t);command_work(NULL);assert(invokes==1);
  invoke_rc=0;busy(1);kb_route_status_received(&t);command_work(NULL);
  assert(invokes==1);puts("PASS actual receiver: failed first COMMIT cannot unlock busy liveness bypass");return 0;
 }
 kb_route_status_received(&t);command_work(NULL);assert(invokes==1&&last_action==21&&last_epoch==7);
 now+=100;
 if(test==13){
  now+=2501;busy(1);kb_route_status_received(&t);command_work(NULL);assert(invokes==1);
  busy(0);command_work(NULL);assert(invokes==2);
  busy(1);kb_route_status_received(&t);command_work(NULL);assert(invokes==3);
  puts("PASS actual receiver: status expiry over 2500ms revokes established liveness until a new idle COMMIT");return 0;
 }
 if(test==14){
  busy(1);struct kb_route_status freeze=t;freeze.mode=KB_ROUTE_SWITCHING;freeze.phase=KB_LEFT_FREEZE;
  kb_route_status_received(&freeze);command_work(NULL);assert(invokes==1);
  kb_route_status_received(&t);command_work(NULL);assert(invokes==1);
  busy(0);command_work(NULL);assert(invokes==2);
  puts("PASS actual receiver: FREEZE->COMMIT same epoch still needs a new idle handoff completion");return 0;
 }
 if(test==16){
  struct kb_discovery discovery={.version=3,.authority=t.authority+1,.station=0,.cookie=77};
  assert(kb_route_discovery_release(&discovery));assert(!kb_route_discovery_received(&discovery));assert(invokes==2);
  busy(1);kb_route_status_received(&t);command_work(NULL);assert(invokes==2);
  busy(0);command_work(NULL);assert(invokes==3);
  busy(1);kb_route_status_received(&t);command_work(NULL);assert(invokes==4);
  puts("PASS actual receiver: discovery release revokes liveness qualification until a new idle COMMIT");return 0;
 }
 if(test<=3){
  busy(test);
  // Status continues every 500 ms while the hold outlasts the 2 s lease.
  for(unsigned i=0;i<6;i++){now+=500;kb_route_status_received(&t);command_work(NULL);}
  check(invokes==7&&last_action==21&&last_epoch==7,true,
        test==1?"same committed tuple echoes while queue busy":test==2?"same committed tuple echoes while holdtap busy":"same committed tuple echoes while combo busy");
  return 0;
 }
 busy(1);
 if(test==5)t.authority++;
 if(test==6)t.epoch++;
 if(test==7)peer++;
 if(test==8){boot++;t.receiver_boot=boot;}
 if(test==11){t.mode=KB_ROUTE_SWITCHING;t.phase=KB_LEFT_FREEZE;}
 if(test==12){t.mode=KB_ROUTE_SWITCHING;t.owner_station=1;}
 kb_route_status_received(&t);
 if(test==9)kb_route_request(KB_ROUTE_BLE);
 command_work(NULL);assert(invokes==1);
 busy(0);command_work(NULL);assert(invokes==2);
 assert(last_action==(test==9?KB_ROUTE_BLE:test==11?20:21));
 puts("PASS actual receiver: changed tuple/phase/owner or manual command requires idle");
}
'''
# Reuse only construction prefixes: no legacy test main is executed.
base_path = P / 'tests/kb_route_native_test.py'
base = {'__file__': str(base_path)}
exec(compile(base_path.read_text().split("route_pre=r'''", 1)[0], str(base_path), 'exec'), base)
mode_path = P / 'tests/kb_mode_transaction_test.py'
fixture = {'__file__': str(mode_path), 'base': base}
prefix_source = mode_path.read_text().split("main=r'''", 1)[0]
prefix_source = prefix_source.replace("b=runpy.run_path(str(P/'tests/kb_route_native_test.py'))", 'b=base')
assert 'runpy.run_path(' not in prefix_source
exec(compile(prefix_source, str(mode_path), 'exec'), fixture)
prefix = fixture['prefix']
# Use actual coordinator state declarations, so new production state is covered
# without reproducing the logic in the test fixture.
start = prefix.index('static struct k_spinlock state_lock;')
end = prefix.index('static uint8_t radio_station;')
globals_ = hub[hub.index('static struct k_spinlock state_lock;'):hub.index('static void finish_commit_locked(')]
globals_ += hub[hub.index('static atomic_t requested='):hub.index('static void route_work(')]
prefix = prefix[:start] + globals_ + prefix[end:]
names = ['finish_commit_locked', 'dongle_confirmed', 'all_released',
         'kb_route_probe_offer', 'kb_route_probe_transmitted', 'probe_window',
         'send_probe', 'left_prepared', 'neutral_ble', 'begin_transaction',
         'application_fenced', 'prepare_local_target', 'sync_left', 'route_work']
coordinator_main = r'''
static void setup(void){
 initialized=true;boot_probe=false;stage=ST_STABLE;selection_pending=false;switching=false;
 current_mode=desired_mode=KB_ROUTE_DONGLE;station=desired_station=preferred_station=0;
 radio_station=0;radio_boot=receiver_boot=gate_boot=77;gate_station=0;epoch=7;
 now=1000;confirmed_at=now;physical[1].keys=1;
}
static void run(void){route_work(NULL);}
int main(int argc,char**argv){
 assert(argc==2);unsigned test=atoi(argv[1]);setup();
 if(test==1){
  now+=2001;run();assert(stage==ST_STABLE&&selection_pending&&epoch==7&&!resets);
  confirmed_at=now;run();physical[1].keys=0;run();
  bool canceled=stage==ST_STABLE&&!selection_pending&&epoch==7&&!resets;
  if(OLD){assert(!canceled&&stage==ST_DRAIN&&epoch==8&&resets==1);puts("XFAIL actual coordinator old source: recovered confirmation while held still reselects after release");}
  else{assert(canceled);puts("PASS actual coordinator: recovered confirmation cancels automatic pending before release");}
 }else if(test==2||test==3||test==6||test==7){
  now+=2001;run();assert(selection_pending&&stage==ST_STABLE);
  requested=test==2?31:test==6?30:test==7?KB_ROUTE_DONGLE:KB_ROUTE_BLE;run();
  assert(stage==ST_STABLE&&selection_pending);
  confirmed_at=now;run();physical[1].keys=0;run();
  assert(stage==ST_DRAIN&&switching&&epoch==8&&resets==1);
  assert(desired_mode==(test==3?KB_ROUTE_BLE:KB_ROUTE_DONGLE));
  if(test==2)assert(desired_station==1);if(test==6||test==7)assert(desired_station==0);
  puts("PASS actual coordinator: explicit station/mode intent survives recovered confirmation");
 }else if(test==4){
  physical[1].keys=0;now=2999;run();assert(stage==ST_STABLE&&!selection_pending&&epoch==7);
  now=3000;run();assert(stage==ST_DRAIN&&switching&&epoch==8&&resets==1);
  puts("PASS actual coordinator: real loss with all released still triggers at 2 seconds");
 }else if(test==5){
  now+=2001;run();assert(stage==ST_STABLE&&selection_pending);
  physical[1].keys=0;run();assert(stage==ST_DRAIN&&epoch==8&&resets==1);
  puts("PASS actual coordinator: unrecovered automatic loss reselects on release");
 }
}
'''
with tempfile.TemporaryDirectory() as directory:
    for label, code, count in [
        ('receiver', receiver_pre + receiver_body + receiver_main, 12 if old else 16),
        ('coordinator', base['pre'] + '#define ATOMIC_INIT(x) (x)\n#include <stdlib.h>\n' + prefix + '\n'.join(function(hub, n) for n in names) + coordinator_main, 7),
    ]:
        c = Path(directory) / f'{label}.c'
        binary = Path(directory) / label
        c.write_text(f'#define OLD {int(old)}\n' + code)
        subprocess.run(['cc', '-std=gnu11', '-Werror=implicit-function-declaration',
                        '-fsanitize=address,undefined', str(c), '-o', str(binary)], check=True)
        for case in range(1, count + 1):
            subprocess.run([str(binary), str(case)], check=True)
print('EXPECTED OLD DEFECTS REPRODUCED' if old else 'PASS liveness regressions: established COMMIT echo and recoverable automatic reconnect; handoff/manual idle fences retained')
