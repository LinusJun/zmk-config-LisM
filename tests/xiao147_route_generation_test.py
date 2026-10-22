"""Run the actual receiver generation update across reboot and mode round trips."""
from pathlib import Path
import subprocess,sys,tempfile,re
P=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
status=(P/'src/kb_route/status.c').read_text()
strip=lambda s:re.sub(r'^\s*#\s*(?:include|pragma once).*$', '',s,flags=re.M)
code=r'''
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <stdio.h>
#define KB_ROUTE_VERSION 3
#define CONFIG_LISM_DONGLE_STATION 0
#define KB_STATION_COUNT 2
#define KB_STATION_NONE 255
static unsigned peer_boot=888;static unsigned kb_receiver_peer_boot(unsigned s){(void)s;return peer_boot;}
static unsigned kb_receiver_boot(void){return 777;}
#define KB_LEFT_COMMIT 1
#define K_NO_WAIT 0
struct k_spinlock{int unused;};typedef int k_spinlock_key_t;
static int k_spin_lock(struct k_spinlock*l){(void)l;return 0;}
static void k_spin_unlock(struct k_spinlock*l,int k){(void)l;(void)k;}
static int64_t now=1000;static int64_t k_uptime_get(void){return now;}
static unsigned flushes;static void kb_route_flush_commands(void){flushes++;}
static unsigned echo_phase,echo_epoch,send_command;
static void atomic_set(unsigned*p,unsigned v){*p=v;}
static void k_work_reschedule(unsigned*p,int t){(void)p;(void)t;}
struct kb_route_status{uint8_t version,mode,profile;uint32_t epoch,authority;uint8_t phase,target,station,owner_station;uint32_t receiver_boot;};
''' + strip((P/'src/kb_route/view.h').read_text()) + r'''
static struct kb_route_view view={.mode=KB_ROUTE_UNKNOWN};
static struct k_spinlock lock;static uint32_t authority,wire_boot,local_route_generation=1;
'''+''.join(function(status,n) for n in ['kb_route_status_valid','kb_receiver_route_generation','kb_route_status_received','kb_route_snapshot'])+r'''
int main(void){
 struct kb_route_status s={.version=3,.mode=KB_ROUTE_DONGLE,.profile=0,.epoch=7,.authority=123,.phase=1,.target=0,.station=0,.owner_station=0,.receiver_boot=777};
 kb_route_status_received(&s);uint32_t g=kb_receiver_route_generation();assert(g==2&&flushes==1);
 for(unsigned i=0;i<10;i++){now+=100;kb_route_status_received(&s);assert(kb_receiver_route_generation()==g);}
 s.mode=KB_ROUTE_BLE;s.target=1;s.owner_station=255;kb_route_status_received(&s);assert(kb_receiver_route_generation()==g+1);
 s.mode=KB_ROUTE_DONGLE;s.target=0;s.owner_station=0;kb_route_status_received(&s);assert(kb_receiver_route_generation()==g+2);g+=2;
 /* Repeated wire epoch after right reboot is still a different input owner. */
 s.authority=456;kb_route_status_received(&s);assert(kb_receiver_route_generation()==++g&&flushes==2);
 now+=2501;struct kb_route_view out;kb_route_snapshot(&out);assert(out.mode==KB_ROUTE_UNKNOWN);
 kb_route_status_received(&s);assert(kb_receiver_route_generation()==++g);
 s.version=1;kb_route_status_received(&s);assert(kb_receiver_route_generation()==g);
 s.version=3;s.epoch++;kb_route_status_received(&s);assert(kb_receiver_route_generation()==++g);
 peer_boot++;kb_route_status_received(&s);assert(kb_receiver_route_generation()==++g);
 local_route_generation=UINT32_MAX;s.epoch++;kb_route_status_received(&s);assert(kb_receiver_route_generation()==1);
 puts("PASS actual receiver route generation: duplicate heartbeats stable, complete mode round trip, same-epoch right reboot, expired lease, invalid status and wrap skip");return 0;
}
'''
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'generation.c';out=Path(t)/'generation';f.write_text(code)
 subprocess.run(['cc','-std=c11','-fsanitize=address,undefined','-Werror=implicit-function-declaration',str(f),'-o',str(out)],check=True)
 subprocess.run([str(out)],check=True)
