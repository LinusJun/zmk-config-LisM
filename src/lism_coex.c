/* ISR: bounded counters only. No calls into Bluetooth, settings, logging or UI. */
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/logging/log.h>
#include "lism_screen.h"
LOG_MODULE_REGISTER(lism_coex,LOG_LEVEL_INF);
static atomic_t last_cycles,max_gap_us,over_20,over_40,over_80,pressure;
static int64_t cooldown;
void lism_coex_grant(void) {
    uint32_t now=k_cycle_get_32(),last=atomic_set(&last_cycles,now);
    if (!last) return;
    uint32_t gap=k_cyc_to_us_floor32(now-last);
    if (gap>(uint32_t)atomic_get(&max_gap_us)) atomic_set(&max_gap_us,gap);
    if (gap>20000) atomic_inc(&over_20);
    if (gap>40000) atomic_inc(&over_40);
    if (gap>80000) {atomic_inc(&over_80);atomic_set(&pressure,1);}
}
bool lism_esb_coex_pressure(void) {
    /* Called only by the serialized generic-input coordinator, not an ISR. */
    uint32_t last=atomic_get(&last_cycles);
    if (!last) return true;
    if (atomic_set(&pressure,0) || k_cyc_to_us_floor32(k_cycle_get_32()-last)>80000)
        cooldown=k_uptime_get()+5000;
    return k_uptime_get()<cooldown;
}
void lism_coex_log(void) {
    LOG_INF("ESB grant gap max_us=%u >20ms=%u >40ms=%u >80ms=%u",
        (unsigned)atomic_get(&max_gap_us),(unsigned)atomic_get(&over_20),
        (unsigned)atomic_get(&over_40),(unsigned)atomic_get(&over_80));
}
