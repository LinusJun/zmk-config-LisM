# Preserve the original SC-only key wire ABI while enabling legacy algorithms.
function(lism_input_patch_keys source destination header)
  file(SHA256 "${source}" digest)
  if(NOT digest STREQUAL "0354ed943485237ff6d63dc41ab55beb3ec864e7c1e54c00ccd6c5ebc9c7aa16")
    message(FATAL_ERROR "Unverified Bluetooth key store; refusing ABI adaptation")
  endif()
  file(SHA256 "${ZEPHYR_BASE}/subsys/bluetooth/host/keys.h" header_digest)
  if(NOT header_digest STREQUAL "c769ad5847be0f8c1d5a98f0e3a8f1c06cc625291eb32c3872e860d796abd77b")
    message(FATAL_ERROR "Unverified Bluetooth key layout")
  endif()
  file(READ "${source}" code)
  lism_smp_replace_once(code "LOG_MODULE_REGISTER(bt_keys);"
    "LOG_MODULE_REGISTER(bt_keys);\n#include \"${header}\"")
  lism_smp_replace_once(code
    "err = bt_settings_store_keys(keys->id, &keys->addr, keys->storage_start,\n\t\t\t\t     BT_KEYS_STORAGE_LEN);"
    "err = lism_keys_store(keys);")
  string(FIND "${code}" "static int keys_set(" start)
  string(FIND "${code}" "static void id_add(" stop)
  if(start LESS 0 OR stop LESS start)
    message(FATAL_ERROR "Pinned key loader region moved")
  endif()
  math(EXPR size "${stop} - ${start}")
  string(SUBSTRING "${code}" ${start} ${size} old)
  lism_smp_replace_once(code "${old}" "")
  lism_smp_replace_once(code "BT_SETTINGS_DEFINE(keys, \"keys\", keys_set, keys_commit);"
    "BT_SETTINGS_DEFINE(keys, \"keys\", lism_keys_set, keys_commit);")
  file(WRITE "${destination}" "#include \"${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../src/lism_input_security.h\"\n${code}")
endfunction()
