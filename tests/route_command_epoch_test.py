"""Actual right command consumer rejects stale queued receiver intent."""
from pathlib import Path
import runpy,tempfile,subprocess
P=Path(__file__).resolve().parents[1];b=runpy.run_path(str(P/'tests/kb_route_native_test.py'));function=b['function'];s=(P/'src/kb_route/hub.c').read_text()
pre=b['pre']+r'''
struct route_command{uint8_t pipe,station;uint32_t epoch,peer;struct zmk_split_transport_central_command command;};
static struct{struct route_command q[16];unsigned n,h;}commands;
static int k_msgq_get(void*q,struct route_command*c,int wait){(void)q;(void)wait;if(commands.h==commands.n)return -ENOMSG;*c=commands.q[commands.h++];return 0;}
static uint32_t epoch=1,ready_epoch,peer=77;static uint8_t ready_phase,ready_station;static uint32_t ready_boot;
enum {ST_STABLE,ST_DRAIN,ST_HUNT,ST_PREPARE,ST_READY,ST_LEFT};static unsigned stage=ST_STABLE;static uint32_t receiver_boot=88,drain_boot=88;
static uint8_t kb_uplink_station_current(void){return 0;}
static int kb_route_select_dongle(unsigned s){assert(s<2);return 0;}static bool boot_probe,switching=true,target_announced;
static int64_t confirmed_at;static struct k_spinlock state_lock;
static uint32_t kb_uplink_peer_boot(void){return peer;}
static unsigned mode_requests,profile_requests,invokes;static void kb_route_request(unsigned m){assert(m<2);mode_requests++;}
static int kb_route_select(unsigned p){assert(p<5);profile_requests++;return 0;}
struct zmk_behavior_binding{const char*behavior_dev;uint32_t param1,param2;};struct zmk_behavior_binding_event{uint32_t position;unsigned source;int64_t timestamp;};
static int zmk_behavior_invoke_binding(const struct zmk_behavior_binding*b,struct zmk_behavior_binding_event e,bool state){(void)b;(void)e;assert(state);invokes++;return 0;}
'''
main=r'''
static void add(uint32_t ep,uint32_t b,unsigned action){struct route_command*c=&commands.q[commands.n++];*c=(struct route_command){.pipe=1,.epoch=ep,.peer=b,.command={.type=ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR}};strcpy(c->command.data.invoke_behavior.behavior_dev,"KB_ROUTE");c->command.data.invoke_behavior.param1=action;c->command.data.invoke_behavior.param2=ep;c->command.data.invoke_behavior.state=1;}
int main(void){
 add(1,77,1);epoch=2;run_commands();assert(!mode_requests);
 add(2,77,1);peer=88;run_commands();assert(!mode_requests);
 add(2,88,1);run_commands();assert(mode_requests==1);
 stage=ST_PREPARE;add(2,88,21);run_commands();assert(!ready_epoch); /* early COMMIT can't satisfy FREEZE */
 add(2,88,20);run_commands();assert(ready_epoch==2&&ready_phase==0);
 stage=ST_READY;target_announced=true;add(2,88,20);run_commands();assert(ready_phase==0);
 add(2,88,21);run_commands();assert(ready_phase==1);
 stage=ST_STABLE;add(1,88,1);run_commands();assert(mode_requests==1);
 puts("PASS actual right command execution: queued old epoch, receiver reboot, valid mode request, phase-specific READY, late/stale ACK");
}
'''
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'commands.c';o=Path(t)/'commands';f.write_text(pre+function(s,'run_commands')+main);subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True);subprocess.run([str(o)],check=True)
