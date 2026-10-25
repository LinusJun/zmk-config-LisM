"""Actual receiver station/session validation and presence-only discovery."""
from pathlib import Path
import subprocess,tempfile,sys,re
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
s=(P/'src/kb_route/status.c').read_text()
pre=r'''
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include "/work/source/src/kb_route/left_protocol.h"
#include "/work/source/src/kb_route/view.h"
#define KB_ROUTE_VERSION 3
#define K_NO_WAIT 0
struct k_spinlock{int x;};typedef int k_spinlock_key_t;
static int k_spin_lock(void*p){(void)p;return 0;}static void k_spin_unlock(void*p,int k){(void)p;(void)k;}
static int64_t now=1000;static int64_t k_uptime_get(void){return now;}
static uint32_t boot=123,peer=456;static uint32_t kb_receiver_boot(void){return boot;}static uint32_t kb_receiver_peer_boot(unsigned n){assert(n==1);return peer;}
struct kb_route_status{uint8_t version,mode,profile;uint32_t epoch,authority;uint8_t phase,target,station,owner_station;uint32_t receiver_boot;} __attribute__((packed));
static struct kb_route_view view={.mode=KB_ROUTE_UNKNOWN};static struct k_spinlock lock;static uint32_t authority,wire_boot,local_route_generation=1;
static uint32_t echo_generation=1,established_echo_generation,echo_receiver_boot;
static bool echo_dongle_commit;
static int echo_epoch,echo_phase,send_command;static void atomic_set(int*p,int v){*p=v;}static void k_work_reschedule(void*p,int n){(void)p;(void)n;}
static unsigned flushes,commands;static void kb_route_flush_commands(void){flushes++;}
struct zmk_behavior_binding {const char*behavior_dev;uint32_t param1,param2;};
struct zmk_behavior_binding_event {unsigned source,position;int64_t timestamp;};
static uint32_t expected_cookie;
static int zmk_split_central_invoke_behavior(unsigned n,const struct zmk_behavior_binding*b,struct zmk_behavior_binding_event e,bool down){
 assert(n==1 && e.source==1 && e.position==CONFIG_LISM_DONGLE_STATION && down);assert(b->param1==expected_cookie && b->param2==boot);commands++;return 0;
}
'''
main=r'''
int main(void){
 struct kb_route_status t={.version=3,.mode=KB_ROUTE_DONGLE,.profile=4,.epoch=7,.authority=900,.phase=KB_LEFT_COMMIT,.target=KB_ROUTE_DONGLE,.station=CONFIG_LISM_DONGLE_STATION,.owner_station=CONFIG_LISM_DONGLE_STATION,.receiver_boot=boot};
 assert(kb_route_status_valid(&t));t.station^=1;assert(!kb_route_status_valid(&t));t.station^=1;
 t.owner_station^=1;assert(!kb_route_status_valid(&t));t.owner_station^=1;
 t.receiver_boot++;assert(!kb_route_status_valid(&t));t.receiver_boot=boot;
 kb_route_status_received(&t);assert(view.mode==KB_ROUTE_DONGLE && wire_boot==peer);
 struct kb_left_packet l={.authority=t.authority,.epoch=t.epoch,.station=CONFIG_LISM_DONGLE_STATION,.receiver_boot=boot,.battery=50};assert(kb_route_left_allowed(&l));l.station^=1;assert(!kb_route_left_allowed(&l));l.station^=1;l.receiver_boot++;assert(!kb_route_left_allowed(&l));l.receiver_boot=boot;
 struct kb_discovery p={.version=3,.authority=t.authority,.station=CONFIG_LISM_DONGLE_STATION,.cookie=77};expected_cookie=p.cookie;
 int64_t seen=view.seen;uint32_t epoch=view.epoch;assert(!kb_route_discovery_release(&p));now+=10;assert(!kb_route_discovery_received(&p));assert(view.seen==seen && view.epoch==epoch && view.mode==KB_ROUTE_DONGLE);
 /* A rebooted right probing closes stale ownership, but never acquires it. */
 peer++;assert(kb_route_discovery_release(&p));assert(!kb_route_discovery_received(&p));assert(view.mode==KB_ROUTE_SWITCHING && view.seen==seen && !kb_route_left_allowed(&l));
 unsigned old=local_route_generation;kb_route_status_received(&t);assert(local_route_generation!=old && wire_boot==peer);
 t.epoch--;assert(!kb_route_status_valid(&t));t.epoch++;
 t.mode=KB_ROUTE_BLE;t.target=KB_ROUTE_BLE;t.owner_station=KB_STATION_NONE;kb_route_status_received(&t);assert(view.mode==KB_ROUTE_BLE && !kb_route_left_allowed(&l));
 seen=view.seen;now+=1000;assert(!kb_route_discovery_release(&p));kb_route_discovery_received(&p);assert(view.seen==seen && view.mode==KB_ROUTE_BLE);
 assert(commands==3);puts("PASS actual receiver: A/B isolation, boot+authority+epoch gate, nonowner rejection, discovery cannot renew input/view lease, right reboot closes old gate, BLE mirror remains input-free");
}
'''
code=pre+'\n'.join(function(s,n) for n in ['kb_route_status_valid','kb_route_discovery_release','kb_route_discovery_received','kb_route_status_received','kb_route_left_allowed'])+main
with tempfile.TemporaryDirectory() as d:
 f=Path(d)/'station.c';f.write_text(code)
 for slot in [0,1]:
  o=Path(d)/f'station{slot}';subprocess.run(['cc','-std=gnu11','-Wall','-Werror=implicit-function-declaration','-fsanitize=address,undefined',f'-DCONFIG_LISM_DONGLE_STATION={slot}',str(f),'-o',str(o)],check=True);subprocess.run([str(o)],check=True)
