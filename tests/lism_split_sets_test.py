"""Compile actual set policy and pinned BLE/central patches; no copied policy."""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
app = Path(os.environ.get("LISM_ZMK_APP", "/private/tmp/lism-trackpad-build/zmk/app"))
header = re.sub(r"^#include <zephyr/.*$", "", (root / "src/lism_split_sets.h").read_text(), flags=re.M)
header = header.replace("#pragma once", "")
source = re.sub(r"^#include .*?$", "", (root / "src/lism_split_sets.c").read_text(), flags=re.M)
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define MIN(a,b) ((a)<(b)?(a):(b))
#define MAX(a,b) ((a)>(b)?(a):(b))
typedef struct {uint8_t bytes[7];} bt_addr_le_t;
static bt_addr_le_t any;
#define BT_ADDR_LE_ANY (&any)
static int bt_addr_le_cmp(const bt_addr_le_t *a,const bt_addr_le_t *b){return memcmp(a,b,sizeof(*a));}
static void bt_addr_le_copy(bt_addr_le_t *a,const bt_addr_le_t *b){*a=*b;}
struct k_spinlock {int locked;};
typedef int k_spinlock_key_t;
static int k_spin_lock(struct k_spinlock *l){assert(!l->locked);l->locked=1;return 0;}
static void k_spin_unlock(struct k_spinlock *l,int k){(void)k;assert(l->locked);l->locked=0;}
static int64_t now=100;
static int64_t k_uptime_get(void){return now;}
struct bt_bond_info {int unused;};
#define BT_ID_DEFAULT 0
#define CONFIG_BT_MAX_PAIRED 12
static unsigned bond_count;
static void bt_foreach_bond(int id,void (*cb)(const struct bt_bond_info *,void *),void *u){
 (void)id;struct bt_bond_info info={0};for(unsigned i=0;i<bond_count;i++)cb(&info,u);
}
'''
addresses = r'''
static bt_addr_le_t addresses[6];
int lism_split_sets_find_addr(const bt_addr_le_t *a){
 for(int i=0;i<6;i++)if(!bt_addr_le_cmp(&addresses[i],a))return i;return -ENOENT;
}
bool lism_split_sets_slot_registered(uint8_t s){return s<6&&bt_addr_le_cmp(&addresses[s],BT_ADDR_LE_ANY);}
int lism_split_sets_bind_addr(uint8_t s,const bt_addr_le_t *a){
 if(lism_split_sets_find_addr(a)>=0||lism_split_sets_slot_registered(s))return -EEXIST;
 addresses[s]=*a;return 0;
}
int lism_split_sets_unbind_addr(uint8_t s,const bt_addr_le_t *a){
 if(bt_addr_le_cmp(&addresses[s],a))return -ESTALE;memset(&addresses[s],0,sizeof(addresses[s]));return 0;
}
static unsigned host_down[128], cleanup_releases;
static void send_key(unsigned slot,unsigned epoch,unsigned position,bool pressed){
 if(lism_split_sets_dispatch(slot,epoch)){
  lism_split_sets_applied(slot,position,pressed);host_down[position]=pressed;
 }
}
static void drain_cleanup(void){
 for(unsigned s=0;s<6;s++){
  uint8_t bits[16];if(!lism_split_sets_take_cleanup(s,bits))continue;
  assert(lism_split_sets_reserve(4)==-EBUSY);
  for(unsigned p=0;p<128;p++)if(bits[p/8]&(1U<<(p%8))){host_down[p]=0;cleanup_releases++;}
  lism_split_sets_cleanup_done(s);
 }
}
int main(void){
 assert(lism_split_sets_active_set()==-1);
 assert(!lism_split_sets_reserve(0));lism_split_sets_connected(0);
 unsigned old=lism_split_sets_generation(0);send_key(0,old,15,true);assert(host_down[15]);
 assert(!lism_split_sets_reserve(1));lism_split_sets_connected(1);
 assert(lism_split_sets_reserve(4)==-EBUSY); /* A right prevents B mixing */
 lism_split_sets_released(0);assert(!lism_split_sets_dispatch(0,old));
 send_key(0,old,16,true);assert(!host_down[16]);
 drain_cleanup();assert(!host_down[15]&&cleanup_releases==1);
 assert(lism_split_sets_active_set()==0&&lism_split_sets_reserve(4)==-EBUSY);
 lism_split_sets_released(1);assert(lism_split_sets_reserve(4)==-EBUSY);
 drain_cleanup();assert(lism_split_sets_active_set()==-1);
 assert(!lism_split_sets_reserve(4));lism_split_sets_connected(4);
 unsigned fresh=lism_split_sets_generation(4);send_key(4,fresh,15,true);
 send_key(0,old,15,false);assert(host_down[15]); /* old A release cannot release B */
 assert(xiao147_active_keyboard_slot(0)==4&&xiao147_active_keyboard_slot(1)==5);
 lism_split_sets_released(4);drain_cleanup();assert(!host_down[15]);
 assert(!lism_split_sets_reserve(0)); /* failed connection rolls back only after fence */
 lism_split_sets_released(0);assert(lism_split_sets_reserve(4)==-EBUSY);drain_cleanup();
 assert(lism_split_sets_active_set()==-1);
 bt_addr_le_t b={{7}},c={{8}},a={{9}};
 addresses[0]=a;addresses[4]=b;
 assert(lism_split_sets_find_addr(&b)==4&&!lism_split_sets_slot_registered(2));
 assert(lism_split_sets_register_begin(4)==-EEXIST);
 assert(!lism_split_sets_register_begin(5));
 lism_split_sets_observe(&b,"known left",-50); /* never candidate */
 lism_split_sets_observe(&c,"right",-60);
 struct lism_split_set_candidate list[8];assert(lism_split_sets_candidates(list,8)==1);
 assert(!strcmp(list[0].name,"right"));unsigned token=list[0].token;
 now+=15001;assert(lism_split_sets_register_select(token)==-ESTALE);
 lism_split_sets_observe(&c,"right",-55);assert(lism_split_sets_candidates(list,8)==1);
 bond_count=12;assert(lism_split_sets_register_select(list[0].token)==-ENOSPC);
 assert(!lism_split_sets_slot_registered(5));bond_count=5;
 assert(!lism_split_sets_register_select(list[0].token));assert(lism_split_sets_find_addr(&c)==5);
 assert(!lism_split_sets_slot_registered(2)&&!lism_split_sets_slot_registered(3));
 assert(!bt_addr_le_cmp(&addresses[0],&a)); /* legacy remains */
 lism_split_sets_register_cancel();assert(!lism_split_sets_slot_registered(5));
 assert(!lism_split_sets_register_begin(5));lism_split_sets_observe(&c,"right",-55);
 assert(lism_split_sets_candidates(list,8)==1);assert(!lism_split_sets_register_select(list[0].token));
 assert(!lism_split_sets_reserve(5));lism_split_sets_connected(5); /* BLE connected but auth/discovery fails */
 lism_split_sets_released(5);drain_cleanup();
 assert(!lism_split_sets_register_begin(3)); /* selected wrong role can be corrected before connecting */
 assert(!lism_split_sets_slot_registered(5));lism_split_sets_observe(&c,"stick",-55);
 assert(lism_split_sets_candidates(list,8)==1);assert(!lism_split_sets_register_select(list[0].token));
 assert(!lism_split_sets_reserve(3));lism_split_sets_connected(3);lism_split_sets_ready(3);
 lism_split_sets_register_cancel();assert(lism_split_sets_slot_registered(3)); /* connected record committed */
 assert(lism_split_sets_register_begin(4)==-EEXIST);assert(!bt_addr_le_cmp(&addresses[4],&b));
 puts("split sets: enrollment, capacity, role gate and asynchronous release fence passed");
}
'''
with tempfile.TemporaryDirectory(prefix="lism-split-sets-") as tmp:
    temp = Path(tmp)
    # Generate actual production patch artifacts and fail on anchor drift.
    script = temp / "patch.cmake"
    script.write_text(f'''include("{root}/cmake/lism_hid_central.cmake")
