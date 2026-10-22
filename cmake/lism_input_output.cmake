# Outbound composition on generated pinned sources only. All anchors fail closed.
function(lism_input_output_replace_once variable old new)
  string(LENGTH "${${variable}}" before)
  string(LENGTH "${old}" expected)
  string(REPLACE "${old}" "" removed "${${variable}}")
  string(LENGTH "${removed}" after)
  math(EXPR actual "${before} - ${after}")
  if(NOT actual EQUAL expected)
    message(FATAL_ERROR "Pinned input output hook moved or duplicated: ${old}")
  endif()
  string(REPLACE "${old}" "${new}" result "${${variable}}")
  set(${variable} "${result}" PARENT_SCOPE)
endfunction()

function(lism_input_patch_endpoints input output header)
  file(READ "${input}" code)
  lism_input_output_replace_once(code "#include <zmk/hid.h>"
    "#include <zmk/hid.h>\n#include \"${header}\"")
  lism_input_output_replace_once(code "void zmk_endpoint_clear_reports(void) {"
    "void zmk_endpoint_clear_reports(void) {\n    lism_input_merge_endpoint_reset();")
  lism_input_output_replace_once(code
    "static void update_current_endpoint(void) {\n    struct zmk_endpoint_instance new_instance = get_selected_instance();"
    "static void update_current_endpoint(void) {\n    lism_input_endpoint_transition_begin();\n    struct zmk_endpoint_instance new_instance = get_selected_instance();")
  lism_input_output_replace_once(code
    "        raise_zmk_endpoint_changed((struct zmk_endpoint_changed){.endpoint = current_instance});\n    }\n}\n\nstatic int endpoint_listener"
    "        raise_zmk_endpoint_changed((struct zmk_endpoint_changed){.endpoint = current_instance});\n    }\n    lism_input_endpoint_transition_end();\n}\n\nstatic int endpoint_listener")
  foreach(kind keyboard consumer)
    lism_input_output_replace_once(code
      "        struct zmk_hid_${kind}_report *${kind}_report = zmk_hid_get_${kind}_report();\n        int err = zmk_hog_send_${kind}_report(&${kind}_report->body);"
      "        uint32_t epoch = lism_input_merge_generation();\n        struct zmk_hid_${kind}_report snapshot = *zmk_hid_get_${kind}_report();\n        lism_input_merge_${kind}(&snapshot);\n        int err = lism_input_hog_send_${kind}(&snapshot.body, epoch);")
  endforeach()
  lism_input_output_replace_once(code
    "        struct zmk_hid_mouse_report *mouse_report = zmk_hid_get_mouse_report();\n        int err = zmk_hog_send_mouse_report(&mouse_report->body);"
    "        uint32_t epoch = lism_input_merge_generation();\n        struct zmk_hid_mouse_report snapshot = *zmk_hid_get_mouse_report();\n        int err = lism_input_hog_send_mouse(&snapshot.body, epoch);")
  file(WRITE "${output}" "${code}")
endfunction()

