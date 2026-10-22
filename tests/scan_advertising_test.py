"""Actual sole scanner + advertising wrapper: address-change serialization."""
from pathlib import Path
import sys,tempfile,subprocess
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'));from prepare_hog_routing import function
s=(P/'src/lism_input_scanner.c').read_text();ble=Path('/work/v51-two-sets/workspace/build-dongle/modules/zmk-config-LisM/lism_ble_zephyr41.c').read_text()
pre=r'''
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <assert.h>
#include <stdio.h>
#define K_FOREVER 0
#define K_MSEC(x) (x)
#define IS_ENABLED(x) (x)
#define CONFIG_LISM_BLE_INPUT_BRIDGE 1
#define LOG_WRN(...) ((void)0)
#define BT_LE_SCAN_TYPE_ACTIVE 1
#define BT_LE_SCAN_OPT_FILTER_DUPLICATE 1
static int scan_lock,depth;static bool running,enabled,bt_ready=true,paused,need=true,pressure;
static unsigned starts,stops,adv_calls,retries,cancels;static int stop_rc,adv_rc;
static void k_mutex_lock(int*p,int w){(void)p;(void)w;assert(!depth);depth++;}
static void k_mutex_unlock(int*p){(void)p;assert(depth==1);depth--;}
struct bt_le_scan_param{unsigned type,options,interval,window;};
static int bt_le_scan_start(const struct bt_le_scan_param*p,void*c){(void)c;assert(depth&&enabled&&p->interval==160&&p->window==8);starts++;return 0;}
static int bt_le_scan_stop(void){assert(depth);stops++;return stop_rc;}
static bool lism_input_host_enabled(void){return enabled;}
static bool bt_is_ready(void){return bt_ready;}
static bool lism_hid_host_scan_paused(void){return paused;}
static bool lism_hid_host_scan_needed(void){return need;}
static bool lism_esb_coex_pressure(void){return pressure;}
static int lism_update_advertising(void){assert(depth==1&&!running);adv_calls++;return adv_rc;}
static int lism_adv_retry_work;
static void k_work_reschedule(int*p,int delay){assert(p==&lism_adv_retry_work&&delay==1000);retries++;}
static void k_work_cancel_delayable(int*p){assert(p==&lism_adv_retry_work);cancels++;}
'''
code=pre+'\n'.join(function(s,n) for n in ['pause_locked','lism_hid_scan_pause','lism_hid_advertising_begin','lism_hid_advertising_end','lism_hid_scan_resume'])+function(ble,'update_advertising')+r'''
int main(void){
 lism_hid_scan_resume();assert(!starts&&!running);enabled=true;lism_hid_scan_resume();assert(starts==1&&running);
 assert(!update_advertising()&&stops==1&&!running&&adv_calls==1&&cancels==1&&!depth);
 /* Asynchronous initiator owns address state after scan_stop, until callback. */
 paused=true;assert(update_advertising()==-EAGAIN&&!depth&&adv_calls==1&&retries==1);
 paused=false;assert(!update_advertising()&&adv_calls==2);
 lism_hid_scan_resume();assert(running);stop_rc=-EIO;assert(update_advertising()==-EIO&&adv_calls==2&&retries==2&&!depth);
 stop_rc=0;adv_rc=-EACCES;assert(update_advertising()==-EACCES&&retries==3&&!running&&!depth);
 enabled=false;lism_hid_scan_resume();assert(!running);adv_rc=0;assert(!update_advertising());
 puts("PASS actual scanner/advertising wrapper: OFF no scan, pause-before-address setup, initiating exclusion, stop/start errors retry, mutex release");
}
'''
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'scan.c';o=Path(t)/'scan';f.write_text(code);subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True);subprocess.run([str(o)],check=True)
