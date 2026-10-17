"""Actual Zephyr settings + ZMK manager/transport initialization handoff.

The backend and OS primitives are mocks; settings list lifecycle, commit order,
transport availability and manager selection execute extracted pinned functions.
No hardware access or persistent settings writes occur. Set LISM_ZMK_APP and
LISM_ZEPHYR_BASE to the CI's pinned source checkouts.
"""
from pathlib import Path
import hashlib
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
app = Path(os.environ.get("LISM_ZMK_APP", root.parent / "xiao147-zmk-reference/app"))
zephyr = Path(os.environ.get("LISM_ZEPHYR_BASE", "/private/tmp/stress-zmk-upstream/zephyr"))
central_path = app / "src/split/bluetooth/central.c"
manager_path = app / "src/split/central.c"
settings_path = zephyr / "subsys/settings/src/settings.c"
subsystem_path = zephyr / "subsys/settings/src/settings_init.c"
central = central_path.read_text()
manager = manager_path.read_text()
settings = settings_path.read_text()
subsystem = subsystem_path.read_text()


def function(source, signature):
    start = source.index(signature)
    # These pinned definitions close at column zero; fail rather than silently
    # accepting a missing/moved function or handwritten replacement.
    return source[start:source.index("\n}", start) + 3]


prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <limits.h>
#define CONFIG_SETTINGS 1
#define CONFIG_SETTINGS_DYNAMIC_HANDLERS 1
#define CONFIG_ZMK_BLE_CLEAR_BONDS_ON_START 0
#define CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS 4
#define ZMK_SPLIT_BLE_PERIPHERAL_COUNT 4
#define CONFIG_ZMK_BLE_THREAD_PRIORITY 5
#define IS_ENABLED(x) (x)
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define MIN(a,b) ((a)<(b)?(a):(b))
#define CONTAINER_OF(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define K_FOREVER -1
#define SETTINGS_NAME_END '='
#define SETTINGS_NAME_SEPARATOR '/'
#define BT_LE_SCAN_ACTIVE 42
#define BT_LE_SCAN_PASSIVE 41
#define PERIPHERAL_SLOT_STATE_CONNECTED 2
#define BT_HCI_ERR_REMOTE_USER_TERM_CONN 19
#define LOG_INF(...) ((void)0)
#define LOG_ERR(...) ((void)0)
#define LOG_WRN(...) ((void)0)
#define LOG_DBG(...) ((void)0)
#define K_THREAD_STACK_SIZEOF(x) sizeof(x)

/* Only linked-list/locking primitives are mocked, not settings registration,
 * initialization or commit. Persisted records belong to the backend sentinel
 * and are deliberately independent from the RAM dynamic-handler list. */
typedef struct sys_snode {struct sys_snode *next;} sys_snode_t;
typedef struct {sys_snode_t *head,*tail;} sys_slist_t;
static void sys_slist_init(sys_slist_t *l){l->head=l->tail=NULL;}
static void sys_slist_append(sys_slist_t *l,sys_snode_t *n){
 n->next=NULL;if(l->tail)l->tail->next=n;else l->head=n;l->tail=n;
}
#define SYS_SLIST_FOR_EACH_CONTAINER(list,var,member) \
 for(sys_snode_t *_node=(list)->head;_node&&((var)=CONTAINER_OF(_node,struct settings_handler,member),1);_node=_node->next)
typedef int (*settings_read_cb)(void *,void *,size_t);
struct settings_handler {
 sys_snode_t node;const char *name;int cprio;
 int (*h_set)(const char *,size_t,settings_read_cb,void *);
 int (*h_commit)(void);
};
struct settings_handler_static {const char *name;int cprio;int (*h_commit)(void);};
static int hid_commits;
static int hid_commit(void){hid_commits++;return 0;}
static struct settings_handler_static static_handlers[]={{"lism_hid",0,hid_commit}};
static sys_slist_t settings_handlers;
static bool settings_subsys_initialized;
static int settings_lock,lock_depth;
static void k_mutex_lock(int *l,int t){assert(l==&settings_lock&&t==K_FOREVER);lock_depth++;}
static void k_mutex_unlock(int *l){assert(l==&settings_lock&&lock_depth>0);lock_depth--;}
static unsigned backend_calls,store_init_calls;
static int backend_result;
static uint8_t persistent_records[]={0xa5,0x12,0x98,0x37,0xef};
static const uint8_t original_records[]={0xa5,0x12,0x98,0x37,0xef};
static unsigned saves,deletes,unpairs;
static void settings_store_init(void){store_init_calls++;}
static int settings_backend_init(void){backend_calls++;return backend_result;}
static int settings_commit_subtree(const char *);

