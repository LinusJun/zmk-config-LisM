# The Prospector observer receives every Bluetooth connection callback. Limit
# its split bookkeeping to connections positively owned by the split transport.
# Work only on a generated copy; fail closed if the pinned source changes.
function(lism_hid_observer_replace_once variable old new)
  string(LENGTH "${${variable}}" before_length)
  string(LENGTH "${old}" old_length)
  string(REPLACE "${old}" "" without "${${variable}}")
  string(LENGTH "${without}" after_length)
  math(EXPR removed "${before_length} - ${after_length}")
  if(NOT removed EQUAL old_length)
    message(FATAL_ERROR "Pinned HID observer hook moved or duplicated: ${old}")
  endif()
  string(REPLACE "${old}" "${new}" result "${${variable}}")
  set(${variable} "${result}" PARENT_SCOPE)
endfunction()

function(lism_hid_patch_observer input output)
  file(READ "${input}" code)
  lism_hid_observer_replace_once(code "#include <zmk/ble.h>"
    "#include <zmk/ble.h>\n\nextern bool lism_split_owns_connection(struct bt_conn *conn);")
  lism_hid_observer_replace_once(code
    "static void split_central_connected(struct bt_conn *conn, uint8_t conn_err) {"
    "static void split_central_connected(struct bt_conn *conn, uint8_t conn_err) {\n    if (!lism_split_owns_connection(conn)) { return; }")
  # On disconnect the central transport may already have released its slot.
  # Only the observer's own registered connection is valid bookkeeping proof.
  lism_hid_observer_replace_once(code
    "static void split_central_disconnected(struct bt_conn *conn, uint8_t reason) {"
    "static void split_central_disconnected(struct bt_conn *conn, uint8_t reason) {\n    if (psptr_peripheral_slot_index_for_conn(conn) < 0) { return; }")
  file(WRITE "${output}" "${code}")
endfunction()
