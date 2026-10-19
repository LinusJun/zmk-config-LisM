#include "lism_split_sets.h"
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#if defined(CONFIG_LISM_SPLIT_ESB)
#include "esb/integration.h"
#endif
#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
#include "lism_split_auto.h"
#endif

#if defined(CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS) && CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS != 2
#error "Legacy ESB requires exactly two split slots"
#endif

extern void lism_legacy_commit(uint8_t slot,uint32_t generation);
extern void lism_legacy_rollback(uint8_t slot);
static struct k_spinlock lock;
static bool reserved[2], connected[2], draining[2], cleaning[2];
static bool fault[2];
static bool enrollment_busy[2];
static uint32_t generations[2];
static uint8_t applied[2][16];
static int active = -1, enrolling = -1;
static int pending_slot = -1;
static bt_addr_le_t pending_address;
static uint32_t next_token;
static struct lism_split_set_candidate candidates[8];
static int64_t seen[8];
static char status[80] = "Automatic LisM pairing ready";
static int set_for(uint8_t slot) { return slot < 2 ? 0 : -1; }
static bool busy_set(int set) {
    unsigned left = 0; (void)set;
    return reserved[left] || reserved[left + 1] || draining[left] || draining[left + 1];
}
static void unlock_empty_set(void) { if (active >= 0 && !busy_set(active)) active = -1; }
static bool any_draining(void) {
    for (unsigned i = 0; i < 2; ++i) if (draining[i]) return true;
    return false;
}
int lism_split_sets_active_set(void) {
    k_spinlock_key_t key = k_spin_lock(&lock); int result = active;
    k_spin_unlock(&lock, key); return result;
}
void lism_split_sets_connected_snapshot(bool out[LISM_SPLIT_SET_SLOTS]) {
    k_spinlock_key_t key=k_spin_lock(&lock);
    for(unsigned i=0;i<LISM_SPLIT_SET_SLOTS;i++)out[i]=connected[i]&&!draining[i];
    k_spin_unlock(&lock,key);
}
uint8_t lism_split_sets_logical_slot(uint8_t role) {
    return role == 1;
}
int lism_split_sets_reserve(uint8_t slot) {
    if (slot >= 2) return -EINVAL;
    k_spinlock_key_t key = k_spin_lock(&lock);
    int set = set_for(slot), result = 0;
    if (enrollment_busy[slot] || reserved[slot] || draining[slot] || (set >= 0 && any_draining())) result = -EBUSY;
    else if (set >= 0 && active >= 0 && active != set) result = -EBUSY;
    else {
        if (set >= 0) active = set;
        reserved[slot] = true; connected[slot] = false; ++generations[slot];
    }
    k_spin_unlock(&lock, key); return result;
}
void lism_split_sets_connected(uint8_t slot) {
    if (slot >= 2) return;
    k_spinlock_key_t key = k_spin_lock(&lock);
    if (reserved[slot] && !draining[slot]) {
        connected[slot] = true;
    }
    k_spin_unlock(&lock, key);
}
void lism_split_sets_ready(uint8_t slot) {
    if (slot >= 2) return;
    k_spinlock_key_t key = k_spin_lock(&lock);
    bool ready=connected[slot] && !draining[slot];
    uint32_t ready_generation=generations[slot];
    if (ready && pending_slot == slot) pending_slot = -1;
    k_spin_unlock(&lock, key);
#if defined(CONFIG_LISM_SPLIT_ESB)
    if (ready) { lism_legacy_commit(slot,ready_generation); lism_esb_peer_ready(slot,ready_generation); }
#endif
#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
    if(ready)lism_split_auto_ready(slot);
#endif
}
void lism_split_sets_released(uint8_t slot) {
    if (slot >= 2) return;
    k_spinlock_key_t key = k_spin_lock(&lock);
    reserved[slot] = connected[slot] = false;
    if (!draining[slot]) { draining[slot] = true; ++generations[slot]; }
    k_spin_unlock(&lock, key);
    lism_legacy_rollback(slot);
}
void lism_split_sets_fault(uint8_t slot) {
    if (slot >= 2) return;
    k_spinlock_key_t key = k_spin_lock(&lock);
    if (reserved[slot] && !draining[slot]) {
        connected[slot] = false; draining[slot] = true; fault[slot] = true; ++generations[slot];
    }
    k_spin_unlock(&lock, key);
}
bool lism_split_sets_take_fault(uint8_t slot) {
    if (slot >= 2) return false;
    k_spinlock_key_t key = k_spin_lock(&lock); bool result = fault[slot]; fault[slot] = false;
    k_spin_unlock(&lock, key); return result;
}
uint32_t lism_split_sets_generation(uint8_t slot) {
    if (slot >= 2) return 0;
    k_spinlock_key_t key = k_spin_lock(&lock); uint32_t result = generations[slot];
    k_spin_unlock(&lock, key); return result;
}
bool lism_split_sets_dispatch(uint8_t slot, uint32_t generation) {
    if (slot >= 2) return false;
    k_spinlock_key_t key = k_spin_lock(&lock); int set = set_for(slot);
    bool result = reserved[slot] && connected[slot] && !draining[slot] &&
                  generations[slot] == generation && (set < 0 || set == active);
    k_spin_unlock(&lock, key); return result;
}
void lism_split_sets_applied(uint8_t slot, uint16_t position, bool pressed) {
    if (slot >= 2 || position >= 128) return;
    k_spinlock_key_t key = k_spin_lock(&lock); uint8_t mask = 1U << (position % 8);
    if (pressed) applied[slot][position / 8] |= mask;
    else applied[slot][position / 8] &= ~mask;
    k_spin_unlock(&lock, key);
}
bool lism_split_sets_take_cleanup(uint8_t slot, uint8_t positions[16]) {
    if (slot >= 2) return false;
    k_spinlock_key_t key = k_spin_lock(&lock);
    bool result = draining[slot] && !cleaning[slot];
    if (result) { cleaning[slot] = true; memcpy(positions, applied[slot], 16); memset(applied[slot], 0, 16); }
    k_spin_unlock(&lock, key); return result;
}
void lism_split_sets_cleanup_done(uint8_t slot) {
    if (slot >= 2) return;
    k_spinlock_key_t key = k_spin_lock(&lock);
    if (cleaning[slot]) draining[slot] = cleaning[slot] = false;
    unlock_empty_set(); k_spin_unlock(&lock, key);
}

