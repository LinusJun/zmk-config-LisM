/* Inserted only into the isolated OPERATOR battery widget compilation unit.
 * Runs on its existing LVGL/display thread. Read state, RELEASE getter locks,
 * then paint. No event producer waits for the display thread.
 */
#include <zmk/split/central.h>
#include <zephyr/logging/log.h>
LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);
extern bool lism_connected(unsigned source);
BUILD_ASSERT(PERIPHERAL_COUNT==2, "This experiment has exactly two ESB halves");
static lv_timer_t *lism_esb_battery_timer;
static int64_t lism_esb_next_display_log;

static void lism_esb_battery_refresh(bool force) {
    bool diagnostics=k_uptime_get()>=lism_esb_next_display_log;
    if(diagnostics)lism_esb_next_display_log=k_uptime_get()+10000;
    for(unsigned source=0;source<PERIPHERAL_COUNT;source++) {
        uint8_t level;
        int rc=zmk_split_central_get_peripheral_battery_level(source,&level);
        if(rc) {
            if(diagnostics)LOG_WRN("ESB display source=%u battery read failed=%d",source,rc);
            continue;
        }
        bool connected=lism_connected(source); /* getter releases state_mutex */
        bool changed=force || peripheral_battery[source]!=level ||
                     peripheral_connected[source]!=connected;
        if(changed) {
            peripheral_battery[source]=level; /* zero is a valid cached value */
            peripheral_connected[source]=connected;
            update_peripheral_display(source);
        }
        if(diagnostics)LOG_INF("ESB display source=%u online=%u battery=%u refreshed=%u",
                              source,connected,level,changed);
    }
}
static void lism_esb_battery_timer_cb(lv_timer_t *timer) {
    ARG_UNUSED(timer);
    lism_esb_battery_refresh(false);
}
static void lism_esb_battery_sync_start(void) {
    /* Called only after all objects exist and this widget is in its list. */
    lism_esb_battery_refresh(true);
    if(!lism_esb_battery_timer)
        lism_esb_battery_timer=lv_timer_create(lism_esb_battery_timer_cb,250,NULL);
}
