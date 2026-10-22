"""Execute the production v50 coordinator, with deterministic clocks, grants and host queues."""
from pathlib import Path
import runpy,tempfile,subprocess
P=Path(__file__).resolve().parents[1];b=runpy.run_path(str(P/'tests/kb_route_native_test.py'));pre=b['pre'];hub=b['hub'];function=b['function']
prefix=r'''

static struct k_spinlock state_lock;static struct lism_snapshot physical[2];
static uint8_t current_mode,desired_mode;static int selected=-1;static uint32_t epoch=1;
static bool switching,initialized=true,boot_probe=true;static int64_t confirmed_at,quiet_at,left_seen;
static struct kb_left_control left_control;
static bool awaiting_left_applied;static uint32_t ready_epoch,ready_boot;static uint8_t ready_phase,ready_station=KB_STATION_NONE;
enum station_stage {ST_STABLE,ST_DRAIN,ST_HUNT,ST_PREPARE,ST_READY,ST_LEFT};
static enum station_stage stage=ST_STABLE;
static uint8_t station=KB_STATION_NONE,preferred_station,desired_station=KB_STATION_NONE;
static uint8_t gate_station=KB_STATION_NONE,drain_station=KB_STATION_NONE;
static uint32_t receiver_boot,gate_boot,drain_boot;
static bool selection_pending=true,settings_pending;
static uint8_t hunt_station,offer_station=KB_STATION_NONE;
static uint32_t probe_cookie,offer_boot;
static int64_t hunt_started,hunt_deadline,station_started,next_probe,target_started;
static bool left_disabled=true,monitor_probe;static int64_t blocked_until[2];
static atomic_t requested=-1,fenced,ble_fenced;static bool fence_sent,ble_fence_sent;static uint8_t neutral_queued;
static uint8_t radio_station;static uint32_t radio_boot;
static uint8_t kb_uplink_station_current(void){return radio_station;}
static uint32_t kb_uplink_peer_boot(void){return radio_boot;}
static bool grant_station=true;
static void kb_uplink_station_request(uint8_t s){if(grant_station && s!=radio_station){radio_station=s;radio_boot=0;}}
static unsigned random_value=500;static uint32_t sys_rand32_get(void){return ++random_value;}
static int kb_uplink_send(unsigned s,const struct zmk_split_transport_peripheral_event*e){(void)s;(void)e;return 0;}
static uint32_t kb_private_authority(void){return 42;}
static unsigned commanded_phase,commanded_target;static uint32_t commanded_epoch;
static uint8_t commanded_slot;static uint32_t commanded_boot;
static void kb_left_set_route(uint32_t ep,uint8_t phase,uint8_t target,uint8_t slot,uint32_t boot){commanded_slot=slot;commanded_boot=boot;commanded_epoch=ep;commanded_phase=phase;commanded_target=target;}

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

static void sync_left(void);

'''
main=r'''

static void tick(unsigned ms){now+=ms;route_work(NULL);}
static void left(unsigned phase,unsigned mode,unsigned slot,unsigned boot,uint64_t keys){
 left_control=(struct kb_left_control){.physical={.authority=42,.epoch=epoch,.station=slot,.receiver_boot=boot},.phase=phase,.target=mode,.prepared=1};
 kb_left_pack_keys(&left_control.physical,keys);physical[0].keys=keys;left_seen=now;
}
static void ack(unsigned phase){ready_epoch=epoch;ready_phase=phase;ready_station=radio_station;ready_boot=radio_boot;confirmed_at=now;}
static void offer(unsigned boot){radio_boot=boot;assert(kb_route_probe_offer(radio_station,probe_cookie,boot));route_work(NULL);assert(stage==ST_PREPARE);}
static void candidate(bool with_left){
 if(with_left)left(KB_LEFT_FREEZE,KB_ROUTE_DONGLE,desired_station,receiver_boot,0);
 else now=target_started+2200;
 ack(KB_LEFT_FREEZE);route_work(NULL);assert(stage==ST_READY);
 ack(KB_LEFT_COMMIT);route_work(NULL);
 if(with_left){assert(stage==ST_LEFT);left(KB_LEFT_APPLIED,KB_ROUTE_DONGLE,desired_station,receiver_boot,0);route_work(NULL);}
 assert(stage==ST_STABLE && current_mode==KB_ROUTE_DONGLE && station==radio_station);
}
static void drain_to_hunt(void){
 if(drain_station<2){radio_boot=drain_boot;ack(KB_LEFT_FREEZE);}
 left(KB_LEFT_FREEZE,KB_ROUTE_BLE,KB_STATION_NONE,0,0);
 tick(4600);assert(stage==ST_HUNT);
}
static void choose(unsigned s){requested=30+s;route_work(NULL);assert(stage==ST_DRAIN);drain_to_hunt();}
int main(void){
 current_mode=KB_ROUTE_UNKNOWN;desired_mode=KB_ROUTE_DONGLE;desired_station=0;
 physical[1].keys=1;route_work(NULL);assert(stage==ST_STABLE && !switching);
 physical[1].keys=0;route_work(NULL);assert(stage==ST_DRAIN && switching);
 queue_idle=false;tick(5000);assert(stage==ST_DRAIN);queue_idle=true;
 holdtap_idle=false;route_work(NULL);assert(stage==ST_DRAIN);holdtap_idle=true;
 combo_idle=false;route_work(NULL);assert(stage==ST_DRAIN);combo_idle=true;
 drain_to_hunt();assert(!station_started && !hunt_started);
 tick(1000);assert(hunt_station==0 && !station_started); /* no grant: no fake 900ms window */
 kb_route_probe_transmitted(1,probe_cookie);assert(!station_started);
 kb_route_probe_transmitted(0,probe_cookie-1);assert(!station_started);
 kb_route_probe_transmitted(0,probe_cookie);assert(station_started==now && hunt_started==now);
 assert(!kb_route_probe_offer(1,probe_cookie,200));assert(!kb_route_probe_offer(0,probe_cookie-1,100));
 offer(100);assert(commanded_phase==KB_LEFT_FREEZE);
 /* A READY from another receiver, boot, or phase cannot open a gate. */
 left(KB_LEFT_FREEZE,0,0,100,0);ack(KB_LEFT_FREEZE);ready_station=1;route_work(NULL);assert(stage==ST_PREPARE);
 ack(KB_LEFT_FREEZE);ready_boot=99;route_work(NULL);assert(stage==ST_PREPARE);
 ack(KB_LEFT_COMMIT);route_work(NULL);assert(stage==ST_PREPARE);
 candidate(true);assert(preferred_station==0);
 for(unsigned i=0;i<5;i++){ack(KB_LEFT_COMMIT);tick(1000);assert(station==0 && stage==ST_STABLE && !monitor_probe);}
 /* Manual intent survives a simultaneous application timeout while held. */
 confirmed_at=0;physical[1].keys=1;requested=31;route_work(NULL);assert(desired_station==1 && selection_pending && stage==ST_STABLE);
 physical[1].keys=0;route_work(NULL);assert(desired_station==1 && stage==ST_DRAIN);drain_to_hunt();offer(200);candidate(true);
 choose(0);offer(100);candidate(true);
 /* Manual A->B: old gate cannot be bypassed by a physical all-up alone. */
 requested=31;route_work(NULL);assert(stage==ST_DRAIN && drain_station==0);
 tick(2400);assert(stage==ST_DRAIN); /* current receiver hardware ACK but no app release */
 drain_to_hunt();assert(hunt_station==1);offer(200);candidate(true);assert(preferred_station==1);
 /* The old A returning cannot steal a healthy B session. */
 assert(!kb_route_probe_offer(0,1234,100));ack(KB_LEFT_COMMIT);route_work(NULL);assert(station==1);
 /* Every in-flight stage keeps its tuple while held and queues a new intent. */
 for(unsigned st=ST_PREPARE;st<=ST_LEFT;st++){
  choose(0);offer(100);stage=st;
  uint32_t old_epoch=epoch,old_boot=receiver_boot;uint8_t old_slot=desired_station;
  physical[1].keys=1;requested=31;route_work(NULL);
  assert(epoch==old_epoch && desired_station==old_slot && receiver_boot==old_boot && requested==31);
  physical[1].keys=0;route_work(NULL);assert(stage==ST_DRAIN && desired_station==1 && epoch!=old_epoch);
  drain_to_hunt();offer(200);candidate(true);
 }
 /* Left can disappear just after COMMIT. It must not trap the right forever. */
 choose(0);offer(100);left(KB_LEFT_FREEZE,0,0,100,0);ack(0);route_work(NULL);ack(1);route_work(NULL);assert(stage==ST_LEFT);
 tick(2201);assert(stage==ST_DRAIN && commanded_phase==KB_LEFT_FREEZE);
 /* Silent left stays FREEZE: lease wait, right-only commit, then real rejoin. */
 radio_live=false;tick(4600);assert(stage==ST_HUNT);offer(100);candidate(false);
 assert(left_disabled && commanded_phase==KB_LEFT_FREEZE);
 left(KB_LEFT_FREEZE,0,0,100,0);ack(1);route_work(NULL);assert(!left_disabled && commanded_phase==KB_LEFT_COMMIT);
 /* Receiver loss searches another station; no two active data routes. */
 confirmed_at=0;route_work(NULL);assert(stage==ST_DRAIN);tick(4600);assert(stage==ST_HUNT);
 kb_route_probe_transmitted(hunt_station,probe_cookie);tick(901);assert(hunt_station==1);offer(200);candidate(false);
 /* Same station receiver reboot: hardware ACK persists, application boot
  * changed. Presence proof must break DRAIN, not wait forever on RF ACK. */
 radio_live=true;confirmed_at=0;route_work(NULL);assert(stage==ST_DRAIN);
 uint32_t old_receiver=drain_boot;assert(old_receiver==200);
 tick(5000);assert(stage==ST_DRAIN);
 assert(!kb_route_probe_offer(radio_station^1,probe_cookie,old_receiver+1));
 assert(!kb_route_probe_offer(radio_station,probe_cookie-1,old_receiver+1));
 assert(kb_route_probe_offer(radio_station,probe_cookie,old_receiver));route_work(NULL);assert(stage==ST_DRAIN);
 radio_boot=old_receiver+1;assert(kb_route_probe_offer(radio_station,probe_cookie,radio_boot));
 route_work(NULL);assert(stage==ST_HUNT);offer(201);candidate(false);
 /* No dongle -> direct BLE. Late receiver is display-only, never steals. */
 requested=KB_ROUTE_BLE;route_work(NULL);drain_station=KB_STATION_NONE;tick(2300);assert(current_mode==KB_ROUTE_BLE && stage==ST_STABLE && left_disabled);
 confirmed_at=0;route_work(NULL);assert(monitor_probe);offer_station=hunt_station;offer_boot=300;radio_boot=300;route_work(NULL);assert(current_mode==KB_ROUTE_BLE && stage==ST_STABLE);
 /* Real neutral queue backpressure blocks a host profile change. */
 requested=14;fail_neutral=1;route_work(NULL);tick(5000);assert(stage==ST_DRAIN && profile==0);fail_neutral=0;drain_station=KB_STATION_NONE;route_work(NULL);assert(profile==4 && current_mode==KB_ROUTE_BLE);
 puts("PASS actual v50 worker: A/B keeping/manual/failover, slot/boot ACK, physical holds/new intent, grants, silent-left lease/right-only/rejoin, left loss after COMMIT, BLE mirror no steal, macro fences and neutral backpressure");
}

'''
names=['finish_commit_locked','dongle_confirmed','all_released','kb_route_probe_offer','kb_route_probe_transmitted','probe_window','send_probe','left_prepared','neutral_ble','begin_transaction','application_fenced','prepare_local_target','sync_left','route_work']
code=pre+prefix+'\n'.join(function(hub,n) for n in names)+main
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'mode.c';out=Path(t)/'mode';f.write_text(code);subprocess.run(['cc','-std=gnu11','-Wall','-fsanitize=address,undefined',str(f),'-o',str(out)],check=True);subprocess.run([str(out)],check=True)
