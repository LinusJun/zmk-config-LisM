"""Real pinned BLE profile and output behavior bodies with mocked transports.
Test report routing/order at the enqueue API, not actual USB/BLE delivery.
"""
from pathlib import Path
import argparse,json,re,subprocess,tempfile
P=Path(__file__).resolve().parents[2]
a=argparse.ArgumentParser();a.add_argument('--workspace',type=Path,required=True);W=a.parse_args().workspace
def function(t,name):
    m=re.search(r'(?m)^(?:static )?[^\n;]+\b'+name+r'\([^;]*?\)\s*\{',t);assert m,name
    i=t.index('{',m.start())+1;depth=1
    while depth:depth+=(t[i]=='{')-(t[i]=='}');i+=1
    return t[m.start():i]
ble=next((W/'build-dongle').rglob('lism_ble_zephyr41.c')).read_text()
ep=next((W/'build-dongle').rglob('lism_endpoints.c')).read_text()
original=(W/'zmk/app/src/endpoints.c').read_text()
unpatched=ep.replace('#include <lism_hog_routing.h>\n','',1).replace('    zmk_endpoint_send_mouse_report(); // Release on the current, old transport.\n','',1)
for kind in ['keyboard','consumer','mouse']:
    unpatched=unpatched.replace(f'zmk_hog_send_{kind}_report_to_profile(&{kind}_report->body, current_instance.ble.profile_index)',f'zmk_hog_send_{kind}_report(&{kind}_report->body)')
assert unpatched==original

behavior=(W/'zmk/app/src/behaviors/behavior_outputs.c').read_text()
ui=(P/'source/src/esb_upstream/host_output.c').read_text()
assert (W/'display-dongle/boards/shields/prospector_adapter/src/layouts/operator/output.c').read_text()==ui
assert 'ZMK_LISTENER(' not in ui and 'lv_timer_create(output_refresh,100,NULL)' in ui
assert 'for (int i = 0; i < ZMK_BLE_PROFILE_COUNT; i++)' in ui
original_ui=(W/'prospector-zmk-module/boards/shields/prospector_adapter/src/layouts/operator/output.c').read_text()
for name in ['set_usb_btn_state','set_ble_btn_state','set_slot_active','update_output_widget','create_toggle_btn','create_slot_btn']:
    assert function(ui,name)==function(original_ui,name),name
assert function(ui,'zmk_widget_output_init').replace('\n    if(!output_timer)output_timer=lv_timer_create(output_refresh,100,NULL);','',1)==function(original_ui,'zmk_widget_output_init')
compat=W/'include-dongle'
prefix=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define IS_ENABLED(x) (x)
#define CONFIG_ZMK_BLE 1
#define CONFIG_ZMK_POINTING 1
#define LOG_DBG(...) do {} while(0)
#define LOG_INF(...) do {} while(0)
#define LOG_ERR(...) do {} while(0)
#define HID_USAGE_KEY 7
#define HID_USAGE_CONSUMER 12
#define ZMK_ENDPOINT_STR_LEN 20
enum zmk_transport {ZMK_TRANSPORT_NONE,ZMK_TRANSPORT_USB,ZMK_TRANSPORT_BLE};
enum {OUT_TOG,OUT_USB,OUT_BLE,OUT_NONE};
struct zmk_behavior_binding {unsigned param1;};
struct zmk_behavior_binding_event {int unused;};
struct zmk_endpoint_instance {enum zmk_transport transport;struct {int profile_index;} ble;};
struct zmk_endpoint_changed {struct zmk_endpoint_instance endpoint;};
static uint8_t active_profile;
static enum zmk_transport preferred_transport=ZMK_TRANSPORT_USB;
static struct zmk_endpoint_instance current_instance={.transport=ZMK_TRANSPORT_USB},selected;
static int saved,advertised,notified,key_held,consumer_held,mouse_held,sent;
static enum zmk_transport targets[30];
static int ble_save_profile(void){saved++;return 0;}
static void update_advertising(void){advertised++;}
static void raise_profile_changed_event(void){notified++;}
static int endpoints_save_preferred(void){return 0;}
static void update_current_endpoint(void);
static struct zmk_endpoint_instance get_selected_instance(void){return selected;}
static int zmk_ble_active_profile_index(void){return active_profile;}
static bool zmk_endpoint_instance_eq(struct zmk_endpoint_instance a,struct zmk_endpoint_instance b){return a.transport==b.transport && (a.transport!=ZMK_TRANSPORT_BLE || a.ble.profile_index==b.ble.profile_index);}
static void zmk_hid_keyboard_clear(void){key_held=0;}
static void zmk_hid_consumer_clear(void){consumer_held=0;}
static void zmk_hid_mouse_clear(void){mouse_held=0;}
static int zmk_endpoint_send_report(int page){assert((page==7 && !key_held)||(page==12 && !consumer_held));targets[sent++]=current_instance.transport;return 0;}
static int zmk_endpoint_send_mouse_report(void){assert(!mouse_held);targets[sent++]=current_instance.transport;return 0;}
static int zmk_endpoint_instance_to_str(struct zmk_endpoint_instance e,char*s,size_t n){return snprintf(s,n,"%d",e.transport);}
static void raise_zmk_endpoint_changed(struct zmk_endpoint_changed e){assert(zmk_endpoint_instance_eq(e.endpoint,current_instance));}
'''
# Preprocess the same compatibility header used by every real translation unit.
with tempfile.TemporaryDirectory() as d:
    d=Path(d);(d/'zmk/ble').mkdir(parents=True)
    (d/'zmk/keys.h').write_text('')
    (d/'zmk/ble/profile.h').write_text('typedef struct { unsigned char val[7]; } bt_addr_le_t;\n')
    prefix+='\n#include <zmk/ble.h>\n_Static_assert(ZMK_BLE_PROFILE_COUNT==5,"five host profiles");\n'
    main=r'''