include("{root}/cmake/lism_split_sets.cmake")
lism_hid_patch_ble("{app}/src/ble.c" "{temp}/ble1.c")
lism_split_sets_patch_ble("{temp}/ble1.c" "{temp}/ble2.c" "{root}/src/lism_split_sets.h")
lism_hid_patch_central("{app}/src/split/bluetooth/central.c" "{temp}/central1.c" "{root}/src/lism_hid_host.h")
lism_split_sets_patch_central("{temp}/central1.c" "{temp}/central2.c" "{root}/src/lism_split_sets.h")
lism_split_sets_patch_input_split("{app}/src/pointing/input_split.c" "{temp}/input_split.c")
lism_split_sets_patch_input_core("{os.environ.get('LISM_ZEPHYR_BASE', '/private/tmp/stress-zmk-upstream/zephyr')}/subsys/input/input.c" "{temp}/input_core.c")
''')
    subprocess.run(["cmake", "-P", str(script)], check=True)
    central = (temp / "central2.c").read_text()
    assert central.count("k_msgq_put(&peripheral_event_msgq") == 1
    assert central.count("lism_split_queue_event(&") == 6
    assert "lism_split_sets_dispatch(ev.source, ev.generation)" in central
    assert "lism_split_sets_cleanup_done(source)" in central
    assert central.index("lism_split_sets_released(index)") < central.index("bt_conn_unref(slot->conn)")
    assert "release_peripheral_slot(i);\n            peripherals" not in central
    roles = re.sub(r"^#include.*$", "", (root / "src/xiao147_peripheral_roles.h").read_text(), flags=re.M)
    roles = roles.replace("#pragma once", "")
    code = temp / "policy.c"
    code.write_text(prelude + header + source + "\n#define CONFIG_LISM_TWO_KEYBOARD_SETS 1\n" + roles + addresses)
    exe = temp / "policy"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-misleading-indentation", "-fsanitize=address,undefined", str(code), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
    # Execute actual automatic/manual arbitration and release fences too, not
    # just the automatic worker's fake slot allocator.
    auto_stubs = r'''
