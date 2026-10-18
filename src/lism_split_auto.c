/* Automatic enrollment is commissioning, not cryptographic role attestation.
 * A durable pending transaction precedes a RAM-only address reservation. The
 * final role is saved only by the encrypted split-discovery READY callback.
 */
#include "lism_split_auto.h"
#include "lism_split_sets.h"
#include "lism_hid_host.h"
#include "lism_input_security.h"
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/random/random.h>
#include <zephyr/logging/log.h>
#include <errno.h>
#include <string.h>
LOG_MODULE_REGISTER(lism_split_auto, LOG_LEVEL_INF);

struct auto_candidate {
    bt_addr_le_t addr;
    int64_t adv_at,role_at,first_at;
    uint8_t count;
    int8_t role;
    bool used,invalid;
};
static struct auto_candidate candidates[8];
static struct k_spinlock lock;
struct pending_record {uint8_t version,slot;bt_addr_le_t addr;uint32_t nonce;};
static struct pending_record pending;
static struct pending_record recovery;
static bool recovery_valid;
static bool loaded,pending_valid,staged,committed;
static atomic_t pending_flag,cancel_requested;
static uint32_t ready_generations[6];
static bool ready_valid[6];
static int64_t started_at;
static void automatic_work(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(auto_work,automatic_work);

void lism_split_auto_observe(const bt_addr_le_t *addr,int8_t rssi,uint8_t type,
                            bool split,int role) {
    if (!addr || lism_input_non_split_identity(addr) ||
        lism_split_sets_find_addr(addr)>=0) return;
    k_spinlock_key_t recovery_key=k_spin_lock(&lock);
    bool recoverable=recovery_valid && !bt_addr_le_cmp(addr,&recovery.addr);
    k_spin_unlock(&lock,recovery_key);
    if(recoverable) {
        if(type==BT_GAP_ADV_TYPE_ADV_DIRECT_IND || type==BT_GAP_ADV_TYPE_ADV_IND)
            lism_split_sets_observe(addr,"LisM pending: select to retry",rssi);
        return;
    }
    if(rssi < -80)return;
    bool advertisement=type==BT_GAP_ADV_TYPE_ADV_IND && split;
    bool response=type==BT_GAP_ADV_TYPE_SCAN_RSP && role!=-1;
    if (!advertisement && !response) return;
    int64_t now=k_uptime_get();
    k_spinlock_key_t key=k_spin_lock(&lock);
    int index=-1,empty=-1;
    for (unsigned i=0;i<ARRAY_SIZE(candidates);i++) {
        if (candidates[i].used && !bt_addr_le_cmp(&candidates[i].addr,addr)) {index=i;break;}
        if (!candidates[i].used || now-MAX(candidates[i].adv_at,candidates[i].role_at)>3000) empty=i;
    }
    if (index<0)index=empty;
    if (index>=0) {
        struct auto_candidate *c=&candidates[index];
        if (!c->used || now-MAX(c->adv_at,c->role_at)>3000) {
            memset(c,0,sizeof(*c));c->addr=*addr;c->used=true;c->role=-1;c->first_at=now;
        }
        if (advertisement) {c->adv_at=now;if(c->count<255)c->count++;}
        if (response) {
            if (role<0 || (c->role>=0 && c->role!=role)) c->invalid=true;
            c->role=role;c->role_at=now;
        }
    }
    k_spin_unlock(&lock,key);
    k_work_schedule(&auto_work,K_MSEC(250));
}
void lism_split_auto_ready(uint8_t slot) {
    if(slot>=6)return;
    uint32_t generation=lism_split_sets_generation(slot);
    k_spinlock_key_t key=k_spin_lock(&lock);
    ready_generations[slot]=generation;ready_valid[slot]=true;
    k_spin_unlock(&lock,key);
    k_work_reschedule(&auto_work,K_NO_WAIT);
}
bool lism_split_auto_pending(void) {return atomic_get(&pending_flag)!=0;}
void lism_split_auto_cancel(void) {
    atomic_set(&cancel_requested,1);k_work_reschedule(&auto_work,K_NO_WAIT);
}
static void forget_transaction(void) {
    pending_valid=staged=committed=false;atomic_clear(&pending_flag);
    atomic_clear(&cancel_requested);lism_split_sets_auto_release();
}
static bool release_incomplete(void) {
    if(!lism_split_sets_auto_begin_release(pending.slot))return false;
    atomic_set(&cancel_requested,1); /* once release starts, never fall through to commit */
    bool released=false;
    /* Retain the exact failed identity for manual retry, including directed
     * advertisements from a peer whose SMP bond completed before discovery. */
    int rc=settings_save_one("lism_sets/recover",&pending,sizeof(pending));
    if(rc)goto done;
    k_spinlock_key_t key=k_spin_lock(&lock);recovery=pending;recovery_valid=true;k_spin_unlock(&lock,key);
    int found=lism_split_sets_find_addr(&pending.addr);
    if(found==pending.slot)rc=lism_split_sets_unbind_addr(pending.slot,&pending.addr);
    else rc=found<0 && !lism_split_sets_slot_registered(pending.slot)?0:-ESTALE;
    if(rc)goto done;
    rc=settings_delete("lism_sets/pending");
    if(rc && rc!=-ENOENT)goto done;
    released=true;
done:
    lism_split_sets_auto_end_release(pending.slot);
    if(!released)return false;
    forget_transaction();
    lism_split_sets_auto_status("Auto paused; Register can retry exact pending device");
    return true;
}
static void automatic_work(struct k_work *work) {
    ARG_UNUSED(work);
    if (!loaded)return;
    if (pending_valid) {
        if(!lism_split_sets_auto_hold()) {k_work_reschedule(&auto_work,K_SECONDS(1));return;}
        if(!committed && (atomic_get(&cancel_requested) || k_uptime_get()-started_at>=45000)) {
            if(release_incomplete())return;
            lism_split_sets_auto_status(atomic_get(&cancel_requested)?
                "Cancel pending: power off new half to release":"Slow pairing: waiting for secure split ready");
            k_work_reschedule(&auto_work,K_SECONDS(1));
            if(atomic_get(&cancel_requested))return;
        }
        if (!staged && !committed) {
            if(lism_input_non_split_identity(&pending.addr)) {
                atomic_set(&cancel_requested,1);k_work_reschedule(&auto_work,K_NO_WAIT);return;
            }
            int rc=lism_split_sets_stage_addr(pending.slot,&pending.addr);
            if (rc) {lism_split_sets_auto_status("Pending role conflicts; existing roles kept");return;}
            staged=true;
            LOG_INF("Automatic LisM slot %u pending; roles and bonds preserved",pending.slot+1);
        }
        k_spinlock_key_t key=k_spin_lock(&lock);
        bool ready=ready_valid[pending.slot];uint32_t generation=ready_generations[pending.slot];
        k_spin_unlock(&lock,key);
        if (!committed && ready && generation==lism_split_sets_generation(pending.slot) &&
            lism_split_sets_find_addr(&pending.addr)==pending.slot &&
            !lism_input_non_split_identity(&pending.addr)) {
            int rc=lism_split_sets_commit_addr(pending.slot,&pending.addr);
            if (!rc)committed=true;
            else {lism_split_sets_auto_status("Connected; saving role will retry");}
        }
        if (committed) {
            int rc=settings_delete("lism_sets/pending");
            if (!rc || rc==-ENOENT) {
                LOG_INF("Automatic LisM slot %u registered after secure split ready",pending.slot+1);
                lism_split_sets_auto_status(pending.slot<2?"First LisM set role registered":"Second LisM set role registered");
                forget_transaction();
            }
        }
        lism_hid_scan_resume();
        if(pending_valid)k_work_reschedule(&auto_work,K_SECONDS(1));
        return;
    }
    /* Do not overwrite the only recovery record with a second failed peer.
     * Manual Register remains available, including the exact directed target. */
    if(recovery_valid && lism_split_sets_find_addr(&recovery.addr)<0) {
        lism_split_sets_auto_status("Resolve pending device with manual Register first");return;
    }
    struct auto_candidate copy[8];
    k_spinlock_key_t key=k_spin_lock(&lock);memcpy(copy,candidates,sizeof(copy));k_spin_unlock(&lock,key);
    int64_t now=k_uptime_get();
    int count[2]={0},selected[2]={-1,-1};
    for(unsigned i=0;i<ARRAY_SIZE(copy);i++) {
        const struct auto_candidate *c=&copy[i];
        if (!c->used || c->invalid || c->role<0 || c->role>1 || !c->adv_at || !c->role_at ||
            now-c->adv_at>2000 || now-c->role_at>2000 ||
            lism_split_sets_find_addr(&c->addr)>=0 || lism_input_non_split_identity(&c->addr)) continue;
        count[c->role]++;selected[c->role]=i;
    }
    if(count[0]>1 || count[1]>1) {
        lism_split_sets_auto_status("Multiple same-side LisM: leave only one powered");return;
    }
    for(unsigned role=0;role<2;role++) {
        if(count[role]!=1)continue;
        const struct auto_candidate *c=&copy[selected[role]];
        if(c->count<3 || now-c->first_at<1200)continue;
        int slot=lism_split_sets_auto_claim(role);
        if(slot<0)continue;
        struct pending_record next={.version=1,.slot=slot,.addr=c->addr,.nonce=sys_rand32_get()};
        if(!next.nonce)next.nonce=1;
        int rc=settings_save_one("lism_sets/pending",&next,sizeof(next));
        if(rc) {
            lism_split_sets_auto_release();lism_split_sets_auto_status("Automatic role save failed; no role changed");return;
        }
        pending=next;pending_valid=true;atomic_set(&pending_flag,1);started_at=now;
        key=k_spin_lock(&lock);memset(ready_valid,0,sizeof(ready_valid));k_spin_unlock(&lock,key);
        lism_split_sets_auto_status("Automatic pairing; waiting for secure split");
        k_work_reschedule(&auto_work,K_NO_WAIT);return;
    }
}
static int auto_settings_set(const char *name,size_t length,settings_read_cb read,void *context) {
    if(strcmp(name,"pending") && strcmp(name,"recover"))return -ENOENT;
    struct pending_record item;
    if(length!=sizeof(item) || read(context,&item,sizeof(item))!=sizeof(item) ||
        item.version!=1 || (item.slot!=0 && item.slot!=1 && item.slot!=4 && item.slot!=5) ||
        !bt_addr_le_cmp(&item.addr,BT_ADDR_LE_ANY)) return -EINVAL;
    if(!strcmp(name,"recover")) {recovery=item;recovery_valid=true;}
    else {pending=item;pending_valid=true;atomic_set(&pending_flag,1);}
    return 0;
}
static int auto_settings_commit(void) {
    loaded=true;
    started_at=k_uptime_get();
    if(pending_valid) {
        int found=lism_split_sets_find_addr(&pending.addr);
        if(recovery_valid && recovery.slot==pending.slot && recovery.nonce==pending.nonce &&
           !bt_addr_le_cmp(&recovery.addr,&pending.addr) && found<0) {
            atomic_set(&cancel_requested,1);
        }
        if(found==pending.slot)committed=true; /* power loss after final role write */
        else if(found>=0 || lism_split_sets_slot_registered(pending.slot)) {
            lism_split_sets_auto_status("Saved pending role conflicts; no overwrite");
            return 0;
        }
    }
    k_work_reschedule(&auto_work,K_SECONDS(1));return 0;
}
SETTINGS_STATIC_HANDLER_DEFINE(lism_sets,"lism_sets",NULL,auto_settings_set,auto_settings_commit,NULL);