int main(void) {
 for(unsigned i=0;i<5;i++){assert(!zmk_ble_prof_select(i));assert(active_profile==i);}
 assert(zmk_ble_prof_select(5)==-ERANGE && active_profile==4);
 assert(zmk_ble_prof_select(255)==-ERANGE && active_profile==4);
 assert(!zmk_ble_prof_next() && active_profile==0);
 assert(!zmk_ble_prof_prev() && active_profile==4);
 assert(saved==6 && advertised==6 && notified==6);
 struct zmk_behavior_binding b={.param1=OUT_BLE};
 selected=(struct zmk_endpoint_instance){.transport=ZMK_TRANSPORT_BLE,.ble={.profile_index=4}};
 key_held=consumer_held=mouse_held=1;
 assert(!on_keymap_binding_pressed(&b,(struct zmk_behavior_binding_event){0}));
 assert(preferred_transport==ZMK_TRANSPORT_BLE && current_instance.transport==ZMK_TRANSPORT_BLE);
 assert(sent==3 && targets[0]==ZMK_TRANSPORT_USB && targets[1]==ZMK_TRANSPORT_USB && targets[2]==ZMK_TRANSPORT_USB);
 b.param1=OUT_USB;selected.transport=ZMK_TRANSPORT_USB;
 key_held=consumer_held=mouse_held=1;
 assert(!on_keymap_binding_pressed(&b,(struct zmk_behavior_binding_event){0}));
 assert(preferred_transport==ZMK_TRANSPORT_USB && current_instance.transport==ZMK_TRANSPORT_USB);
 assert(sent==6 && targets[3]==ZMK_TRANSPORT_BLE && targets[4]==ZMK_TRANSPORT_BLE && targets[5]==ZMK_TRANSPORT_BLE);
 selected.transport=ZMK_TRANSPORT_BLE;b.param1=OUT_TOG;
 assert(!on_keymap_binding_pressed(&b,(struct zmk_behavior_binding_event){0}));
 assert(preferred_transport==ZMK_TRANSPORT_BLE && sent==9);
 b.param1=999;assert(on_keymap_binding_pressed(&b,(struct zmk_behavior_binding_event){0})==-ENOTSUP);
 puts("PASS: five real BLE profile indices, range/cycle, unchanged OUT behavior, mouse/keyboard/consumer zero enqueued on old USB/BLE transport before switch");
}
'''
    names=['zmk_ble_prof_select','zmk_ble_prof_next','zmk_ble_prof_prev']
    code=prefix+'\n'+'\n'.join(function(ble,n) for n in names)+'\n'+'\n'.join(function(ep,n) for n in ['zmk_endpoint_set_preferred_transport','zmk_endpoint_toggle_preferred_transport','zmk_endpoint_clear_reports','update_current_endpoint'])+'\n'+function(behavior,'on_keymap_binding_pressed')+'\n'+main
    c=d/'test.c';exe=d/'test';c.write_text(code)
    subprocess.run(['cc','-std=c11','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I'+str(compat),'-I'+str(d),str(c),'-o',str(exe)],check=True)
    r=subprocess.run([str(exe)],capture_output=True,text=True,check=True)
report={'passed':True,'real_profile_count_header':5,'actual_pinned_BLE_output_and_endpoint_bodies':True,
        'original_output_visual_layout_retained':True,'display_refresh_on_LVGL_timer':True,
        'mouse_release_and_explicit_HOG_profile_only_changes_to_endpoints':True,'stdout':r.stdout,
        'scope':'Mocked enqueue APIs, not async HOG delivery, BLE RF, UI pixels or real host pairing'}
(W/'host-outputs-test.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
