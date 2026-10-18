#include "lism_split_sets.h"
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
#include "lism_split_auto.h"
#endif

#if defined(CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS) && CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS != 6
#error "Two LisM sets require exactly six physical split slots"
#endif

static struct k_spinlock lock;
static bool reserved[6], connected[6], draining[6], cleaning[6];
static bool fault[6];
static bool enrollment_busy[6];
static uint32_t generations[6];
static uint8_t applied[6][16];
static int active = -1, enrolling = -1;
static int pending_slot = -1;
static bt_addr_le_t pending_address;
static uint32_t next_token;
static struct lism_split_set_candidate candidates[8];
static int64_t seen[8];
static char status[80] = "Choose an empty role to register";
#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
static bool auto_busy;
static bool any_draining(void);
int lism_split_sets_auto_claim(uint8_t role) {
    if (role > 1) return -EINVAL;
    /* Address writers run only in work/UI contexts, not under the policy lock. */
    bool present[4]={lism_split_sets_slot_registered(0),lism_split_sets_slot_registered(1),
                     lism_split_sets_slot_registered(4),lism_split_sets_slot_registered(5)};
    k_spinlock_key_t key=k_spin_lock(&lock);
    int result=-EBUSY;
    if (!auto_busy && enrolling<0 && pending_slot<0 && !any_draining()) {
        int set=active>=0?active:(!present[0]||!present[1]?0:1);
        if (!present[set*2+role] && (!role || present[set*2])) {
            auto_busy=true;result=(set?4:0)+role;
        }
    }
    k_spin_unlock(&lock,key);return result;
}
void lism_split_sets_auto_release(void) {
    k_spinlock_key_t key=k_spin_lock(&lock);auto_busy=false;k_spin_unlock(&lock,key);
}
bool lism_split_sets_auto_hold(void) {
    k_spinlock_key_t key=k_spin_lock(&lock);
    bool allowed=enrolling<0 && pending_slot<0;
    if (allowed)auto_busy=true;
    k_spin_unlock(&lock,key);return allowed;
}
bool lism_split_sets_auto_begin_release(uint8_t slot) {
    if(slot>=6)return false;
    k_spinlock_key_t key=k_spin_lock(&lock);
    bool safe=!reserved[slot] && !connected[slot] && !draining[slot] && !enrollment_busy[slot];
    if(safe)enrollment_busy[slot]=true;
    k_spin_unlock(&lock,key);return safe;
}
void lism_split_sets_auto_end_release(uint8_t slot) {
    if(slot>=6)return;
    k_spinlock_key_t key=k_spin_lock(&lock);enrollment_busy[slot]=false;k_spin_unlock(&lock,key);
}
void lism_split_sets_auto_status(const char *text) {
    k_spinlock_key_t key=k_spin_lock(&lock);snprintf(status,sizeof(status),"%s",text);k_spin_unlock(&lock,key);
}
#endif

