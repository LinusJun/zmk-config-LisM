from pathlib import Path
import re,subprocess,tempfile,sys
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
src=re.sub(r'^#include.*$','',(P/'src/lism_coex.c').read_text(),flags=re.M)
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#define LOG_MODULE_REGISTER(...)
#define LOG_INF(...)
typedef uint32_t atomic_t;
static uint32_t cycle;static int64_t now;
static uint32_t atomic_get(atomic_t*p){return *p;}
static uint32_t atomic_set(atomic_t*p,uint32_t v){uint32_t old=*p;*p=v;return old;}
static uint32_t atomic_inc(atomic_t*p){return (*p)++;}
static uint32_t k_cycle_get_32(void){return cycle;}
static uint32_t k_cyc_to_us_floor32(uint32_t v){return v;}
static int64_t k_uptime_get(void){return now;}
'''
main=r'''
int main(void){
 assert(lism_esb_coex_pressure());
 cycle=1;lism_coex_grant();assert(!lism_esb_coex_pressure());
 cycle+=10000;now+=10;lism_coex_grant();assert(!lism_esb_coex_pressure());
 cycle+=85000;now+=85;lism_coex_grant();assert(lism_esb_coex_pressure());
 assert(over_80==1 && max_gap_us==85000);
 for(unsigned i=0;i<499;i++){cycle+=10000;now+=10;lism_coex_grant();}
 assert(lism_esb_coex_pressure());cycle+=10000;now+=10;lism_coex_grant();assert(!lism_esb_coex_pressure());
 cycle+=85000;now+=85;assert(lism_esb_coex_pressure()); /* no new grants */
 last_cycles=0xffffff00;cycle=0x800;lism_coex_grant();assert(max_gap_us==85000); /* cycle wrap */
 return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
 f=Path(tmp)/'coex.c';f.write_text(pre+src+main);b=Path(tmp)/'coex'
 subprocess.run(['cc','-std=c11','-Wall','-Werror','-fsanitize=address,undefined',str(f),'-o',str(b)],check=True);subprocess.run([str(b)],check=True)
scan=(P/'src/lism_input_scanner.c').read_text();host=(P/'src/lism_input_host.c').read_text()
assert '.interval=160,.window=8' in scan
assert '.interval=160,.window=8,.timeout=300' in host
assert 'BT_CONN_LE_CREATE_CONN' not in host and 'BT_LE_SCAN_ACTIVE' not in scan
assert 'BT_LE_CONN_PARAM(24,40,0,400)' in host
assert 'p->interval_min>=24' in function(host,'input_param_request')
for name,queue,budget in [('control','commands',4),('control','events',8),('control','advertisements',4),('bridge_input','frames',8)]:
 assert f'budget<{budget} && !k_msgq_get(&{queue}' in function(host,name)
assert 'if (pending) break;' in function(host,'bridge_input')
print('PASS: grant-gap guard, cooldown, cycle wrap, bounded discovery/initiation, param gate and coordinator budgets')
