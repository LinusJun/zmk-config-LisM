/* Source-owned external state, composed only at the outbound HID boundary. */
#include "lism_input_merge.h"
#include <errno.h>
#include <string.h>
#include <zephyr/kernel.h>

static struct k_spinlock merge_lock;
static struct lism_input_merge_state peers[LISM_INPUT_MERGE_SLOTS];
static uint32_t generation = 1;

uint32_t lism_input_merge_generation(void) {
    k_spinlock_key_t key = k_spin_lock(&merge_lock);
    uint32_t value = generation;
    k_spin_unlock(&merge_lock, key);
    return value;
}

int lism_input_merge_set(unsigned int slot, uint32_t epoch,
                         const struct lism_input_merge_state *state) {
    if (slot >= LISM_INPUT_MERGE_SLOTS || !state ||
        state->consumer_count > LISM_INPUT_MERGE_CONSUMERS) {
        return -EINVAL;
    }
    k_spinlock_key_t key = k_spin_lock(&merge_lock);
    int result = epoch == generation ? 0 : -ESTALE;
    if (!result) {
        peers[slot] = *state;
    }
    k_spin_unlock(&merge_lock, key);
    return result;
}

int lism_input_merge_clear(unsigned int slot, uint32_t epoch) {
    const struct lism_input_merge_state empty = {0};
    return lism_input_merge_set(slot, epoch, &empty);
}

__attribute__((weak)) void lism_input_merge_endpoint_changed(uint32_t epoch) { (void)epoch; }
__attribute__((weak)) void lism_input_merge_endpoint_changing(void) {}

void lism_input_merge_endpoint_reset(void) {
    lism_input_merge_endpoint_changing();
    k_spinlock_key_t key = k_spin_lock(&merge_lock);
    memset(peers, 0, sizeof(peers));
    if (++generation == 0) {
        generation = 1;
    }
    uint32_t epoch = generation;
    k_spin_unlock(&merge_lock, key);
    lism_input_merge_endpoint_changed(epoch);
}

static bool key_set(const uint8_t keys[32], unsigned int usage) {
    return (keys[usage / 8] & (1u << (usage % 8))) != 0;
}

static void merge_array(uint8_t *keys, unsigned int capacity, uint8_t *mods) {
    uint8_t union_keys[32] = {0};
    bool overflow = false;
    for (unsigned int i = 0; i < capacity; i++) {
        unsigned int usage = keys[i];
        if (usage >= 1 && usage <= 3) {
            overflow = true;
        } else if (usage >= 4) {
            union_keys[usage / 8] |= 1u << (usage % 8);
        }
    }
    for (unsigned int slot = 0; slot < LISM_INPUT_MERGE_SLOTS; slot++) {
        *mods |= peers[slot].modifiers;
        overflow |= peers[slot].rollover;
        for (unsigned int i = 0; i < sizeof(union_keys); i++) {
            union_keys[i] |= peers[slot].keys[i];
        }
    }
    memset(keys, 0, capacity);
    unsigned int count = 0;
    for (unsigned int usage = 4; usage < 256; usage++) {
        if (!key_set(union_keys, usage)) {
            continue;
        }
        if (usage >= 0xe0 && usage <= 0xe7) {
            *mods |= 1u << (usage - 0xe0);
        } else if (count < capacity) {
            keys[count++] = usage;
        } else {
            overflow = true;
        }
    }
    if (overflow) {
        memset(keys, 1, capacity); /* HID ErrorRollOver, recover on next complete state. */
    }
}

void lism_input_merge_keyboard(struct zmk_hid_keyboard_report *copy) {
    k_spinlock_key_t key = k_spin_lock(&merge_lock);
#if IS_ENABLED(CONFIG_ZMK_HID_REPORT_TYPE_HKRO)
    merge_array(copy->body.keys, sizeof(copy->body.keys), &copy->body.modifiers);
#else
    for (unsigned int slot = 0; slot < LISM_INPUT_MERGE_SLOTS; slot++) {
        copy->body.modifiers |= peers[slot].modifiers;
        for (unsigned int usage = 4; usage <= ZMK_HID_KEYBOARD_NKRO_MAX_USAGE; usage++) {
            if (usage < 256 && key_set(peers[slot].keys, usage)) {
                copy->body.keys[usage / 8] |= 1u << (usage % 8);
            }
        }
    }
#endif
    k_spin_unlock(&merge_lock, key);
}

#if IS_ENABLED(CONFIG_ZMK_USB_BOOT)
void lism_input_merge_boot(zmk_hid_boot_report_t *copy) {
    k_spinlock_key_t key = k_spin_lock(&merge_lock);
    merge_array(copy->keys, sizeof(copy->keys), &copy->modifiers);
    k_spin_unlock(&merge_lock, key);
}
#endif

void lism_input_merge_consumer(struct zmk_hid_consumer_report *copy) {
    k_spinlock_key_t key = k_spin_lock(&merge_lock);
    /* Keep local usages first, then peer slots in stable order; drop overflow,
     * never drop an unrelated held usage merely because another source releases. */
    unsigned int used = 0;
    for (unsigned int i = 0; i < ARRAY_SIZE(copy->body.keys); i++) {
        uint16_t usage = copy->body.keys[i];
        bool found = false;
        for (unsigned int j = 0; j < used; j++) { found |= copy->body.keys[j] == usage; }
        if (usage && !found) { copy->body.keys[used++] = usage; }
    }
    for (unsigned int slot = 0; slot < LISM_INPUT_MERGE_SLOTS; slot++) {
        for (unsigned int i = 0; i < peers[slot].consumer_count; i++) {
            uint16_t usage = peers[slot].consumer[i];
#if IS_ENABLED(CONFIG_ZMK_HID_CONSUMER_REPORT_USAGES_BASIC)
            if (usage > UINT8_MAX) { continue; }
#endif
            bool found = false;
            for (unsigned int j = 0; j < used; j++) { found |= copy->body.keys[j] == usage; }
            if (usage && !found && used < ARRAY_SIZE(copy->body.keys)) {
                copy->body.keys[used++] = usage;
            }
        }
    }
    for (; used < ARRAY_SIZE(copy->body.keys); used++) { copy->body.keys[used] = 0; }
    k_spin_unlock(&merge_lock, key);
}
