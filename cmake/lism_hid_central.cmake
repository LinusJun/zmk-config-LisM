# Pinned ZMK scan-owner integration. Every anchor must occur exactly once;
# upstream drift is a build error, never an uncoordinated second scanner.
function(lism_hid_replace_once variable old new)
  string(LENGTH "${${variable}}" before_length)
  string(LENGTH "${old}" old_length)
  string(REPLACE "${old}" "" without "${${variable}}")
  string(LENGTH "${without}" after_length)
  math(EXPR removed "${before_length} - ${after_length}")
  if(NOT removed EQUAL old_length)
    message(FATAL_ERROR "Pinned HID scan hook moved or duplicated: ${old}")
  endif()
  string(REPLACE "${old}" "${new}" result "${${variable}}")
  set(${variable} "${result}" PARENT_SCOPE)
endfunction()

# Request the cleared mouse report before ZMK updates its selected endpoint.
# This is best-effort, not an acknowledgement from an unreachable old host;
# existing BLE active-profile routing limitations are documented for the test.
function(lism_hid_patch_endpoints input output)
  file(READ "${input}" code)
  lism_hid_replace_once(code "    zmk_hid_mouse_clear();"
    "    zmk_hid_mouse_clear();\n    zmk_endpoint_send_mouse_report();")
  file(WRITE "${output}" "${code}")
endfunction()

function(lism_hid_patch_ble input output)
  file(READ "${input}" code)
  set(accessor [=[/* Read-only identity filter: never reserve or save a split slot for HID. */
bool lism_split_known_addr(const bt_addr_le_t *addr) {
    if (addr == NULL || bt_addr_le_cmp(addr, BT_ADDR_LE_ANY) == 0) { return false; }
    for (int i = 0; i < ZMK_SPLIT_BLE_PERIPHERAL_COUNT; i++) {
        if (bt_addr_le_cmp(&peripheral_addrs[i], BT_ADDR_LE_ANY) != 0 &&
            bt_addr_le_cmp(&peripheral_addrs[i], addr) == 0) { return true; }
    }
    return false;
}

]=])
  lism_hid_replace_once(code
    "int zmk_ble_put_peripheral_addr(const bt_addr_le_t *addr) {"
    "${accessor}int zmk_ble_put_peripheral_addr(const bt_addr_le_t *addr) {")
  file(WRITE "${output}" "${code}")
endfunction()

function(lism_hid_patch_central input output header)
  file(READ "${input}" code)
  set(stop_old [=[static int stop_scanning(void) {
    LOG_DBG("Stopping peripheral scanning");
    is_scanning = false;

    int err = bt_le_scan_stop();
    if (err < 0) {
        LOG_ERR("Stop LE scan failed (err %d)", err);
        return err;
    }

    return 0;
}]=])
  set(stop_new [=[static int stop_scanning(void) {
    LOG_DBG("Stopping peripheral scanning");
    int err = bt_le_scan_stop();
    if (err < 0 && err != -EALREADY) {
        LOG_ERR("Stop LE scan failed (err %d)", err);
        return err;
    }
    is_scanning = false;
    return 0;
}]=])
  lism_hid_replace_once(code "${stop_old}" "${stop_new}")
  lism_hid_replace_once(code "#include <zmk/ble.h>"
    "#include <zmk/ble.h>\n#include \"${header}\"\nextern bool lism_split_known_addr(const bt_addr_le_t *addr);")
  set(scan_api [=[int lism_hid_scan_pause(void) { return is_scanning ? stop_scanning() : 0; }
void lism_hid_scan_resume(void) {
    if (lism_hid_host_scan_paused()) { return; }
    bool needed = lism_hid_host_scan_needed();
    for (size_t i = 0; i < ARRAY_SIZE(peripherals); i++) {
        if (peripherals[i].conn == NULL) { needed = true; break; }
    }
    if (!needed && is_scanning) { stop_scanning(); }
    else { start_scanning(); }
}

]=])
  lism_hid_replace_once(code "static bool split_central_eir_found(const bt_addr_le_t *addr) {"
    "${scan_api}static bool split_central_eir_found(const bt_addr_le_t *addr) {")
  lism_hid_replace_once(code "static int start_scanning(void) {"
    "static int start_scanning(void) {\n    if (lism_hid_host_scan_paused()) { return 0; }")
  lism_hid_replace_once(code
    "                                       struct net_buf_simple *ad) {\n    char dev[BT_ADDR_LE_STR_LEN];"
    "                                       struct net_buf_simple *ad) {\n    if (lism_hid_host_claim_ad(addr)) { return; }\n    char dev[BT_ADDR_LE_STR_LEN];")
  lism_hid_replace_once(code
    "    } else if (type == BT_GAP_ADV_TYPE_ADV_DIRECT_IND) {\n        split_central_eir_found(addr);"
    "    } else if (type == BT_GAP_ADV_TYPE_ADV_DIRECT_IND && lism_split_known_addr(addr)) {\n        split_central_eir_found(addr);")
  lism_hid_replace_once(code "    bool has_unconnected = false;"
    "    bool has_unconnected = lism_hid_host_scan_needed();")
  lism_hid_replace_once(code
    "    is_scanning = true;\n    int err = bt_le_scan_start(BT_LE_SCAN_PASSIVE, split_central_device_found);"
    "    int err = bt_le_scan_start(BT_LE_SCAN_ACTIVE, split_central_device_found);")
  lism_hid_replace_once(code "    LOG_DBG(\"Scanning successfully started\");"
    "    is_scanning = true;\n    LOG_DBG(\"Scanning successfully started\");")
  lism_hid_replace_once(code
    "static void split_central_connected(struct bt_conn *conn, uint8_t conn_err) {"
    "static void split_central_connected(struct bt_conn *conn, uint8_t conn_err) {\n    if (peripheral_slot_for_conn(conn) == NULL) { return; }")
  lism_hid_replace_once(code
    "static void split_central_disconnected(struct bt_conn *conn, uint8_t reason) {"
    "static void split_central_disconnected(struct bt_conn *conn, uint8_t reason) {\n    if (peripheral_slot_for_conn(conn) == NULL) { return; }")
  file(WRITE "${output}" "${code}")
endfunction()