void lism_split_sets_observe(const bt_addr_le_t *addr,const char *name,int8_t rssi) {(void)addr;(void)name;(void)rssi;}

/* Volatile role observations: no advertised identity is persisted here. */
static struct k_spinlock role_lock;
static struct {bt_addr_le_t addr; int role; int64_t seen;} role_cache[8];
void lism_legacy_role_observe(const bt_addr_le_t *addr,int role) {
    if(!addr || role<0 || role>1)return;
    k_spinlock_key_t key=k_spin_lock(&role_lock);
    unsigned index=0;int64_t oldest=INT64_MAX;
    for(unsigned i=0;i<8;i++) {
        if(role_cache[i].seen && !bt_addr_le_cmp(addr,&role_cache[i].addr)){index=i;break;}
        if(role_cache[i].seen<oldest){oldest=role_cache[i].seen;index=i;}
    }
    role_cache[index].addr=*addr;role_cache[index].role=role;role_cache[index].seen=k_uptime_get()+1;
    k_spin_unlock(&role_lock,key);
}
int lism_legacy_role_for_addr(const bt_addr_le_t *addr) {
    int role=-1;int64_t now=k_uptime_get()+1;
    k_spinlock_key_t key=k_spin_lock(&role_lock);
    for(unsigned i=0;i<8;i++) if(role_cache[i].seen && now-role_cache[i].seen<5000 &&
        !bt_addr_le_cmp(addr,&role_cache[i].addr)){role=role_cache[i].role;break;}
    k_spin_unlock(&role_lock,key);return role;
}
