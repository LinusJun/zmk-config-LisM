"""Pinned Zephyr identity selector; model controller address checks, not RF."""
from pathlib import Path
import sys,os,tempfile,subprocess,hashlib
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
Z=Path(os.environ.get('LISM_ZEPHYR_BASE','/sdk/zephyr'))
src=(Z/'subsys/bluetooth/host/id.c').read_text()
build=(P/'tools/build_upstream_esb.py').read_text()
assert "if xiao:options+=['-DCONFIG_BT_SCAN_WITH_IDENTITY=y'" in build
pre=r'''
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <assert.h>
#include <stdio.h>
#include <stddef.h>
#define IS_ENABLED(x) (x)
#define CHECKIF(x) if(x)
#define CONFIG_BT_PRIVACY 0
#define CONFIG_BT_BROADCASTER 1
#define CONFIG_BT_EXT_ADV 0
#define CONFIG_BT_SCAN_WITH_IDENTITY scan_identity
#define BT_DEV_FEAT_LE_EXT_ADV(x) false
#define BT_FEAT_LE_PRIVACY(x) false
#define BT_ID_DEFAULT 0
#define BT_ADDR_LE_PUBLIC 0
#define BT_ADDR_LE_RANDOM 1
#define BT_HCI_OWN_ADDR_PUBLIC 0
#define BT_HCI_OWN_ADDR_RANDOM 1
#define BT_HCI_OWN_ADDR_RPA_OR_RANDOM 3
#define BT_DEV_SCANNING 0
#define BT_DEV_INITIATING 1
#define BT_ADV_ENABLED 0
#define LOG_WRN(...) ((void)0)
typedef uint8_t bt_addr_t;
struct bt_le_ext_adv {unsigned flags;};static struct bt_le_ext_adv adv;
static struct {struct {int features;}le;struct {uint8_t type;bt_addr_t a;}id_addr[1];unsigned flags;}bt_dev;
static bool scan_identity,private_fail;
static bt_addr_t controller_random;
static unsigned random_writes,private_calls;
static bool atomic_test_bit(unsigned flags,unsigned bit){return flags&(1U<<bit);}
static struct bt_le_ext_adv *bt_le_adv_lookup_legacy(void){return &adv;}
/* Models the real set_random_address same-address fast path and controller
 * prohibition on changing the shared legacy address while radio roles run. */
static int set_random_address(const bt_addr_t *addr){
 if(*addr==controller_random)return 0;
 if(adv.flags||bt_dev.flags)return -EACCES;
 controller_random=*addr;random_writes++;return 0;
}
static int bt_id_set_private_addr(unsigned id){assert(id==0);private_calls++;if(private_fail)return -EIO;bt_addr_t nrpa=42;return set_random_address(&nrpa);}
'''
code=pre+function(src,'is_adv_using_rand_addr')+function(src,'bt_id_set_scan_own_addr')+r'''
static int start(void){
 uint8_t own_type=0; /* actual start_le_scan_legacy zero-initializes set_param */
 int rc=bt_id_set_scan_own_addr(true,&own_type);if(rc)return rc;
 /* nRF52 has no public address: SDC LE Set Scan Enable returns 0x12 when
  * Own_Address_Type is PUBLIC; Zephyr maps it to -EINVAL, as captured. */
 if(own_type!=BT_HCI_OWN_ADDR_RANDOM || !controller_random)return -EINVAL;
 bt_dev.flags|=1U<<BT_DEV_SCANNING;return 0;
}
static void stop(void){bt_dev.flags=0;}
int main(void){
 bt_dev.id_addr[0].type=BT_ADDR_LE_RANDOM;bt_dev.id_addr[0].a=99;
 adv.flags=1;controller_random=99;scan_identity=false;
 assert(start()==-EINVAL && !private_calls); /* original failure control */
 scan_identity=true;assert(!start() && !random_writes);stop();
 assert(!start() && !random_writes);stop(); /* same enabled advertiser */
 adv.flags=0;assert(!start());stop(); /* host connected, advertising stopped */
 for(unsigned profile=0;profile<5;profile++){
  adv.flags=1;assert(!start());stop();assert(controller_random==99);
 }
 adv.flags=0;controller_random=42;assert(!start() && random_writes==1);stop();
 /* Connection initiation shares the already restored default identity. */
 assert(!set_random_address(&bt_dev.id_addr[0].a));bt_dev.flags=1U<<BT_DEV_INITIATING;
 assert(!set_random_address(&bt_dev.id_addr[0].a));stop();
 /* Existing private-address route and failures are left untouched. */
 scan_identity=false;assert(!start() && private_calls==1);stop();
 private_fail=true;assert(start()==-EIO);assert(bt_id_set_scan_own_addr(true,NULL)==-EINVAL);
 puts("PASS actual Zephyr identity selector: original advertising+scan failure reproduced; identity scan handles active/inactive advertising, restart, five modeled profile transitions, initiator address reuse and errors");
}
'''
with tempfile.TemporaryDirectory() as d:
 f=Path(d)/'identity.c';o=Path(d)/'identity';f.write_text(code)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True)
 subprocess.run([str(o)],check=True)
print('Pinned id.c SHA256',hashlib.sha256(src.encode()).hexdigest())
