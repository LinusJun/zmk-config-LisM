"""Execute the actual mode worker with deterministic queues, fences and clocks."""
from pathlib import Path
import runpy,tempfile,subprocess
P=Path(__file__).resolve().parents[1];b=runpy.run_path(str(P/'tests/kb_route_native_test.py'));pre=b['pre'];hub=b['hub'];function=b['function']
prefix=r'''
static struct k_spinlock state_lock;static struct lism_snapshot physical[2];
static uint8_t current_mode,desired_mode;static int selected=-1;static uint32_t epoch=1;
static bool switching,initialized=true,boot_probe=true;static int64_t boot_deadline,confirmed_at,quiet_at;
static atomic_t requested=-1,fenced,ble_fenced;static bool fence_sent,ble_fence_sent;static uint8_t neutral_queued;
static int64_t left_seen;static struct kb_left_control left_control;
static bool target_announced,awaiting_left_applied;static uint32_t ready_epoch;static uint8_t ready_phase;
static uint32_t kb_private_authority(void){return 42;}
static unsigned commanded_phase,commanded_target;static uint32_t commanded_epoch;
static void kb_left_set_route(uint32_t ep,uint8_t phase,uint8_t target){commanded_epoch=ep;commanded_phase=phase;commanded_target=target;}

struct k_work {int x;};struct work {struct k_work work;};static struct work worker;static int hog_work_q,hog_fence_work,fence_dev;
#define K_MSEC(x) (x)
#define INPUT_EV_MSC 4
static bool atomic_cas(atomic_t*p,int old,int n){if(*p!=old)return false;*p=n;return true;}
static void atomic_clear(atomic_t*p){*p=0;}
static void run_commands(void){}
static bool radio_live=true;static bool kb_uplink_connected(void){return radio_live;}
static int k_work_reschedule(void*w,int t){(void)w;(void)t;return 0;}
static unsigned profile,commits,resets;static int fail_neutral;static bool queue_idle=true,holdtap_idle=true,combo_idle=true,inject_profile_key;
static void kb_uplink_reset(void){resets++;}
static void emit_snapshot(unsigned s,bool empty){(void)s;assert(empty);}
static unsigned publishes;static void publish(void){publishes++;}
static void kb_route_ble_refresh(void){}
static int input_report(void*d,int a,int b,int c,bool sync,int t){(void)d;(void)a;(void)b;(void)c;(void)sync;(void)t;fenced=1;return 0;}
static int k_work_submit_to_queue(void*q,void*w){(void)q;(void)w;ble_fenced=1;return 0;}
static bool kb_route_queue_idle(void){return queue_idle;}
static bool kb_route_holdtap_idle(void){return holdtap_idle;}
static bool kb_route_combo_idle(void){return combo_idle;}
static unsigned zmk_ble_active_profile_index(void){return profile;}
static int zmk_ble_prof_select(unsigned n){assert(n<5);profile=n;if(inject_profile_key)physical[1].keys=1;return 0;}
static int settings_save_one(const char*n,const void*p,size_t l){(void)n;(void)p;(void)l;commits++;return 0;}
static void disconnect_host(void*c,void*a){(void)c;(void)a;}
#define BT_CONN_TYPE_LE 1
static void bt_conn_foreach(int t,void(*fn)(void*,void*),void*a){(void)t;(void)fn;*(unsigned*)a=0;}
struct zmk_hid_keyboard_report_body{uint8_t x[8];};struct zmk_hid_consumer_report_body{uint8_t x[4];};struct zmk_hid_mouse_report_body{uint8_t x[6];};
struct key_report{struct zmk_hid_keyboard_report_body body;} key;
struct consumer_report{struct zmk_hid_consumer_report_body body;} consumer;
struct mouse_report{struct zmk_hid_mouse_report_body body;} mouse;
static void zmk_hid_keyboard_clear(void){memset(&key,0,sizeof(key));}static void zmk_hid_consumer_clear(void){memset(&consumer,0,sizeof(consumer));}static void zmk_hid_mouse_clear(void){memset(&mouse,0,sizeof(mouse));}
static struct key_report*zmk_hid_get_keyboard_report(void){return &key;}static struct consumer_report*zmk_hid_get_consumer_report(void){return &consumer;}static struct mouse_report*zmk_hid_get_mouse_report(void){return &mouse;}
int zmk_hog_send_keyboard_report_to_profile(struct zmk_hid_keyboard_report_body*r,uint8_t p){assert(p==profile);for(size_t i=0;i<sizeof(*r);i++)assert(!((uint8_t*)r)[i]);return fail_neutral?-EAGAIN:0;}
int zmk_hog_send_consumer_report_to_profile(struct zmk_hid_consumer_report_body*r,uint8_t p){assert(p==profile);(void)r;return fail_neutral?-EAGAIN:0;}
int zmk_hog_send_mouse_report_to_profile(struct zmk_hid_mouse_report_body*r,uint8_t p){assert(p==profile);(void)r;return fail_neutral?-EAGAIN:0;}
'''
main=r'''
static void left(unsigned phase,unsigned target,uint64_t keys){
 left_control=(struct kb_left_control){.physical={.authority=42,.epoch=epoch},.phase=phase,.target=target};
 kb_left_pack_keys(&left_control.physical,keys);physical[0].keys=keys;left_seen=now;
}
static void ack(unsigned phase){ready_epoch=epoch;ready_phase=phase;confirmed_at=now;}
static void wait_fences(void){now+=1800;route_work(NULL);}
int main(void){
 current_mode=KB_ROUTE_UNKNOWN;desired_mode=KB_ROUTE_DONGLE;
 route_work(NULL);assert(boot_probe&&!switching);boot_deadline=now+4500;confirmed_at=now;
 physical[1].keys=1;route_work(NULL);assert(!switching&&!boot_probe);physical[1].keys=0;
 route_work(NULL);assert(switching && commanded_phase==KB_LEFT_FREEZE);
 /* Manual BLE request wins, but starts its own quiet window and announcement. */
 requested=KB_ROUTE_BLE;route_work(NULL);assert(switching);wait_fences();assert(target_announced&&switching);
 route_work(NULL);assert(!switching&&current_mode==KB_ROUTE_BLE&&commits==1);
 confirmed_at=now;route_work(NULL);assert(commits==1&&current_mode==KB_ROUTE_BLE);
 /* Held left blocks initial handoff. Real release permits PREPARE. */
 left(KB_LEFT_APPLIED,KB_ROUTE_BLE,2);requested=KB_ROUTE_DONGLE;route_work(NULL);assert(!switching);
 left(KB_LEFT_APPLIED,KB_ROUTE_BLE,0);fail_neutral=1;route_work(NULL);assert(switching&&neutral_queued!=7);
 left(KB_LEFT_FREEZE,KB_ROUTE_DONGLE,0);now+=2000;route_work(NULL);assert(!target_announced);
 fail_neutral=0;queue_idle=false;route_work(NULL);assert(switching);queue_idle=true;
 holdtap_idle=false;route_work(NULL);assert(switching);holdtap_idle=true;
 combo_idle=false;route_work(NULL);assert(switching);combo_idle=true;
 route_work(NULL);assert(target_announced&&switching);
 /* PREPARE echo, stale COMMIT, and lost COMMIT cannot admit left data. */
 ack(KB_LEFT_FREEZE);route_work(NULL);assert(switching&&commanded_phase==KB_LEFT_FREEZE);
 ready_epoch=epoch-1;ready_phase=KB_LEFT_COMMIT;route_work(NULL);assert(switching);
 ack(KB_LEFT_COMMIT);route_work(NULL);assert(!switching&&current_mode==KB_ROUTE_DONGLE&&commits==2&&commanded_phase==KB_LEFT_COMMIT);
 /* Left held then loses power: observation expiry permits right H, while
  * output cleanup is still protected by the old-owner neutral/fence path. */
 left(KB_LEFT_APPLIED,KB_ROUTE_DONGLE,4);requested=KB_ROUTE_BLE;route_work(NULL);assert(!switching);
 now+=1501;route_work(NULL);assert(switching&&!physical[0].keys);
 now+=3000;route_work(NULL);assert(switching&&!target_announced); /* old DG long macro >1700ms */
 ack(KB_LEFT_FREEZE);route_work(NULL);assert(target_announced&&switching);
 route_work(NULL);assert(current_mode==KB_ROUTE_BLE&&!switching);
 requested=14;route_work(NULL);wait_fences();inject_profile_key=true;route_work(NULL);
 assert(switching&&profile==4&&physical[1].keys);inject_profile_key=false;physical[1].keys=0;route_work(NULL);assert(!switching&&profile==4);
 puts("PASS actual mode worker: boot preference, manual priority, no late steal, real release, BLE neutral retry, macro/holdtap/combo fence, target COMMIT application ACK and stale/lost ACK, left held power-off recovery, profile final-key race");
}

'''
code=pre+(P/'src/kb_route/auto_policy.h').read_text()+prefix+'\n'.join(function(hub,n) for n in ['dongle_confirmed','all_released','route_work'])+main
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'mode.c';out=Path(t)/'mode';f.write_text(code);subprocess.run(['cc','-std=gnu11','-fsanitize=address,undefined',str(f),'-o',str(out)],check=True);subprocess.run([str(out)],check=True)
