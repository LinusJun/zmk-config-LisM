"""Actual startup snapshot code only reads hardware and logs after early init."""
from pathlib import Path
import tempfile,subprocess,sys
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
s=(P/'src/left_boot_trace.c').read_text();defs=s[s.index('struct snapshot {'):s.index('static void capture(')]
pre=r'''
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>
#include <stdio.h>
static struct{uint32_t RESETREAS,GPREGRET,GPREGRET2;}power={1,2,3};
static struct{uint32_t HFCLKSTAT,EVENTS_HFCLKSTARTED,INTENSET;}clock_regs={0x10001,0,0x380};
static struct{uint32_t ENABLE;}usb={1};static struct{uint32_t CHEN;}ppi={0x303};
static struct{uint32_t MODE,BITMODE,PRESCALER,SHORTS,INTENSET,CC[4];}timer={1,2,3,4,5,{6,7,8,9}};
static struct{uint32_t POWER,STATE,SHORTS,INTENSET,MODE,FREQUENCY;}radio={1,2,3,4,5,37};
#define NRF_POWER (&power)
#define NRF_CLOCK (&clock_regs)
#define NRF_USBD (&usb)
#define NRF_PPI (&ppi)
#define NRF_TIMER2 (&timer)
#define NRF_RADIO (&radio)
static unsigned locked,logs;
static unsigned irq_lock(void){assert(!locked);locked=1;return 0;}
static void irq_unlock(unsigned key){assert(locked&&!key);locked=0;}
#define LOG_INF(...) do{assert(!locked);logs++;}while(0)
void lism_left_clock_snapshot(uint32_t out[16]){for(unsigned i=0;i<16;i++)out[i]=i;}
'''
main=r'''
int main(void){
 unsigned char hw[sizeof(power)+sizeof(clock_regs)+sizeof(usb)+sizeof(ppi)+sizeof(timer)+sizeof(radio)];
 unsigned char*cursor=hw;
#define SAVE(x) do{memcpy(cursor,&x,sizeof(x));cursor+=sizeof(x);}while(0)
 SAVE(power);SAVE(clock_regs);SAVE(usb);SAVE(ppi);SAVE(timer);SAVE(radio);
 assert(capture_early()==0&&!logs&&!locked);assert(early.hf==0x10001&&early.usb==1&&early.cc[0]==6&&early.gpregret2==3);
 lism_left_trace_configured();assert(configured_valid&&!memcmp(&early,&configured,sizeof(early))&&!logs);
 lism_left_trace_boot_log();assert(logs==8&&!locked);
 lism_left_trace_expiry(42,3,1,1500,17);assert(logs==14&&!locked);
 cursor=hw;
#define SAME(x) do{assert(!memcmp(cursor,&x,sizeof(x)));cursor+=sizeof(x);}while(0)
 SAME(power);SAME(clock_regs);SAME(usb);SAME(ppi);SAME(timer);SAME(radio);
 puts("PASS actual early/configured/expiry snapshots: no MMIO writes, no early logging, fixed cache and logs outside IRQ lock");
}
'''
with tempfile.TemporaryDirectory() as d:
 f=Path(d)/'trace.c';o=Path(d)/'trace';f.write_text(pre+defs+'\n'.join(function(s,n) for n in ['capture','capture_early','lism_left_trace_configured','log_snapshot','log_clock','lism_left_trace_boot_log','lism_left_trace_expiry'])+main)
 subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True);subprocess.run([str(o)],check=True)
assert 'SYS_INIT(capture_early,PRE_KERNEL_1,0)' in s
