# Apply after lism_hid_patch_ble / lism_hid_patch_central to their generated
# sources. Each pinned anchor is checked: upstream drift must stop the build.
function(lism_split_sets_region variable first last replacement)
  string(FIND "${${variable}}" "${first}" begin)
  if(begin LESS 0)
    message(FATAL_ERROR "Pinned split-set region missing: ${first}")
  endif()
  string(SUBSTRING "${${variable}}" ${begin} -1 remaining)
  string(FIND "${remaining}" "${last}" relative_end)
  math(EXPR end "${begin} + ${relative_end}")
  if(relative_end LESS 0)
    message(FATAL_ERROR "Pinned split-set region moved: ${first}")
  endif()
  math(EXPR length "${end} - ${begin}")
  string(SUBSTRING "${${variable}}" ${begin} ${length} old)
  lism_hid_replace_once(${variable} "${old}" "${replacement}")
  set(${variable} "${${variable}}" PARENT_SCOPE)
endfunction()

function(lism_split_sets_patch_ble input output header)
  file(READ "${input}" code)
  lism_hid_replace_once(code "#include <zmk/ble.h>"
    "#include <zmk/ble.h>\n#include \"${header}\"")
  set(allocator [=[static struct k_spinlock lism_split_address_lock;
static K_MUTEX_DEFINE(lism_split_address_write_lock);
int lism_split_sets_find_addr(const bt_addr_le_t *addr) {
    if (!addr || !bt_addr_le_cmp(addr, BT_ADDR_LE_ANY)) return -EINVAL;
    k_spinlock_key_t key = k_spin_lock(&lism_split_address_lock);
    int found = -ENOENT;
    for (int i = 0; i < ZMK_SPLIT_BLE_PERIPHERAL_COUNT; ++i) {
        if (!bt_addr_le_cmp(&peripheral_addrs[i], addr)) { found = i; break; }
    }
    k_spin_unlock(&lism_split_address_lock, key);
    return found;
}
bool lism_split_sets_slot_registered(uint8_t slot) {
    if (slot >= ZMK_SPLIT_BLE_PERIPHERAL_COUNT) return false;
    k_spinlock_key_t key = k_spin_lock(&lism_split_address_lock);
    bool present = bt_addr_le_cmp(&peripheral_addrs[slot], BT_ADDR_LE_ANY) != 0;
    k_spin_unlock(&lism_split_address_lock, key);
    return present;
}
int lism_split_sets_bind_addr(uint8_t slot, const bt_addr_le_t *addr) {
    if (slot >= ZMK_SPLIT_BLE_PERIPHERAL_COUNT || !addr ||
        !bt_addr_le_cmp(addr, BT_ADDR_LE_ANY)) return -EINVAL;
    k_mutex_lock(&lism_split_address_write_lock, K_FOREVER);
    int result = lism_split_sets_find_addr(addr);
    if (result >= 0) { result = result == slot ? 0 : -EEXIST; goto done; }
    if (lism_split_sets_slot_registered(slot)) { result = -EEXIST; goto done; }
#if IS_ENABLED(CONFIG_SETTINGS)
    char name[40];
    snprintf(name, sizeof(name), "ble/peripheral_addresses/%u", slot);
    result = settings_save_one(name, addr, sizeof(*addr));
    if (result) goto done;
#endif
    k_spinlock_key_t key = k_spin_lock(&lism_split_address_lock);
    bt_addr_le_copy(&peripheral_addrs[slot], addr);
    k_spin_unlock(&lism_split_address_lock, key);
    result = 0;
done:
    k_mutex_unlock(&lism_split_address_write_lock);
    return result;
}
int lism_split_sets_unbind_addr(uint8_t slot, const bt_addr_le_t *expected) {
    if (slot >= ZMK_SPLIT_BLE_PERIPHERAL_COUNT || !expected) return -EINVAL;
    k_mutex_lock(&lism_split_address_write_lock, K_FOREVER);
    int result = -ESTALE;
    if (lism_split_sets_find_addr(expected) != slot) goto done_unbind;
#if IS_ENABLED(CONFIG_SETTINGS)
    char name[40];
    snprintf(name, sizeof(name), "ble/peripheral_addresses/%u", slot);
    result = settings_delete(name);
    if (result) goto done_unbind;
#endif
    k_spinlock_key_t key = k_spin_lock(&lism_split_address_lock);
    bt_addr_le_copy(&peripheral_addrs[slot], BT_ADDR_LE_ANY);
    k_spin_unlock(&lism_split_address_lock, key);
    result = 0;
done_unbind:
    k_mutex_unlock(&lism_split_address_write_lock);
    return result;
}
#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
extern bool lism_input_non_split_identity(const bt_addr_le_t *addr);
/* Staging changes RAM only. A separate pending transaction survives power loss;
 * the actual role address is committed only after encrypted split discovery. */
int lism_split_sets_stage_addr(uint8_t slot,const bt_addr_le_t *addr) {
    if (slot>=ZMK_SPLIT_BLE_PERIPHERAL_COUNT || !addr ||
        !bt_addr_le_cmp(addr,BT_ADDR_LE_ANY)) return -EINVAL;
    k_mutex_lock(&lism_split_address_write_lock,K_FOREVER);
    int found=lism_split_sets_find_addr(addr),result=0;
    if(lism_input_non_split_identity(addr)) {result=-EACCES;goto stage_done;}
    if (found>=0) { result=found==slot?0:-EEXIST; goto stage_done; }
    if (lism_split_sets_slot_registered(slot)) { result=-EEXIST; goto stage_done; }
    k_spinlock_key_t key=k_spin_lock(&lism_split_address_lock);
    peripheral_addrs[slot]=*addr;k_spin_unlock(&lism_split_address_lock,key);
stage_done:
    k_mutex_unlock(&lism_split_address_write_lock);return result;
}
int lism_split_sets_commit_addr(uint8_t slot,const bt_addr_le_t *addr) {
    k_mutex_lock(&lism_split_address_write_lock,K_FOREVER);
    int result=-ESTALE;
    if (lism_split_sets_find_addr(addr)==slot && !lism_input_non_split_identity(addr)) {
        char name[40];snprintf(name,sizeof(name),"ble/peripheral_addresses/%u",slot);
        result=settings_save_one(name,addr,sizeof(*addr));
    }
    k_mutex_unlock(&lism_split_address_write_lock);return result;
}
#endif
/* Never allocate while walking: known slot 4 must not occupy empty slot 2.
 * Unknown peers require a user-selected role/address enrollment. */
int zmk_ble_put_peripheral_addr(const bt_addr_le_t *addr) {
    return lism_split_sets_find_addr(addr);
}

]=])
  lism_split_sets_region(code "int zmk_ble_put_peripheral_addr(const bt_addr_le_t *addr) {"
    "#endif /* IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL) */" "${allocator}")
  file(WRITE "${output}" "${code}")
