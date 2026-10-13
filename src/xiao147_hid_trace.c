/* XIAO-only, rate-limited observation. Forward every report unchanged. */
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/logging/log.h>
#include <zmk/hid.h>
#include <zmk/endpoints.h>

LOG_MODULE_REGISTER(xiao147_hid_trace, LOG_LEVEL_INF);
int __real_zmk_endpoint_send_mouse_report(void);

int __wrap_zmk_endpoint_send_mouse_report(void)
{
    const struct zmk_hid_mouse_report *report = zmk_hid_get_mouse_report();
    int x = report->body.d_scroll_x, y = report->body.d_scroll_y;
    int ret = __real_zmk_endpoint_send_mouse_report();
    if (x || y) {
        static atomic_t count, failures, last_trace;
        atomic_inc(&count);
        if (ret < 0) { atomic_inc(&failures); }
        uint32_t now = k_uptime_get_32();
        atomic_val_t before = atomic_get(&last_trace);
        if (now - (uint32_t)before >= 1000 && atomic_cas(&last_trace, before, now)) {
            LOG_INF("HID endpoint wheel %d %d, send result %d, reports %ld failures %ld",
                    x, y, ret, (long)atomic_get(&count), (long)atomic_get(&failures));
        }
    }
    return ret;
}
