"""Private pinned-source hooks for right hub. Never alter the shared SDK."""
from pathlib import Path
import sys
from prepare_upstream_esb import replace

def prepare(app,ble,endpoint,behavior,dest):
    app=Path(app);dest=Path(dest);dest.mkdir(exist_ok=True)
    t=(app/'src/event_manager.c').read_text()
    t='#include <kb_route/route.h>\n'+t
    t=replace(t,'int zmk_event_manager_raise(zmk_event_t *event) { return zmk_event_manager_handle_from(event, 0); }', '''
void kb_route_replay(const struct zmk_position_state_changed *data) {
    struct zmk_position_state_changed_event e={.header={.event=&zmk_event_zmk_position_state_changed},.data=*data};
    zmk_event_manager_handle_from(&e.header,0);
}
int zmk_event_manager_raise(zmk_event_t *event) {
    if(kb_route_capture(event))return 0;
    return zmk_event_manager_handle_from(event,0);
}''');(dest/'event_manager.c').write_text(t)
    f=Path(ble);t='#include <kb_route/route.h>\n'+f.read_text();t=replace(t,'static int lism_update_advertising(void) {','''static int lism_update_advertising(void) {
    if(!kb_route_ble_enabled()) {
        if(advertising_status!=ZMK_ADV_NONE)bt_le_adv_stop();
        advertising_status=ZMK_ADV_NONE;return 0;
    }''');t=replace(t,'int zmk_ble_prof_select(uint8_t index) {','int zmk_ble_prof_select(uint8_t index) {\n    if(kb_route_ble_enabled() && index!=zmk_ble_active_profile_index())return kb_route_select(index);')
    t+='\nvoid kb_route_ble_refresh(void) { update_advertising(); }\n';f.write_text(t)
    f=Path(endpoint);t='#include <kb_route/route.h>\n'+f.read_text()
    for sig in ['int zmk_endpoint_send_report(uint16_t usage_page) {','int zmk_endpoint_send_mouse_report() {']:
        t=replace(t,sig,sig+'\n    if(!kb_route_output_enabled())return 0;')
    f.write_text(t)
    t=(app/'src/behavior.c').read_text()
    t=replace(t,'    struct zmk_behavior_binding binding = *src_binding;', '''    struct zmk_behavior_binding binding = *src_binding;
    /* These remain Prospector controls; standalone profile selection uses
     * the explicit KB_ROUTE behavior, never the ordinary dongle BT/OUT row. */
    if(!strcmp(binding.behavior_dev,"outputs") || !strcmp(binding.behavior_dev,"screen_next"))return 0;''')
    Path(behavior).write_text(t)
    t=(app/'src/pointing/input_listener.c').read_text()
    t='#include <kb_route/route.h>\n'+t
    # Drop stale decompressed proxy events before updating listener state.
    from prepare_hog_routing import function
    old=function(t,'input_handler')
    t=replace(t,old,old.replace('{','{\n    if(!kb_route_output_enabled())return;',1))
    t+=r"""
#define KB_RESET_LISTENER(n) \
    COND_CODE_1(DT_NODE_HAS_STATUS(DT_INST_PHANDLE(n,device),okay), \
        (memset(&data_##n.mouse,0,sizeof(data_##n.mouse)); \
         memset(data_##n.base_processor_data.remainders,0,data_##n.base_processor_data.remainders_len*sizeof(struct input_processor_remainder_data)); \
         for(unsigned i=0;i<config_##n.layer_overrides_len;i++) \
             memset(data_##n.layer_override_data[i].remainders,0,data_##n.layer_override_data[i].remainders_len*sizeof(struct input_processor_remainder_data));),())
void kb_route_listener_reset(void) {DT_INST_FOREACH_STATUS_OKAY(KB_RESET_LISTENER)}
"""
    (dest/'input_listener.c').write_text(t)
    prepare_idle(app,dest)

def prepare_idle(app,dest):
    app=Path(app);dest=Path(dest);dest.mkdir(exist_ok=True)
    from prepare_hog_routing import function
    t='#include <zephyr/sys/atomic.h>\nstatic atomic_t kb_queue_executing;\n'+(app/'src/behavior_queue.c').read_text()
    old=function(t,'behavior_queue_process_next')
    patched=old.replace('{','{\n    atomic_inc(&kb_queue_executing);',1)
    patched=patched[:-1]+'    atomic_dec(&kb_queue_executing);\n}'
    t=replace(t,old,patched)
    t+='\nbool kb_route_queue_idle(void) {return !atomic_get(&kb_queue_executing) && !k_msgq_num_used_get(&zmk_behavior_queue_msgq) && !k_work_delayable_is_pending(&queue_work); }\n'

    (dest/'behavior_queue.c').write_text(t)
    t=(app/'src/behaviors/behavior_hold_tap.c').read_text()+"""
bool kb_route_holdtap_idle(void) {
#if DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT)
    for(unsigned i=0;i<ZMK_BHV_HOLD_TAP_MAX_HELD;i++)if(active_hold_taps[i].position!=ZMK_BHV_HOLD_TAP_POSITION_NOT_USED)return false;
#endif
    return true;
}
"""
    (dest/'behavior_hold_tap.c').write_text(t)
    t=(app/'src/combo.c').read_text()+"""
bool kb_route_combo_idle(void) {
    return !pressed_keys_count && !active_combo_count;
}
"""
    (dest/'combo.c').write_text(t)

def prepare_receiver(app,dest,endpoint):
    prepare_idle(app,dest)
    f=Path(endpoint);t='#include <kb_route/route.h>\n'+f.read_text()
    for sig,usage in [('int zmk_endpoint_send_report(uint16_t usage_page) {','usage_page'),('int zmk_endpoint_send_mouse_report() {','0')]:
        t=replace(t,sig,sig+'\n    if(!kb_route_receiver_output_allowed('+usage+'))return 0;')
    f.write_text(t)

if __name__=='__main__':
    if sys.argv[1]=='--idle':prepare_idle(*sys.argv[2:])
    elif sys.argv[1]=='--receiver':prepare_receiver(*sys.argv[2:])
    else:prepare(*sys.argv[1:])