function(lism_input_patch_usb input output header)
  file(READ "${input}" code)
  lism_input_output_replace_once(code "#include <zmk/hid.h>"
    "#include <zmk/hid.h>\n#include <string.h>\n#include \"${header}\"")
  set(old [=[static uint8_t *get_keyboard_report(size_t *len) {
#if IS_ENABLED(CONFIG_ZMK_USB_BOOT)
    if (hid_protocol != HID_PROTOCOL_REPORT) {
        zmk_hid_boot_report_t *boot_report = zmk_hid_get_boot_report();
        *len = sizeof(*boot_report);
        return (uint8_t *)boot_report;
    }
#endif
    struct zmk_hid_keyboard_report *report = zmk_hid_get_keyboard_report();
    *len = sizeof(*report);
    return (uint8_t *)report;
}]=])
  set(new [=[union lism_usb_keyboard_snapshot {
    struct zmk_hid_keyboard_report report;
#if IS_ENABLED(CONFIG_ZMK_USB_BOOT)
    zmk_hid_boot_report_t boot;
#endif
};

static uint8_t *get_keyboard_report(size_t *len, union lism_usb_keyboard_snapshot *copy) {
#if IS_ENABLED(CONFIG_ZMK_USB_BOOT)
    if (hid_protocol != HID_PROTOCOL_REPORT) {
        copy->boot = *zmk_hid_get_boot_report();
        lism_input_merge_boot(&copy->boot);
        *len = sizeof(copy->boot);
        return (uint8_t *)&copy->boot;
    }
#endif
    copy->report = *zmk_hid_get_keyboard_report();
    lism_input_merge_keyboard(&copy->report);
    *len = sizeof(copy->report);
    return (uint8_t *)&copy->report;
}]=])
  lism_input_output_replace_once(code "${old}" "${new}")
  lism_input_output_replace_once(code "            *data = get_keyboard_report(&size);"
    "            /* EP0 requests are serialized by the USB control transfer state machine.\n             * This storage is never used by interrupt IN or other report getters. */\n            static union lism_usb_keyboard_snapshot control_keyboard;\n            *data = get_keyboard_report(&size, &control_keyboard);")
  lism_input_output_replace_once(code
    "            struct zmk_hid_consumer_report *report = zmk_hid_get_consumer_report();"
    "            static struct zmk_hid_consumer_report control_consumer;\n            control_consumer = *zmk_hid_get_consumer_report();\n            lism_input_merge_consumer(&control_consumer);\n            struct zmk_hid_consumer_report *report = &control_consumer;")
  lism_input_output_replace_once(code
    "static int zmk_usb_hid_send_report(const uint8_t *report, size_t len) {"
    "static int zmk_usb_hid_send_report(const uint8_t *report, size_t len, uint32_t epoch) {\n    /* nRF USB uses asynchronous DMA: stack report copies must not escape.\n     * The semaphore owns this buffer until int_in_ready, including mouse reports. */\n    static uint8_t transfer[128] __aligned(4);\n    if (len > sizeof(transfer)) { return -EMSGSIZE; }")
  lism_input_output_replace_once(code
    "        k_sem_take(&hid_sem, K_MSEC(30));\n        int err = hid_int_ep_write(hid_dev, report, len, NULL);"
    "        int err = k_sem_take(&hid_sem, lism_input_usb_nonblocking() ? K_NO_WAIT : K_MSEC(30));\n        if (err) { return err; }\n        if (epoch != lism_input_merge_generation()) {\n            k_sem_give(&hid_sem);\n            return -ESTALE;\n        }\n        memcpy(transfer, report, len);\n        err = hid_int_ep_write(hid_dev, transfer, len, NULL);")
  lism_input_output_replace_once(code
    "    uint8_t *report = get_keyboard_report(&len);\n    return zmk_usb_hid_send_report(report, len);"
    "    uint32_t epoch = lism_input_merge_generation();\n    union lism_usb_keyboard_snapshot snapshot;\n    uint8_t *report = get_keyboard_report(&len, &snapshot);\n    return zmk_usb_hid_send_report(report, len, epoch);")
  lism_input_output_replace_once(code
    "    struct zmk_hid_consumer_report *report = zmk_hid_get_consumer_report();\n    return zmk_usb_hid_send_report((uint8_t *)report, sizeof(*report));"
    "    uint32_t epoch = lism_input_merge_generation();\n    struct zmk_hid_consumer_report snapshot = *zmk_hid_get_consumer_report();\n    lism_input_merge_consumer(&snapshot);\n    return zmk_usb_hid_send_report((uint8_t *)&snapshot, sizeof(snapshot), epoch);")
  lism_input_output_replace_once(code
    "    struct zmk_hid_mouse_report *report = zmk_hid_get_mouse_report();\n    return zmk_usb_hid_send_report((uint8_t *)report, sizeof(*report));"
    "    uint32_t epoch = lism_input_merge_generation();\n    struct zmk_hid_mouse_report snapshot = *zmk_hid_get_mouse_report();\n    lism_input_merge_mouse(&snapshot);\n    return zmk_usb_hid_send_report((uint8_t *)&snapshot, sizeof(snapshot), epoch);")
  file(WRITE "${output}" "${code}")
endfunction()

