"""Run real host session/input/settings/auth functions with bounded fake I/O.

No implementation is copied into this harness: selected function bodies and
session structures are extracted directly from the production coordinator.
"""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / "src/lism_input_host.c").read_text()

def function(name):
    match = re.search(r"^(?:static )?[^\n;{}]+\b" + re.escape(name) + r"\([^;]*?\)\s*\{", source, re.M)
    assert match, name
    start = source.index("{", match.start())
    depth = 1
    end = start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end] + "\n"

definitions = source[source.index("#define SERVICES"):source.index("K_MUTEX_DEFINE(status_lock)")]
with tempfile.TemporaryDirectory(prefix="lism-input-host-") as tmp:
    out = Path(tmp)
    (out / "zmk").mkdir()
    (out / "zephyr/bluetooth").mkdir(parents=True)
    (out / "zephyr/bluetooth/addr.h").write_text(r'''
#pragma once
#include <stdbool.h>
#include <stdint.h>
typedef union {uint8_t value;struct {uint8_t value;} a;} bt_addr_le_t;
struct bt_conn {bt_addr_le_t address;};
''')
    (out / "zmk/hid.h").write_text(r'''
#pragma once
#include <stdint.h>
#define IS_ENABLED(x) x
#define CONFIG_ZMK_USB_BOOT 1
#define CONFIG_ZMK_HID_REPORT_TYPE_HKRO 1
#define CONFIG_ZMK_HID_CONSUMER_REPORT_USAGES_BASIC 0
struct zmk_hid_keyboard_report_body {uint8_t modifiers,_reserved,keys[6];};
struct zmk_hid_keyboard_report {uint8_t report_id;struct zmk_hid_keyboard_report_body body;};
typedef struct zmk_hid_keyboard_report_body zmk_hid_boot_report_t;
struct zmk_hid_mouse_report {struct {uint8_t buttons;} body;};
struct zmk_hid_consumer_report_body {uint16_t keys[6];};
struct zmk_hid_consumer_report {uint8_t report_id;struct zmk_hid_consumer_report_body body;};
''')
    (out / "zephyr/kernel.h").write_text(r'''
#pragma once
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
struct k_spinlock {int dummy;};
typedef int k_spinlock_key_t;
static inline int k_spin_lock(struct k_spinlock*p){(void)p;return 0;}
static inline void k_spin_unlock(struct k_spinlock*p,int k){(void)p;(void)k;}
''')
    prelude = r'''
static int ui_active=1;static unsigned ui_generation;
static int lism_screen_active(void){return ui_active;}
static unsigned lism_screen_generation(void){return ui_generation;}

#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <zephyr/kernel.h>
#include "lism_input_host.h"
#include "lism_input_parser.h"
#include "lism_input_merge.h"
#include "lism_input_security.h"
#define LOG_INF(...) ((void)0)
#define ARG_UNUSED(x) (void)(x)
#define BIT(x) (1u<<(x))
#define BIT_MASK(x) (BIT(x)-1)
#define MIN(a,b) ((a)<(b)?(a):(b))
#define CLAMP(x,a,b) ((x)<(a)?(a):(x)>(b)?(b):(x))
#define K_FOREVER 0
#define K_NO_WAIT 0
#define ZMK_HID_MOUSE_NUM_BUTTONS 5
#define INPUT_EV_MSC 4
#define HID_USAGE_KEY 7
#define HID_USAGE_CONSUMER 12
#define BT_ID_DEFAULT 0
#define BT_SECURITY_L2 2
#define CONFIG_LISM_INPUT_LEGACY_COMPAT 1
#define CONFIG_LISM_INPUT_AUTO_PAIR 0
#define BT_CONN_ROLE_CENTRAL 0
#define BT_CONN_TYPE_LE 1
#define BT_SECURITY_ERR_AUTH_REQUIREMENT 4
#define BT_HCI_ERR_REMOTE_USER_TERM_CONN 0x13
#define BT_CONN_LE_OPT_NONE 0
struct bt_conn_le_create_param {unsigned options,interval,window,timeout;};
#define BT_LE_CONN_PARAM(...) 0
#define CONFIG_BT_MAX_PAIRED 14
#define ZMK_BLE_PROFILE_COUNT 6
#define ZMK_EV_EVENT_BUBBLE 0
struct bt_gatt_discover_params {int unused;};
struct bt_gatt_read_params {int unused;};
struct bt_gatt_subscribe_params {int unused;};
typedef int atomic_t;
#define ATOMIC_INIT(x) (x)
static int atomic_get(const atomic_t*p){return *p;}
static int atomic_set(atomic_t*p,int v){int old=*p;*p=v;return old;}
static void atomic_clear(atomic_t*p){*p=0;}
static void atomic_inc(atomic_t*p){(*p)++;}
struct k_work {int unused;};
struct input_event {int type,code;};
typedef struct {int kind;} zmk_event_t;
static const void *as_zmk_endpoint_changed(const zmk_event_t*e){return e->kind==1?e:NULL;}
typedef long (*settings_read_cb)(void*,void*,size_t);
'''
    mocks = r'''
static int status_lock,report_lock;
static struct k_work control_work;
static int commands,events,advertisements;
static uint32_t now=1000;
static int endpoint=0,kicks,closed,confirmed,entered,cancelled;
static struct bt_conn *answered_conn;
static bt_addr_le_t protected_split={200},hosts[6];
static bool mock_bonded=true;
static int create_result=-ECONNREFUSED,create_calls,saved_count;
static int unpair_result,delete_result,legacy_delete_result,save_result;
static char last_message[80];
static unsigned unpaired_count,deleted_count;
static bt_addr_le_t unpaired[32];
static char deleted[32][32];
static const bt_addr_le_t any={0};
#define BT_ADDR_LE_ANY (&any)
static int bt_addr_le_cmp(const bt_addr_le_t*a,const bt_addr_le_t*b){return a->value!=b->value;}
static const bt_addr_le_t *bt_conn_get_dst(struct bt_conn*c){return &c->address;}
static unsigned info_role,info_type=BT_CONN_TYPE_LE;
struct bt_conn_info {unsigned role,type;};
static int bt_conn_get_info(struct bt_conn*c,struct bt_conn_info*info){
 (void)c;info->role=info_role;info->type=info_type;return 0;
}
static bool lism_input_split_identity(const bt_addr_le_t*a){return !bt_addr_le_cmp(a,&protected_split);}
static const bt_addr_le_t *zmk_ble_profile_address(unsigned i){assert(i<6);return &hosts[i];}
static bool lism_input_protected_identity(const bt_addr_le_t*a){
 if(!bt_addr_le_cmp(a,&protected_split))return true;
 for(unsigned i=0;i<6;i++){if(!bt_addr_le_cmp(a,&hosts[i]))return true;}return false;
}
bool lism_input_computer_identity(const bt_addr_le_t*a){
 for(unsigned i=0;i<6;i++){if(!bt_addr_le_cmp(a,&hosts[i]))return true;}return false;
}
static void bt_addr_to_str(const void*a,char*out,size_t n){(void)a;snprintf(out,n,"address");}
static uint32_t k_uptime_get_32(void){return now;}
static void k_mutex_lock(int*m,int wait){(void)m;(void)wait;}
static void k_mutex_unlock(int*m){(void)m;}
static void k_work_submit(struct k_work*w){(void)w;}
static void kick(void){kicks++;}
static struct event observed_security;
static unsigned security_events;
static unsigned free_events=32;
static int event_put_error,conn_refs;
static int owned_connection(struct bt_conn*c){
 for(unsigned i=0;i<LISM_INPUT_PEERS;i++){if(sessions[i].conn==c)return i;}return -1;
}
static unsigned k_msgq_num_free_get(int*q){(void)q;return free_events;}
static struct bt_conn*bt_conn_ref(struct bt_conn*c){conn_refs++;return c;}
static int k_msgq_put(int*q,const struct event*e,int wait){
 (void)q;(void)wait;if(event_put_error)return event_put_error;
 observed_security=*e;security_events++;return 0;
}
static int xiao147_pointer_endpoint(void){return endpoint;}
struct frame_queue {struct frame frames[32];unsigned n,read;};
static struct frame_queue frames;
static int k_msgq_get(void*queue,void*out,int wait){
 (void)wait;if(queue!=&frames)return -ENOMSG;
 struct frame_queue*q=queue;if(q->read==q->n){q->read=q->n=0;return -ENOMSG;}
 memcpy(out,&q->frames[q->read++],sizeof(struct frame));return 0;
}
static void k_msgq_purge(struct frame_queue*q){q->n=q->read=0;}
static unsigned k_msgq_num_used_get(void*queue){if(queue!=&frames)return 0;struct frame_queue*q=queue;return q->n-q->read;}
static unsigned buttons[5],keyboard_sends,consumer_sends,mouse_sends;
static unsigned fail_keyboard,fail_mouse;
static bool usb_bridge_context;
void lism_input_usb_bridge(bool active){usb_bridge_context=active;}
static void bridge_retry(void){kicks++;}
static struct zmk_hid_keyboard_report keyboard_history[32];
static void zmk_hid_mouse_button_press(unsigned i){buttons[i]++;}
static void zmk_hid_mouse_button_release(unsigned i){assert(buttons[i]);buttons[i]--;}
static int pointer_x,pointer_history[32],pointer_count;
static void zmk_hid_mouse_movement_set(int x,int y){pointer_x=x;(void)y;}
static void zmk_hid_mouse_scroll_set(int x,int y){(void)x;(void)y;}
static int zmk_endpoint_send_mouse_report(void){mouse_sends++;if(fail_mouse){fail_mouse--;return -EAGAIN;}if(pointer_x && pointer_count<32)pointer_history[pointer_count++]=pointer_x;return 0;}
static int zmk_endpoint_send_report(int usage){
 if(usage==HID_USAGE_KEY){
  if(fail_keyboard){fail_keyboard--;return -EAGAIN;}
  struct zmk_hid_keyboard_report report={.report_id=1};lism_input_merge_keyboard(&report);
  assert(keyboard_sends<32);keyboard_history[keyboard_sends++]=report;
 }else{consumer_sends++;}return 0;
}
static void message(int slot,const char*msg){(void)slot;snprintf(last_message,sizeof(last_message),"%s",msg);}
static void scanner_state(void);
void lism_hid_scan_resume(void){}
int lism_hid_scan_pause(void){return 0;}
static bool busy_setup(void){return false;}
static bool lism_esb_coex_pressure(void){return false;}
static void clear_prompt(unsigned slot);
static void close_session(unsigned slot,const char*why);
static void cancel_requests(struct session*s){(void)s;}
static int bt_conn_disconnect(struct bt_conn*c,int reason){(void)c;(void)reason;return 0;}
static void bt_conn_unref(struct bt_conn*c){(void)c;conn_refs--;}
static void finish(unsigned slot){sessions[slot].conn=NULL;clear_prompt(slot);}
static void advance(struct session*s,uint8_t stage){s->stage=stage;s->operation=false;}
static int bt_unpair(int id,const bt_addr_le_t*addr){(void)id;unpaired[unpaired_count++]=*addr;return unpair_result;}
static int settings_delete(const char*key){
 snprintf(deleted[deleted_count++],32,"%s",key);
 return !strcmp(key,"lism_hid/peer")?legacy_delete_result:delete_result;
}
static int settings_save_one(const char*key,const void*v,size_t n){(void)key;(void)v;(void)n;saved_count++;return save_result;}
static bool bonded(const bt_addr_le_t*a){(void)a;return mock_bonded;}
struct bt_bond_info {int unused;};
static void count_bonds(const struct bt_bond_info*i,void*c){(void)i;(*(unsigned*)c)++;}
static void bt_foreach_bond(int id,void(*cb)(const struct bt_bond_info*,void*),void*c){(void)id;(void)cb;(void)c;}
static int bt_conn_le_create(const bt_addr_le_t*a,const struct bt_conn_le_create_param *create,int params,struct bt_conn**conn){
 (void)a;(void)create;(void)params;(void)conn;create_calls++;return create_result;
}
static void progress(unsigned slot){(void)slot;}
static int bt_conn_auth_cancel(struct bt_conn*c){cancelled++;answered_conn=c;return 0;}
static int bt_conn_auth_passkey_confirm(struct bt_conn*c){confirmed++;answered_conn=c;return 0;}
static int bt_conn_auth_passkey_entry(struct bt_conn*c,unsigned key){(void)key;entered++;answered_conn=c;return 0;}
'''
    selected = ["elapsed", "queue_event", "identity_conflict", "publish", "bind_legacy_connection",
                "lism_input_legacy_allowed", "lism_input_legacy_key_allowed", "lism_input_non_split_identity", "lism_input_identity_allowed", "legacy_status", "legacy_token_refresh",
                "release_slot", "lism_input_endpoint_transition_begin",
                "lism_input_endpoint_transition_end", "lism_input_merge_endpoint_changing",
                "lism_input_merge_endpoint_changed", "current_frame", "boot_decode", "union_peer", "bridge_send_pending",
                "bridge_input", "endpoint_event", "clear_prompt", "close_session", "scanner_state",
                "name_hint", "advertisement", "connect_target", "save_identity", "ready",
                "lism_input_smp_received", "lism_input_smp_blocked", "lism_input_smp_failed", "identity_resolved",
                "security_observed", "security_failure", "event_received",
                "command_received", "control", "lism_input_host_enabled", "lism_input_host_set_enabled", "valid_saved", "settings_set", "legacy_set"]
    tests = r'''
static struct input_event wake={INPUT_EV_MSC,KICK};
static void enqueue_key(unsigned slot,unsigned service,unsigned key){
 struct frame*f=&frames.frames[frames.n++];*f=(struct frame){
  .slot=slot,.service=service,.epoch=atomic_get(&sessions[slot].epoch),
  .generation=lism_input_merge_generation(),.endpoint=endpoint,.boot=1,.len=8};
 f->data[2]=key;
}
static bool has_key(const struct zmk_hid_keyboard_report*r,unsigned key){
 for(unsigned i=0;i<6;i++){if(r->body.keys[i]==key)return true;}
 return false;
}
static long settings_copy(void*context,void*dst,size_t length){memcpy(dst,context,length);return length;}
int main(void){
 assert(!lism_input_host_enabled() && !requested_enabled);
 lism_input_host_set_enabled(true);control(NULL);assert(lism_input_host_enabled());
 /* First ever input wake already contains a valid first keydown. No spontaneous
  * all-up notification is required, and stale old backlogs are rejected per-frame. */
 for(unsigned i=0;i<2;i++){sessions[i].epoch=1;sessions[i].input_ready=1;sessions[i].service_count=1;}
 enqueue_key(0,0,4);bridge_input(&wake,NULL);
 assert(keyboard_sends>=1 && has_key(&keyboard_history[keyboard_sends-1],4));
 enqueue_key(1,0,5);bridge_input(&wake,NULL);
 assert(has_key(&keyboard_history[keyboard_sends-1],4)&&has_key(&keyboard_history[keyboard_sends-1],5));
 /* An unrelated USB supply/status event while the selected BLE endpoint stays
  * the same must preserve held keys and avoid an extra generation reset. */
 uint32_t unchanged_generation=lism_input_merge_generation();
 zmk_event_t unrelated_usb={.kind=0};endpoint_event(&unrelated_usb);
 assert(lism_input_merge_generation()==unchanged_generation&&!atomic_get(&endpoint_changed));
 struct zmk_hid_keyboard_report still_held={0};lism_input_merge_keyboard(&still_held);
 assert(has_key(&still_held,4)&&has_key(&still_held,5));
 /* Source-specific cleanup cannot release the other keyboard or mouse owner. */
 sessions[0].own_buttons=sessions[1].own_buttons=1;buttons[0]=2;
 atomic_set(&sessions[0].clear,1);bridge_input(&wake,NULL);
 assert(buttons[0]==2 && !sessions[0].own_buttons && sessions[1].own_buttons==1);
 assert(!has_key(&keyboard_history[keyboard_sends-1],4)&&has_key(&keyboard_history[keyboard_sends-1],5));
 /* The same Report ID can be used by independent HID services. */
 sessions[0].service_count=2;
 enqueue_key(0,0,6);enqueue_key(0,1,7);bridge_input(&wake,NULL);
 assert(has_key(&keyboard_history[keyboard_sends-1],6)&&has_key(&keyboard_history[keyboard_sends-1],7));
 enqueue_key(0,0,0);bridge_input(&wake,NULL);
 assert(!has_key(&keyboard_history[keyboard_sends-1],6)&&has_key(&keyboard_history[keyboard_sends-1],7));
 /* Endpoint freeze blocks frames until commit; after commit the first new
  * keydown survives backlog cleanup, while an old-generation frame does not. */
 struct frame stale={.slot=0,.service=0,.epoch=1,.generation=lism_input_merge_generation(),.endpoint=endpoint};
 lism_input_merge_endpoint_reset();assert(atomic_get(&endpoint_frozen));
 struct frame current=stale;current.generation=lism_input_merge_generation();
 assert(!current_frame(&current));
 zmk_event_t committed={.kind=1};endpoint_event(&committed);
 assert(!atomic_get(&endpoint_frozen));
 frames.frames[frames.n++]=stale;
 enqueue_key(0,0,8);bridge_input(&wake,NULL);
 assert(has_key(&keyboard_history[keyboard_sends-1],8));
 assert(!has_key(&keyboard_history[keyboard_sends-1],5));

 /* Settings migration is RAM-only until a successful new Ready save. New slot0
  * takes precedence; forgetting slot1 never deletes legacy slot0 or other bonds. */
 memset(sessions,0,sizeof(sessions));memset(loaded_valid,0,sizeof(loaded_valid));
 struct saved_peer old={.version=1,.addr={10},.name="Legacy mouse"};
 struct saved_peer newer={.version=2,.addr={20},.name="New keyboard"};
 assert(!legacy_set("peer",sizeof(old),settings_copy,&old));
 assert(!settings_set("peer1",sizeof(newer),settings_copy,&newer));
 struct event settings={.type=SETTINGS_READY};event_received(&settings);
 assert(sessions[0].have_saved && sessions[0].saved.addr.value==10 && sessions[0].saved.version==2);
 assert(sessions[1].have_saved && sessions[1].saved.addr.value==20 && !deleted_count);
 struct command forget={.type=FORGET,.slot=1};command_received(&forget);
 assert(unpaired_count==1 && unpaired[0].value==20 && deleted_count==1);
 assert(!strcmp(deleted[0],"lism_inputs/peer1") && legacy_valid && sessions[0].have_saved);
 forget.slot=0;command_received(&forget);
 assert(unpaired_count==2 && unpaired[1].value==10 && deleted_count==3 && !legacy_valid);
 assert(!strcmp(deleted[1],"lism_inputs/peer0")&&!strcmp(deleted[2],"lism_hid/peer"));
 newer.addr.value=30;assert(!settings_set("peer0",sizeof(newer),settings_copy,&newer));
 legacy_valid=true;event_received(&settings);assert(sessions[0].saved.addr.value==30);
 assert(settings_set("peer2",sizeof(newer),settings_copy,&newer)==-ENOENT);
 newer.version=9;assert(settings_set("peer1",sizeof(newer),settings_copy,&newer)==-EINVAL);

 /* Auth prompts require the matching current connection, epoch, token and live
  * timeout. A token from an old prompt cannot approve another device. */
 struct bt_conn a={{1}},b={{2}};sessions[0].conn=&a;sessions[0].epoch=7;sessions[0].stage=SECURITY;
 /* The receive hook must not dereference a secret-bearing opcode, malformed
  * public packet, or packet belonging to a split/computer/unknown connection. */
 for(unsigned opcode=0;opcode<16;opcode++){
  if(opcode!=1&&opcode!=2&&opcode!=5&&opcode!=11){
   lism_input_smp_received(&a,opcode,(const void*)1,16);
  }
 }
 uint8_t public_features[6]={3,0,1,16,0,1};
 lism_input_smp_received(&a,2,public_features,5);
 lism_input_smp_received(&a,2,NULL,6);
 lism_input_smp_received(&b,2,public_features,6);assert(!security_events);
 lism_input_smp_received(&a,2,public_features,6);
 assert(security_events==1&&observed_security.conn==&a&&observed_security.epoch==7);
 assert(observed_security.type==SMP_RECEIVED&&observed_security.length==6);
 assert(!memcmp(observed_security.data,public_features,6));
 for(unsigned i=6;i<sizeof(observed_security.data);i++){assert(!observed_security.data[i]);}
 lism_input_smp_blocked(&a,0);lism_input_smp_blocked(&a,255);
 lism_input_smp_blocked(&b,LISM_INPUT_SMP_SC_REQUIRED);assert(security_events==1);
 lism_input_smp_blocked(&a,LISM_INPUT_SMP_SC_REQUIRED);
 assert(security_events==2&&observed_security.type==SMP_BLOCKED);
 lism_input_smp_failed(&b,2,3);lism_input_smp_failed(&a,2,0);assert(security_events==2);
 lism_input_smp_failed(&a,2,3);
 assert(security_events==3&&observed_security.type==SMP_FAILED&&observed_security.error==3);
 /* Diagnostics must not fault a connection under queue pressure or a racing
  * put failure. Missing evidence is marked, and owned refs remain balanced. */
 int refs_before=conn_refs;free_events=7;
 lism_input_smp_received(&a,2,public_features,6);
 assert(security_events==3&&sessions[0].security_lost&&!sessions[0].fault&&conn_refs==refs_before);
 free_events=32;atomic_clear(&sessions[0].security_lost);event_put_error=-ENOMSG;
 lism_input_smp_blocked(&a,LISM_INPUT_SMP_SC_REQUIRED);
 assert(security_events==3&&sessions[0].security_lost&&!sessions[0].fault&&conn_refs==refs_before);
 event_put_error=0;atomic_clear(&sessions[0].security_lost);
 struct event auth={.type=AUTH,.slot=0,.conn=&a,.epoch=6,.auth=LISM_INPUT_AUTH_CONFIRM,.passkey=123456};
 event_received(&auth);assert(!status.auth_type);
 auth.epoch=7;auth.conn=&b;event_received(&auth);assert(!status.auth_type);
 auth.conn=&a;event_received(&auth);assert(status.auth_type==LISM_INPUT_AUTH_CONFIRM);
 uint32_t token=status.auth_token;
 /* An unrelated peer's late cancellation cannot erase the active prompt or
  * rotate its token/timeout. Even its own setup deadline must stay untouched. */
 sessions[1].conn=&b;sessions[1].epoch=8;sessions[1].stage=READY;sessions[1].deadline=2345;
 uint32_t active_until=prompt_until,active_deadline=sessions[0].deadline;
 struct event other_cancel={.type=AUTH,.slot=1,.conn=&b,.epoch=8,.auth=LISM_INPUT_AUTH_NONE};
 event_received(&other_cancel);
 assert(status.auth_type==LISM_INPUT_AUTH_CONFIRM&&status.auth_slot==0&&status.auth_token==token);
 assert(status.passkey==123456&&prompt_until==active_until&&sessions[0].deadline==active_deadline);
 assert(sessions[1].deadline==2345);
 /* A delayed positive prompt cannot reopen authentication after this exact
  * connection advanced to HID discovery. Prior-epoch cancellation is ignored. */
 sessions[0].stage=SERVICES_FIND;event_received(&auth);
 assert(status.auth_token==token&&prompt_until==active_until&&sessions[0].deadline==active_deadline);
 struct event own_cancel=auth;own_cancel.auth=LISM_INPUT_AUTH_NONE;own_cancel.epoch=6;
 event_received(&own_cancel);assert(status.auth_type==LISM_INPUT_AUTH_CONFIRM);
 sessions[0].stage=SECURITY;
 struct command reply={.type=AUTH_REPLY,.token=token+1,.accept=true};
 command_received(&reply);assert(!confirmed);
 reply.token=token;command_received(&reply);assert(confirmed==1&&answered_conn==&a&&!status.auth_type);
 command_received(&reply);assert(confirmed==1);
 auth.auth=LISM_INPUT_AUTH_ENTRY;event_received(&auth);reply.token=status.auth_token;reply.passkey=987654;
 now=prompt_until;command_received(&reply);assert(!entered);
 now=1000;event_received(&auth);reply.token=status.auth_token;reply.passkey=1000000;
 command_received(&reply);assert(!entered);
 reply.passkey=123;command_received(&reply);assert(entered==1&&answered_conn==&a);
 /* Current owner cancellation clears only its prompt, without giving the
  * connection a fresh setup deadline or creating an actionable new token. */
 event_received(&auth);token=status.auth_token;active_deadline=sessions[0].deadline;
 own_cancel.epoch=7;event_received(&own_cancel);
 assert(!status.auth_type&&!status.passkey&&!prompt_until&&status.auth_token==token);
 assert(sessions[0].deadline==active_deadline);

 /* Specific security explanations require the actual local rejection branch,
  * never infer old protocols or key size from the lossy Zephyr error 4 alone. */
 struct event metadata={.type=SMP_RECEIVED,.slot=0,.conn=&a,.epoch=7,
  .properties=2,.length=6,.data={3,0,1,16,0,1}};
 event_received(&metadata);
 assert(sessions[0].security.features&&sessions[0].security.key_size==16);
 char security_text[48];security_failure(&sessions[0],4,1,security_text);
 assert(strstr(security_text,"requirements mismatch"));
 struct event blocked=metadata;blocked.type=SMP_BLOCKED;blocked.properties=LISM_INPUT_SMP_SC_REQUIRED;
 event_received(&blocked);
 security_failure(&sessions[0],4,1,security_text);assert(strstr(security_text,"requirements mismatch"));
 struct event failed=metadata;failed.type=SMP_FAILED;failed.properties=2;failed.error=3;
 event_received(&failed);
 security_failure(&sessions[0],4,1,security_text);assert(strstr(security_text,"Secure Connections required"));
 blocked.properties=LISM_INPUT_SMP_KEY_SIZE;event_received(&blocked);
 security_failure(&sessions[0],4,1,security_text);assert(strstr(security_text,"key size 16 rejected"));
 blocked.properties=LISM_INPUT_SMP_STORED_BOND;event_received(&blocked);
 security_failure(&sessions[0],4,1,security_text);assert(strstr(security_text,"Existing bond policy"));
 blocked.properties=LISM_INPUT_SMP_AUTH_LEVEL;event_received(&blocked);
 security_failure(&sessions[0],4,1,security_text);assert(strstr(security_text,"Requested security"));
 security_failure(&sessions[0],0,1,security_text);assert(strstr(security_text,"below L2"));
 atomic_set(&sessions[0].security_lost,1);
 security_failure(&sessions[0],4,1,security_text);assert(strstr(security_text,"details lost"));
 atomic_clear(&sessions[0].security_lost);
 metadata.epoch=6;metadata.data[3]=7;event_received(&metadata);
 assert(sessions[0].security.key_size==16);
 metadata.epoch=7;metadata.conn=&b;event_received(&metadata);
 assert(sessions[0].security.key_size==16);
 metadata.conn=&a;metadata.properties=5;metadata.length=1;metadata.data[0]=6;
 sessions[0].security=(struct security_detail){0};event_received(&metadata);
 security_failure(&sessions[0],4,1,security_text);assert(strstr(security_text,"Peer rejected security (4/SMP 06)"));
 sessions[0].stage=READY;metadata.properties=2;metadata.length=6;event_received(&metadata);
 assert(!sessions[0].security.features);
 sessions[0].stage=SECURITY;

 /* Report Reference ID collisions are scoped to their service, not global. */
 sessions[0].stage=REFERENCE;sessions[0].service_count=2;sessions[0].report_count=2;
 sessions[0].report_index=1;sessions[0].reference_len=0;
 sessions[0].reports[0]=(struct report){.service=0,.id=1,.type=1,.reference=11};
 sessions[0].reports[1]=(struct report){.service=1,.reference=22};
 struct event ref={.type=READ,.slot=0,.conn=&a,.epoch=7,.stage=REFERENCE,.report=1,.length=2,.complete=true,.data={1,1}};
 event_received(&ref);
 assert(sessions[0].stage==SUBSCRIBE);
 sessions[0].stage=REFERENCE;sessions[0].reference_len=0;sessions[0].reports[1].service=0;
 event_received(&ref);assert(sessions[0].stage==CLOSING);

 /* Protect all six computer profiles, split identities, and the other generic
  * peer's saved OR not-yet-saved selected target. Own role may reconnect. */
 memset(sessions,0,sizeof(sessions));
 for(unsigned i=0;i<6;i++){hosts[i].value=100+i;assert(identity_conflict(&hosts[i],0));}
 assert(identity_conflict(NULL,0)&&identity_conflict(BT_ADDR_LE_ANY,0));
 assert(identity_conflict(&protected_split,0));
 sessions[1].have_saved=true;sessions[1].saved.addr.value=40;
 sessions[1].target_known=true;sessions[1].target.value=41;
 bt_addr_le_t addr={40};assert(identity_conflict(&addr,0)&&!identity_conflict(&addr,1));
 addr.value=41;assert(identity_conflict(&addr,0));
 addr.value=77;assert(!identity_conflict(&addr,0));
 /* Selection gate cannot redirect an input slot at a host. */
 now=1000;search_slot=0;search_until=61000;
 candidates[0]=(struct candidate){.addr={100},.seen=now,.connectable_seen=now,.public={.valid=true,.token=123}};
 struct command select={.type=SELECT,.index=0,.token=123};command_received(&select);
 assert(!sessions[0].target_known&&!sessions[0].pending);
 candidates[0].addr.value=77;command_received(&select);
 assert(sessions[0].target_known&&sessions[0].pending&&sessions[0].target.value==77);
 /* Same-coordinator SELECT -> CANCEL must never initiate after leaving the page. */
 struct command cancel_pending={.type=CANCEL};int before_cancel_create=create_calls;
 command_received(&cancel_pending);connect_target(0);
 assert(create_calls==before_cancel_create && !sessions[0].pending && !sessions[0].target_known);
 search_until=now+30000;command_received(&select);
 /* First failed attempt retains its target. Close stops looping until Retry;
  * Retry enables scan and a fresh matching advertisement can start again. */
 int before_create=create_calls;connect_target(0);
 assert(create_calls==before_create+1&&!sessions[0].conn&&sessions[0].target_known);
 close_session(0,"failed first pairing");assert(sessions[0].suspended);
 sessions[1].suspended=true;
 settings_ready=true;scanner_state();assert(!atomic_get(&scan_needed));
 struct command retry={.type=RETRY,.slot=0};command_received(&retry);
 assert(!sessions[0].suspended); /* reconnect windows are bounded by clock */
 struct advertisement advertised={.addr={77},.seen=now,.connectable=true};
 advertisement(&advertised);assert(sessions[0].pending&&sessions[0].seen==now);
 connect_target(0);assert(create_calls==before_create+2);
 /* Connection creation rechecks role ownership even if selection was stale. */
 sessions[0].target.value=100;sessions[0].pending=true;sessions[0].retry_at=now;
 connect_target(0);assert(create_calls==before_create+2&&sessions[0].suspended);
 /* Resolved identity and persistence gates cannot claim/unpair a host. */
 a.address.value=100;sessions[0].conn=&a;sessions[0].stage=CONNECTING;sessions[0].suspended=false;
 struct event connected={.type=CONNECTED,.slot=0,.conn=&a};event_received(&connected);
 assert(sessions[0].stage==CLOSING);
 sessions[0].stage=READY;int before_saved=saved_count;save_identity(0);
 assert(sessions[0].stage==CLOSING&&saved_count==before_saved);
 sessions[0].have_saved=true;sessions[0].saved.addr.value=100;
 unsigned before_unpair=unpaired_count,before_delete=deleted_count;
 forget.slot=0;command_received(&forget);
 assert(unpaired_count==before_unpair&&deleted_count==before_delete&&sessions[0].have_saved);
 /* Settings load gate ignores conflicts without deleting any protected bond. */
 memset(sessions,0,sizeof(sessions));loaded_valid[0]=true;loaded_valid[1]=false;
 loaded[0]=(struct saved_peer){.version=2,.addr={100}};event_received(&settings);
 assert(!sessions[0].have_saved&&unpaired_count==before_unpair);
 loaded[0].addr=protected_split;loaded_valid[0]=true;event_received(&settings);
 assert(!sessions[0].have_saved&&unpaired_count==before_unpair);
 /* Partial forget failure preserves retryable RAM identity until every targeted
  * storage operation succeeds. It never broadens into clearing another bond. */
 sessions[0].have_saved=sessions[0].target_known=true;
 sessions[0].saved.addr.value=sessions[0].target.value=77;
 legacy_valid=true;legacy.addr.value=77;
 unpair_result=-EIO;before_delete=deleted_count;command_received(&forget);
 assert(sessions[0].have_saved&&sessions[0].target_known&&legacy_valid&&deleted_count==before_delete);
 assert(strstr(last_message,"Forget failed"));
 unpair_result=0;delete_result=-EIO;command_received(&forget);
 assert(sessions[0].have_saved&&sessions[0].target_known&&legacy_valid);
 assert(strstr(last_message,"storage failed"));
 delete_result=0;legacy_delete_result=-EIO;command_received(&forget);
 assert(sessions[0].have_saved&&sessions[0].target_known&&legacy_valid);
 assert(strstr(last_message,"Legacy save removal failed"));
 legacy_delete_result=0;command_received(&forget);
 assert(!sessions[0].have_saved&&!sessions[0].target_known&&!legacy_valid);
 /* Ready keeps storage errors visible while allowing the live connection. */
 a.address.value=77;sessions[0].conn=&a;sessions[0].service_count=1;
 sessions[0].services[0].descriptor.capabilities=LISM_INPUT_CAP_KEYBOARD;
 save_result=-EIO;ready(0);
 assert(sessions[0].input_ready&&status.peers[0].ready&&strstr(last_message,"identity save failed"));
 save_result=0;
 /* Temporary USB backpressure retains the original press before consuming its release. */
 memset(sessions,0,sizeof(sessions));
 for(unsigned i=0;i<2;i++){sessions[i].epoch=11;sessions[i].input_ready=1;sessions[i].service_count=1;}
 lism_input_merge_endpoint_reset();endpoint_event(&committed);bridge_input(&wake,NULL);
 enqueue_key(1,0,5);bridge_input(&wake,NULL);
 fail_keyboard=1;enqueue_key(0,0,4);enqueue_key(0,0,0);bridge_input(&wake,NULL);
 assert(sessions[0].input_ready&&!sessions[0].fault&&sessions[1].input_ready);
 assert(!usb_bridge_context);bridge_input(&wake,NULL);
 assert(has_key(&keyboard_history[keyboard_sends-2],4));
 assert(!has_key(&keyboard_history[keyboard_sends-1],4)&&has_key(&keyboard_history[keyboard_sends-1],5));
 /* A blocked mouse edge is retained and the next frame is untouched. */
 struct frame mouse_frame={.slot=0,.service=0,.epoch=11,.generation=lism_input_merge_generation(),.endpoint=endpoint,.boot=2,.len=3,.data={0,7,0}};
 frames.n=frames.read=0;frames.frames[frames.n++]=mouse_frame;
 mouse_frame.data[1]=9;frames.frames[frames.n++]=mouse_frame;
 fail_mouse=1;unsigned before_mouse=mouse_sends;bridge_input(&wake,NULL);
 assert(mouse_sends==before_mouse+1 && frames.n-frames.read==1 && !usb_bridge_context);
 bridge_input(&wake,NULL);assert(pointer_count>=2 && pointer_history[pointer_count-2]==7 && pointer_history[pointer_count-1]==9);
 /* A dropped immediate KICK is recoverable: periodic control still sees clear. */
 memset(sessions,0,sizeof(sessions));search_until=prompt_until=0;
 atomic_clear(&endpoint_changed);atomic_clear(&endpoint_generation);atomic_set(&sessions[0].clear,1);
 int before_kicks=kicks;control(NULL);assert(kicks>before_kicks);

 /* Compatibility is not inferred from error 4, name, slot, or a lost diagnostic.
  * Only the actual SC-required local failure offers a fresh per-target token. */
 memset(sessions,0,sizeof(sessions));memset(legacy_policy,0,sizeof(legacy_policy));
 memset(loaded_grant_valid,0,sizeof(loaded_grant_valid));
 a.address.value=77;b.address.value=78;
 sessions[0].conn=&a;sessions[0].target=a.address;sessions[0].target_known=true;
 sessions[0].epoch=41;sessions[0].stage=SECURITY;
 struct event secured={.type=SECURED,.slot=0,.conn=&a,.epoch=41,.error=4,.level=1};
 event_received(&secured);assert(!sessions[0].legacy_available);
 sessions[0].stage=SECURITY;sessions[0].security.local_failure=true;
 sessions[0].security.block=LISM_INPUT_SMP_SC_REQUIRED;secured.epoch=sessions[0].epoch;
 atomic_set(&sessions[0].security_lost,1);event_received(&secured);
 assert(!sessions[0].legacy_available);
 sessions[0].stage=SECURITY;atomic_clear(&sessions[0].security_lost);
 secured.epoch=sessions[0].epoch;event_received(&secured);
 assert(sessions[0].legacy_available&&!sessions[0].legacy_allowed);
 uint32_t compat_token=sessions[0].legacy_token;assert(compat_token);
 finish(0);
 struct command compat={.type=LEGACY,.slot=0,.token=compat_token+1,.accept=true};
 command_received(&compat);assert(!sessions[0].legacy_allowed);
 compat.token=compat_token;command_received(&compat);
 assert(sessions[0].legacy_allowed&&sessions[0].legacy_token!=compat_token);
 /* Live permission needs the exact connection and epoch; fast create callback
  * binds before create returns, never a different address/role or reused epoch. */
 sessions[0].stage=CONNECTING;sessions[0].pending=true;publish();
 assert(!lism_input_legacy_allowed(&a));bind_legacy_connection(&b,0);
 assert(!lism_input_legacy_allowed(&b));bind_legacy_connection(&a,0);
 assert(lism_input_legacy_allowed(&a));
 info_role=1;assert(!lism_input_legacy_allowed(&a));info_role=0;
 info_type=2;assert(!lism_input_legacy_allowed(&a));info_type=BT_CONN_TYPE_LE;
 atomic_inc(&sessions[0].epoch);assert(!lism_input_legacy_allowed(&a));
 sessions[0].conn=&a;sessions[0].pending=false;sessions[0].stage=SECURITY;publish();
 assert(lism_input_legacy_allowed(&a));
 /* Actual negotiated SC flag, not absent peer metadata, labels legacy used. */
 secured=(struct event){.type=SECURED,.slot=0,.conn=&a,.epoch=sessions[0].epoch,
  .level=2,.length=16,.complete=false};
 event_received(&secured);assert(sessions[0].legacy_used&&sessions[0].stage==SERVICES_FIND);
 sessions[0].stage=SECURITY;secured.complete=true;event_received(&secured);
 assert(!sessions[0].legacy_used&&sessions[0].stage==SERVICES_FIND);
 sessions[0].stage=SECURITY;secured.length=7;event_received(&secured);
 assert(sessions[0].stage==CLOSING&&!lism_input_legacy_allowed(&a));finish(0);
 /* SMP resolves the exact authorized RPA before legacy key distribution.
  * The synchronous callback migrates only this conn+epoch; publish must not
  * overwrite its pending alias before the coordinator processes the event. */
 sessions[0].conn=&a;sessions[0].target.value=sessions[0].legacy_target.value=77;
 sessions[0].target_known=sessions[0].legacy_allowed=true;
 sessions[0].stage=SECURITY;publish();assert(lism_input_legacy_allowed(&a));
 bt_addr_le_t original_rpa={77},resolved_addr={88};a.address=resolved_addr;
 assert(lism_input_identity_allowed(&a,&resolved_addr));
 identity_resolved(&a,&original_rpa,&resolved_addr);
 assert(observed_security.type==IDENTITY_RESOLVED&&observed_security.complete);
 struct event resolved_event=observed_security;
 assert(lism_input_legacy_allowed(&a)&&sessions[0].target.value==77);
 publish();assert(lism_input_legacy_allowed(&a)&&legacy_policy[0].resolved_valid);
 assert(!lism_input_legacy_allowed(&b));
 unsigned before_identity_events=security_events;
 struct bt_conn unknown={.address={88}};
 identity_resolved(&unknown,&original_rpa,&resolved_addr);
 assert(security_events==before_identity_events&&!lism_input_legacy_allowed(&unknown));
 /* Reject protected IDs before SMP touches an existing key, and callback may
  * not authorize host/split/other generic saved or pending identities. */
 assert(!lism_input_identity_allowed(&a,&protected_split));
 assert(!lism_input_identity_allowed(&a,&hosts[0]));
 sessions[1].have_saved=true;sessions[1].saved.addr.value=90;
 sessions[1].target_known=sessions[1].pending=true;sessions[1].target.value=91;publish();
 bt_addr_le_t other_id={90};assert(!lism_input_identity_allowed(&a,&other_id));
 other_id.value=91;assert(!lism_input_identity_allowed(&a,&other_id));
 a.address=hosts[0];identity_resolved(&a,&original_rpa,&hosts[0]);
 assert(security_events==before_identity_events&&!lism_input_legacy_allowed(&a));
 a.address=resolved_addr;event_received(&resolved_event);
 assert(sessions[0].target.value==88&&sessions[0].legacy_target.value==88);
 assert(!legacy_policy[0].resolved_valid&&lism_input_legacy_allowed(&a));
 /* Unknown/non-generic roles retain upstream identity behavior, never get a
  * grant. A callback from an obsolete epoch cannot migrate the new session. */
 assert(lism_input_identity_allowed(&unknown,&hosts[0]));
 original_rpa.value=88;resolved_addr.value=89;a.address=resolved_addr;
 atomic_inc(&sessions[0].epoch);identity_resolved(&a,&original_rpa,&resolved_addr);
 assert(security_events==before_identity_events&&!lism_input_legacy_allowed(&a));
 a.address.value=77;
 /* A saved grant matches one versioned exact identity and old saves stay strict. */
 struct legacy_grant grant={.version=1,.allowed=1,.addr={77}};
 assert(!settings_set("legacy0",sizeof(grant),settings_copy,&grant));
 loaded[0]=(struct saved_peer){.version=2,.addr={77}};loaded_valid[0]=true;
 loaded_valid[1]=false;legacy_valid=false;memset(sessions,0,sizeof(sessions));
 struct event settings_event={.type=SETTINGS_READY};event_received(&settings_event);
 assert(sessions[0].legacy_allowed&&sessions[0].legacy_target.value==77);
 memset(sessions,0,sizeof(sessions));loaded[0].addr.value=78;event_received(&settings_event);
 assert(!sessions[0].legacy_allowed);
 /* Revoke has no unpair operation. Storage failure revokes the live gate but
  * remains visibly pending; only successful deletion claims persistent disable. */
 sessions[0].saved.addr.value=sessions[0].target.value=sessions[0].legacy_target.value=77;
 sessions[0].legacy_allowed=true;sessions[0].conn=&a;sessions[0].stage=READY;
 legacy_token_refresh(0);publish();assert(lism_input_legacy_allowed(&a));
 before_unpair=unpaired_count;delete_result=-EIO;
 compat=(struct command){.type=LEGACY,.slot=0,.token=sessions[0].legacy_token,.accept=false};
 command_received(&compat);
 assert(!lism_input_legacy_allowed(&a)&&sessions[0].legacy_allowed&&sessions[0].suspended);
 assert(unpaired_count==before_unpair&&strstr(last_message,"Disable save failed"));
 /* Retry and a new exact-target connection cannot undo requested revocation
  * when deletion failed. Only a fresh explicit allow can restore permission. */
 command_received(&retry);sessions[0].stage=CONNECTING;publish();
 assert(!sessions[0].suspended&&!lism_input_legacy_allowed(&a));
 sessions[0].stage=SECURITY;publish();assert(!lism_input_legacy_allowed(&a));
 delete_result=0;compat.token=sessions[0].legacy_token;command_received(&compat);
 assert(!sessions[0].legacy_allowed&&!loaded_grant_valid[0]&&unpaired_count==before_unpair);
 /* OFF fences queued and pending input, retries neutral reports, retains bonds.
  * Fast OFF/ON still closes the old connection, then enables fresh reconnect. */
 memset(sessions,0,sizeof(sessions));
 sessions[0].have_saved=true;sessions[0].saved.addr.value=77;
 sessions[0].epoch=11;sessions[0].input_ready=1;sessions[0].service_count=1;
 sessions[0].stage=READY;sessions[0].conn=&a;
 frames.n=frames.read=0;mouse_frame.data[1]=11;frames.frames[frames.n++]=mouse_frame;
 mouse_frame.data[1]=13;frames.frames[frames.n++]=mouse_frame;
 fail_mouse=1;bridge_input(&wake,NULL);int delivered=pointer_count;
 unsigned before_unpair_off=unpaired_count;int before_create_off=create_calls;
 struct command stale_search={.type=SEARCH,.slot=0,.switch_epoch=disable_generation};
 lism_input_host_set_enabled(false);assert(!lism_input_host_enabled());
 assert(!current_frame(&mouse_frame));
 bridge_input(&wake,NULL);assert(pointer_count==delivered && !k_msgq_num_used_get(&frames));
 lism_input_host_set_enabled(true);control(NULL);
 assert(lism_input_host_enabled() && sessions[0].stage==CLOSING && sessions[0].have_saved);
 search_until=0;command_received(&stale_search);assert(!search_until);
 assert(unpaired_count==before_unpair_off);
 lism_input_host_set_enabled(false);control(NULL);
 struct event late={.type=CONNECTED,.slot=0,.conn=&a};event_received(&late);
 assert(sessions[0].stage==CLOSING&&!sessions[0].input_ready);
 late.type=SECURED;late.epoch=sessions[0].epoch;late.level=2;late.length=16;event_received(&late);
 assert(sessions[0].stage==CLOSING);
 sessions[0].pending=true;connect_target(0);scanner_state();
 assert(create_calls==before_create_off&&!scan_needed&&!advertisements_enabled);
 /* Old queued advertisements cannot revive disabled targets or candidates. */
 advertisement(&advertised);assert(!scan_needed);
 lism_input_host_set_enabled(true);control(NULL);
 memset(sessions,0,sizeof(sessions));search_until=0;
 ui_active=0;struct command hidden_search={.type=SEARCH,.slot=0,.ui_epoch=0,.switch_epoch=disable_generation};
 command_received(&hidden_search);assert(!search_until);
 ui_active=1;ui_generation=1;command_received(&hidden_search);assert(!search_until);
 hidden_search.ui_epoch=1;command_received(&hidden_search);assert(search_until);
 ui_active=0;scanner_state();assert(!search_until && !scan_needed);
 ui_active=1;ui_generation=0;
 puts("PASS: real host sources/services, first keydown, pre-freeze, protected roles, retry, wake cleanup, failed edges, migration/forget and auth identity/token");
}
'''
    harness = prelude + definitions + mocks + "\n".join(function(name) for name in selected) + tests
    (out / "host.c").write_text(harness)
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                    "-Wno-unused-variable", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-I", str(out), "-I", str(root / "src"), str(out / "host.c"),
                    str(root / "src/lism_input_merge.c"), str(root / "src/lism_input_parser.c"),
                    "-o", str(out / "host")], check=True)
    subprocess.run([str(out / "host")], check=True)
    # Default-off build must link without the compat-only protected-registry
    # implementation and cannot revive persisted grants or offer new ones.
    off_mocks = re.sub(r'static bool lism_input_protected_identity\([^\n]*\)\{.*?\n\}',
                       'extern bool lism_input_protected_identity(const bt_addr_le_t*);',
                       mocks, count=1, flags=re.S)
    assert off_mocks != mocks
    off_tests = r'''
int main(void) {
 lism_input_host_set_enabled(true);control(NULL);assert(lism_input_host_enabled());
 struct bt_conn a={.address={77}};
 sessions[0].conn=&a;sessions[0].target=a.address;sessions[0].target_known=true;
 sessions[0].stage=SECURITY;sessions[0].epoch=42;
 sessions[0].legacy_allowed=true;publish();
 assert(!lism_input_legacy_allowed(&a));
 assert(lism_input_identity_allowed(&a,&protected_split));
 unsigned before=security_events;bt_addr_le_t rpa={77},identity={88};
 a.address=identity;identity_resolved(&a,&rpa,&identity);
 assert(security_events==before);
 sessions[0].legacy_allowed=false;sessions[0].legacy_available=true;
 sessions[0].legacy_target=sessions[0].target;sessions[0].legacy_token=123;
 sessions[0].conn=NULL;
 struct command allow={.type=LEGACY,.slot=0,.token=123,.accept=true};
 command_received(&allow);assert(!sessions[0].legacy_allowed);
 assert(strstr(last_message,"not available"));
 memset(sessions,0,sizeof(sessions));
 loaded[0]=(struct saved_peer){.version=2,.addr={77}};loaded_valid[0]=true;
 loaded_grants[0]=(struct legacy_grant){.version=1,.allowed=1,.addr={77}};
 loaded_grant_valid[0]=true;
 struct event setup={.type=SETTINGS_READY};event_received(&setup);
 assert(sessions[0].have_saved&&!sessions[0].legacy_allowed);
 sessions[0].conn=&a;sessions[0].target=a.address;sessions[0].target_known=true;
 sessions[0].stage=SECURITY;sessions[0].epoch=42;
 sessions[0].security.local_failure=true;sessions[0].security.block=LISM_INPUT_SMP_SC_REQUIRED;
 struct event secured={.type=SECURED,.slot=0,.conn=&a,.epoch=42,.level=1,.error=4};
 event_received(&secured);assert(!sessions[0].legacy_available);
 puts("PASS: default-off host links without protected registry; persisted grants, live gate, enable and eligibility remain off");
}
'''
    off_harness = (prelude.replace('#define CONFIG_LISM_INPUT_LEGACY_COMPAT 1',
                                   '#define CONFIG_LISM_INPUT_LEGACY_COMPAT 0') +
                   definitions + off_mocks + "\n".join(function(name) for name in selected) + off_tests)
    (out / "host-off.c").write_text(off_harness)
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                    "-Wno-unused-variable", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-I", str(out), "-I", str(root / "src"), str(out / "host-off.c"),
                    str(root / "src/lism_input_merge.c"), str(root / "src/lism_input_parser.c"),
                    "-o", str(out / "host-off")], check=True)
    subprocess.run([str(out / "host-off")], check=True)
    auto_tests = r'''
int main(void) {
 lism_input_host_set_enabled(true);control(NULL);assert(lism_input_host_enabled());
 struct bt_conn a={.address={77}},other={.address={78}};
 settings_ready=true;search_until=now+60000;search_slot=0;
 candidates[0].public.valid=true;candidates[0].public.token=123;
 candidates[0].addr=a.address;candidates[0].seen=now;candidates[0].connectable_seen=now;
 struct command select={.type=SELECT,.index=0,.token=123};command_received(&select);
 assert(sessions[0].legacy_allowed&&sessions[0].legacy_target.value==77);
 assert(!lism_input_legacy_allowed(&a)); /* not yet the exact live connection */
 sessions[0].conn=&a;sessions[0].stage=SECURITY;sessions[0].epoch=42;publish();
 assert(lism_input_legacy_allowed(&a)&&!lism_input_legacy_allowed(&other));
 assert(lism_input_legacy_key_allowed(0,&a.address));
 assert(lism_input_non_split_identity(&a.address));
 assert(!lism_input_non_split_identity(&other.address));
 assert(!lism_input_legacy_key_allowed(1,&a.address));
 assert(!lism_input_legacy_key_allowed(0,&other.address));
 assert(!lism_input_identity_allowed(&a,&protected_split));
 info_role=1;assert(!lism_input_legacy_allowed(&a));info_role=0;
 struct event sec={.type=SECURED,.slot=0,.conn=&a,.epoch=42,.level=2,.length=16,.complete=false};
 event_received(&sec);assert(sessions[0].stage==SERVICES_FIND);
 sessions[0].stage=SECURITY;sec.complete=true;event_received(&sec);
 assert(sessions[0].stage==SERVICES_FIND&&!sessions[0].legacy_used);
 atomic_inc(&sessions[0].epoch);assert(!lism_input_legacy_key_allowed(0,&a.address));
 memset(sessions,0,sizeof(sessions));memset(legacy_policy,0,sizeof(legacy_policy));
 loaded[0]=(struct saved_peer){.version=2,.addr={77}};loaded_valid[0]=true;
 loaded_valid[1]=false;legacy_valid=false;loaded_grant_valid[0]=false;
 struct event setup={.type=SETTINGS_READY};event_received(&setup);
 assert(sessions[0].have_saved&&sessions[0].legacy_allowed);
 assert(!lism_input_legacy_key_allowed(0,&a.address)); /* no live connection */
 puts("PASS: automatic generic pairing, exact identity/epoch/role, SC first and saved reconnection");
}
'''
    auto_harness = (prelude.replace('#define CONFIG_LISM_INPUT_AUTO_PAIR 0',
                                    '#define CONFIG_LISM_INPUT_AUTO_PAIR 1') + definitions + mocks +
                    "\n".join(function(name) for name in selected) + auto_tests)
    (out / "host-auto.c").write_text(auto_harness)
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                    "-Wno-unused-variable", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-I", str(out), "-I", str(root / "src"), str(out / "host-auto.c"),
                    str(root / "src/lism_input_merge.c"), str(root / "src/lism_input_parser.c"),
                    "-o", str(out / "host-auto")], check=True)
    subprocess.run([str(out / "host-auto")], check=True)