enum zmk_split_transport_connections_status {
 ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_DISCONNECTED,
 ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_SOME_CONNECTED,
 ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_ALL_CONNECTED
};
struct zmk_split_transport_status {
 bool available,enabled;enum zmk_split_transport_connections_status connections;
};
struct zmk_split_transport_central;
typedef int (*zmk_split_transport_central_status_changed_cb_t)(
 const struct zmk_split_transport_central *,struct zmk_split_transport_status);
struct zmk_split_transport_central_api {
 int (*set_enabled)(bool);
 int (*set_status_callback)(zmk_split_transport_central_status_changed_cb_t);
 struct zmk_split_transport_status (*get_status)(void);
};
struct zmk_split_transport_central {const struct zmk_split_transport_central_api *api;};
static const struct zmk_split_transport_central bt_central;
static zmk_split_transport_central_status_changed_cb_t transport_status_cb;
static const struct zmk_split_transport_central *active_transport;
#define ITER_settings_handler_static(var) \
 for(struct settings_handler_static *var=static_handlers;var<static_handlers+ARRAY_SIZE(static_handlers);var++)
#define ITER_zmk_split_transport_central(var) \
 for(const struct zmk_split_transport_central *var=&bt_central;var;var=NULL)
#define STRUCT_SECTION_FOREACH(type,var) ITER_##type(var)

typedef long atomic_t;
static long atomic_inc(atomic_t *a){return (*a)++;}
static long atomic_get(atomic_t *a){return *a;}
static long atomic_set(atomic_t *a,long v){long old=*a;*a=v;return old;}
struct bt_conn {int unused;};
struct net_buf_simple {int unused;};
struct peripheral {struct bt_conn *conn;int state;};
static struct peripheral peripherals[4];
static bool is_enabled,is_scanning,settings_loaded,need_hid;
static unsigned scan_attempts,stop_attempts,disconnect_attempts;
static bool controller_ready;
static bool lism_hid_host_scan_paused(void){return false;}
static bool lism_hid_host_scan_needed(void){return need_hid;}
static void split_central_device_found(void){}
static int bt_le_scan_start(int kind,void (*callback)(void)){
 assert(kind==BT_LE_SCAN_ACTIVE||kind==BT_LE_SCAN_PASSIVE);(void)callback;
 scan_attempts++;assert(controller_ready);return 0;
}
static int bt_le_scan_stop(void){stop_attempts++;return 0;}
static int bt_conn_disconnect(struct bt_conn *c,int why){(void)c;(void)why;disconnect_attempts++;return 0;}
static int split_central_split_run_q,conn_callbacks;
static uint8_t split_central_split_run_q_stack[32];
static unsigned work_queue_starts,conn_registrations;
static void k_work_queue_start(int *q,uint8_t *stack,size_t bytes,int priority,void *cfg){
 assert(q==&split_central_split_run_q&&stack==split_central_split_run_q_stack);
 assert(bytes==sizeof(split_central_split_run_q_stack)&&priority==5&&!cfg);work_queue_starts++;
}
static int bt_conn_cb_register(int *c){assert(c==&conn_callbacks);conn_registrations++;return 0;}
static int finish_init();
'''

real_settings = "\n".join(function(settings, signature) for signature in (
    "void settings_init(void)",
    "int settings_register_with_cprio(",
    "int settings_register(struct settings_handler *handler)",
    "int settings_name_steq(",
    "static int set_next_cprio(",
    "int settings_commit_subtree(",
    "int settings_commit(void)"))
real_subsystem = function(subsystem, "int settings_subsys_init(void)")
real_manager = "\n".join(function(manager, signature) for signature in (
    "static int select_first_available_transport(void)",
    "static int transport_status_changed_cb(",
    "static int central_init(void)"))


def transport_parts(source, patched):
    diagnostics = source[source.index("/* LISM owner diagnostics begin:"):
                         source.index("/* LISM owner diagnostics end */")] if patched else ""
    handler = source[source.index("static struct settings_handler ble_central_settings_handler = {"):]
    handler = handler[:handler.index(";") + 1]
    parts = [diagnostics, function(source, "static int central_ble_handle_set("), handler]
    parts.extend(function(source, signature) for signature in (
        "static int start_scanning(void) {",
        "static int stop_scanning(void) {",
        "static int split_central_bt_get_available_source_ids(",
        "static int split_central_bt_set_enabled(",
        "static int\nsplit_central_bt_set_status_callback(",
        "static struct zmk_split_transport_status split_central_bt_get_status()",
        "static int finish_init() {",
        "static int zmk_split_bt_central_init(void)"))
    # The fixture exposes the real extracted transport callbacks through the
    # exact interface used by the extracted manager. No manual enable call.
    parts.append(r'''