endfunction()

function(lism_split_sets_patch_central input output header)
  file(READ "${input}" code)
  lism_hid_replace_once(code "#include <zmk/ble.h>"
    "#include <zmk/ble.h>\n#include \"${header}\"")
  set(input_state [=[#if IS_ENABLED(CONFIG_ZMK_INPUT_SPLIT)
static bool lism_input_cleanup_regs[LISM_SPLIT_SET_SLOTS][256];
static bool lism_input_cleanup_started[LISM_SPLIT_SET_SLOTS];
static bool lism_input_fence_requested[LISM_SPLIT_SET_SLOTS];
static uint32_t lism_input_fence_token[LISM_SPLIT_SET_SLOTS];
static atomic_t lism_input_fence_ack[LISM_SPLIT_SET_SLOTS];
int peripheral_slot_index_for_conn(struct bt_conn *conn);
#endif

]=])
  lism_hid_replace_once(code "static int start_scanning(void);"
    "${input_state}static int start_scanning(void);")
  lism_hid_replace_once(code "            zmk_input_split_peripheral_disconnected(peripheral_input_slots[i].reg);"
    "            /* Worker tracks only actually dispatched proxy regs. */")
  # Invalidate the source before detaching proxy subscriptions. The releases
  # themselves run only on the fenced event worker, never the BT callback.
  lism_hid_replace_once(code "    release_peripheral_input_subs(conn);" "    /* Proxy detach/cleanup is owned by release_peripheral_slot. */")
  lism_hid_replace_once(code "struct peripheral_event_wrapper {\n    uint8_t source;"
    "struct peripheral_event_wrapper {\n    uint8_t source;\n    uint32_t generation;")
  set(enqueue [=[K_WORK_DELAYABLE_DEFINE(peripheral_event_work, peripheral_event_work_callback);
static struct bt_conn *lism_pending_disconnect[LISM_SPLIT_SET_SLOTS];
static uint32_t lism_disconnect_generation[LISM_SPLIT_SET_SLOTS];
static int64_t lism_disconnect_deadline[LISM_SPLIT_SET_SLOTS];
static uint16_t lism_disconnect_delay[LISM_SPLIT_SET_SLOTS];

void lism_split_sets_input_fence_done(uint8_t source, uint32_t token) {
#if IS_ENABLED(CONFIG_ZMK_INPUT_SPLIT)
    if (source >= LISM_SPLIT_SET_SLOTS) return;
    atomic_set(&lism_input_fence_ack[source], token);
    k_work_reschedule(&peripheral_event_work, K_NO_WAIT);
#endif
}

static int lism_split_queue_event(struct peripheral_event_wrapper *event) {
    if (event->source >= LISM_SPLIT_SET_SLOTS) return -EINVAL;
    event->generation = lism_split_sets_generation(event->source);
    int result = k_msgq_put(&peripheral_event_msgq, event, K_NO_WAIT);
    /* position_state is already updated by the notification callback. A lost
     * key-up cannot be recovered by waiting for a new change notification. */
    if (result) lism_split_sets_fault(event->source);
    return result;
}]=])
  lism_hid_replace_once(code "K_WORK_DEFINE(peripheral_event_work, peripheral_event_work_callback);" "${enqueue}")
  # release is now a queue fence, not lossy K_NO_WAIT cleanup enqueues.
  lism_hid_replace_once(code "    LOG_DBG(\"Releasing peripheral slot at %d\", index);"
    "    LOG_DBG(\"Releasing peripheral slot at %d\", index);\n    lism_split_sets_released(index);\n#if IS_ENABLED(CONFIG_ZMK_INPUT_SPLIT)\n    if (slot->conn) release_peripheral_input_subs(slot->conn);\n#endif\n    k_work_submit(&peripheral_event_work);")
  lism_split_sets_region(code "    // Raise events releasing any active positions from this peripheral"
    "    // Clean up previously discovered handles;"
    "    memset(slot->position_state, 0, sizeof(slot->position_state));\n    memset(slot->changed_positions, 0, sizeof(slot->changed_positions));\n\n")
  # Remaining production enqueues include sensor, pointer, position, battery.
  string(REPLACE "k_msgq_put(&peripheral_event_msgq, &event_wrapper, K_NO_WAIT)"
    "lism_split_queue_event(&event_wrapper)" code "${code}")
  string(REPLACE "k_msgq_put(&peripheral_event_msgq, &ev, K_NO_WAIT)"
    "lism_split_queue_event(&ev)" code "${code}")
  lism_hid_replace_once(code "            release_peripheral_slot(i);\n            peripherals[i].state = PERIPHERAL_SLOT_STATE_CONNECTING;"
    "            if (lism_split_sets_reserve(i) != 0) return -EBUSY;\n            memset(peripherals[i].position_state, 0, sizeof(peripherals[i].position_state));\n            memset(peripherals[i].changed_positions, 0, sizeof(peripherals[i].changed_positions));\n            peripherals[i].state = PERIPHERAL_SLOT_STATE_CONNECTING;")
  lism_hid_replace_once(code "    peripherals[idx].state = PERIPHERAL_SLOT_STATE_CONNECTED;"
    "    peripherals[idx].state = PERIPHERAL_SLOT_STATE_CONNECTED;\n    lism_split_sets_connected(idx);")
  # reg=0 is a valid left-hand proxy. It cannot double as an unfinished
  # descriptor sentinel, nor may another disconnected role block this peer's
  # initialization forever.
  lism_hid_replace_once(code "    uint8_t reg;\n};"
    "    uint8_t reg;\n    bool lism_reg_ready;\n};")
  lism_hid_replace_once(code "            peripheral_input_slots[i].reg = 0;"
    "            peripheral_input_slots[i].reg = 0;\n            peripheral_input_slots[i].lism_reg_ready = false;")
  lism_hid_replace_once(code "!peripheral_input_slots[i].sub.ccc_handle || !peripheral_input_slots[i].reg)"
    "!peripheral_input_slots[i].sub.ccc_handle || !peripheral_input_slots[i].lism_reg_ready)")
  lism_hid_replace_once(code "                input_slot->reg = cpf->description;"
    "                input_slot->reg = cpf->description;\n                input_slot->lism_reg_ready = true;")
  lism_hid_replace_once(code "        if (input_slot_is_open(i) || input_slot_is_pending(i)) {"
    "        if (peripheral_input_slots[i].conn == conn && input_slot_is_pending(i)) {")
  lism_hid_replace_once(code "    enum peripheral_slot_state state;"
    "    enum peripheral_slot_state state;\n    bool lism_initialized;\n    bool lism_subscribe_failed;")
  lism_hid_replace_once(code "            peripherals[i].state = PERIPHERAL_SLOT_STATE_CONNECTING;"
    "            peripherals[i].lism_initialized = false;\n            peripherals[i].lism_subscribe_failed = false;\n            peripherals[i].state = PERIPHERAL_SLOT_STATE_CONNECTING;")
  lism_hid_replace_once(code "        LOG_ERR(\"Subscribe failed (err %d)\", err);"
    "        ;\n        struct peripheral_slot *failed_slot = peripheral_slot_for_conn(conn);\n        if (failed_slot) failed_slot->lism_subscribe_failed = true;\n        LOG_ERR(\"Subscribe failed (err %d)\", err);")
  lism_hid_replace_once(code "    return subscribed ? BT_GATT_ITER_STOP : BT_GATT_ITER_CONTINUE;"
    "    slot->lism_initialized = subscribed && !slot->lism_subscribe_failed;\n    if (slot->lism_initialized && bt_conn_get_security(conn) >= BT_SECURITY_L2)\n        lism_split_sets_ready(peripheral_slot_index_for_conn(conn));\n    return subscribed ? BT_GATT_ITER_STOP : BT_GATT_ITER_CONTINUE;")
  lism_hid_replace_once(code "    k_work_submit(&update_peripherals_selected_layouts_work);\n}"
    "    if (slot->lism_initialized && !slot->lism_subscribe_failed && !err && level >= BT_SECURITY_L2)\n        lism_split_sets_ready(peripheral_slot_index_for_conn(conn));\n    k_work_submit(&update_peripherals_selected_layouts_work);\n}")
  # A scan-stop failure must release the policy reservation as well.
  lism_hid_replace_once(code "    int err = stop_scanning();\n    if (err < 0) {\n        return false;"
    "    int err = stop_scanning();\n    if (err < 0) {\n        release_peripheral_slot(slot_idx);\n        return false;")
  set(ad_parser [=[#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
#include "@AUTO_HEADER@"
#include "@ROLE_HEADER@"
static const struct bt_uuid_128 lism_role_uuid=BT_UUID_INIT_128(LISM_SPLIT_ROLE_UUID);
#endif
struct lism_set_ad_info { bool split; char name[32]; int role; };
static bool lism_set_ad_parse(struct bt_data *data, void *user) {
    struct lism_set_ad_info *info = user;
    if (data->type == BT_DATA_NAME_COMPLETE || data->type == BT_DATA_NAME_SHORTENED) {
        size_t n = MIN(data->data_len, sizeof(info->name) - 1);
        memcpy(info->name, data->data, n); info->name[n] = 0;
    }
    if (data->type == BT_DATA_UUID128_SOME || data->type == BT_DATA_UUID128_ALL) {
        for (size_t i = 0; i + 16 <= data->data_len; i += 16)
            if (!memcmp(&data->data[i], split_service_uuid.val, 16)) info->split = true;
    }
#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
    if (data->type==BT_DATA_SVC_DATA128 && data->data_len>=16 &&
        !memcmp(data->data,lism_role_uuid.val,16)) {
        int role=data->data_len==18 && data->data[16]==LISM_SPLIT_ROLE_VERSION &&
                 data->data[17]<=1 ? data->data[17] : -2;
        info->role=info->role==-1?role:info->role==role?role:-2;
    }
#endif
    return true;
}
static void lism_set_observe_ad(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
                                struct net_buf_simple *ad) {
    struct lism_set_ad_info info = {.role=-1};
    struct net_buf_simple copy = *ad;
    bt_data_parse(&copy, lism_set_ad_parse, &info);
    if (info.split) lism_split_sets_observe(addr, info.name, rssi);
#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
    lism_split_auto_observe(addr,rssi,type,info.split,info.role);
#endif
}

]=])
  get_filename_component(sets_src_dir "${header}" DIRECTORY)
  string(REPLACE "@AUTO_HEADER@" "${sets_src_dir}/lism_split_auto.h" ad_parser "${ad_parser}")
  string(REPLACE "@ROLE_HEADER@" "${sets_src_dir}/lism_split_role.h" ad_parser "${ad_parser}")
  lism_hid_replace_once(code "static void split_central_device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,"
    "${ad_parser}static void split_central_device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,")
  lism_hid_replace_once(code "    atomic_inc(&lism_owner_diag[OWNER_RX]);"
    "    atomic_inc(&lism_owner_diag[OWNER_RX]);\n    lism_set_observe_ad(addr, rssi, type, ad);")
  set(dispatch [=[        if (!lism_split_sets_dispatch(ev.source, ev.generation)) continue;
#if IS_ENABLED(CONFIG_ZMK_INPUT_SPLIT)
        if (ev.event.type == ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT)
            lism_input_cleanup_regs[ev.source][ev.event.data.input_event.reg] = true;
#endif
        if (ev.event.type == ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT)
            lism_split_sets_applied(ev.source, ev.event.data.key_position_event.position,
                                   ev.event.data.key_position_event.pressed);
        zmk_split_transport_central_peripheral_event_handler(&bt_central, ev.source, ev.event);]=])
  lism_hid_replace_once(code
    "        zmk_split_transport_central_peripheral_event_handler(&bt_central, ev.source, ev.event);" "${dispatch}")
  set(cleanup [=[
    bool cleanup_retry = false;
    uint32_t retry_delay = 2000;
    /* This is the same single worker that drains queued input. No replacement
     * keyboard can reserve until every applied key's release is delivered. */
    for (uint8_t source = 0; source < LISM_SPLIT_SET_SLOTS; ++source) {
        if (lism_split_sets_take_fault(source) && peripherals[source].conn &&
            !lism_pending_disconnect[source]) {
#if IS_ENABLED(CONFIG_ZMK_INPUT_SPLIT)
            release_peripheral_input_subs(peripherals[source].conn);
#endif
            lism_pending_disconnect[source] = bt_conn_ref(peripherals[source].conn);
            lism_disconnect_generation[source] = lism_split_sets_generation(source);
            lism_disconnect_deadline[source] = k_uptime_get();
            lism_disconnect_delay[source] = 100;
        }
        if (lism_pending_disconnect[source]) {
            struct bt_conn *pending = lism_pending_disconnect[source];
            uint32_t generation = lism_split_sets_generation(source);
            if (peripherals[source].conn != pending || generation != lism_disconnect_generation[source]) {
                bt_conn_unref(pending); lism_pending_disconnect[source] = NULL;
            } else {
                int64_t now = k_uptime_get();
                if (now >= lism_disconnect_deadline[source]) {
                    int rc = bt_conn_disconnect(pending, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
                    if (rc == -ENOTCONN) {
                        release_peripheral_slot(source);
                        bt_conn_unref(pending); lism_pending_disconnect[source] = NULL;
                    } else {
                        if (rc) LOG_WRN("Split input overflow: disconnect retry %d", rc);
                        lism_disconnect_deadline[source] = now + lism_disconnect_delay[source];
                        lism_disconnect_delay[source] = MIN(2000, lism_disconnect_delay[source] * 2);
                    }
                }
                if (lism_pending_disconnect[source]) {
                    uint32_t wait = MAX(10, lism_disconnect_deadline[source] - now);
                    retry_delay = MIN(retry_delay, wait); cleanup_retry = true;
                }
            }
        }
        uint8_t held[16];
        bool begin_cleanup = lism_split_sets_take_cleanup(source, held);
#if IS_ENABLED(CONFIG_ZMK_INPUT_SPLIT)
        if (begin_cleanup) lism_input_cleanup_started[source] = true;
        if (!lism_input_cleanup_started[source]) continue;
#else
        if (!begin_cleanup) continue;
#endif
        if (begin_cleanup) {
        for (uint16_t position = 0; position < 128; ++position) {
            if (!(held[position / 8] & BIT(position % 8))) continue;
            struct zmk_split_transport_peripheral_event release = {
                .type = ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,
                .data.key_position_event = {.position = position, .pressed = false}};
            zmk_split_transport_central_peripheral_event_handler(&bt_central, source, release);
        }
#if IS_ENABLED(CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)
        struct zmk_split_transport_peripheral_event battery = {
            .type = ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT,
            .data.battery_event.level = 0};
        zmk_split_transport_central_peripheral_event_handler(&bt_central, source, battery);
#endif
        }
#if IS_ENABLED(CONFIG_ZMK_INPUT_SPLIT)
        bool input_pending = false;
        for (unsigned reg = 0; reg < 256; ++reg) {
            if (!lism_input_cleanup_regs[source][reg]) continue;
            int rc = zmk_input_split_peripheral_disconnected(reg);
            if (rc == 0 || rc == -ENODEV)
                lism_input_cleanup_regs[source][reg] = false;
            else input_pending = true;
        }
        if (input_pending) { cleanup_retry = true; retry_delay = 10; continue; }
        if (!lism_input_fence_requested[source]) {
            uint32_t token = lism_split_sets_generation(source);
            lism_input_fence_token[source] = token;
            if (lism_split_input_fence_enqueue(source, token)) { cleanup_retry = true; retry_delay = 10; continue; }
            lism_input_fence_requested[source] = true;
        }
        if ((uint32_t)atomic_get(&lism_input_fence_ack[source]) != lism_input_fence_token[source]) {
            cleanup_retry = true; retry_delay = 10; continue;
        }
        lism_input_fence_requested[source] = false;
        lism_input_cleanup_started[source] = false;
#endif
        lism_split_sets_cleanup_done(source);
    }
    if (cleanup_retry) k_work_reschedule(&peripheral_event_work, K_MSEC(retry_delay));
    start_scanning();
]=])
  # File ends in this worker. Check that exact suffix rather than replacing an
  # arbitrary closing brace elsewhere in upstream source.
  set(tail "        zmk_split_transport_central_peripheral_event_handler(&bt_central, ev.source, ev.event);\n    }\n}")
  lism_hid_replace_once(code "${tail}"
    "        zmk_split_transport_central_peripheral_event_handler(&bt_central, ev.source, ev.event);\n    }\n${cleanup}\n}")
  lism_hid_replace_once(code
    "        zmk_split_transport_central_peripheral_event_handler(&bt_central, ev.source, ev.event);"
    "        int dispatch_rc = zmk_split_transport_central_peripheral_event_handler(&bt_central, ev.source, ev.event);\n        if (dispatch_rc < 0) lism_split_sets_fault(ev.source);")
  string(REPLACE "k_work_submit(&peripheral_event_work)"
    "k_work_reschedule(&peripheral_event_work, K_NO_WAIT)" code "${code}")
  file(WRITE "${output}" "${code}")
endfunction()

# The proxy cleanup keeps a tracked key until its release has actually entered
# the input queue. Retrying cannot silently forget a dropped release.
function(lism_split_sets_patch_input_split input output)
  file(READ "${input}" code)
  lism_split_sets_region(code "static int replace_active_key(size_t input, uint16_t find, uint16_t replace) {"
    "int zmk_input_split_report_peripheral_event(uint8_t reg, uint8_t type, uint16_t code," "")
  set(report [=[int zmk_input_split_report_peripheral_event(uint8_t reg, uint8_t type, uint16_t code,
                                            int32_t value, bool sync) {
    for (size_t i = 0; i < ARRAY_SIZE(proxy_inputs); ++i) {
        if (reg != proxy_inputs[i].reg) continue;
        size_t found = CONFIG_ZMK_INPUT_SPLIT_MAX_TRACKED_KEYS;
        size_t free_slot = CONFIG_ZMK_INPUT_SPLIT_MAX_TRACKED_KEYS;
        if (type == INPUT_EV_KEY && code) {
            for (size_t k = 0; k < CONFIG_ZMK_INPUT_SPLIT_MAX_TRACKED_KEYS; ++k) {
                if (proxy_active_keys[i][k] == code) found = k;
                if (!proxy_active_keys[i][k]) free_slot = k;
            }
            if (value && found == CONFIG_ZMK_INPUT_SPLIT_MAX_TRACKED_KEYS &&
                free_slot == CONFIG_ZMK_INPUT_SPLIT_MAX_TRACKED_KEYS) return -ENOMEM;
        }
        int rc = input_report(proxy_inputs[i].dev, type, code, value, sync, K_NO_WAIT);
        if (rc) return rc;
        /* Commit tracking only after accepted enqueue. Failed key-up must
         * remain tracked so the overflow fence can retry its release. */
        if (type == INPUT_EV_KEY && code) {
            if (value && found == CONFIG_ZMK_INPUT_SPLIT_MAX_TRACKED_KEYS)
                proxy_active_keys[i][free_slot] = code;
            else if (!value && found < CONFIG_ZMK_INPUT_SPLIT_MAX_TRACKED_KEYS)
                proxy_active_keys[i][found] = 0;
        }
        return 0;
    }
    return -ENODEV;
}

]=])
  lism_split_sets_region(code "int zmk_input_split_report_peripheral_event(uint8_t reg, uint8_t type, uint16_t code,"
    "int zmk_input_split_peripheral_disconnected(uint8_t reg) {" "${report}")
  set(cleanup [=[int zmk_input_split_peripheral_disconnected(uint8_t reg) {
    for (size_t i = 0; i < ARRAY_SIZE(proxy_inputs); ++i) {
        if (reg != proxy_inputs[i].reg) continue;
        for (size_t k = 0; k < CONFIG_ZMK_INPUT_SPLIT_MAX_TRACKED_KEYS; ++k) {
            uint16_t code = proxy_active_keys[i][k];
            if (!code) continue;
            int rc = input_report(proxy_inputs[i].dev, INPUT_EV_KEY, code, 0, true, K_NO_WAIT);
            if (rc) return rc;
            proxy_active_keys[i][k] = 0;
        }
        return 0;
    }
    return -ENODEV;
}

]=])
  lism_split_sets_region(code "int zmk_input_split_peripheral_disconnected(uint8_t reg) {"
    "#define ZIS_INST(n)" "${cleanup}")
  file(WRITE "${output}" "${code}")
endfunction()

# A marker is consumed after all prior input callbacks return. Empty-queue
# polling is insufficient because an event may already be executing.
function(lism_split_sets_patch_input_core input output)
  file(READ "${input}" code)
  set(marker [=[static const struct device lism_split_fence_device;
extern void lism_split_sets_input_fence_done(uint8_t source, uint32_t generation);
int lism_split_input_fence_enqueue(uint8_t source, uint32_t generation) {
#ifdef CONFIG_INPUT_MODE_THREAD
    struct input_event marker = {.dev = &lism_split_fence_device,
        .code = source, .value = (int32_t)generation};
    return k_msgq_put(&input_msgq, &marker, K_NO_WAIT);
#else
    lism_split_sets_input_fence_done(source, generation);
    return 0;
#endif
}

]=])
  lism_hid_replace_once(code "static void input_process(struct input_event *evt)"
    "${marker}static void input_process(struct input_event *evt)")
  lism_hid_replace_once(code "\t\tinput_process(&evt);"
    "\t\tif (evt.dev == &lism_split_fence_device) {\n\t\t\tlism_split_sets_input_fence_done(evt.code, (uint32_t)evt.value);\n\t\t\tcontinue;\n\t\t}\n\t\tinput_process(&evt);")
  file(WRITE "${output}" "${code}")
endfunction()