static int set_for(uint8_t slot) { return slot < 2 ? 0 : slot >= 4 && slot < 6 ? 1 : -1; }
static bool busy_set(int set) {
    unsigned left = set ? 4 : 0;
    return reserved[left] || reserved[left + 1] || draining[left] || draining[left + 1];
}
static void unlock_empty_set(void) { if (active >= 0 && !busy_set(active)) active = -1; }
static bool any_draining(void) {
    for (unsigned i = 0; i < 6; ++i) if (draining[i]) return true;
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
    return (lism_split_sets_active_set() == 1 ? 4 : 0) + (role == 1);
}
int lism_split_sets_reserve(uint8_t slot) {
    if (slot >= 6) return -EINVAL;
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
    if (slot >= 6) return;
    k_spinlock_key_t key = k_spin_lock(&lock);
    if (reserved[slot] && !draining[slot]) {
        connected[slot] = true;
    }
    k_spin_unlock(&lock, key);
}
void lism_split_sets_ready(uint8_t slot) {
    if (slot >= 6) return;
    k_spinlock_key_t key = k_spin_lock(&lock);
    bool ready=connected[slot] && !draining[slot];
    if (ready && pending_slot == slot) pending_slot = -1;
    k_spin_unlock(&lock, key);
#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
    if(ready)lism_split_auto_ready(slot);
#endif
}
void lism_split_sets_released(uint8_t slot) {
    if (slot >= 6) return;
    k_spinlock_key_t key = k_spin_lock(&lock);
    reserved[slot] = connected[slot] = false;
    if (!draining[slot]) { draining[slot] = true; ++generations[slot]; }
    k_spin_unlock(&lock, key);
}
void lism_split_sets_fault(uint8_t slot) {
    if (slot >= 6) return;
    k_spinlock_key_t key = k_spin_lock(&lock);
    if (reserved[slot] && !draining[slot]) {
        connected[slot] = false; draining[slot] = true; fault[slot] = true; ++generations[slot];
    }
    k_spin_unlock(&lock, key);
}
bool lism_split_sets_take_fault(uint8_t slot) {
    if (slot >= 6) return false;
    k_spinlock_key_t key = k_spin_lock(&lock); bool result = fault[slot]; fault[slot] = false;
    k_spin_unlock(&lock, key); return result;
}
uint32_t lism_split_sets_generation(uint8_t slot) {
    if (slot >= 6) return 0;
    k_spinlock_key_t key = k_spin_lock(&lock); uint32_t result = generations[slot];
    k_spin_unlock(&lock, key); return result;
}
bool lism_split_sets_dispatch(uint8_t slot, uint32_t generation) {
    if (slot >= 6) return false;
    k_spinlock_key_t key = k_spin_lock(&lock); int set = set_for(slot);
    bool result = reserved[slot] && connected[slot] && !draining[slot] &&
                  generations[slot] == generation && (set < 0 || set == active);
    k_spin_unlock(&lock, key); return result;
}
void lism_split_sets_applied(uint8_t slot, uint16_t position, bool pressed) {
    if (slot >= 6 || position >= 128) return;
    k_spinlock_key_t key = k_spin_lock(&lock); uint8_t mask = 1U << (position % 8);
    if (pressed) applied[slot][position / 8] |= mask;
    else applied[slot][position / 8] &= ~mask;
    k_spin_unlock(&lock, key);
}
bool lism_split_sets_take_cleanup(uint8_t slot, uint8_t positions[16]) {
    if (slot >= 6) return false;
    k_spinlock_key_t key = k_spin_lock(&lock);
    bool result = draining[slot] && !cleaning[slot];
    if (result) { cleaning[slot] = true; memcpy(positions, applied[slot], 16); memset(applied[slot], 0, 16); }
    k_spin_unlock(&lock, key); return result;
}
void lism_split_sets_cleanup_done(uint8_t slot) {
    if (slot >= 6) return;
    k_spinlock_key_t key = k_spin_lock(&lock);
    if (cleaning[slot]) draining[slot] = cleaning[slot] = false;
    unlock_empty_set(); k_spin_unlock(&lock, key);
}
/* Rollback only an address added by this boot's unconnected enrollment.
 * No bt_unpair: existing peers/host bonds and pre-existing roles are untouched. */
