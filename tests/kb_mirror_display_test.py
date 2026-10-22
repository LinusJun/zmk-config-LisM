"""Actual telemetry cache + actual generated widget callbacks, no HID mutations."""
from pathlib import Path
import runpy,tempfile,subprocess
P=Path(__file__).resolve().parents[1];b=runpy.run_path(str(P/'tests/kb_route_native_test.py'));pre=b['pre'];function=b['function'];mirror=(P/'src/kb_route/mirror_display.c').read_text();D=Path('/work/workspace/display-dongle/boards/shields/prospector_adapter/src/layouts/operator')
pre+=(P/'src/kb_route/mirror.h').read_text()+r'''
#define ZMK_KEYMAP_LAYERS_LEN 6
#define LV_UNUSED(x) (void)(x)
typedef int lv_timer_t;typedef int lv_obj_t;typedef int lv_color_t;
static struct k_spinlock lock;static struct kb_mirror received,shown;static int64_t received_at;static int source;
static struct kb_route_view view;
void kb_route_snapshot(struct kb_route_view*out){*out=view;}
void kb_mirror_wpm_refresh(void);void kb_mirror_layer_refresh(void);void kb_mirror_mods_refresh(void);void kb_mirror_locks_refresh(void);
'''
code=pre+'\n'.join(function(mirror,n) for n in ['kb_mirror_received','kb_mirror_snapshot','refresh'])+r'''
#define LV_PART_MAIN 0
#define COLOR_CAPS_ACTIVE 0xffbf00
#define COLOR_NUM_ACTIVE 0xb1e5f0
#define COLOR_INACTIVE 0x3b527c
#define CONFIG_PROSPECTOR_LAYER_NAME_UPPERCASE 0
#define DISPLAY_COLOR_LAYER_DOT_ACTIVE 1
#define DISPLAY_COLOR_LAYER_DOT_INACTIVE 0
#define LAYER_DOT_COUNT 6
#define HID_INDICATOR_CAPS_LOCK 2
#define HID_INDICATOR_NUM_LOCK 1
#define K_MSEC(x) (x)
#define SYS_SLIST_FOR_EACH_CONTAINER(list,item,node) for(item=(void*)&fixture;item!=NULL;item=NULL)
enum modifier_type{MOD_TYPE_GUI,MOD_TYPE_ALT,MOD_TYPE_CTRL,MOD_TYPE_SHIFT};
static int modifier_order_get(int i){return i;}
struct zmk_widget_modifier_indicator{lv_obj_t *mod_labels[4];};struct zmk_widget_layer_display{lv_obj_t*dots[6];};struct zmk_widget_output{int x;};
static struct {lv_obj_t*labels[6];} fixture;static lv_obj_t ids[6];
static bool mod_colors[4];static int dot_colors[6];static uint32_t cap_color,num_color;
static void set_modifier_color(lv_obj_t *l,bool active){mod_colors[l-ids]=active;}
static int lv_color_hex(int color){return color;}
static void lv_obj_set_style_bg_color(lv_obj_t*l,int color,int part){(void)part;dot_colors[l-ids]=color;}
static lv_obj_t *caps_label=(lv_obj_t*)1,*num_label=(lv_obj_t*)2;
static void lv_obj_set_style_text_color(lv_obj_t*l,int color,int part){(void)part;if(l==caps_label)cap_color=color;else if(l==num_label)num_color=color;else assert(0);}
static bool create_lock_indicator_row(void){return true;}static int deferred_init_work;
static int k_work_reschedule(void*w,int t){(void)w;(void)t;return 0;}
struct modifier_indicator_state{bool mods[4];};struct layer_display_state{uint8_t index;};struct lock_indicator_state{uint8_t indicators;};struct wpm_meter_state{uint8_t wpm;};
static float target_wpm;static int wpm_smooth_work;
static int k_work_schedule(void*w,int t){(void)w;(void)t;return 0;}
#define ZMK_TRANSPORT_NONE 0
#define ZMK_TRANSPORT_USB 1
#define ZMK_TRANSPORT_BLE 2
struct zmk_endpoint_instance{int transport;};static int active_transport=1;static uint8_t active_profile_index=2;static unsigned output_refreshes;
static struct zmk_endpoint_instance zmk_endpoint_get_selected(void){return(struct zmk_endpoint_instance){.transport=ZMK_TRANSPORT_USB};}
static uint8_t zmk_ble_active_profile_index(void){return 2;}
static void update_output_widget(struct zmk_widget_output*w){(void)w;output_refreshes++;}
'''
for filename,fn in [('wpm_meter.c','wpm_meter_update_cb'),('modifier_indicator.c','modifier_indicator_update_cb'),('layer_display.c','layer_display_update_cb')]:code+=function((D/filename).read_text(),fn)
code+=function((P/'src/prospector_lock_indicators.c').read_text(),'lock_indicator_update_cb')+function((P/'src/esb_upstream/host_output.c').read_text(),'output_refresh')+r'''
void kb_mirror_wpm_refresh(void){wpm_meter_update_cb((struct wpm_meter_state){.wpm=0});}
void kb_mirror_layer_refresh(void){layer_display_update_cb((struct layer_display_state){.index=0});}
void kb_mirror_mods_refresh(void){modifier_indicator_update_cb((struct modifier_indicator_state){.mods={1,1,1,1}});}
void kb_mirror_locks_refresh(void){lock_indicator_update_cb((struct lock_indicator_state){.indicators=0});}
int main(void){
 for(int i=0;i<6;i++)fixture.labels[i]=&ids[i];view=(struct kb_route_view){.mode=KB_ROUTE_BLE,.last_mode=KB_ROUTE_BLE,.epoch=7};
 struct kb_mirror m={.epoch=7,.version=1,.wpm=74,.layer=2,.mods=0x88,.indicators=3,.profile=4,.host_connected=1};
 kb_mirror_received(&m);struct kb_mirror copy;assert(kb_mirror_snapshot(&copy)==0); /* packet before UI init is cached only */
 refresh(NULL);assert(kb_mirror_snapshot(&copy)==1&&copy.wpm==74&&target_wpm==74);assert(mod_colors[0]&&!mod_colors[1]&&!mod_colors[2]&&!mod_colors[3]);assert(dot_colors[2]&&!dot_colors[0]);assert(cap_color==COLOR_CAPS_ACTIVE&&num_color==COLOR_NUM_ACTIVE);output_refresh(NULL);assert(active_transport==ZMK_TRANSPORT_BLE&&active_profile_index==4);
 /* Local queued display events cannot overwrite the current remote owner. */
 wpm_meter_update_cb((struct wpm_meter_state){.wpm=199});modifier_indicator_update_cb((struct modifier_indicator_state){.mods={0,1,1,1}});layer_display_update_cb((struct layer_display_state){.index=5});lock_indicator_update_cb((struct lock_indicator_state){.indicators=0});assert(target_wpm==74&&mod_colors[0]&&!mod_colors[1]&&dot_colors[2]&&!dot_colors[5]&&cap_color==COLOR_CAPS_ACTIVE);
 m.epoch=6;m.wpm=200;kb_mirror_received(&m);refresh(NULL);assert(target_wpm==74);m.epoch=7;m.profile=5;kb_mirror_received(&m);refresh(NULL);assert(active_profile_index==4);
 now+=1201;refresh(NULL);assert(kb_mirror_snapshot(&copy)==-1&&copy.layer==255&&target_wpm==0);assert(!mod_colors[0]);output_refresh(NULL);assert(active_transport==ZMK_TRANSPORT_NONE&&active_profile_index==255);
 view.mode=KB_ROUTE_UNKNOWN;refresh(NULL);assert(kb_mirror_snapshot(&copy)==-1); /* never fallback to local BASE on RF loss */
 view.mode=KB_ROUTE_BLE;m.profile=0;m.wpm=42;m.indicators=2;kb_mirror_received(&m);refresh(NULL);assert(target_wpm==42&&cap_color==COLOR_CAPS_ACTIVE&&num_color==COLOR_INACTIVE);output_refresh(NULL);assert(active_profile_index==0);
 view.mode=view.last_mode=KB_ROUTE_DONGLE;view.epoch=8;refresh(NULL);assert(kb_mirror_snapshot(&copy)==0);wpm_meter_update_cb((struct wpm_meter_state){.wpm=11});assert(target_wpm==11);output_refresh(NULL);assert(active_transport==ZMK_TRANSPORT_USB&&active_profile_index==2);
 puts("PASS actual telemetry + generated widget callbacks: early packet, epoch/profile validation, remote WPM/modifiers/layer/locks/profile, local-event suppression, stale blanking, local source restore; no HID/keymap/profile setters linked");return 0;}
'''
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'mirror.c';out=Path(t)/'mirror';f.write_text(code);subprocess.run(['cc','-std=gnu11','-fsanitize=address,undefined',str(f),'-o',str(out)],check=True);subprocess.run([str(out)],check=True)

