"""Execute actual generated BLE split lifecycle functions with a saturated queue."""
from pathlib import Path
import os
import subprocess
import tempfile
from lism_standalone_test import COMMON,ROOT,generate

def function(text,signature):
    start=text.index(signature);end=text.index('{',start)+1;depth=1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]

STUB=r'''
#define IS_ENABLED(x) 0
#define ZMK_SPLIT_BLE_PERIPHERAL_COUNT 1
#define POSITION_STATE_DATA_LEN 16
#define LOG_DBG(...) ((void)0)
struct k_spinlock {int unused;};
typedef int k_spinlock_key_t;
static k_spinlock_key_t k_spin_lock(struct k_spinlock *s){(void)s;return 0;}
static void k_spin_unlock(struct k_spinlock *s,int k){(void)s;(void)k;}
static void bt_conn_unref(struct bt_conn *c){(void)c;}
enum {PERIPHERAL_SLOT_STATE_OPEN,PERIPHERAL_SLOT_STATE_CONNECTING,PERIPHERAL_SLOT_STATE_CONNECTED};
struct peripheral_slot {int state;struct bt_conn *conn;uint8_t position_state[16],changed_positions[16];
 struct {int value_handle;} subscribe_params;int run_behavior_handle,selected_physical_layout_handle;};
static struct peripheral_slot peripherals[1];
static struct k_work_delayable peripheral_event_work;
static unsigned scan_calls;
static int start_scanning(void){scan_calls++;return 0;}
static bool running=true,direct=true;
static bool lism_standalone_running(void){return running;}
static bool lism_standalone_direct(void){return direct;}
static unsigned host_down[128],releases;
static int bt_central;
static int raise_zmk_position_state_changed(struct zmk_position_state_changed p){host_down[p.position]=p.state;if(!p.state)releases++;return 0;}
static int zmk_split_transport_central_peripheral_event_handler(int *t,uint8_t s,struct zmk_split_transport_peripheral_event e){
 (void)t;(void)s;if(e.type==1)return raise_zmk_position_state_changed((struct zmk_position_state_changed){.position=e.data.key_position_event.position,.state=e.data.key_position_event.pressed});return 0;}
'''
QUEUE=r'''
static struct {unsigned head,count;struct peripheral_event_wrapper data[3];} peripheral_event_msgq;
static int k_msgq_put(void *q,struct peripheral_event_wrapper *e,int t){(void)q;(void)t;
 if(peripheral_event_msgq.count==3)return -ENOSPC;
 peripheral_event_msgq.data[(peripheral_event_msgq.head+peripheral_event_msgq.count++)%3]=*e;return 0;}
static int k_msgq_get(void *q,struct peripheral_event_wrapper *e,int t){(void)q;(void)t;
 if(!peripheral_event_msgq.count)return -ENOMSG;
 *e=peripheral_event_msgq.data[peripheral_event_msgq.head++%3];peripheral_event_msgq.count--;return 0;}
static unsigned k_msgq_num_used_get(void *q){(void)q;return peripheral_event_msgq.count;}
'''
TEST=r'''
int main(void){
 struct peripheral_event_wrapper e={.source=0,.event={.type=1,.data.key_position_event={.position=31,.pressed=true}}};
 peripherals[0].conn=&connection;peripherals[0].state=PERIPHERAL_SLOT_STATE_CONNECTED;
 assert(!life_enqueue(&e));peripheral_event_work_callback(NULL);assert(host_down[31]);
 uint32_t old=life_generation;
 for(unsigned p=1;p<4;p++){e.event.data.key_position_event.position=p;assert(!life_enqueue(&e));}
 assert(life_enqueue(&e)==-ENOSPC && life_faulted && life_generation!=old);
 unsigned previous=disconnections;peripheral_event_work_callback(NULL);
 assert(!host_down[31] && releases==1 && life_faulted && !peripheral_event_msgq.count);
 peripheral_event_work_callback(NULL);assert(disconnections>=previous+2); /* retries while waiting */
 assert(life_enqueue(&e)==-ESHUTDOWN);
 assert(!release_peripheral_slot(0));peripheral_event_work_callback(NULL);
 assert(!life_cleanup && !life_faulted && !peripherals[0].conn);
 peripherals[0].conn=&connection;peripherals[0].state=PERIPHERAL_SLOT_STATE_CONNECTED;
 e.event.data.key_position_event.position=1;assert(!life_enqueue(&e));peripheral_event_work_callback(NULL);assert(host_down[1]);
 e.generation=old;e.event.data.key_position_event.pressed=false;assert(!k_msgq_put(NULL,&e,0));
 peripheral_event_work_callback(NULL);assert(host_down[1]); /* stale release cannot affect new input */
 assert(!release_peripheral_slot(0));peripheral_event_work_callback(NULL);assert(!host_down[1] && releases==2);
 puts("PASS actual generated standalone worker: queue overflow, synchronous applied-key cleanup, disconnect retry, generation fence and stale-release rejection");
}
'''

def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);generate(Path(os.environ.get('LISM_ZMK_APP',ROOT.parent/'zmk-core/app')),tmp/'gen',ROOT)
        text=(tmp/'gen/central.c').read_text()
        a=text.index('struct peripheral_event_wrapper {');wrapper=text[a:text.index('};',a)+2]
        a=text.index('static struct k_spinlock life_lock;');b=text.index('int peripheral_slot_index_for_conn',a)
        code=COMMON.replace('#pragma once','')+STUB+wrapper+QUEUE+text[a:b]+function(text,'int release_peripheral_slot(int index) {')+function(text,'void peripheral_event_work_callback(struct k_work *work) {')+TEST
        (tmp/'test.c').write_text(code)
        subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable',
            '-Wno-unused-parameter','-fsanitize=address,undefined','-fno-omit-frame-pointer','-g',str(tmp/'test.c'),'-o',str(tmp/'test')],check=True)
        subprocess.run([str(tmp/'test')],check=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
if __name__=='__main__':main()
