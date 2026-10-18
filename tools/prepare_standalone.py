"""Generate checked overrides for the pinned ZMK tree; never edit upstream."""
from pathlib import Path
import argparse


def replace(s, old, new):
    if s.count(old)!=1: raise ValueError(f'pinned standalone anchor moved ({s.count(old)}): {old[:100]}')
    return s.replace(old,new,1)


def generate(app,out,root):
    out.mkdir(parents=True,exist_ok=True)
    header=f'#include "{root}/src/standalone/mode.h"\n'
    def source(name): return (app/'src'/name).read_text()
    def write(name,s): (out/name).write_text(s)
    s=header+source('split/bluetooth/service.c')
    s=replace(s,'static uint8_t position_state[POS_STATE_LEN];',
        'static uint8_t position_state[POS_STATE_LEN];\nstatic K_MUTEX_DEFINE(position_lock);\nint send_position_state(void);')
    s=replace(s,'    LOG_DBG("value %d", value);\n}\n\n#if IS_ENABLED(CONFIG_ZMK_SPLIT_PERIPHERAL_HID_INDICATORS)',
        '    if(value==BT_GATT_CCC_NOTIFY)send_position_state();\n}\n\n#if IS_ENABLED(CONFIG_ZMK_SPLIT_PERIPHERAL_HID_INDICATORS)')
    s=replace(s,'void send_position_state_callback(struct k_work *work) {',
        """static void selected_connection(struct bt_conn *conn,void *arg) {
    struct bt_conn_info info;struct bt_conn **out=arg;
    if(!*out && !bt_conn_get_info(conn,&info) && info.id==1 &&
       info.role==BT_CONN_ROLE_PERIPHERAL && info.state==BT_CONN_STATE_CONNECTED &&
       bt_conn_get_security(conn)>=BT_SECURITY_L2 && lism_standalone_left_addr(bt_conn_get_dst(conn)))
        *out=bt_conn_ref(conn);
}
void send_position_state_callback(struct k_work *work) {""")
    s=replace(s,'        int err = bt_gatt_notify(NULL, &split_svc.attrs[1], &state, sizeof(state));',
        """        struct bt_conn *conn=NULL;
        if(!lism_standalone_direct())continue;
        bt_conn_foreach(BT_CONN_TYPE_LE,selected_connection,&conn);
        if(!conn)continue;
        int err=bt_gatt_notify(conn,&split_svc.attrs[1],state,sizeof(state));
        /* Never continue after losing one half of a key transition. A fresh
         * encrypted link begins with a full state snapshot. */
        if(err && err!=-ENOTCONN)bt_conn_disconnect(conn,BT_HCI_ERR_REMOTE_USER_TERM_CONN);
        bt_conn_unref(conn);""")
    start=s.index('int send_position_state() {');end=s.index('static int zmk_split_bt_position_pressed',start)
    s=s[:start]+"""int send_position_state(void) {
    if(!lism_standalone_direct())return 0;
    k_mutex_lock(&position_lock,K_FOREVER);
    int rc=k_msgq_put(&position_state_msgq,position_state,K_NO_WAIT);
    k_mutex_unlock(&position_lock);
    if(rc) {
        struct bt_conn *conn=NULL;bt_conn_foreach(BT_CONN_TYPE_LE,selected_connection,&conn);
        if(conn){bt_conn_disconnect(conn,BT_HCI_ERR_REMOTE_USER_TERM_CONN);bt_conn_unref(conn);}
    } else k_work_submit_to_queue(&service_work_q,&service_position_notify_work);
    return rc;
}

"""+s[end:]
    s=s.replace('    WRITE_BIT(position_state[position / 8], position % 8, true);',
        '    k_mutex_lock(&position_lock,K_FOREVER);\n    WRITE_BIT(position_state[position / 8], position % 8, true);\n    int rc=send_position_state();k_mutex_unlock(&position_lock);return rc;')
    s=s.replace('    WRITE_BIT(position_state[position / 8], position % 8, false);',
        '    k_mutex_lock(&position_lock,K_FOREVER);\n    WRITE_BIT(position_state[position / 8], position % 8, false);\n    int rc=send_position_state();k_mutex_unlock(&position_lock);return rc;')
    # service commands arriving from a HID host cannot mutate split state.
    s=replace(s,'    struct zmk_split_run_behavior_payload *payload = attrs->user_data;',
        '    if(!lism_standalone_split_conn(conn))return BT_GATT_ERR(BT_ATT_ERR_AUTHORIZATION);\n    struct zmk_split_run_behavior_payload *payload = attrs->user_data;')
    s=replace(s,'    selected_phys_layout = *(uint8_t *)buf;',
        '    if(!lism_standalone_split_conn(conn))return BT_GATT_ERR(BT_ATT_ERR_AUTHORIZATION);\n    selected_phys_layout = *(uint8_t *)buf;')
    write('service.c',s)
    s=source('event_manager.c')
    s=replace(s,'#include <zmk/event_manager.h>','#include <zmk/event_manager.h>\n#include <zmk/events/position_state_changed.h>\n'+header)
    s=replace(s,'int zmk_event_manager_raise(zmk_event_t *event) { return zmk_event_manager_handle_from(event, 0); }',
        '''void lism_standalone_replay(const struct zmk_position_state_changed *data) {
    struct zmk_position_state_changed_event ev={.header={.event=&zmk_event_zmk_position_state_changed},.data=*data};
    zmk_event_manager_handle_from(&ev.header,0);
}
int zmk_event_manager_raise(zmk_event_t *event) {
    if(lism_standalone_event(event))return 0;
    return zmk_event_manager_handle_from(event,0);
}''')
    write('event_manager.c',s)
    s=header+source('ble.c')
    s=replace(s,'int zmk_ble_prof_select(uint8_t index) {',
        """int zmk_ble_prof_select(uint8_t index) {
    if(lism_standalone_running() && lism_standalone_direct() &&
       index!=zmk_ble_active_profile_index())return lism_standalone_profile_select(index);""")
    s=replace(s,'int update_advertising(void) {','''int update_advertising(void) {
    if(!lism_standalone_direct() || !lism_standalone_running()) {
        if(advertising_status!=ZMK_ADV_NONE)bt_le_adv_stop();
        advertising_status=ZMK_ADV_NONE;return 0;
    }''')
    # Only HID host peripheral connections update host profile state. A dongle
    # connection and a central-role left connection never occupy a host profile.
    if s.count('if (info.role != BT_CONN_ROLE_PERIPHERAL) {')!=3: raise ValueError('pinned host callback guards moved')
    s=s.replace('if (info.role != BT_CONN_ROLE_PERIPHERAL) {',
                'if (!lism_standalone_host_conn(conn)) {')
    s=replace(s,'    if (info.role == BT_CONN_ROLE_PERIPHERAL && !pairing_allowed_for_current_profile(conn)) {',
        '''    if(!lism_standalone_pairing_allowed(conn))return BT_SECURITY_ERR_PAIR_NOT_ALLOWED;
    if (lism_standalone_host_conn(conn) && !pairing_allowed_for_current_profile(conn)) {''')
    start=s.index('int zmk_ble_put_peripheral_addr(const bt_addr_le_t *addr) {')
    end=s.index('\n}',start)+2
    s=s[:start]+'''int zmk_ble_put_peripheral_addr(const bt_addr_le_t *addr) {
    return lism_standalone_left_addr(addr)?0:-EACCES;
}'''+s[end:]
    s+='\nvoid lism_standalone_host_update(void) {update_advertising();}\nvoid lism_standalone_host_adv_consumed(void) {advertising_status=ZMK_ADV_NONE;}\n'
    write('ble.c',s)
    s=(app/'include/zmk/ble.h').read_text()
    s=replace(s,'#define ZMK_BLE_PROFILE_COUNT (CONFIG_BT_MAX_PAIRED - CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS)',
        '#define ZMK_BLE_PROFILE_COUNT 4')
    (out/'include/zmk').mkdir(parents=True,exist_ok=True)
    (out/'include/zmk/ble.h').write_text(s)
    s=source('split/peripheral.c')
    end=s.index('int zmk_split_peripheral_report_event(')
    s=s[:end].replace('const struct zmk_split_transport_peripheral *active_transport;','')
    write('command.c',s)
    s=header+source('hog.c')
    # Drop queued output while closing a route, and fence/purge before opening
    # another profile. No report can be dequeued for the new host by an old job.
    s=replace(s,'    struct bt_conn *conn = zmk_ble_active_profile_conn();',
        '    struct bt_conn *conn = lism_standalone_running() && lism_standalone_direct() ? zmk_ble_active_profile_conn() : NULL;') if s.count('    struct bt_conn *conn = zmk_ble_active_profile_conn();')==1 else s.replace(
        'struct bt_conn *conn = zmk_ble_active_profile_conn();',
        'struct bt_conn *conn = lism_standalone_running() && lism_standalone_direct() ? zmk_ble_active_profile_conn() : NULL;')
    s+='''\nvoid lism_standalone_hog_purge(void) {
    k_msgq_purge(&zmk_hog_keyboard_msgq);
    k_msgq_purge(&zmk_hog_consumer_msgq);
#if IS_ENABLED(CONFIG_ZMK_POINTING)
    k_msgq_purge(&zmk_hog_mouse_msgq);
#endif
}\n'''
    write('hog.c',s)
    s=header+source('endpoints.c')
    for signature in ['int zmk_endpoint_send_report(uint16_t usage_page) {','int zmk_endpoint_send_mouse_report() {']:
        s=replace(s,signature,signature+'\n    if(!lism_standalone_direct() || !lism_standalone_running())return 0;')
    write('endpoints.c',s)
    s=source('activity.c')+'\nvoid lism_standalone_note_activity(void) {note_activity();}\n'
    write('activity.c',s)
    s=source('pointing/input_listener.c')
    s+=r"""
#define LISM_RESET_LISTENER(n) \
    COND_CODE_1(DT_NODE_HAS_STATUS(DT_INST_PHANDLE(n,device),okay), \
        (memset(&data_##n.mouse,0,sizeof(data_##n.mouse)); \
         memset(data_##n.base_processor_data.remainders,0,data_##n.base_processor_data.remainders_len*sizeof(struct input_processor_remainder_data)); \
         for(unsigned i=0;i<config_##n.layer_overrides_len;i++) \
             memset(data_##n.layer_override_data[i].remainders,0,data_##n.layer_override_data[i].remainders_len*sizeof(struct input_processor_remainder_data));),())
void lism_standalone_listener_reset(void) {
    DT_INST_FOREACH_STATUS_OKAY(LISM_RESET_LISTENER)
}
"""
    write('input_listener.c',s)
    s=header+source('split/bluetooth/central.c')
    s=replace(s,'struct peripheral_event_wrapper {','struct peripheral_event_wrapper {\n    uint32_t generation;')
    anchor='int peripheral_slot_index_for_conn(struct bt_conn *conn) {'
    lifecycle='''static struct k_spinlock life_lock;
static uint32_t life_generation=1;
static bool life_cleanup,life_faulted;
static uint8_t life_applied[16];
static int life_enqueue(struct peripheral_event_wrapper *event) {
    k_spinlock_key_t key=k_spin_lock(&life_lock);
    if(life_faulted){k_spin_unlock(&life_lock,key);return -ESHUTDOWN;}
    event->generation=life_generation;
    int rc=life_cleanup?-ESHUTDOWN:k_msgq_put(&peripheral_event_msgq,event,K_NO_WAIT);
    if(rc){life_generation++;life_cleanup=life_faulted=true;}
    k_spin_unlock(&life_lock,key);
    if(rc && peripherals[0].conn)bt_conn_disconnect(peripherals[0].conn,BT_HCI_ERR_REMOTE_USER_TERM_CONN);
    k_work_submit(&peripheral_event_work);return rc;
}
'''
    s=replace(s,anchor,lifecycle+anchor)
    # Native position releases used a bounded queue without checking put().
    # Instead invalidate the old generation, and drain actual applied keys in
    # the sole event worker before permitting the slot to be reserved again.
    start=s.index('    // Raise events releasing any active positions from this peripheral')
    end=s.index('    for (int i = 0; i < POSITION_STATE_DATA_LEN; i++) {\n        slot->position_state[i] = 0U;',start)
    s=s[:start]+'''    k_spinlock_key_t key=k_spin_lock(&life_lock);
    life_generation++;life_cleanup=true;life_faulted=false;
    k_spin_unlock(&life_lock,key);
    k_work_submit(&peripheral_event_work);

'''+s[end:]
    s=replace(s,'int reserve_peripheral_slot(const bt_addr_le_t *addr) {',
        '''int reserve_peripheral_slot(const bt_addr_le_t *addr) {
    if(!lism_standalone_direct() || !lism_standalone_running() || life_cleanup)return -EACCES;''')
    # Zero-length/malformed notifications must not read past the payload.
    s=replace(s,'    LOG_DBG("[NOTIFICATION] data %p length %u", data, length);',
        '''    if(length!=16 || bt_conn_get_security(conn)<BT_SECURITY_L2)return BT_GATT_ITER_CONTINUE;
    LOG_DBG("[NOTIFICATION] data %p length %u", data, length);''')
    s=replace(s,'    LOG_DBG("[INPUT EVENT] data %p length %u", data, length);',
        '''    /* This variant's left is a digital matrix/stick, with no pointer.
     * Never admit a remote reg=1 event into the local right trackball. */
    return BT_GATT_ITER_STOP;
    LOG_DBG("[INPUT EVENT] data %p length %u", data, length);''')
    s=s.replace('k_msgq_put(&peripheral_event_msgq, &ev, K_NO_WAIT);','life_enqueue(&ev);')
    s=s.replace('k_msgq_put(&peripheral_event_msgq, &event_wrapper, K_NO_WAIT);','life_enqueue(&event_wrapper);')
    s=replace(s,'if (bt_uuid_cmp(&uuid.uuid, BT_UUID_DECLARE_128(ZMK_SPLIT_BT_SERVICE_UUID)) != 0) {',
        'if (bt_uuid_cmp(&uuid.uuid, BT_UUID_DECLARE_128(LISM_COMPANION_UUID)) != 0) {')
    s=replace(s,'static int start_scanning(void) {',
        '''static int start_scanning(void) {
    if(!lism_standalone_direct() || !lism_standalone_running() || life_cleanup)return 0;''')
    s=replace(s,'static void split_central_disconnected(struct bt_conn *conn, uint8_t reason) {',
        '''static void split_central_disconnected(struct bt_conn *conn, uint8_t reason) {
    if(peripheral_slot_index_for_conn(conn)<0)return;''')
    s=replace(s,'        LOG_DBG("Trigger key position state change of type %d", ev.event.type);',
        '''        if(ev.generation!=life_generation || life_cleanup || !lism_standalone_direct() ||
           !lism_standalone_running())continue;
        if(ev.event.type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT) {
            unsigned p=ev.event.data.key_position_event.position;
            if(p>=128)continue;
            if(ev.event.data.key_position_event.pressed)life_applied[p/8]|=BIT(p%8);
            else life_applied[p/8]&=~BIT(p%8);
        }
        LOG_DBG("Trigger key position state change of type %d", ev.event.type);''')
    s=replace(s,'void peripheral_event_work_callback(struct k_work *work) {',
        '''void peripheral_event_work_callback(struct k_work *work) {
    if(life_faulted && peripherals[0].conn) {
        bt_conn_disconnect(peripherals[0].conn,BT_HCI_ERR_REMOTE_USER_TERM_CONN);
        k_work_reschedule(&peripheral_event_work,K_MSEC(100));
    }
    if(life_cleanup) {
        for(unsigned p=0;p<128;p++)if(life_applied[p/8]&BIT(p%8)) {
            raise_zmk_position_state_changed((struct zmk_position_state_changed){
                .source=0,.position=p,.state=false,.timestamp=k_uptime_get()});
        }
        memset(life_applied,0,sizeof(life_applied));
        k_spinlock_key_t key=k_spin_lock(&life_lock);life_cleanup=false;k_spin_unlock(&life_lock,key);
        start_scanning();
    }''')
    s=replace(s,'struct central_cmd_wrapper {','struct central_cmd_wrapper {\n    uint32_t generation;')
    s=replace(s,'        if (peripherals[payload_wrapper.source].state != PERIPHERAL_SLOT_STATE_CONNECTED) {',
        '        if(payload_wrapper.generation!=life_generation)continue;\n        if (peripherals[payload_wrapper.source].state != PERIPHERAL_SLOT_STATE_CONNECTED) {')
    s=replace(s,'    int err = k_msgq_put(&zmk_split_central_split_run_msgq, &payload_wrapper, K_MSEC(100));',
        '    payload_wrapper.generation=life_generation;\n    int err = k_msgq_put(&zmk_split_central_split_run_msgq, &payload_wrapper, K_MSEC(100));')
    s=s.replace('k_work_submit(&peripheral_event_work);','k_work_reschedule(&peripheral_event_work,K_NO_WAIT);')
    s=replace(s,'K_WORK_DEFINE(peripheral_event_work, peripheral_event_work_callback);',
        'K_WORK_DELAYABLE_DEFINE(peripheral_event_work, peripheral_event_work_callback);')
    s+='\nvoid lism_standalone_central_enable(bool enabled) {split_central_bt_set_enabled(enabled); }\n'.replace('enabled); }','enabled);}')
    s+='\nbool lism_standalone_central_drained(void) {return !peripherals[0].conn && !life_cleanup && !life_faulted && !k_msgq_num_used_get(&peripheral_event_msgq); }\n' 
    write('central.c',s)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('app',type=Path);p.add_argument('out',type=Path);p.add_argument('root',type=Path)
    a=p.parse_args();generate(a.app,a.out,a.root)