# Actual right-hand emitter fed by the pinned HID implementation's final mask.
# Implicit LS/LG and masked modifiers differ from explicit key state.
hid=Path('/deps/zmk/app/src/hid.c').read_text()
emitter=(P/'src/kb_route/mirror_hub.c').read_text()
hid_fixture=b['pre']+r'''
void kb_route_left_telemetry(uint8_t*b,uint8_t*o){*b=81;*o=1;}
'''+(P/'src/kb_route/mirror.h').read_text()+r'''
typedef uint8_t zmk_mod_flags_t;typedef uint8_t zmk_mod_t;
#define LOG_DBG(...) ((void)0)
#define LOG_ERR(...) ((void)0)
#define WRITE_BIT(v,b,on) do{if(on)(v)|=BIT(b);else(v)&=~BIT(b);}while(0)
struct zmk_hid_keyboard_report{struct{uint8_t modifiers;}body;};
static struct zmk_hid_keyboard_report keyboard_report;
static struct kb_mirror emitted;static unsigned emissions;static bool ble_enabled=true;
static bool kb_route_mirror_enabled(void){return ble_enabled;}
void kb_route_snapshot(struct kb_route_view*v){v->epoch=7;}
static uint8_t zmk_wpm_get_state(void){return 52;}
static uint8_t zmk_keymap_highest_layer_active(void){return 2;}
static uint8_t zmk_hid_indicators_get_current_profile(void){return 3;}
static uint8_t zmk_ble_active_profile_index(void){return 4;}
static bool zmk_ble_active_profile_is_connected(void){return true;}
static int kb_uplink_send(unsigned s,const struct zmk_split_transport_peripheral_event*e){assert(s==1&&e->type==125);memcpy(&emitted,&e->data,sizeof(emitted));emissions++;return 0;}
'''
hid_fixture+=hid[hid.index('static int explicit_modifier_counts'):hid.index('zmk_mod_flags_t zmk_hid_get_explicit_mods')]
for fn in ['zmk_hid_get_explicit_mods','zmk_hid_register_mod','zmk_hid_unregister_mod','zmk_hid_implicit_modifiers_press','zmk_hid_implicit_modifiers_release','zmk_hid_masked_modifiers_set','zmk_hid_masked_modifiers_clear','zmk_hid_get_keyboard_report']:hid_fixture+=function(hid,fn)
hid_fixture+=function(emitter,'kb_mirror_emit')+r'''
int main(void){
 zmk_hid_implicit_modifiers_press(BIT(1)|BIT(3));kb_mirror_emit();assert(!zmk_hid_get_explicit_mods()&&emitted.mods==10);
 zmk_hid_register_mod(0);zmk_hid_masked_modifiers_set(BIT(0));kb_mirror_emit();assert(zmk_hid_get_explicit_mods()==1&&emitted.mods==10);
 zmk_hid_implicit_modifiers_release();kb_mirror_emit();assert(emitted.mods==0);
 zmk_hid_masked_modifiers_clear();kb_mirror_emit();assert(emitted.mods==1);
 assert(emitted.epoch==7&&emitted.wpm==52&&emitted.layer==2&&emitted.indicators==3&&emitted.profile==4&&emitted.host_connected);
 zmk_hid_unregister_mod(0);kb_mirror_emit();assert(!emitted.mods);
 ble_enabled=false;kb_mirror_emit();assert(emissions==5);
 puts("PASS actual mirror emitter + pinned HID modifier implementation: implicit LS/LG, masked explicit, mask clear and release match final HID report");return 0;}
'''
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'emitter.c';out=Path(t)/'emitter';f.write_text(hid_fixture);subprocess.run(['cc','-std=gnu11','-fsanitize=address,undefined',str(f),'-o',str(out)],check=True);subprocess.run([str(out)],check=True)