static unsigned auto_ready_calls;
void lism_split_auto_ready(uint8_t slot){(void)slot;auto_ready_calls++;}
bool lism_split_auto_pending(void){return false;}
void lism_split_auto_cancel(void){}
'''
    auto_main = r'''
int main(void){
 bt_addr_le_t a={{11}},b={{12}},c={{21}},d={{22}};
 assert(lism_split_sets_auto_claim(1)==-EBUSY);
 assert(lism_split_sets_auto_claim(0)==0);
 assert(lism_split_sets_register_begin(4)==-EBUSY);lism_split_sets_auto_release();
 addresses[0]=a;
 assert(!lism_split_sets_reserve(0));lism_split_sets_connected(0);
 bool online[6];lism_split_sets_connected_snapshot(online);assert(online[0]&&!online[5]);
 lism_split_sets_ready(0);assert(auto_ready_calls==1);
 assert(lism_split_sets_auto_claim(1)==1);lism_split_sets_auto_release();addresses[1]=b;
 assert(lism_split_sets_auto_claim(0)==-EBUSY); /* A must fully shut down first */
 lism_split_sets_released(0);drain_cleanup();
 assert(lism_split_sets_auto_claim(0)==4);lism_split_sets_auto_release();
 assert(lism_split_sets_auto_begin_release(4));
 assert(lism_split_sets_reserve(4)==-EBUSY); /* cannot connect during settings I/O */
 assert(!lism_split_sets_auto_begin_release(4));
 lism_split_sets_auto_end_release(4);assert(!lism_split_sets_reserve(4));
 assert(!lism_split_sets_auto_begin_release(4));
 lism_split_sets_released(4);drain_cleanup();
 assert(!lism_split_sets_register_begin(4));assert(lism_split_sets_auto_claim(0)==-EBUSY);
 lism_split_sets_register_cancel();addresses[4]=c;addresses[5]=d;
 assert(!lism_split_sets_reserve(5));lism_split_sets_connected(5);
 lism_split_sets_connected_snapshot(online);assert(online[5]&&!online[1]);
 assert(lism_split_sets_logical_slot(1)==5);
 puts("PASS: actual auto/manual exclusion, A/B selection, release fence and physical5 connection snapshot");
}
'''
    auto_c = temp / "policy-auto.c"
    auto_c.write_text(prelude + header + auto_stubs + source +
                     addresses.split("int main(void){", 1)[0] + auto_main)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-misleading-indentation",
                    "-Wno-unused-function", "-DCONFIG_LISM_AUTO_SPLIT_SETS=1",
                    "-fsanitize=address,undefined", str(auto_c), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
    # Execute the actual generated release routine, queue helper and dequeue
    # worker. In particular cleanup must not depend on room in the msgq.
    def function(text, signature):
        start = text.index(signature)
        brace = text.index("{", start)
        depth = 1
        end = brace + 1
        while depth:
            depth += (text[end] == "{") - (text[end] == "}")
            end += 1
        return text[start:end]
    wrapper_start = central.index("struct peripheral_event_wrapper {")
    wrapper = central[wrapper_start:central.index("};", wrapper_start) + 2]
    worker = function(central, "void peripheral_event_work_callback(struct k_work *work) {")
    release = function(central, "int release_peripheral_slot(int index) {")
    enqueue = function(central, "static int lism_split_queue_event(struct peripheral_event_wrapper *event) {")
    disconnect_state = central[central.index("static struct bt_conn *lism_pending_disconnect["):central.index("void lism_split_sets_input_fence_done(")]
    transport = r'''