function(lism_input_patch_hog input output header)
  file(READ "${input}" code)
  lism_input_output_replace_once(code "#include <zmk/hid.h>"
    "#include <zmk/hid.h>\n#include \"${header}\"\n#include <zmk/endpoints.h>")
  # Bind every queued input packet to its actual destination, not
  # whichever profile happens to be active when the queue worker wakes.
  foreach(kind keyboard consumer mouse)
    if(NOT kind STREQUAL "mouse")
      lism_input_output_replace_once(code
      "    struct zmk_hid_${kind}_report_body *report_body = &zmk_hid_get_${kind}_report()->body;"
      "    struct zmk_hid_${kind}_report copy = *zmk_hid_get_${kind}_report();\n    struct zmk_endpoint_instance selected = zmk_endpoint_get_selected();\n    if (selected.transport == ZMK_TRANSPORT_BLE &&\n        bt_addr_le_cmp(bt_conn_get_dst(conn), zmk_ble_profile_address(selected.ble.profile_index)) == 0) {\n        lism_input_merge_${kind}(&copy);\n    }\n    struct zmk_hid_${kind}_report_body *report_body = &copy.body;")
    endif()
    string(TOUPPER "${kind}" upper)
    set(start "K_MSGQ_DEFINE(zmk_hog_${kind}_msgq,")
    string(FIND "${code}" "${start}" begin)
    if(kind STREQUAL "keyboard")
      set(end "K_MSGQ_DEFINE(zmk_hog_consumer_msgq,")
      set(attr 5)
    elseif(kind STREQUAL "consumer")
      set(end "#if IS_ENABLED(CONFIG_ZMK_POINTING)\n\nK_MSGQ_DEFINE(zmk_hog_mouse_msgq,")
      set(attr 9)
    else()
      set(end "#endif // IS_ENABLED(CONFIG_ZMK_POINTING)\n\nstatic int zmk_hog_init(void)")
      set(attr 13)
    endif()
    string(FIND "${code}" "${end}" finish)
    if(begin LESS 0 OR finish LESS begin)
      message(FATAL_ERROR "Pinned HOG ${kind} queue boundaries moved")
    endif()
    math(EXPR size "${finish} - ${begin}")
    string(SUBSTRING "${code}" ${begin} ${size} old)
    set(new "struct lism_hog_${kind}_packet {\n    struct zmk_hid_${kind}_report_body body;\n    bt_addr_le_t destination;\n    uint32_t epoch;\n};\n\nK_MSGQ_DEFINE(zmk_hog_${kind}_msgq, sizeof(struct lism_hog_${kind}_packet),\n              CONFIG_ZMK_BLE_${upper}_REPORT_QUEUE_SIZE, 4);\n\nvoid send_${kind}_report_callback(struct k_work *work) {\n    struct lism_hog_${kind}_packet packet;\n    while (k_msgq_get(&zmk_hog_${kind}_msgq, &packet, K_NO_WAIT) == 0) {\n        if (packet.epoch != lism_input_merge_generation()) { continue; }\n        struct bt_conn *conn = bt_conn_lookup_addr_le(BT_ID_DEFAULT, &packet.destination);\n        if (!conn) { continue; }\n        struct bt_gatt_notify_params params = {\n            .attr = &hog_svc.attrs[${attr}], .data = &packet.body, .len = sizeof(packet.body),\n        };\n        int err = bt_gatt_notify_cb(conn, &params);\n        if (err == -EPERM) { bt_conn_set_security(conn, BT_SECURITY_L2); }\n        else if (err) { LOG_DBG(\"Error notifying %d\", err); }\n        bt_conn_unref(conn);\n    }\n}\n\nK_WORK_DEFINE(hog_${kind}_work, send_${kind}_report_callback);\n\nint zmk_hog_send_${kind}_report(struct zmk_hid_${kind}_report_body *report) {\n    struct zmk_endpoint_instance endpoint = zmk_endpoint_get_selected();\n    if (endpoint.transport != ZMK_TRANSPORT_BLE) { return -ENODEV; }\n    bt_addr_le_t *address = zmk_ble_profile_address(endpoint.ble.profile_index);\n    if (!address || bt_addr_le_cmp(address, BT_ADDR_LE_ANY) == 0) { return -ENOTCONN; }\n    struct lism_hog_${kind}_packet packet = {\n        .body = *report, .destination = *address, .epoch = lism_input_merge_generation(),\n    };\n    int err = k_msgq_put(&zmk_hog_${kind}_msgq, &packet, K_NO_WAIT);\n    if (err == -ENOMSG) {\n        struct lism_hog_${kind}_packet discarded;\n        k_msgq_get(&zmk_hog_${kind}_msgq, &discarded, K_NO_WAIT);\n        err = k_msgq_put(&zmk_hog_${kind}_msgq, &packet, K_NO_WAIT);\n    }\n    if (!err) { k_work_submit_to_queue(&hog_work_q, &hog_${kind}_work); }\n    return err;\n}\n\n")
    string(REPLACE
      "int zmk_hog_send_${kind}_report(struct zmk_hid_${kind}_report_body *report) {"
      "int lism_input_hog_send_${kind}(struct zmk_hid_${kind}_report_body *report, uint32_t epoch) {\n    if (epoch != lism_input_merge_generation()) { return -ESTALE; }"
      new "${new}")
    string(REPLACE ".epoch = lism_input_merge_generation()," ".epoch = epoch," new "${new}")
    string(REPLACE "void send_${kind}_report_callback(struct k_work *work) {"
      "void send_${kind}_report_callback(struct k_work *work) {\n    (void)work;" new "${new}")
    # A release is safe to deliver after another endpoint switch only because
    # every local AND external body byte is zero and its old destination is
    # captured. FIFO preserves release-before-new-input when returning to it.
    string(REPLACE "    uint32_t epoch;\n};"
      "    uint32_t epoch;\n    bool release;\n};" new "${new}")
    string(REPLACE "if (packet.epoch != lism_input_merge_generation()) { continue; }"
      "if (!packet.release && packet.epoch != lism_input_merge_generation()) { continue; }"
      new "${new}")
    string(REPLACE "    int err = k_msgq_put(&zmk_hog_${kind}_msgq, &packet, K_NO_WAIT);"
      "    packet.release = true;\n    for (size_t i = 0; i < sizeof(packet.body); i++) {\n        if (((const uint8_t *)&packet.body)[i] != 0) { packet.release = false; break; }\n    }\n    int err = k_msgq_put(&zmk_hog_${kind}_msgq, &packet, K_NO_WAIT);"
      new "${new}")
    string(APPEND new "int zmk_hog_send_${kind}_report(struct zmk_hid_${kind}_report_body *report) {\n    return lism_input_hog_send_${kind}(report, lism_input_merge_generation());\n}\n\n")
    lism_input_output_replace_once(code "${old}" "${new}")
  endforeach()
  file(WRITE "${output}" "${code}")
endfunction()
