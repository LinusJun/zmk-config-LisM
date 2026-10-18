function(lism_split_patch_role source destination header)
  file(READ "${source}" code)
  set(anchor "static const struct bt_data zmk_ble_ad[] = {")
  set(add [=[#include "@HEADER@"
#if defined(CONFIG_SHIELD_LISM_L)
#define LISM_LOCAL_SIDE 0
#elif defined(CONFIG_SHIELD_LISM_R)
#define LISM_LOCAL_SIDE 1
#else
#error "Automatic role advertisement is only for LisM keyboard halves"
#endif
static const struct bt_data lism_role_scan_response[] = {
    BT_DATA_BYTES(BT_DATA_SVC_DATA128, LISM_SPLIT_ROLE_UUID,
                  LISM_SPLIT_ROLE_VERSION, LISM_LOCAL_SIDE),
};

]=])
  string(REPLACE "@HEADER@" "${header}" add "${add}")
  lism_hid_replace_once(code "${anchor}" "${add}${anchor}")
  lism_hid_replace_once(code
    "return bt_le_adv_start(BT_LE_ADV_CONN_FAST_2, zmk_ble_ad, ARRAY_SIZE(zmk_ble_ad), NULL, 0);"
    "return bt_le_adv_start(BT_LE_ADV_CONN_FAST_2, zmk_ble_ad, ARRAY_SIZE(zmk_ble_ad),\n                               lism_role_scan_response, ARRAY_SIZE(lism_role_scan_response));")
  file(WRITE "${destination}" "${code}")
endfunction()