#define ZMK_SPLIT_BLE_PERIPHERAL_COUNT 6
#define POSITION_STATE_DATA_LEN 16
#define ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT 1
#define ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT 2
#define LOG_DBG(...) (void)0
#define BIT(n) (1U<<(n))
#define IS_ENABLED(x) 0
#define K_NO_WAIT 0
#define K_MSEC(n) (n)
struct k_work {int unused;};
static struct k_work peripheral_event_work;
static void k_work_reschedule(struct k_work *w,int delay){(void)w;(void)delay;}
#define LOG_WRN(...) (void)0
#define BT_HCI_ERR_REMOTE_USER_TERM_CONN 19
static unsigned disconnect_requests;
static int disconnect_result;
static int bt_conn_disconnect(void *conn,int reason){(void)conn;(void)reason;disconnect_requests++;return disconnect_result;}
static void *bt_conn_ref(void *conn){return conn;}
struct zmk_split_transport_peripheral_event {
 int type;
 union {struct {uint16_t position;bool pressed;} key_position_event;
        struct {uint8_t level;} battery_event;} data;
};
static unsigned host_down[128], actual_releases;
static int bt_central;
static int zmk_split_transport_central_peripheral_event_handler(int *c,uint8_t source,struct zmk_split_transport_peripheral_event ev){
 (void)c;(void)source;
 if(ev.type==1){host_down[ev.data.key_position_event.position]=ev.data.key_position_event.pressed;
 if(!ev.data.key_position_event.pressed)actual_releases++;}return 0;
}
enum {PERIPHERAL_SLOT_STATE_OPEN,PERIPHERAL_SLOT_STATE_CONNECTING,PERIPHERAL_SLOT_STATE_CONNECTED};
struct peripheral_slot {int state;void *conn;uint8_t position_state[16],changed_positions[16];
 struct {int value_handle;} subscribe_params;int run_behavior_handle,selected_physical_layout_handle;};
static struct peripheral_slot peripherals[6];
static void bt_conn_unref(void *p){(void)p;}
static int start_scanning(void){return 0;}
'''
    queue = r'''
