"""Actual auto-enrollment state machine: advertisements, failure and power loss."""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
source=(root/'src/lism_split_auto.c').read_text()
source=re.sub(r'^#include .*$', '',source,flags=re.M)
prelude=r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
#define ARRAY_SIZE(a) (sizeof(a)/sizeof(a[0]))
#define MIN(a,b) ((a)<(b)?(a):(b))
#define MAX(a,b) ((a)>(b)?(a):(b))
#define ARG_UNUSED(a) (void)(a)
#define LOG_MODULE_REGISTER(...)
#define LOG_INF(...) ((void)0)
#define LOG_LEVEL_INF 3
#define BT_GAP_ADV_TYPE_ADV_IND 0
#define BT_GAP_ADV_TYPE_ADV_DIRECT_IND 1
#define BT_GAP_ADV_TYPE_SCAN_RSP 4
#define K_MSEC(n) (n)
#define K_SECONDS(n) ((n)*1000)
#define K_NO_WAIT 0
#define CONFIG_LISM_AUTO_SPLIT_SETS 1
#define BT_DATA_NAME_COMPLETE 9
#define BT_DATA_NAME_SHORTENED 8
#define BT_DATA_UUID128_SOME 6
#define BT_DATA_UUID128_ALL 7
#define BT_DATA_SVC_DATA128 0x21
#define LISM_SPLIT_ROLE_VERSION 1
#define LISM_SPLIT_ROLE_UUID 0
#define BT_UUID_INIT_128(...) {{3,0xef,0x91,0xd1,0x29,0x7c,0x28,0xa3,0x56,0x4a,0x38,0x61,0x10,0x9c,0x7b,0x8a}}
struct bt_uuid_128 {uint8_t val[16];};
static const struct bt_uuid_128 split_service_uuid={{0x2a,0x48,0xc2,0xb1,0xcf,0xc5,0x67,0xc9,7,0x71,0x96,0,0,0,0,0}};
struct bt_data {uint8_t type,data_len;const uint8_t *data;};
struct net_buf_simple {const uint8_t *data;size_t len;};
static void bt_data_parse(struct net_buf_simple*b,bool(*cb)(struct bt_data*,void*),void*ctx){
 while(b->len){unsigned n=b->data[0];if(!n||n+1>b->len)return;
  struct bt_data d={b->data[1],n-1,b->data+2};if(!cb(&d,ctx))return;b->data+=n+1;b->len-=n+1;}
}
typedef struct {uint8_t val[7];} bt_addr_le_t;
static bt_addr_le_t zero,roles[6],persisted_roles[6];
#define BT_ADDR_LE_ANY (&zero)
static int bt_addr_le_cmp(const bt_addr_le_t*a,const bt_addr_le_t*b){return memcmp(a,b,sizeof(*a));}
struct k_spinlock {bool locked;};typedef int k_spinlock_key_t;
static int k_spin_lock(struct k_spinlock*l){assert(!l->locked);l->locked=true;return 0;}
static void k_spin_unlock(struct k_spinlock*l,int k){(void)k;assert(l->locked);l->locked=false;}
typedef int atomic_t;
static void atomic_set(int*p,int v){*p=v;}
static int atomic_get(int*p){return *p;}
static void atomic_clear(int*p){*p=0;}
static int64_t now=1000;
static int64_t k_uptime_get(void){return now;}
static uint32_t sys_rand32_get(void){static uint32_t nonce=100;return ++nonce;}
struct k_work {int unused;};struct k_work_delayable {bool queued;};
#define K_WORK_DELAYABLE_DEFINE(n,cb) struct k_work_delayable n
static void k_work_reschedule(struct k_work_delayable*w,int t){(void)t;w->queued=true;}
static void k_work_schedule(struct k_work_delayable*w,int t){(void)t;w->queued=true;}
typedef long (*settings_read_cb)(void*,void*,size_t);
static int lism_split_sets_find_addr(const bt_addr_le_t*a){
 for(unsigned i=0;i<6;i++)if(!bt_addr_le_cmp(a,&roles[i])&&bt_addr_le_cmp(a,BT_ADDR_LE_ANY))return i;return -ENOENT;
}
static bool held,manual,connected_a,write_failure,delete_failure,role_failure;
static unsigned recoveries;
static bool lism_input_non_split_identity(const bt_addr_le_t*a){return a->val[0]==99;}
static uint32_t lism_split_sets_generation(unsigned s){return 10+s;}
static void lism_split_sets_observe(const bt_addr_le_t*a,const char*n,int r){(void)a;(void)n;(void)r;recoveries++;}
static bool release_busy,release_allowed=true;
static bool lism_split_sets_auto_begin_release(unsigned s){(void)s;if(!release_allowed)return false;assert(!release_busy);release_busy=true;return true;}
static void lism_split_sets_auto_end_release(unsigned s){(void)s;assert(release_busy);release_busy=false;}
static int lism_split_sets_unbind_addr(unsigned s,const bt_addr_le_t*a){
 assert(!bt_addr_le_cmp(a,&roles[s]));memset(&roles[s],0,sizeof(roles[s]));return 0;
}
static int staged_slot=-1;static bt_addr_le_t staged_address;
static unsigned role_writes,transaction_writes,deletes,resumes;
static uint8_t disk[64],recovery_disk[64];static size_t disk_len,recovery_disk_len;
static char status[80];
static bool lism_split_sets_slot_registered(unsigned s){return bt_addr_le_cmp(&roles[s],BT_ADDR_LE_ANY);}
static int lism_split_sets_auto_claim(unsigned side){
 if(held||manual||connected_a)return -EBUSY;
 unsigned base=(!lism_split_sets_slot_registered(0)||!lism_split_sets_slot_registered(1))?0:4;
 if(lism_split_sets_slot_registered(base+side)|| (side&&!lism_split_sets_slot_registered(base)))return -EBUSY;
 held=true;return base+side;
}
static bool lism_split_sets_auto_hold(void){if(manual)return false;held=true;return true;}
static void lism_split_sets_auto_release(void){held=false;}
static void lism_split_sets_auto_status(const char*s){snprintf(status,sizeof(status),"%s",s);}
static int lism_split_sets_stage_addr(unsigned s,const bt_addr_le_t*a){
 if(lism_split_sets_slot_registered(s)&&bt_addr_le_cmp(&roles[s],a))return -EEXIST;
 staged_slot=s;staged_address=*a;roles[s]=*a;return 0;
}
static int lism_split_sets_commit_addr(unsigned s,const bt_addr_le_t*a){
 if(role_failure)return -EIO;assert((int)s==staged_slot||!bt_addr_le_cmp(&roles[s],a));
 roles[s]=persisted_roles[s]=*a;role_writes++;return 0;
}
static int settings_save_one(const char*k,const void*p,size_t n){
 assert(!strcmp(k,"lism_sets/pending")||!strcmp(k,"lism_sets/recover"));transaction_writes++;
 if(!strcmp(k,"lism_sets/recover")){
  if(write_failure)return -EIO;memcpy(recovery_disk,p,n);recovery_disk_len=n;return 0;
 }
 if(write_failure)return -EIO;memcpy(disk,p,n);disk_len=n;return 0;
}
static int settings_delete(const char*k){assert(!strcmp(k,"lism_sets/pending"));
 deletes++;if(delete_failure)return -EIO;disk_len=0;return 0;
}
static void lism_hid_scan_resume(void){resumes++;}
#define SETTINGS_STATIC_HANDLER_DEFINE(...)
'''
tests=r'''
static void reset_runtime(void){
 memset(candidates,0,sizeof(candidates));pending_valid=staged=committed=loaded=false;
 held=manual=connected_a=write_failure=delete_failure=role_failure=false;
 release_allowed=true;release_busy=false;
 staged_slot=-1;memset(ready_valid,0,sizeof(ready_valid));atomic_clear(&pending_flag);
 atomic_clear(&cancel_requested);recovery_valid=false;status[0]=0;now+=4000;
 memcpy(roles,persisted_roles,sizeof(roles));
}
static void packet(bt_addr_le_t a,int role){
 uint8_t ad[18]={17,BT_DATA_UUID128_ALL};memcpy(ad+2,split_service_uuid.val,16);
 uint8_t response[20]={19,BT_DATA_SVC_DATA128};memcpy(response+2,lism_role_uuid.val,16);
 response[18]=1;response[19]=role;
 struct net_buf_simple first={ad,sizeof(ad)},second={response,sizeof(response)};
 lism_set_observe_ad(&a,-40,BT_GAP_ADV_TYPE_ADV_IND,&first);
 lism_set_observe_ad(&a,-40,BT_GAP_ADV_TYPE_SCAN_RSP,&second);
 assert(first.len==sizeof(ad)&&second.len==sizeof(response)); /* parser preserves buffer */
}
static void stable(bt_addr_le_t a,int role){
 for(unsigned i=0;i<5;i++){packet(a,role);now+=300;}
 automatic_work(NULL);
}
static long read_disk(void*x,void*p,size_t n){(void)x;if(n>disk_len)n=disk_len;memcpy(p,disk,n);return n;}
static long read_recovery(void*x,void*p,size_t n){(void)x;if(n>recovery_disk_len)n=recovery_disk_len;memcpy(p,recovery_disk,n);return n;}
int main(void){
 bt_addr_le_t left={{11}},right={{12}},left2={{21}},right2={{22}},other={{23}};
 auto_settings_commit();stable(left,0);assert(pending_valid&&pending.slot==0&&!role_writes);
 automatic_work(NULL);assert(staged_slot==0&&!role_writes&&disk_len);
 /* Neither bonding nor discovery alone commits a role. Only secure READY. */
 automatic_work(NULL);assert(!role_writes);
 lism_split_auto_ready(1);automatic_work(NULL);assert(!role_writes);
 lism_split_auto_ready(0);role_failure=true;automatic_work(NULL);assert(!role_writes&&pending_valid);
 role_failure=false;delete_failure=true;automatic_work(NULL);
 assert(role_writes==1&&committed&&pending_valid&&disk_len);
 /* Power loss after final role write, before pending cleanup. */
 reset_runtime();assert(!auto_settings_set("pending",disk_len,read_disk,NULL));
 auto_settings_commit();assert(committed);automatic_work(NULL);assert(!pending_valid&&!disk_len);
 stable(right,1);automatic_work(NULL);assert(pending.slot==1);
 lism_split_auto_ready(1);automatic_work(NULL);assert(!pending_valid&&roles[1].val[0]==12);
 connected_a=true;stable(left2,0);assert(!pending_valid);connected_a=false;
 stable(left2,0);automatic_work(NULL);assert(pending.slot==4&&!persisted_roles[4].val[0]);
 /* Power loss after staging, before successful connection: transaction resumes. */
 reset_runtime();assert(!auto_settings_set("pending",disk_len,read_disk,NULL));
 auto_settings_commit();automatic_work(NULL);assert(staged_slot==4&&!persisted_roles[4].val[0]);
 lism_split_auto_ready(4);automatic_work(NULL);assert(!pending_valid&&roles[4].val[0]==21);
 stable(right2,1);automatic_work(NULL);lism_split_auto_ready(5);automatic_work(NULL);
 assert(roles[5].val[0]==22&&roles[0].val[0]==11&&roles[1].val[0]==12);
 unsigned before=role_writes;
 reset_runtime();loaded=true;stable(other,0);assert(!pending_valid&&role_writes==before);
 /* Two simultaneous unknown left roles cannot race/steal a vacant slot. */
 memset(roles,0,sizeof(roles));memset(persisted_roles,0,sizeof(persisted_roles));reset_runtime();loaded=true;
 for(unsigned i=0;i<5;i++){packet(left,0);packet(other,0);now+=300;}
 automatic_work(NULL);assert(!pending_valid&&strstr(status,"Multiple"));
 reset_runtime();loaded=true;packet(left,0);packet(left,1);now+=2000;
 stable(left,0);assert(!pending_valid); /* sticky contradictory metadata */
 reset_runtime();loaded=true;
 for(unsigned i=0;i<5;i++){lism_split_auto_observe(&left,-40,0,false,0);now+=300;}
 automatic_work(NULL);assert(!pending_valid); /* no split advertisement */
 reset_runtime();loaded=true;stable(right,1);assert(!pending_valid); /* left first */
 reset_runtime();loaded=true;manual=true;stable(left,0);assert(!pending_valid);
 manual=false;write_failure=true;stable(left,0);assert(!pending_valid&&!held&&role_writes==before);
 reset_runtime();loaded=true;stable(left,0);automatic_work(NULL);
 lism_split_auto_cancel();automatic_work(NULL);
 assert(!pending_valid&&!held&&!roles[0].val[0]&&recovery_valid);
 unsigned recovered=recoveries;
 lism_split_auto_observe(&left,-40,BT_GAP_ADV_TYPE_ADV_DIRECT_IND,false,-1);
 assert(recoveries==recovered+1); /* bonded failed peer stays manually discoverable */
 reset_runtime();loaded=true;stable(left,0);automatic_work(NULL);now+=45001;
 automatic_work(NULL);assert(!pending_valid&&!held&&recovery_valid);
 reset_runtime();loaded=true;bt_addr_le_t protected={{99}};stable(protected,0);
 assert(!pending_valid&&!held);
 reset_runtime();loaded=true;stable(left,0);automatic_work(NULL);
 lism_split_auto_ready(0);lism_split_auto_ready(2);automatic_work(NULL);
 assert(!pending_valid&&persisted_roles[0].val[0]==11); /* other READY cannot overwrite */
 memset(persisted_roles,0,sizeof(persisted_roles));reset_runtime();loaded=true;
 stable(left,0);automatic_work(NULL);unsigned saved_roles=role_writes;
 lism_split_auto_cancel();release_allowed=false;lism_split_auto_ready(0);automatic_work(NULL);
 assert(pending_valid&&role_writes==saved_roles); /* Cancel cannot fall through to commit */
 release_allowed=true;delete_failure=true;automatic_work(NULL);
 assert(pending_valid&&recovery_disk_len&&disk_len&&!roles[0].val[0]);
 reset_runtime();assert(!auto_settings_set("pending",disk_len,read_disk,NULL));
 assert(!auto_settings_set("recover",recovery_disk_len,read_recovery,NULL));
 auto_settings_commit();assert(cancel_requested);automatic_work(NULL);
 assert(!pending_valid&&!roles[0].val[0]&&role_writes==saved_roles);
 stable(other,0);assert(!pending_valid); /* unresolved recovery never overwritten */
 puts("PASS: actual auto enrollment, first/second set, ambiguity, role conflict, manual priority, storage failures and two power-loss phases");
}
'''
with tempfile.TemporaryDirectory(prefix='lism-auto-test-') as tmp:
    out=Path(tmp)
    app=Path(os.environ.get('LISM_ZMK_APP','/private/tmp/lism-trackpad-build/zmk/app'))
    script=out/'patch.cmake'
    script.write_text(f'include("{root}/cmake/lism_hid_central.cmake")\n'
        f'include("{root}/cmake/lism_split_sets.cmake")\n'
        f'lism_hid_patch_central("{app}/src/split/bluetooth/central.c" "{out}/central.c" "{root}/src/lism_hid_host.h")\n'
        f'lism_split_sets_patch_central("{out}/central.c" "{out}/central.c" "{root}/src/lism_split_sets.h")\n')
    subprocess.run(['cmake','-P',str(script)],check=True)
    central=(out/'central.c').read_text()
    parser=central[central.index('#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)\n#include'):
                   central.index('static void split_central_device_found(')]
    parser=re.sub(r'^#include .*$', '',parser,flags=re.M)
    harness=out/'auto.c';harness.write_text(prelude+source+parser+tests)
    subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',
                    '-fsanitize=address,undefined',str(harness),'-o',str(out/'test')],check=True)
    subprocess.run([str(out/'test')],check=True)
