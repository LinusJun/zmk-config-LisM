/* XIAO-only, rate-limited observation. Forward every report unchanged. */
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/logging/log.h>
#include <zmk/hid.h>
#include <zmk/endpoints.h>
#include <zmk/usb.h>
#include <zmk/ble.h>

int xiao147_pointer_endpoint(void)
{
    struct zmk_endpoint_instance ep = zmk_endpoint_get_selected();
    if (ep.transport == ZMK_TRANSPORT_NONE) { return -1; }
#if IS_ENABLED(CONFIG_ZMK_USB)
    if (ep.transport == ZMK_TRANSPORT_USB && !zmk_usb_is_hid_ready()) { return -1; }
#endif
#if IS_ENABLED(CONFIG_ZMK_BLE)
    if (ep.transport == ZMK_TRANSPORT_BLE && !zmk_ble_profile_is_connected(ep.ble.profile_index)) { return -1; }
#endif
    return zmk_endpoint_instance_to_index(ep);
}

LOG_MODULE_REGISTER(xiao147_hid_trace, LOG_LEVEL_INF);
int __real_zmk_endpoint_send_mouse_report(void);

int __wrap_zmk_endpoint_send_mouse_report(void)
{
    const struct zmk_hid_mouse_report *report = zmk_hid_get_mouse_report();
    /* Snapshot before forwarding: the real transport may consume the report.
     * Observe pointer displacement as well as wheels; accepting an input event
     * in the driver alone does not prove a mouse report reached the transport. */
    int x = report->body.d_x, y = report->body.d_y;
    int pan = report->body.d_scroll_x, wheel = report->body.d_scroll_y;
    unsigned buttons = report->body.buttons;
    struct zmk_endpoint_instance endpoint = zmk_endpoint_get_selected();
    int ret = __real_zmk_endpoint_send_mouse_report();
    if (x || y || pan || wheel || buttons || ret < 0) {
        static atomic_t count, failures, last_trace;
        atomic_inc(&count);
        if (ret < 0) { atomic_inc(&failures); }
        uint32_t now = k_uptime_get_32();
        atomic_val_t before = atomic_get(&last_trace);
        if (now - (uint32_t)before >= 1000 && atomic_cas(&last_trace, before, now)) {
            LOG_INF("HID endpoint %d pointer %d %d wheel %d %d buttons 0x%x, send result %d, reports %ld failures %ld",
                    zmk_endpoint_instance_to_index(endpoint), x, y, pan, wheel, buttons,
                    ret, (long)atomic_get(&count), (long)atomic_get(&failures));
        }
    }
    return ret;
}