static struct peripheral_event_wrapper queue[2];
static unsigned queue_count;
static int peripheral_event_msgq;
static int k_msgq_put(int *q,struct peripheral_event_wrapper *ev,int timeout){
 (void)q;(void)timeout;if(queue_count==2)return -ENOSPC;queue[queue_count++]=*ev;return 0;
}
static int k_msgq_get(int *q,struct peripheral_event_wrapper *ev,int timeout){
 (void)q;(void)timeout;if(!queue_count)return -EAGAIN;*ev=queue[0];queue[0]=queue[--queue_count];return 0;
}
'''
    worker_test = r'''
int lism_split_sets_find_addr(const bt_addr_le_t *a){(void)a;return -ENOENT;}
bool lism_split_sets_slot_registered(uint8_t s){(void)s;return false;}
int lism_split_sets_bind_addr(uint8_t s,const bt_addr_le_t *a){(void)s;(void)a;return 0;}
int lism_split_sets_unbind_addr(uint8_t s,const bt_addr_le_t *a){(void)s;(void)a;return 0;}
static void put_key(uint8_t source,uint16_t pos,bool down){
 struct peripheral_event_wrapper ev={.source=source,.event={.type=1,.data.key_position_event={pos,down}}};
 assert(!lism_split_queue_event(&ev));
}
int main(void){
 assert(!lism_split_sets_reserve(0));lism_split_sets_connected(0);
 peripherals[0].state=PERIPHERAL_SLOT_STATE_CONNECTED;
 put_key(0,22,true);peripheral_event_work_callback(NULL);assert(host_down[22]);
 /* Saturate queue with old A events, then disconnect: original cleanup could
  * lose its release here. New fence must drop old press and release applied22. */
 put_key(0,23,true);put_key(0,24,true);assert(queue_count==2);
 struct peripheral_event_wrapper stale=queue[0];
 assert(!release_peripheral_slot(0));assert(lism_split_sets_reserve(4)==-EBUSY);
 peripheral_event_work_callback(NULL);
 assert(!host_down[22]&&!host_down[23]&&!host_down[24]&&actual_releases==1);
 assert(!lism_split_sets_reserve(4));lism_split_sets_connected(4);
 put_key(4,23,true);peripheral_event_work_callback(NULL);assert(host_down[23]);
 stale.event.data.key_position_event.pressed=false;
 assert(!k_msgq_put(&peripheral_event_msgq,&stale,0));
 peripheral_event_work_callback(NULL);assert(host_down[23]&&actual_releases==1);
 /* Queue full during an otherwise live connection loses key-up: immediately
  * fault this source, release held state and force disconnect before reuse. */
 peripherals[4].state=PERIPHERAL_SLOT_STATE_CONNECTED;peripherals[4].conn=(void*)1;
 disconnect_result=-EBUSY;
 put_key(4,25,true);put_key(4,26,true);
 struct peripheral_event_wrapper up={.source=4,.event={.type=1,.data.key_position_event={23,false}}};
 assert(lism_split_queue_event(&up)==-ENOSPC);
 peripheral_event_work_callback(NULL);
 assert(!host_down[23]&&!host_down[25]&&!host_down[26]);
 assert(disconnect_requests==1&&lism_split_sets_reserve(0)==-EBUSY);
 peripheral_event_work_callback(NULL);assert(disconnect_requests==1); /* no tight retry */
 now+=99;peripheral_event_work_callback(NULL);assert(disconnect_requests==1);
 now++;peripheral_event_work_callback(NULL);assert(disconnect_requests==2);
 disconnect_result=0;now+=199;peripheral_event_work_callback(NULL);assert(disconnect_requests==2);
 now++;peripheral_event_work_callback(NULL);assert(disconnect_requests==3);
 assert(lism_split_sets_reserve(0)==-EBUSY); /* successful request is not disconnect ACK */
 assert(!release_peripheral_slot(4));peripheral_event_work_callback(NULL);
 assert(!lism_split_sets_reserve(0));assert(!lism_pending_disconnect[4]);
 /* A stale retry token must never disconnect a replacement connection. */
 lism_pending_disconnect[0]=(void*)42;lism_disconnect_generation[0]=lism_split_sets_generation(0);
 peripherals[0].conn=(void*)43;lism_disconnect_deadline[0]=0;
 peripheral_event_work_callback(NULL);assert(disconnect_requests==3&&!lism_pending_disconnect[0]);
 puts("actual generated central worker: full queue cleanup and stale A release isolation passed");
}
'''
    code.write_text(prelude + header + source + transport + wrapper + queue + disconnect_state + enqueue + release + worker + worker_test)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Wno-unused-parameter", "-fsanitize=address,undefined", str(code), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
    pending_func = function(central, "static bool input_slot_is_pending(size_t i) {")
    readiness = r'''
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
struct pending_proxy {void *conn;struct {unsigned value_handle,ccc_handle;} sub;uint8_t reg;bool lism_reg_ready;};
static struct pending_proxy peripheral_input_slots[2];
'''
    readiness_main = r'''
