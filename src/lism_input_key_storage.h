/* Private adapter included by the pinned Zephyr keys.c copy, after keys.h.
 * Do not persist sizeof(struct bt_keys): SC-only and compatibility builds have
 * different sizes. One real 52-byte record, no shadow keys or resurrection path.
 */
#pragma once

BUILD_ASSERT(!IS_ENABLED(CONFIG_BT_SIGNING), "Signing changes the key wire ABI");
BUILD_ASSERT(!IS_ENABLED(CONFIG_BT_KEYS_OVERWRITE_OLDEST), "Never evict existing bonds");
BUILD_ASSERT(!IS_ENABLED(CONFIG_BT_SMP_SC_PAIR_ONLY), "Adapter requires legacy runtime");
#define LISM_KEYS_WIRE_LEN 52u
BUILD_ASSERT(offsetof(struct bt_keys, periph_ltk) -
             offsetof(struct bt_keys, storage_start) == LISM_KEYS_WIRE_LEN);
BUILD_ASSERT(BT_KEYS_STORAGE_LEN == 80u);
BUILD_ASSERT(sizeof(struct bt_keys) == 92u);

static bool lism_keys_valid(const struct bt_keys *keys, bool extended)
{
    const uint8_t known_flags = BT_KEYS_AUTHENTICATED | BT_KEYS_SC | BT_KEYS_OOB |
                                BIT(2) | BIT(3); /* documented historical bits */
    if (keys->enc_size != 16 || (keys->flags & ~known_flags)) return false;
    if (keys->flags & BT_KEYS_SC) {
        if (!(keys->keys & BT_KEYS_LTK_P256) ||
            (keys->keys & ~(BT_KEYS_LTK_P256 | BT_KEYS_IRK))) return false;
        if (extended) {
            const uint8_t *extra = keys->storage_start + LISM_KEYS_WIRE_LEN;
            for (size_t i = 0; i < BT_KEYS_STORAGE_LEN - LISM_KEYS_WIRE_LEN; ++i)
                if (extra[i]) return false;
        }
    } else {
        /* Only the peer-distributed central LTK is usable. Never reinterpret
         * our distributed peripheral-role LTK as that peer's central LTK. */
        if (!(keys->keys & BT_KEYS_LTK) ||
            (keys->keys & ~(BT_KEYS_LTK | BT_KEYS_IRK |
                           (extended ? BT_KEYS_PERIPH_LTK : 0)))) return false;
    }
    return true;
}

static int lism_keys_store(struct bt_keys *keys)
{
    struct bt_keys wire = {0};
    memcpy(wire.storage_start, keys->storage_start, LISM_KEYS_WIRE_LEN);
    if (!(wire.flags & BT_KEYS_SC)) {
        if (!lism_input_legacy_key_allowed(keys->id, &keys->addr)) return -EACCES;
        wire.keys &= ~BT_KEYS_PERIPH_LTK;
    }
    if (!lism_keys_valid(&wire, false)) return -EINVAL;
    return bt_settings_store_keys(keys->id, &keys->addr, wire.storage_start,
                                  LISM_KEYS_WIRE_LEN);
}

static int lism_keys_set(const char *name, size_t length, settings_read_cb read,
                          void *context)
{
    bt_addr_le_t addr;
    uint8_t id = BT_ID_DEFAULT;
    const char *next;
    if (!name || bt_settings_decode_key(name, &addr) ||
        bt_addr_le_eq(&addr, BT_ADDR_LE_ANY)) return -EINVAL;
    settings_name_next(name, &next);
    if (next) {
        char *end;
        if (!*next) return -EINVAL;
        unsigned long value = strtoul(next, &end, 10);
        if (*end || value >= CONFIG_BT_ID_MAX) return -EINVAL;
        id = value;
    }
    if (!length) {
        uint8_t unused;
        if (read(context, &unused, 1) != 0) return -EINVAL;
        struct bt_keys *keys = bt_keys_find_addr(id, &addr);
        if (keys) {
            if (keys->state & BT_KEYS_ID_ADDED) bt_id_del(keys);
            memset(keys, 0, sizeof(*keys));
        }
        return 0;
    }
    if (length != LISM_KEYS_WIRE_LEN && length != BT_KEYS_STORAGE_LEN) {
        LOG_WRN("Key record length %zu unsupported; retained unchanged", length);
        return -EINVAL;
    }
    struct bt_keys decoded = {0};
    /* Inspect len_rd before read: NVS may clamp a longer record to our buffer. */
    if (read(context, decoded.storage_start, length) != (ssize_t)length ||
        !lism_keys_valid(&decoded, length == BT_KEYS_STORAGE_LEN)) {
        LOG_WRN("Key record invalid; retained unchanged");
        return -EINVAL;
    }
    struct bt_keys *keys = bt_keys_get_addr(id, &addr);
    if (!keys) return -ENOMEM;
    memset(keys->storage_start, 0, BT_KEYS_STORAGE_LEN);
    memcpy(keys->storage_start, decoded.storage_start, LISM_KEYS_WIRE_LEN);
    keys->keys &= ~BT_KEYS_PERIPH_LTK;
    /* Never migrate/save/delete during settings_load. A later explicit pair
     * stores canonical52; clearing this same record still uses upstream unpair. */
    return 0;
}
