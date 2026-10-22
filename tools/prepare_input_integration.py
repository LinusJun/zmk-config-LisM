"""Compose generic input state while preserving v4.4 exact-connection routing."""
from pathlib import Path
import sys
from prepare_upstream_esb import replace
from prepare_hog_routing import function

def patch_hog(path):
    t=Path(path).read_text()
    t='#include "lism_input_merge.h"\n#include <zmk/endpoints.h>\n'+t
    for kind in ['keyboard','consumer','mouse']:
        t=replace(t,f'struct bt_conn *conn; struct zmk_hid_{kind}_report_body report;',
                    f'struct bt_conn *conn; struct zmk_hid_{kind}_report_body report; uint32_t epoch; bool release;')
        name='send_'+kind+'_report_callback';old=function(t,name)
        new=replace(old,'        struct bt_conn *conn = item.conn;\n        /* The queued reference remains valid even after a disconnect. */',
                    '        struct bt_conn *conn = item.conn;\n        if (!item.release && item.epoch != lism_input_merge_generation()) {\n            bt_conn_unref(conn); continue;\n        }')
        t=replace(t,old,new)
        name=f'zmk_hog_send_{kind}_report_to_profile';old=function(t,name)
        new=old.replace(f'.conn = conn, .report = *report',f'.conn = conn, .report = *report, .epoch = epoch, .release = true')
        new=new.replace(f'int {name}(struct zmk_hid_{kind}_report_body *report, uint8_t profile)',f'static int lism_queue_{kind}(struct zmk_hid_{kind}_report_body *report, uint8_t profile,uint32_t epoch)')
        new=new.replace('    if (profile >= ZMK_BLE_PROFILE_COUNT)', '    if (epoch != lism_input_merge_generation()) return -ESTALE;\n    if (profile >= ZMK_BLE_PROFILE_COUNT)')
        new=replace(new,f'    int err = k_msgq_put(&zmk_hog_{kind}_msgq, &item, K_MSEC(100));',f'''    for (size_t i=0;i<sizeof(item.report);i++)
        if (((const uint8_t *)&item.report)[i]) {{item.release=false;break;}}
    int err = k_msgq_put(&zmk_hog_{kind}_msgq, &item, K_NO_WAIT);''')
        new=new.replace('if (err == -EAGAIN)', 'if (err == -EAGAIN || err == -ENOMSG)')
        t=replace(t,old,new+f"\nint {name}(struct zmk_hid_{kind}_report_body *report,uint8_t profile) {{\n    return lism_queue_{kind}(report,profile,lism_input_merge_generation());\n}}\n")
        # Preserve old explicit host routing, but attach original frame epoch.
        t+=f'''
int lism_input_hog_send_{kind}(struct zmk_hid_{kind}_report_body *report,uint32_t epoch) {{
    if (epoch!=lism_input_merge_generation()) return -ESTALE;
    struct zmk_endpoint_instance ep=zmk_endpoint_get_selected();
    if (ep.transport!=ZMK_TRANSPORT_BLE) return -ENODEV;
    return lism_queue_{kind}(report,ep.ble.profile_index,epoch);
}}
'''
        if kind!='mouse':
            anchor=f'    struct zmk_hid_{kind}_report_body *report_body = &zmk_hid_get_{kind}_report()->body;'
            t=replace(t,anchor,f'''    struct zmk_hid_{kind}_report copy=*zmk_hid_get_{kind}_report();
    if(!lism_input_merge_local_allowed())memset(&copy.body,0,sizeof(copy.body));
    struct zmk_endpoint_instance ep=zmk_endpoint_get_selected();
    if (ep.transport==ZMK_TRANSPORT_BLE && !bt_addr_le_cmp(bt_conn_get_dst(conn),zmk_ble_profile_address(ep.ble.profile_index)))
        lism_input_merge_{kind}(&copy);
    struct zmk_hid_{kind}_report_body *report_body=&copy.body;''')
    Path(path).write_text(t)

def patch_endpoints(path):
    t='#include "lism_input_merge.h"\n'+Path(path).read_text()
    t=replace(t,'void zmk_endpoint_clear_reports(void) {','void zmk_endpoint_clear_reports(void) {\n    lism_input_merge_endpoint_reset();')
    t=replace(t,'static void update_current_endpoint(void) {','static void update_current_endpoint(void) {\n    lism_input_endpoint_transition_begin();')
    t=replace(t,'        raise_zmk_endpoint_changed((struct zmk_endpoint_changed){.endpoint = current_instance});\n    }\n}',
                '        raise_zmk_endpoint_changed((struct zmk_endpoint_changed){.endpoint = current_instance});\n    }\n    lism_input_endpoint_transition_end();\n}')
    for kind in ['keyboard','consumer','mouse']:
        anchor=f'        struct zmk_hid_{kind}_report *{kind}_report = zmk_hid_get_{kind}_report();'
        new=f'        struct zmk_hid_{kind}_report copy=*zmk_hid_get_{kind}_report();\n'
        new+=f'        lism_input_merge_{kind}(&copy);\n'
        new+=f'        struct zmk_hid_{kind}_report *{kind}_report=&copy;'
        t=replace(t,anchor,new)
    Path(path).write_text(t)

def patch_identity(path):
    t=Path(path).read_text()
    t=replace(t,'void set_profile_address(uint8_t index, const bt_addr_le_t *addr) {','static struct k_spinlock lism_profile_identity_lock;\nvoid set_profile_address(uint8_t index, const bt_addr_le_t *addr) {')
    t=replace(t,'    memcpy(&profiles[index].peer, addr, sizeof(bt_addr_le_t));','    k_spinlock_key_t key=k_spin_lock(&lism_profile_identity_lock);\n    memcpy(&profiles[index].peer, addr, sizeof(bt_addr_le_t));\n    k_spin_unlock(&lism_profile_identity_lock,key);')
    old='''        int err = read_cb(cb_arg, &profiles[idx], sizeof(struct zmk_ble_profile));
        if (err <= 0) {
            LOG_ERR("Failed to handle profile address from settings (err %d)", err);
            return err;
        }'''
    new='''        struct zmk_ble_profile loaded;
        int err=read_cb(cb_arg,&loaded,sizeof(loaded));
        if (err!=sizeof(loaded)) return err<0?err:-EIO;
        k_spinlock_key_t key=k_spin_lock(&lism_profile_identity_lock);
        profiles[idx]=loaded;
        k_spin_unlock(&lism_profile_identity_lock,key);'''
    t=replace(t,old,new)
    t+='''
bool lism_input_split_identity(const bt_addr_le_t *addr) {(void)addr;return false;} /* pure ESB */
bool lism_input_computer_identity(const bt_addr_le_t *addr) {
    if (!addr || !bt_addr_le_cmp(addr,BT_ADDR_LE_ANY)) return true;
    bool found=false;k_spinlock_key_t key=k_spin_lock(&lism_profile_identity_lock);
    for(unsigned i=0;i<ZMK_BLE_PROFILE_COUNT;i++)
        if(!bt_addr_le_cmp(addr,&profiles[i].peer)) {found=true;break;}
    k_spin_unlock(&lism_profile_identity_lock,key);return found;
}
bool lism_input_protected_identity(const bt_addr_le_t *addr) {return lism_input_computer_identity(addr);}
'''
    Path(path).write_text(t)
if __name__=='__main__':
    patch_hog(sys.argv[1]);patch_endpoints(sys.argv[2]);patch_identity(sys.argv[3])