int main(void){peripheral_input_slots[0].conn=(void*)1;
 peripheral_input_slots[0].sub.value_handle=1;peripheral_input_slots[0].sub.ccc_handle=2;
 assert(input_slot_is_pending(0));peripheral_input_slots[0].lism_reg_ready=true;
 assert(peripheral_input_slots[0].reg==0&&!input_slot_is_pending(0));
 assert(!input_slot_is_pending(1));puts("actual reg0 readiness: explicit descriptor flag passed");}
'''
    code.write_text(prelude + readiness + pending_func + readiness_main)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-unused-variable", "-fsanitize=address,undefined", str(code), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
    # Threaded INPUT_EV_KEY uses another queue. Exercise the actual proxy
    # cleanup and input-core marker with the actual generated central worker.
    proxy = (temp / "input_split.c").read_text()
    input_core = (temp / "input_core.c").read_text()
    marker_start = input_core.index("static const struct device lism_split_fence_device;")
    marker = input_core[marker_start:input_core.index("static void input_process", marker_start)]
    input_state_start = central.index("#if IS_ENABLED(CONFIG_ZMK_INPUT_SPLIT)\nstatic bool lism_input_cleanup_regs")
    input_state = central[input_state_start:central.index("static int start_scanning(void);", input_state_start)]
    fence_ack = function(central, "void lism_split_sets_input_fence_done(uint8_t source, uint32_t token) {")
    proxy_functions = "\n".join(function(proxy, sig) for sig in (
        "int zmk_input_split_report_peripheral_event(uint8_t reg, uint8_t type, uint16_t code,",
        "int zmk_input_split_peripheral_disconnected(uint8_t reg) {"))
    threaded_transport = transport.replace("#define IS_ENABLED(x) 0", """#define IS_ENABLED(x) x
#define CONFIG_ZMK_INPUT_SPLIT 1
#define CONFIG_ZMK_SPLIT_PERIPHERAL_HID_INDICATORS 0
#define CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING 0
#define CONFIG_INPUT_MODE_THREAD 1
#define CONFIG_ZMK_INPUT_SPLIT_MAX_TRACKED_KEYS 4
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define INPUT_EV_KEY 1
typedef int atomic_t;
static void atomic_set(atomic_t *a,uint32_t n){*a=(int)n;}
static int atomic_get(atomic_t *a){return *a;}
struct device {int id;};
struct input_event {const struct device *dev;bool sync;uint8_t type;uint16_t code;int32_t value;};
static int peripheral_slot_index_for_conn(struct bt_conn *conn);
""")
    # Match production transport signature exactly enough for its pointer type.
    threaded_transport = threaded_transport.replace("static int peripheral_slot_index_for_conn(struct bt_conn *conn);", "struct bt_conn;")
    old_handler = function(threaded_transport, "static int zmk_split_transport_central_peripheral_event_handler(")
    threaded_transport = threaded_transport.replace(old_handler, "static int zmk_split_transport_central_peripheral_event_handler(int *,uint8_t,struct zmk_split_transport_peripheral_event);")
    threaded_transport = threaded_transport.replace("struct {uint8_t level;} battery_event;", "struct {uint8_t level;} battery_event; struct {uint8_t reg,type;uint16_t code;int32_t value;bool sync;} input_event;")
    threaded_transport += r'''
