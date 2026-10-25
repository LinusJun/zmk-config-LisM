"""Actual mirror codec/emitter/cache and receiver RSSI source selection.

No RF/controller/LVGL is modeled; UI callbacks are counters. The old mirror's
13-byte layout and old receiver's truthy online interpretation are asserted.
"""
from pathlib import Path
import hashlib,json,subprocess,sys,tempfile
S=Path(__file__).resolve().parents[1];P=S.parent;V=P/'verification';sys.path.insert(0,str(S/'tools'))
from prepare_hog_routing import function
mirror=S/'src/kb_route/mirror_display.c';radio=S/'src/esb_upstream/radio.c';emitter=S/'src/kb_route/mirror_hub.c'
strip=lambda s:'\n'.join(x for x in s.splitlines() if not x.startswith('#include'))
pre=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stddef.h>
#include <string.h>
#include <kb_route/mirror.h>
#include <kb_route/signal.h>
#include <kb_route/view.h>
#define ZMK_KEYMAP_LAYERS_LEN 7
#define LV_UNUSED(x) (void)(x)
#define ARG_UNUSED(x) (void)(x)
#define BUILD_ASSERT(x) _Static_assert(x,#x)
struct k_spinlock{int held;};typedef int k_spinlock_key_t;
static int k_spin_lock(struct k_spinlock*l){assert(!l->held);l->held=1;return 0;}
static void k_spin_unlock(struct k_spinlock*l,int k){(void)k;assert(l->held);l->held=0;}
static int64_t now=1000;static int64_t k_uptime_get(void){return now;}
static struct kb_route_view route={.mode=KB_ROUTE_DONGLE,.last_mode=KB_ROUTE_DONGLE,.epoch=7};
void kb_route_snapshot(struct kb_route_view*out){*out=route;}
typedef int lv_timer_t;
static void*lv_timer_create(void(*fn)(lv_timer_t*),unsigned period,void*data){assert(fn&&period==50&&!data);return NULL;}
static unsigned callbacks;
void kb_mirror_wpm_refresh(void){callbacks++;}void kb_mirror_layer_refresh(void){callbacks++;}
void kb_mirror_mods_refresh(void){callbacks++;}void kb_mirror_locks_refresh(void){callbacks++;}
static struct k_spinlock signal_lock;
static struct {int8_t dbm;int64_t seen;} signals[2];
static bool emission_enabled=true,physical_online=true,private_valid=true;
static int8_t private_dbm=-61;static unsigned private_reads,emissions;
static struct kb_mirror emitted;
static bool kb_route_mirror_enabled(void){return emission_enabled;}
static void kb_route_left_telemetry(uint8_t*b,uint8_t*o){*b=81;*o=physical_online;}
bool kb_private_left_signal(int8_t*d){private_reads++;*d=private_dbm;return private_valid;}
static struct{struct{uint8_t modifiers;}body;} hid={.body.modifiers=0x88};
static __typeof__(hid)*zmk_hid_get_keyboard_report(void){return &hid;}
static uint8_t zmk_wpm_get_state(void){return 77;}
static uint8_t zmk_keymap_highest_layer_active(void){return 2;}
static uint8_t zmk_hid_indicators_get_current_profile(void){return 3;}
static uint8_t zmk_ble_active_profile_index(void){return 4;}
static bool zmk_ble_active_profile_is_connected(void){return true;}
struct zmk_split_transport_peripheral_event{int type;uint8_t data[24];};
static int kb_uplink_send(unsigned source,const struct zmk_split_transport_peripheral_event*e){assert(source==1&&e->type==125);memcpy(&emitted,&e->data,sizeof(emitted));emissions++;return 0;}
'''
main=r'''
static void codec_cases(void){
 _Static_assert(sizeof(struct kb_mirror)==13,"legacy wire length");_Static_assert(offsetof(struct kb_mirror,left_online)==12,"legacy online offset");
 for(unsigned status=0;status<256;status++){
  bool canonical=status==0||((status&1)&&(status>>1)<127),has=canonical&&(status>>1)>0;int8_t dbm=42;
  assert(kb_mirror_left_status_valid(status)==canonical);assert(kb_mirror_left_online(status)==(canonical&&(status&1)));
  assert(kb_mirror_left_rssi(status,&dbm)==has);if(has)assert(dbm==-(int)(status>>1));else assert(dbm==42);
  assert(!kb_mirror_left_rssi(status,NULL));
 }
 for(int mag=1;mag<=126;mag++){
  uint8_t status=kb_mirror_left_status(true,true,-mag);int8_t dbm=0;
  assert(status==(1|(mag<<1))&&status&&kb_mirror_left_rssi(status,&dbm)&&dbm==-mag);
  assert(kb_mirror_left_status(false,true,-mag)==0); /* Old receiver truthiness stays offline. */
 }
 assert(kb_mirror_left_status(true,false,-61)==1&&kb_mirror_left_status(true,true,0)==1&&kb_mirror_left_status(true,true,-127)==1&&kb_mirror_left_status(true,true,-128)==1&&kb_mirror_left_status(true,true,1)==1);
 puts("PASS all256 status bytes, 126 measured values, legacy0/1 and offline truthiness; mirror version1 size13 offset12 unchanged");
}
static void receiver_cases(void){
 int8_t dbm=0;signals[0].dbm=-45;signals[0].seen=now;signals[1].dbm=-66;signals[1].seen=now;
 assert(kb_esb_signal(0,&dbm)&&dbm==-45);assert(kb_esb_signal(1,&dbm)&&dbm==-66);assert(!kb_esb_signal(2,&dbm)&&!kb_esb_signal(0,NULL));
 route.mode=route.last_mode=KB_ROUTE_BLE;
 struct kb_mirror m={.epoch=7,.version=1,.profile=4,.layer=2,.host_connected=1,.left_battery=81,.left_online=kb_mirror_left_status(true,true,-61)};
 kb_mirror_received(&m);refresh(NULL);assert(source==1&&callbacks==4&&kb_esb_signal(0,&dbm)&&dbm==-61);
 assert(kb_esb_signal(1,&dbm)&&dbm==-66);unsigned before=callbacks;
 m.left_online=kb_mirror_left_status(true,true,-72);kb_mirror_received(&m);refresh(NULL);assert(callbacks==before&&kb_esb_signal(0,&dbm)&&dbm==-72);
 m.left_online=0;kb_mirror_received(&m);refresh(NULL);assert(callbacks==before+4&&!kb_esb_signal(0,&dbm));
 m.left_online=1;kb_mirror_received(&m);refresh(NULL);assert(callbacks==before+8&&!kb_esb_signal(0,&dbm)); /* Legacy sender, not a fabricated signal. */
 for(unsigned i=0;i<3;i++){m.left_online=(uint8_t[]){2,254,255}[i];int64_t prior=received_at;now++;kb_mirror_received(&m);assert(received_at==prior);refresh(NULL);assert(callbacks==before+8&&!kb_esb_signal(0,&dbm));}
 m.left_online=kb_mirror_left_status(true,true,-55);m.wpm=52;kb_mirror_received(&m);refresh(NULL);assert(callbacks==before+12&&kb_esb_signal(0,&dbm)&&dbm==-55);
 now+=1200;signals[0].seen=signals[1].seen=now;refresh(NULL);assert(source==-1&&!kb_esb_signal(0,&dbm)&&kb_esb_signal(1,&dbm));
 route.mode=route.last_mode=KB_ROUTE_DONGLE;route.epoch=8;refresh(NULL);assert(source==0&&kb_esb_signal(0,&dbm)&&dbm==-45);
 puts("PASS actual mirror receive/refresh + actual kb_esb_signal: remoteL overrides direct cache, stale/legacy/invalid dark, DG local restored, R unchanged; RSSI-only no widget callbacks, online/WPM transitions still refresh");
}
static void emitter_cases(void){
 unsigned before=emissions;kb_mirror_emit();int8_t dbm=0;
 assert(emissions==before+1&&emitted.version==1&&emitted.epoch==8&&emitted.wpm==77&&emitted.layer==2&&emitted.mods==0x88&&emitted.indicators==3&&emitted.profile==4&&emitted.host_connected&&emitted.left_battery==81);
 assert(kb_mirror_left_rssi(emitted.left_online,&dbm)&&dbm==-61&&emitted.left_online); /* Legacy receiver sees online. */
 private_valid=false;kb_mirror_emit();assert(emissions==before+2&&emitted.left_online==1);
 physical_online=false;unsigned reads=private_reads;kb_mirror_emit();assert(emissions==before+3&&emitted.left_online==0&&private_reads==reads);
 emission_enabled=false;kb_mirror_emit();assert(emissions==before+3);
 puts("PASS actual existing emitter sends once per call only, same fields/type/size, no private read offline or emission disabled; legacy online/battery semantics unchanged");
}
int main(void){codec_cases();receiver_cases();emitter_cases();return 0;}
'''
code=pre+strip(mirror.read_text())+function(radio.read_text(),'kb_esb_signal')+function(emitter.read_text(),'kb_mirror_emit')+main
tracked=[Path(__file__),mirror,radio,emitter,S/'src/kb_route/mirror.h',S/'src/kb_route/signal.h'];hashes={str(p.relative_to(P)):hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked}
with tempfile.TemporaryDirectory(prefix='mirror-signal-') as td:
 d=Path(td);(d/'test.c').write_text(code);r=subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(S/'src'),str(d/'test.c'),'-o',str(d/'test')],capture_output=True,text=True);logs=[r.stdout+r.stderr];compile_rc=r.returncode
 if not compile_rc:r=subprocess.run([str(d/'test')],capture_output=True,text=True);logs.append(r.stdout+r.stderr)
 log=V/'mirror-signal-v50417.log';log.write_text(''.join(logs));print(''.join(logs),end='');changed=[n for n,h in hashes.items() if hashlib.sha256((P/n).read_bytes()).hexdigest()!=h];rc=r.returncode or bool(changed)
 (V/'mirror-signal-v50417-result.json').write_text(json.dumps({'passed':rc==0,'exit_code':int(rc),'compile_exit_code':compile_rc,'source_sha256':hashes,'changed_during_run':changed,'log':log.name,'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'boundary':'Actual header codec, mirror_display whole TU, actual receiver kb_esb_signal and emitter kb_mirror_emit. Provider/clock/lock/UI-callback/transport mocks; no radio/controller/LVGL physical timing. ASanUBSan all extracted C.'},indent=2)+'\n');raise SystemExit(rc)