static const struct zmk_split_transport_central_api central_api={
 .set_enabled=split_central_bt_set_enabled,
 .set_status_callback=split_central_bt_set_status_callback,
 .get_status=split_central_bt_get_status};
static const struct zmk_split_transport_central bt_central={&central_api};
''')
    return "\n".join(parts)


checks = r'''
static unsigned dynamic_handlers(void){
 unsigned count=0;for(sys_snode_t *n=settings_handlers.head;n;n=n->next){assert(++count<8);}return count;
}
static int bluetooth_init(void){controller_ready=true;return settings_subsys_init();}
static void unchanged_storage(void){
 assert(!memcmp(persistent_records,original_records,sizeof(original_records)));
 assert(!saves&&!deletes&&!unpairs&&!stop_attempts&&!disconnect_attempts&&!lock_depth);
}
static void assert_precommit_disabled(void){
 assert(!settings_loaded&&!is_enabled&&!is_scanning&&!active_transport&&!scan_attempts);
 assert(!split_central_bt_get_status().available);
}
static void commit_handoff(void){
 assert_precommit_disabled();assert(transport_status_cb);
 assert(settings_commit()==0);
 assert(hid_commits==1&&settings_loaded&&is_enabled&&is_scanning);
 assert(active_transport==&bt_central&&scan_attempts==1);
 assert(split_central_bt_get_status().available);
 assert(split_central_bt_get_status().enabled);
 unchanged_storage();
}
static int inert_commit(void){return 0;}
int main(int argc,char **argv){
 assert(argc==2);const char *scenario=argv[1];
 if(!strcmp(scenario,"central-first")){
   assert(zmk_split_bt_central_init()==0);assert(central_init()==-ENODEV);
#if PATCHED
   assert(settings_subsys_initialized&&dynamic_handlers()==1);
   assert(store_init_calls==1&&backend_calls==1);
#else
   assert(!settings_subsys_initialized&&dynamic_handlers()==1);
   assert(!store_init_calls&&!backend_calls);
#endif
   assert_precommit_disabled();assert(bluetooth_init()==0);
#if PATCHED
   assert(dynamic_handlers()==1&&store_init_calls==1&&backend_calls==1);commit_handoff();
#else
   /* Regression reproduced: first Zephyr settings_init clears the dynamic
    * central handler; static HID still commits but manager never enables BLE. */
   assert(!dynamic_handlers()&&store_init_calls==1&&backend_calls==1);
   assert(settings_commit()==0&&hid_commits==1);assert_precommit_disabled();
   unchanged_storage();
#endif
 }else if(!strcmp(scenario,"bluetooth-first")){
   assert(bluetooth_init()==0);assert(zmk_split_bt_central_init()==0);
   assert(central_init()==-ENODEV);assert(dynamic_handlers()==1);commit_handoff();
 }else if(!strcmp(scenario,"manager-last")){
   assert(zmk_split_bt_central_init()==0);assert(bluetooth_init()==0);
   assert(settings_commit()==0&&settings_loaded&&hid_commits==1);
   assert(!transport_status_cb&&!is_enabled&&!scan_attempts);
   assert(central_init()==0&&is_enabled&&scan_attempts==1&&active_transport==&bt_central);
 }else if(!strcmp(scenario,"backend-failure")){
   backend_result=-EIO;assert(zmk_split_bt_central_init()==-EIO);
   assert(!settings_subsys_initialized&&!dynamic_handlers());
   assert(central_init()==-ENODEV);assert_precommit_disabled();
   assert(store_init_calls==1&&backend_calls==1);
   assert(settings_commit()==0&&hid_commits==1);assert_precommit_disabled();
 }else if(!strcmp(scenario,"register-failure")){
   assert(bluetooth_init()==0);
   struct settings_handler duplicate={.name="ble_central",.h_commit=inert_commit};
   assert(settings_register(&duplicate)==0);assert(dynamic_handlers()==1);
   assert(zmk_split_bt_central_init()==-EEXIST&&dynamic_handlers()==1);
   assert(central_init()==-ENODEV);assert(settings_commit()==0&&hid_commits==1);
   assert_precommit_disabled();
 }else if(!strcmp(scenario,"idempotent")){
   assert(zmk_split_bt_central_init()==0);assert(dynamic_handlers()==1);
   sys_snode_t *first=settings_handlers.head;
   for(unsigned i=0;i<20;i++)assert(settings_subsys_init()==0);
   assert(settings_handlers.head==first&&dynamic_handlers()==1);
   assert(store_init_calls==1&&backend_calls==1);assert(bluetooth_init()==0);
   assert(central_init()==-ENODEV);commit_handoff();
   for(unsigned i=0;i<20;i++)assert(settings_subsys_init()==0);
   assert(dynamic_handlers()==1&&settings_handlers.head==first);
   assert(store_init_calls==1&&backend_calls==1&&scan_attempts==1);
 }else{assert(!"unknown scenario");}
 unchanged_storage();assert(work_queue_starts==1&&conn_registrations==1);
 puts(scenario);return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="lism-hid-init-") as directory:
    temp = Path(directory)
    patched = temp / "central.c"
    driver = temp / "patch.cmake"
    driver.write_text(f'include("{root / "cmake/lism_hid_central.cmake"}")\n'
                      f'lism_hid_patch_central("{central_path}" "{patched}" '
                      f'"{root / "src/lism_hid_host.h"}")\n')
    subprocess.run(["cmake", "-P", str(driver)], check=True)
    output = patched.read_text()
    init_body = function(output, "static int zmk_split_bt_central_init(void)")
    assert init_body.index("settings_subsys_init()") < init_body.index("settings_register(")
    for forbidden in ("settings_load(", "settings_save", "settings_delete", "bt_unpair", "set_enabled("):
        assert forbidden not in init_body
    for variant, source in (("original", central), ("patched", output)):
        c, binary = temp / f"{variant}.c", temp / variant
        c.write_text(f"#define PATCHED {int(variant == 'patched')}\n" + prelude + real_settings +
                     real_subsystem + transport_parts(source, variant == "patched") + real_manager + checks)
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-function", "-Wno-unused-variable", "-Wno-unused-parameter",
                        "-fsanitize=address,undefined", str(c), "-o", str(binary)], check=True)
        scenarios = ("central-first", "bluetooth-first") if variant == "original" else (
            "central-first", "bluetooth-first", "manager-last", "backend-failure", "register-failure", "idempotent")
        for scenario in scenarios:
            subprocess.run([str(binary), scenario], check=True)
for path in (central_path, manager_path, settings_path, subsystem_path):
    print(f"source sha256 {hashlib.sha256(path.read_bytes()).hexdigest()} {path.name}")
print("PASS: real Zephyr settings lifecycle reproduces old lost dynamic handler; patched both SYS_INIT orders commit through real ZMK manager to scan; errors stay disabled; repeated init preserves handler/backend records")
