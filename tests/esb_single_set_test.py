from pathlib import Path
import re,subprocess,argparse
parser=argparse.ArgumentParser();parser.add_argument('--build',type=Path,required=True);args=parser.parse_args()
src=Path(__file__).resolve().parents[1];build=args.build;gen=build/'modules/zmk-config-LisM/legacy_ble.c'
header=re.sub(r'^#include <zephyr/.*$', '',(src/'src/lism_split_sets.h').read_text(),flags=re.M).replace('#pragma once','')
policy=re.sub(r'^#include .*?$','',(src/'src/lism_split_sets.c').read_text(),flags=re.M)
ble=gen.read_text();start=ble.index('static struct k_spinlock lism_split_address_lock;');allocator=ble[start:ble.index('#endif /* IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL) */',start)]
prelude=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
#include <limits.h>
#define CONFIG_LISM_SPLIT_ESB 1
#define CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS 2
#define CONFIG_SETTINGS 1
#define IS_ENABLED(x) 1
#define ZMK_SPLIT_BLE_PERIPHERAL_COUNT 2
#define K_FOREVER 0
#define K_MUTEX_DEFINE(n) pthread_mutex_t n=PTHREAD_MUTEX_INITIALIZER
static void k_mutex_lock(pthread_mutex_t *p,int t){(void)t;assert(!pthread_mutex_lock(p));}
static void k_mutex_unlock(pthread_mutex_t *p){assert(!pthread_mutex_unlock(p));}
typedef struct {uint8_t bytes[7];} bt_addr_le_t;
static bt_addr_le_t any;
#define BT_ADDR_LE_ANY (&any)
static int bt_addr_le_cmp(const bt_addr_le_t*a,const bt_addr_le_t*b){return memcmp(a,b,sizeof(*a));}
static void bt_addr_le_copy(bt_addr_le_t*a,const bt_addr_le_t*b){*a=*b;}
struct k_spinlock {pthread_mutex_t m;};
typedef int k_spinlock_key_t;
static _Thread_local int spins;
static int k_spin_lock(struct k_spinlock*l){assert(!pthread_mutex_lock(&l->m));spins++;return 0;}
static void k_spin_unlock(struct k_spinlock*l,int k){(void)k;spins--;assert(!pthread_mutex_unlock(&l->m));}
static int64_t now=100;
static int64_t k_uptime_get(void){return now;}
static bt_addr_le_t peripheral_addrs[2],saved[2];
static unsigned saved_count,peer_ready;
static bool fail_save;
static int settings_save_one(const char*n,const void*p,size_t size){
 assert(!spins);if(fail_save)return -EIO;unsigned i;assert(sscanf(n,"ble/peripheral_addresses/%u",&i)==1&&i<2);assert(size==sizeof(bt_addr_le_t));saved[i]=*(const bt_addr_le_t*)p;saved_count++;return 0;
}
static int settings_delete(const char*n){(void)n;return 0;}
uint32_t lism_split_sets_generation(uint8_t);
void lism_esb_peer_ready(uint8_t s,uint32_t g){assert(s<2);assert(g==lism_split_sets_generation(s));peer_ready++;}
'''
test=r'''
static void cleanup(unsigned s){uint8_t bits[16];assert(lism_split_sets_take_cleanup(s,bits));lism_split_sets_cleanup_done(s);}
int main(void){
 bt_addr_le_t left={{10}},right={{20}},other={{30}};
 assert(zmk_ble_put_peripheral_addr(&left)==-ENOENT);
 /* Right can arrive first; role decides slot, never advertisement order. */
 lism_legacy_role_observe(&right,1);assert(zmk_ble_put_peripheral_addr(&right)==1);
 assert(legacy_staged[1]&&saved_count==0);
 assert(!lism_split_sets_reserve(1));lism_split_sets_connected(1);lism_split_sets_ready(1);
 assert(!legacy_staged[1]&&saved_count==1&&!bt_addr_le_cmp(&saved[1],&right));
 lism_legacy_role_observe(&other,1);assert(zmk_ble_put_peripheral_addr(&other)==-EEXIST);
 /* Failure before ready rolls back RAM only, preserves established right. */
 lism_legacy_role_observe(&left,0);assert(zmk_ble_put_peripheral_addr(&left)==0);
 assert(!lism_split_sets_reserve(0));unsigned old=lism_split_sets_generation(0);
 lism_split_sets_released(0);assert(!lism_split_sets_slot_registered(0));cleanup(0);
 assert(lism_split_sets_slot_registered(1)&&saved_count==1);
 /* A cleanup fence on the other source rejects reserve; undo this stage. */
 lism_split_sets_released(1);
 lism_legacy_role_observe(&left,0);assert(zmk_ble_put_peripheral_addr(&left)==0);
 assert(lism_split_sets_reserve(0)==-EBUSY);lism_legacy_rollback(0);
 assert(!lism_split_sets_slot_registered(0));cleanup(1);
 /* Old ready cannot commit a new stage; current ready can commit it. */
 assert(zmk_ble_put_peripheral_addr(&left)==0);assert(!lism_split_sets_reserve(0));
 lism_split_sets_connected(0);lism_legacy_commit(0,old);assert(saved_count==1&&legacy_staged[0]);
 lism_split_sets_ready(0);assert(saved_count==2&&!legacy_staged[0]);
 lism_split_sets_applied(0,15,true);lism_split_sets_applied(0,17,true);
 lism_split_sets_released(0);uint8_t held[16];assert(lism_split_sets_take_cleanup(0,held));
 assert((held[1]&128)&&(held[2]&2));assert(!lism_split_sets_dispatch(0,old));
 lism_split_sets_cleanup_done(0);assert(lism_split_sets_slot_registered(0));
 /* Only two roles exist; unrecognized/outdated advertisements cannot bind. */
 assert(lism_split_sets_reserve(2)==-EINVAL&&lism_split_sets_reserve(4)==-EINVAL);
 now+=6000;assert(lism_legacy_role_for_addr(&other)==-1);
 assert(zmk_ble_put_peripheral_addr(&right)==1); /* Saved known-first survives cache expiry. */
 puts("PASS actual single-set policy/allocator: role order, two-slot bounds, no overwrite, failed discovery rollback, reserve-busy rollback, stale-ready generation, authenticated commit, held-key cleanup, saved-peer recovery");
}
'''
path=build/'single_set_actual.c';path.write_text(prelude+header+policy+allocator+test)
exe=build/'single_set_actual';subprocess.run(['cc','-std=c11','-pthread','-fsanitize=address,undefined','-g',str(path),'-o',str(exe)],check=True);subprocess.run([str(exe)],check=True)
# Check the real generated call-site reaches rollback on reserve failure.
central=(gen.parent/'legacy_central.c').read_text()
assert 'if (lism_split_sets_reserve(i) != 0) { lism_legacy_rollback(i); return -EBUSY; }' in central
assert 'if (i < 0 || i >= ZMK_SPLIT_BLE_PERIPHERAL_COUNT)' in ble
assert 'read_cb(cb_arg, &peripheral_addrs[i]' in ble
print('PASS generated reserve failure rollback and old NVS bounds-check anchors')
