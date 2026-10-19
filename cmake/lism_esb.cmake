# Opt-in ESB split integration. All upstream changes are generated, pinned,
# and compiled in their original target so private SDK includes remain valid.
include("${CMAKE_CURRENT_LIST_DIR}/lism_hid_central.cmake")
function(lism_esb_patch_central file header)
  file(READ "${file}" code)
  string(PREPEND code "#include \"${header}\"\n")
  lism_hid_replace_once(code
    "    if (event->source >= LISM_SPLIT_SET_SLOTS) return -EINVAL;"
    "    if (event->source >= LISM_SPLIT_SET_SLOTS) return -EINVAL;\n    if (event->source == 0 || event->source == 1 || event->source == 4 || event->source == 5) return 0;")
  # BLE only commissions keyboard halves. ESB packets enter the SAME queue,
  # generation gate, applied bitmap, overflow handling and input cleanup fence.
  string(REPLACE "zmk_split_transport_central_peripheral_event_handler(&bt_central,"
    "zmk_split_transport_central_peripheral_event_handler(&lism_esb_central," code "${code}")
  file(WRITE "${file}" "${code}")
  file(APPEND "${file}" [=[

void lism_esb_control_start(void) {
    if (split_central_bt_get_status().available && !is_enabled)
        split_central_bt_set_enabled(true);
}
int lism_esb_control_command(uint8_t source, struct zmk_split_transport_central_command command) {
    return split_central_bt_send_command(source, command);
}
int lism_esb_control_sources(uint8_t *sources) {
    return split_central_bt_get_available_source_ids(sources);
}
int lism_esb_queue_event(uint8_t source, uint32_t generation,
                         const struct zmk_split_transport_peripheral_event *event) {
    if (!event || source >= LISM_SPLIT_SET_SLOTS ||
        !lism_split_sets_dispatch(source, generation)) return -ESTALE;
    struct peripheral_event_wrapper wrapper = {.source = source,
        .generation = generation, .event = *event};
    int rc = k_msgq_put(&peripheral_event_msgq, &wrapper, K_NO_WAIT);
    if (!rc) k_work_reschedule(&peripheral_event_work, K_NO_WAIT);
    /* Backpressure: ESB retries its immutable frame without acknowledging it.
     * Unlike a BLE notification, this input has not been consumed yet. */
    return rc ? -EAGAIN : 0;
}
void lism_esb_queue_fault(uint8_t source) {
    lism_split_sets_fault(source);
    k_work_reschedule(&peripheral_event_work, K_NO_WAIT);
}
]=])
endfunction()

function(lism_esb_finish_sdk)
  get_property(nrf_dir GLOBAL PROPERTY LISM_ESB_NRF_DIR)
  get_property(esb_copy GLOBAL PROPERTY LISM_ESB_COPY)
  set(esb_dir "${nrf_dir}/subsys/esb")
  get_directory_property(esb_library DIRECTORY "${esb_dir}" DEFINITION ZEPHYR_CURRENT_LIBRARY)
  if(NOT TARGET "${esb_library}")
    message(FATAL_ERROR "Pinned Nordic ESB library target missing")
  endif()
  set_source_files_properties("${esb_dir}/esb.c" DIRECTORY "${esb_dir}" PROPERTIES HEADER_FILE_ONLY ON)
  target_sources(${esb_library} PRIVATE "${esb_copy}")
  target_include_directories(${esb_library} PRIVATE "${esb_dir}")
endfunction()

function(lism_esb_prepare_sdk output)
  get_filename_component(nrf_dir "${ZEPHYR_NRF_MODULE_DIR}" REALPATH)
  if(NOT EXISTS "${nrf_dir}/subsys/esb/esb.c")
    message(FATAL_ERROR "ESB requires the pinned sdk-nrf and sdk-nrfxlib modules")
  endif()
  set(source "${nrf_dir}/subsys/esb/esb.c")
  file(SHA256 "${source}" hash)
  if(NOT hash STREQUAL "0edd4a985f5edc62267e87d1427f790ef81e691a563437aaafc6b02599c732b5")
    message(FATAL_ERROR "Unverified Nordic ESB source: use the pinned badjeff SDK")
  endif()
  file(READ "${source}" code)
  lism_hid_replace_once(code "\tirq_disable(ESB_RADIO_IRQ_NUMBER);"
    "\t/* MPSL retains RADIO IRQ ownership across every ESB slot. */")
  lism_hid_replace_once(code
    "\tARM_IRQ_DIRECT_DYNAMIC_CONNECT(ESB_RADIO_IRQ_NUMBER, CONFIG_ESB_RADIO_IRQ_PRIORITY,\n\t\t\t\t       0, reschedule);"
    "\t/* RADIO is dispatched exclusively by the MPSL timeslot callback. */")
  lism_hid_replace_once(code
    "\tirq_connect_dynamic(ESB_RADIO_IRQ_NUMBER, CONFIG_ESB_RADIO_IRQ_PRIORITY,\n\t\t\t    radio_dynamic_irq_handler, NULL, 0);"
    "\t/* Do not replace MPSL's RADIO vector or software ISR. */")
  file(WRITE "${output}" "${code}")
  set_property(GLOBAL PROPERTY LISM_ESB_NRF_DIR "${nrf_dir}")
  set_property(GLOBAL PROPERTY LISM_ESB_COPY "${output}")
  cmake_language(DEFER DIRECTORY "${APPLICATION_SOURCE_DIR}" CALL lism_esb_finish_sdk)
endfunction()
