/* SPDX-License-Identifier: MIT
 * Telemetry never invokes HID, keymap or endpoint mutation. One LVGL-owned
 * snapshot is selected per refresh; local widget callbacks use the same cache.
 */
#include "route.h"
#include "mirror.h"
#include <lvgl.h>
#include <zmk/keymap.h>
static struct k_spinlock lock;
static struct kb_mirror received,shown;
static int64_t received_at;
static int source;
void kb_mirror_received(const struct kb_mirror *m) {
    if(m->version!=KB_MIRROR_VERSION || m->profile>=5 || m->layer>=ZMK_KEYMAP_LAYERS_LEN || m->host_connected>1 || !kb_mirror_left_status_valid(m->left_online))return;
    struct kb_route_view v;kb_route_snapshot(&v);if(v.last_mode!=KB_ROUTE_BLE || v.epoch!=m->epoch)return;
    k_spinlock_key_t k=k_spin_lock(&lock);received=*m;received_at=k_uptime_get();k_spin_unlock(&lock,k);
}
int kb_mirror_snapshot(struct kb_mirror *out) {k_spinlock_key_t k=k_spin_lock(&lock);*out=shown;int s=source;k_spin_unlock(&lock,k);return s;}
extern void kb_mirror_wpm_refresh(void),kb_mirror_layer_refresh(void),kb_mirror_mods_refresh(void),kb_mirror_locks_refresh(void);
static void refresh(lv_timer_t *timer) {
    LV_UNUSED(timer);struct kb_route_view v;kb_route_snapshot(&v);struct kb_mirror m={.layer=255,.profile=255};int next=0;
    k_spinlock_key_t k=k_spin_lock(&lock);
    if(v.mode==KB_ROUTE_SWITCHING || v.last_mode==KB_ROUTE_BLE) {
        next=-1;
        if(v.mode==KB_ROUTE_BLE && received.epoch==v.epoch && received.version==KB_MIRROR_VERSION && k_uptime_get()-received_at<1200){m=received;next=1;}
    }
    /* RSSI-only changes are consumed by the existing icon timer. They must
     * not restart the WPM/layer/modifier/lock widget callbacks. Online-bit
     * changes and every other telemetry field still follow the normal path.
     */
    struct kb_mirror previous_widgets=shown,next_widgets=m;
    previous_widgets.left_online=kb_mirror_left_online(shown.left_online);
    next_widgets.left_online=kb_mirror_left_online(m.left_online);
    bool dirty=next!=source || memcmp(&next_widgets,&previous_widgets,sizeof(m));
    source=next;shown=m;k_spin_unlock(&lock,k);
    if(dirty){kb_mirror_wpm_refresh();kb_mirror_layer_refresh();kb_mirror_mods_refresh();kb_mirror_locks_refresh();}
}
void kb_mirror_ui_start(void) {refresh(NULL);lv_timer_create(refresh,50,NULL);}