static int rollback_pending(void) {
    k_spinlock_key_t key = k_spin_lock(&lock); int slot = pending_slot;
    if (slot < 0) { k_spin_unlock(&lock, key); return 0; }
    if (reserved[slot] || connected[slot] || draining[slot] || enrollment_busy[slot]) {
        k_spin_unlock(&lock, key); return -EBUSY;
    }
    enrollment_busy[slot] = true; bt_addr_le_t address = pending_address;
    k_spin_unlock(&lock, key);
    int rc = lism_split_sets_unbind_addr(slot, &address);
    key = k_spin_lock(&lock); enrollment_busy[slot] = false;
    if (!rc && pending_slot == slot) pending_slot = -1;
    k_spin_unlock(&lock, key); return rc;
}
int lism_split_sets_register_begin(uint8_t slot) {
    if (slot >= 6) return -EINVAL;
#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
    k_spinlock_key_t auto_key=k_spin_lock(&lock);bool busy=auto_busy;k_spin_unlock(&lock,auto_key);
    if (busy) { lism_split_sets_auto_status("Automatic pairing pending; wait for completion"); return -EBUSY; }
#endif
    int rc = rollback_pending();
    if (rc) return rc;
    if (lism_split_sets_slot_registered(slot)) return -EEXIST;
    k_spinlock_key_t key = k_spin_lock(&lock);
#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
    if(auto_busy) {k_spin_unlock(&lock,key);return -EBUSY;}
#endif
    enrolling = slot; memset(candidates, 0, sizeof(candidates)); memset(seen, 0, sizeof(seen));
    snprintf(status, sizeof(status), "Register slot %u: select exact device", slot + 1);
    k_spin_unlock(&lock, key); return 0;
}
void lism_split_sets_register_cancel(void) {
#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
    if(lism_split_auto_pending())lism_split_auto_cancel();
#endif
    int rc = rollback_pending();
    k_spinlock_key_t key = k_spin_lock(&lock); enrolling = -1;
    snprintf(status, sizeof(status), rc ? "Power off new device before correcting its role" : "Registration cancelled; bonds unchanged");
    k_spin_unlock(&lock, key);
}
void lism_split_sets_observe(const bt_addr_le_t *addr, const char *name, int8_t rssi) {
    if (!addr || lism_split_sets_find_addr(addr) >= 0) return;
    k_spinlock_key_t key = k_spin_lock(&lock);
    if (enrolling < 0) { k_spin_unlock(&lock, key); return; }
    unsigned selected = 8; int64_t oldest = INT64_MAX;
    for (unsigned i = 0; i < 8; ++i) {
        if (candidates[i].token && !bt_addr_le_cmp(addr, &candidates[i].address)) { selected = i; break; }
        if (seen[i] < oldest) { oldest = seen[i]; selected = i; }
    }
    struct lism_split_set_candidate *item = &candidates[selected];
    if (!item->token || bt_addr_le_cmp(addr, &item->address)) {
        if (++next_token == 0) ++next_token;
        memset(item, 0, sizeof(*item));
        item->token = next_token; bt_addr_le_copy(&item->address, addr);
    }
    if (name && name[0]) snprintf(item->name, sizeof(item->name), "%s", name);
    else if (!item->name[0]) snprintf(item->name, sizeof(item->name), "LisM split");
    item->rssi = rssi; seen[selected] = k_uptime_get();
    k_spin_unlock(&lock, key);
}
size_t lism_split_sets_candidates(struct lism_split_set_candidate *out, size_t capacity) {
    if (!out) return 0;
    k_spinlock_key_t key = k_spin_lock(&lock); size_t count = 0; int64_t now = k_uptime_get();
    if (enrolling >= 0) for (unsigned i = 0; i < 8 && count < capacity; ++i)
        if (candidates[i].token && now - seen[i] <= 15000) out[count++] = candidates[i];
    k_spin_unlock(&lock, key); return count;
}
static void count_bond(const struct bt_bond_info *info, void *user) { (void)info; ++*(unsigned *)user; }
int lism_split_sets_register_select(uint32_t token) {
    bt_addr_le_t address; int slot = -1; int64_t now = k_uptime_get();
    k_spinlock_key_t key = k_spin_lock(&lock);
    for (unsigned i = 0; enrolling >= 0 && i < 8; ++i)
        if (candidates[i].token == token && token && now - seen[i] <= 15000) {
            slot = enrolling; bt_addr_le_copy(&address, &candidates[i].address); break;
        }
    k_spin_unlock(&lock, key);
    if (slot < 0) return -ESTALE;
    unsigned bonds = 0; bt_foreach_bond(BT_ID_DEFAULT, count_bond, &bonds);
    key = k_spin_lock(&lock); enrollment_busy[slot] = true; k_spin_unlock(&lock, key);
    int result = bonds >= CONFIG_BT_MAX_PAIRED ? -ENOSPC : lism_split_sets_bind_addr(slot, &address);
    key = k_spin_lock(&lock);
    enrollment_busy[slot] = false;
    if (!result) { enrolling = -1; pending_slot = slot; pending_address = address; snprintf(status, sizeof(status), "Registered slot %d; waiting for connection", slot + 1); }
    else snprintf(status, sizeof(status), result == -ENOSPC ? "Bond storage full; nothing deleted" : "Registration failed; existing roles unchanged");
    k_spin_unlock(&lock, key); return result;
}
void lism_split_sets_get_status(char *out, size_t capacity) {
    if (!out || !capacity) return;
    k_spinlock_key_t key = k_spin_lock(&lock); snprintf(out, capacity, "%s", status);
    k_spin_unlock(&lock, key);
}