#define ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT 3
static unsigned detaches;
static void release_peripheral_input_subs(void *conn){(void)conn;detaches++;}
static const struct device proxy_dev={1};
static const struct {uint8_t reg;const struct device *dev;} proxy_inputs[]={{0,&proxy_dev}};
static uint16_t proxy_active_keys[1][CONFIG_ZMK_INPUT_SPLIT_MAX_TRACKED_KEYS];
static int input_msgq;
static struct input_event input_queue[2];static unsigned input_count;
static bool host_button[1024];
'''
    threaded_queue = queue.replace("struct peripheral_event_wrapper *ev,int timeout", "void *ev,int timeout")
    threaded_queue = threaded_queue.replace("(void)q;(void)timeout;if(queue_count==2)", "(void)timeout;if(q==&input_msgq){if(input_count==2)return -ENOSPC;input_queue[input_count++]=*(struct input_event*)ev;return 0;}if(queue_count==2)")
    threaded_queue = threaded_queue.replace("queue[queue_count++]=*ev", "queue[queue_count++]=*(struct peripheral_event_wrapper*)ev")
    threaded_queue = threaded_queue.replace("*ev=queue[0]", "*(struct peripheral_event_wrapper*)ev=queue[0]")
    input_report = r'''
static int input_report(const struct device *dev,uint8_t type,uint16_t code,int32_t value,bool sync,int timeout){
 struct input_event evt={dev,sync,type,code,value};return k_msgq_put(&input_msgq,&evt,timeout);
}
static void drain_input(void){
 while(input_count){struct input_event ev=input_queue[0];memmove(input_queue,input_queue+1,--input_count*sizeof(*input_queue));
 if(ev.dev==&lism_split_fence_device){lism_split_sets_input_fence_done(ev.code,(uint32_t)ev.value);continue;}
 if(ev.type==INPUT_EV_KEY){assert(ev.code<1024);host_button[ev.code]=ev.value!=0;}}
}
'''
    threaded_handler = r'''
static int zmk_split_transport_central_peripheral_event_handler(int *c,uint8_t s,struct zmk_split_transport_peripheral_event ev){
 (void)c;(void)s;if(ev.type==3)return zmk_input_split_report_peripheral_event(ev.data.input_event.reg,ev.data.input_event.type,ev.data.input_event.code,ev.data.input_event.value,ev.data.input_event.sync);
 if(ev.type==1){host_down[ev.data.key_position_event.position]=ev.data.key_position_event.pressed;if(!ev.data.key_position_event.pressed)actual_releases++;}return 0;
}
'''
    threaded_test = r'''
