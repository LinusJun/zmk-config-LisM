/* Sole generic-input scanner. ESB halves never use this Bluetooth scanner. */
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/logging/log.h>
#include <zmk/endpoints.h>
#include <zmk/ble.h>
#include "lism_hid_host.h"
#include "lism_input_host.h"
#include "lism_screen.h"
LOG_MODULE_REGISTER(lism_input_scan,LOG_LEVEL_INF);
static bool running;
static K_MUTEX_DEFINE(scan_lock);
static int pause_locked(void) {
    if(!running)return 0;
    int rc=bt_le_scan_stop();if(!rc || rc==-EALREADY)running=false;
    return rc;
}
int lism_hid_scan_pause(void) {
    k_mutex_lock(&scan_lock,K_FOREVER);int rc=pause_locked();k_mutex_unlock(&scan_lock);return rc;
}
/* Legacy advertising and scanning share the controller's random-address
 * register. Keep scanning stopped across address setup + advertising start. */
int lism_hid_advertising_begin(void) {
    k_mutex_lock(&scan_lock,K_FOREVER);
    if(lism_hid_host_scan_paused()){k_mutex_unlock(&scan_lock);return -EAGAIN;}
    int rc=pause_locked();
    if(rc && rc!=-EALREADY){k_mutex_unlock(&scan_lock);return rc;}return 0;
}
void lism_hid_advertising_end(void){k_mutex_unlock(&scan_lock);}
void lism_hid_scan_resume(void) {
    k_mutex_lock(&scan_lock,K_FOREVER);
    if(!lism_input_host_enabled() || !bt_is_ready() || lism_hid_host_scan_paused() || !lism_hid_host_scan_needed() || lism_esb_coex_pressure()) {pause_locked();goto done;}
    if(running)goto done;
    const struct bt_le_scan_param p={.type=BT_LE_SCAN_TYPE_ACTIVE,
        .options=BT_LE_SCAN_OPT_FILTER_DUPLICATE,.interval=160,.window=8};
    int rc=bt_le_scan_start(&p,NULL);
    if(!rc || rc==-EALREADY)running=true;else LOG_WRN("Input scan start rc=%d",rc);
done:k_mutex_unlock(&scan_lock);
}
void lism_hid_scan_diagnostics(void) {}
int xiao147_pointer_endpoint(void) {
    struct zmk_endpoint_instance ep=zmk_endpoint_get_selected();
    if (ep.transport==ZMK_TRANSPORT_USB) return 0;
    if (ep.transport==ZMK_TRANSPORT_BLE && zmk_ble_profile_is_connected(ep.ble.profile_index))
        return ep.ble.profile_index+1;
    return -1;
}

/* Only this callback's thread uses nonblocking USB; normal ESB HID retains
 * the v4.4 policy. A different thread can never inherit this send context. */
static atomic_ptr_t bridge_sender;
void lism_input_usb_bridge(bool active) {atomic_ptr_set(&bridge_sender,active?k_current_get():NULL);}
bool lism_input_usb_nonblocking(void) {return atomic_ptr_get(&bridge_sender)==k_current_get();}
