"""Actual delayed observer distinguishes startup stages without changing hardware."""
from pathlib import Path
import tempfile,subprocess,sys
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
s=(P/'src/esb_upstream/left_radio.inc').read_text()
defs=s[s.index('enum left_boot_stage'):s.index('static struct k_spinlock left_lock;')]
pre=r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <stdarg.h>
#include <stdio.h>
#include <assert.h>
#define ARG_UNUSED(x) (void)(x)
#define K_SECONDS(x) ((x)*1000)
typedef int atomic_t;
static int atomic_get(atomic_t*p){return *p;}
static atomic_t ready,enabled;
static void (*callback)(void);
static uint32_t boot_id;
struct zmk_split_transport_peripheral{int unused;};
static struct zmk_split_transport_peripheral fake_transport;
const struct zmk_split_transport_peripheral *active_transport;
static struct{uint32_t RESETREAS;} power_regs={.RESETREAS=4};
static struct{uint32_t HFCLKSTAT,EVENTS_HFCLKSTARTED,INTENSET;} clock_regs={.HFCLKSTAT=0x10001,.EVENTS_HFCLKSTARTED=1,.INTENSET=17};
static struct{uint32_t EVENTS_VALRDY,INTENSET;} rng_regs={.EVENTS_VALRDY=1,.INTENSET=1};
#define NRF_POWER (&power_regs)
#define NRF_CLOCK (&clock_regs)
#define NRF_RNG (&rng_regs)
#define POWER_CLOCK_IRQn 0
#define RNG_IRQn 1
static unsigned NVIC_GetEnableIRQ(unsigned n){return n+1;}
static unsigned NVIC_GetPendingIRQ(unsigned n){return n;}
static unsigned logs,hwlogs,sleeps,mode,stages[15];
static void log_record(const char*fmt,...){va_list a;va_start(a,fmt);if(strstr(fmt,"BOOT42"))stages[logs++]=va_arg(a,unsigned);else hwlogs++;va_end(a);}
#define LOG_INF(...) log_record(__VA_ARGS__)
static void lism_left_trace_boot_log(void){}
static void k_sleep(int);
'''
main=r'''
static void k_sleep(int ms){
 assert(ms==2000);sleeps++;
 if(mode==1){
  if(sleeps==1)left_boot_stage=LEFT_BOOT_RNG;
  if(sleeps==2){left_boot_stage=LEFT_BOOT_READY;ready=1;}
  if(sleeps==3){enabled=1;active_transport=&fake_transport;}
 }
}
int main(void){
 unsigned char hardware[sizeof(power_regs)+sizeof(clock_regs)+sizeof(rng_regs)];
 memcpy(hardware,&power_regs,sizeof(power_regs));memcpy(hardware+sizeof(power_regs),&clock_regs,sizeof(clock_regs));memcpy(hardware+sizeof(power_regs)+sizeof(clock_regs),&rng_regs,sizeof(rng_regs));
 left_boot_stage=LEFT_BOOT_HFCLK;left_boot_observer(NULL,NULL,NULL);
 assert(logs==15&&hwlogs==15&&sleeps==15&&ready==0&&enabled==0&&!active_transport);
 for(unsigned i=0;i<logs;i++)assert(stages[i]==LEFT_BOOT_HFCLK);
 assert(!memcmp(hardware,&power_regs,sizeof(power_regs))&&!memcmp(hardware+sizeof(power_regs),&clock_regs,sizeof(clock_regs))&&!memcmp(hardware+sizeof(power_regs)+sizeof(clock_regs),&rng_regs,sizeof(rng_regs)));
 logs=hwlogs=sleeps=0;mode=1;left_boot_stage=LEFT_BOOT_ENTERED;
 left_boot_observer(NULL,NULL,NULL);
 assert(logs==4&&hwlogs==3&&sleeps==3);
 assert(stages[0]==LEFT_BOOT_ENTERED&&stages[1]==LEFT_BOOT_RNG&&stages[2]==LEFT_BOOT_READY&&stages[3]==LEFT_BOOT_READY);
 logs=hwlogs=sleeps=0;left_boot_observer(NULL,NULL,NULL);assert(logs==1&&!hwlogs&&!sleeps);
 puts("PASS actual delayed boot observer: HF/RNG/ready-to-transport stages visible; no clock/RNG writes, no recovery reset, bounded observer exits after enabled transport");
}
'''
with tempfile.TemporaryDirectory() as d:
 f=Path(d)/'boot.c';o=Path(d)/'boot';f.write_text(pre+defs+function(s,'left_boot_observer')+main)
 subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True);subprocess.run([str(o)],check=True)
