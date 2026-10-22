if(CONFIG_BT_SMP_SC_PAIR_ONLY OR NOT CONFIG_LISM_INPUT_LEGACY_COMPAT)
  message(FATAL_ERROR "Generic input build requires per-connection legacy policy, not global SC-only")
endif()
# Adapt only generated copies. No shared SDK / baseline mutation.
zephyr_include_directories("${CMAKE_CURRENT_LIST_DIR}/../src")
target_sources(app PRIVATE
  "${CMAKE_CURRENT_LIST_DIR}/../src/lism_input_host.c"
  "${CMAKE_CURRENT_LIST_DIR}/../src/lism_input_parser.c"
  "${CMAKE_CURRENT_LIST_DIR}/../src/lism_input_merge.c"
  "${CMAKE_CURRENT_LIST_DIR}/../src/lism_input_scanner.c"
  "${CMAKE_CURRENT_LIST_DIR}/../src/lism_screen.c"
  "${CMAKE_CURRENT_LIST_DIR}/../src/lism_coex.c"
  "${CMAKE_CURRENT_LIST_DIR}/../src/behavior_screen_next.c")
include("${CMAKE_CURRENT_LIST_DIR}/lism_input_security.cmake")
include("${CMAKE_CURRENT_LIST_DIR}/lism_input_keys.cmake")
include("${CMAKE_CURRENT_LIST_DIR}/lism_input_output.cmake")
set(_smp_src "${ZEPHYR_BASE}/subsys/bluetooth/host/smp.c")
set(_smp_copy "${CMAKE_CURRENT_BINARY_DIR}/lism_inputs_smp.c")
lism_input_patch_smp("${_smp_src}" "${_smp_copy}" "${CMAKE_CURRENT_LIST_DIR}/../src/lism_input_security.h")
set(_keys_src "${ZEPHYR_BASE}/subsys/bluetooth/host/keys.c")
set(_keys_copy "${CMAKE_CURRENT_BINARY_DIR}/lism_inputs_keys.c")
lism_input_patch_keys("${_keys_src}" "${_keys_copy}" "${CMAKE_CURRENT_LIST_DIR}/../src/lism_input_key_storage.h")
set_source_files_properties("${_smp_src}" "${_keys_src}" DIRECTORY "${ZEPHYR_BASE}/subsys/bluetooth/host" PROPERTIES HEADER_FILE_ONLY ON)
target_sources(app PRIVATE "${_smp_copy}" "${_keys_copy}")
target_include_directories(app PRIVATE "${ZEPHYR_BASE}/subsys/bluetooth/host")
set(_usb_src "${APPLICATION_SOURCE_DIR}/src/usb_hid.c")
set(_usb_copy "${CMAKE_CURRENT_BINARY_DIR}/lism_inputs_usb.c")
lism_input_patch_usb("${_usb_src}" "${_usb_copy}" "${CMAKE_CURRENT_LIST_DIR}/../src/lism_input_merge.h")
set_source_files_properties("${_usb_src}" TARGET_DIRECTORY app PROPERTIES HEADER_FILE_ONLY ON)
target_sources(app PRIVATE "${_usb_copy}")
execute_process(COMMAND "${PYTHON_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/../tools/prepare_input_integration.py"
  "${_lism_hog_copy}" "${_lism_endpoint_copy}" "${_lism_ble_copy}" RESULT_VARIABLE result)
if(NOT result EQUAL 0)
  message(FATAL_ERROR "Input integration anchors changed")
endif()