int lism_split_sets_find_addr(const bt_addr_le_t *a){(void)a;return -ENOENT;}
bool lism_split_sets_slot_registered(uint8_t s){(void)s;return false;}
int lism_split_sets_bind_addr(uint8_t s,const bt_addr_le_t *a){(void)s;(void)a;return 0;}
int lism_split_sets_unbind_addr(uint8_t s,const bt_addr_le_t *a){(void)s;(void)a;return 0;}
static void put_button(uint8_t source,uint16_t code,bool pressed){
 struct peripheral_event_wrapper ev={.source=source,.event={.type=3,.data.input_event={0,INPUT_EV_KEY,code,pressed,true}}};assert(!lism_split_queue_event(&ev));
}
int main(void){
 assert(!lism_split_sets_reserve(0));lism_split_sets_connected(0);
 peripherals[0].state=PERIPHERAL_SLOT_STATE_CONNECTED;peripherals[0].conn=(void*)1;
 put_button(0,272,true);put_button(0,273,true);peripheral_event_work_callback(NULL);
 assert(input_count==2);assert(!release_peripheral_slot(0));
 peripheral_event_work_callback(NULL); /* cleanup fails against full input queue */
 unsigned tracked=0;for(unsigned i=0;i<4;i++)tracked+=proxy_active_keys[0][i]!=0;
 assert(tracked==2);assert(lism_split_sets_reserve(4)==-EBUSY);
 drain_input();assert(host_button[272]&&host_button[273]);
 peripheral_event_work_callback(NULL); /* two releases queued, marker still full */
 assert(input_count==2&&lism_split_sets_reserve(4)==-EBUSY);drain_input();
 assert(!host_button[272]&&!host_button[273]);
 peripheral_event_work_callback(NULL); /* marker queued but NOT acknowledged */
 assert(input_count==1&&lism_split_sets_reserve(4)==-EBUSY);
 drain_input();peripheral_event_work_callback(NULL);assert(!lism_split_sets_reserve(4));
 lism_split_sets_connected(4);peripherals[4].state=PERIPHERAL_SLOT_STATE_CONNECTED;peripherals[4].conn=(void*)2;
 put_button(4,272,true);put_button(4,273,true);peripheral_event_work_callback(NULL);
 put_button(4,272,false);peripheral_event_work_callback(NULL); /* secondary queue overflow => fault */
 assert(disconnect_requests==1&&!lism_split_sets_dispatch(4,lism_split_sets_generation(4)));
 drain_input();peripheral_event_work_callback(NULL);drain_input();peripheral_event_work_callback(NULL);
 drain_input();peripheral_event_work_callback(NULL);
 assert(!host_button[272]&&!host_button[273]);
 assert(lism_split_sets_reserve(0)==-EBUSY); /* real disconnect still outstanding */
 assert(!release_peripheral_slot(4));peripheral_event_work_callback(NULL);drain_input();peripheral_event_work_callback(NULL);
 assert(!lism_split_sets_reserve(0));
 puts("actual threaded input path: release retry, marker ACK fence and live input overflow recovery passed");
}
'''
    code.write_text(prelude + header + source + threaded_transport + input_state + wrapper + threaded_queue + marker + input_report + proxy_functions + threaded_handler + disconnect_state + enqueue + release + fence_ack + worker + threaded_test)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Wno-unused-parameter", "-Wno-unused-variable", "-fsanitize=address,undefined", str(code), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
    # Compile the actual generated known-first allocator, not a copied version.
    ble = (temp / "ble2.c").read_text()
    allocator_start = ble.index("static struct k_spinlock lism_split_address_lock;")
    allocator = ble[allocator_start:ble.index("#endif /* IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL) */", allocator_start)]
    alloc_prelude = r'''
#define ZMK_SPLIT_BLE_PERIPHERAL_COUNT 6
#define K_MUTEX_DEFINE(n) int n
#define K_FOREVER 0
#define IS_ENABLED(x) 0
static void k_mutex_lock(int *m,int t){(void)m;(void)t;}
static void k_mutex_unlock(int *m){(void)m;}
static bt_addr_le_t peripheral_addrs[6];
'''
    alloc_test = r'''
int main(void){bt_addr_le_t b={{42}},c={{43}};peripheral_addrs[4]=b;
 assert(zmk_ble_put_peripheral_addr(&b)==4);
 assert(!lism_split_sets_slot_registered(2));assert(zmk_ble_put_peripheral_addr(&c)==-ENOENT);
 assert(!lism_split_sets_bind_addr(5,&c));assert(zmk_ble_put_peripheral_addr(&c)==5);
 assert(lism_split_sets_bind_addr(3,&c)==-EEXIST);
 assert(lism_split_sets_bind_addr(4,&c)==-EEXIST);
 assert(lism_split_sets_unbind_addr(5,&b)==-ESTALE);
 assert(!lism_split_sets_unbind_addr(5,&c));assert(!lism_split_sets_slot_registered(5));
 assert(zmk_ble_put_peripheral_addr(&b)==4);
 puts("actual generated allocator: known B before empty joystick slots passed");}
'''
    code.write_text(prelude + header + alloc_prelude + allocator + alloc_test)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-unused-variable", "-fsanitize=address,undefined", str(code), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
