"""Adapt pinned newer NCS TX H4 framing to pinned Zephyr 4.1 metadata ABI.
Keep controller RX implementation untouched and initialize profile-save work
even if Bluetooth enable fails. All generated copies are build-local.
"""
from pathlib import Path
import hashlib,sys
from prepare_upstream_esb import replace

def prepare_hci(source,destination):
    data=Path(source).read_bytes()
    assert hashlib.sha256(data).hexdigest()=='9c9eacdf08634effcd2ac04bc89bc886f4d216d9686c47ea2eb79d5056e14287'
    t=data.decode()
    t=replace(t,'type = net_buf_pull_u8(buf);',
                'type = bt_buf_get_type(buf); /* Zephyr 4.1: type is metadata, no H4 byte. */')
    for old,new in [('BT_HCI_H4_ACL','BT_BUF_ACL_OUT'),('BT_HCI_H4_CMD','BT_BUF_CMD'),('BT_HCI_H4_ISO','BT_BUF_ISO_OUT')]:
        t=replace(t,'case '+old+':','case '+new+':')
    Path(destination).write_text(t)

def prepare_ble(source,destination):
    data=Path(source).read_bytes()
    assert hashlib.sha256(data).hexdigest()=='fcf8925613bcf7685053911a565cf897824a56adf0a63c5e6499c604a4f459c2'
    t=data.decode()
    t=replace(t,'    k_work_init_delayable(&ble_save_work, ble_save_profile_work);\n','')
    t=replace(t,'static int zmk_ble_init(void) {\n', '''static int zmk_ble_init(void) {
#if IS_ENABLED(CONFIG_SETTINGS)
    /* Profile keys remain callable after a failed bt_enable(). */
    k_work_init_delayable(&ble_save_work, ble_save_profile_work);
#endif
''')
    t=replace(t,'int update_advertising(void) {\n', '''int update_advertising(void) {
    if (!bt_is_ready()) {
        LOG_ERR("BLE host not ready; advertising skipped");
        return -EAGAIN;
    }
''')
    t=replace(t,'    settings_register(&profiles_handler);',
                '    LOG_INF("LisM BLE v4.1 bt_enable succeeded; awaiting settings, profiles=%d", ZMK_BLE_PROFILE_COUNT);\n    settings_register(&profiles_handler);')
    t=replace(t,'    advertising_status = ZMK_ADV_CONN;',
                '    advertising_status = ZMK_ADV_CONN; \\\n    LOG_INF("LisM BLE v4.1 advertising active; profile=%u", active_profile);')
    # Only the receiver owns generic central scanning. Right-hand direct BLE
    # keeps its independent route gate and advertising behavior unchanged.
    t=replace(t,'int update_advertising(void) {', 'static int lism_update_advertising(void) {')
    wrapper=r"""
#if IS_ENABLED(CONFIG_LISM_BLE_INPUT_BRIDGE)
#include "lism_hid_host.h"
static void lism_adv_retry(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(lism_adv_retry_work,lism_adv_retry);
#endif
int update_advertising(void) {
#if IS_ENABLED(CONFIG_LISM_BLE_INPUT_BRIDGE)
    int rc=lism_hid_advertising_begin();
    if(!rc){rc=lism_update_advertising();lism_hid_advertising_end();}
    if(rc)k_work_reschedule(&lism_adv_retry_work,K_MSEC(1000));
    else k_work_cancel_delayable(&lism_adv_retry_work);
    return rc;
#else
    return lism_update_advertising();
#endif
}
#if IS_ENABLED(CONFIG_LISM_BLE_INPUT_BRIDGE)
static void lism_adv_retry(struct k_work *work){ARG_UNUSED(work);update_advertising();}
#endif
"""
    t=replace(t,'static void update_advertising_callback(struct k_work *work)',wrapper+'\nstatic void update_advertising_callback(struct k_work *work)')
    Path(destination).write_text(t)

if __name__=='__main__':
    prepare_hci(sys.argv[1],sys.argv[2])
    prepare_ble(sys.argv[3],sys.argv[4])
