if(CONFIG_LISM_PROSPECTOR_LOCK_INDICATORS OR CONFIG_LISM_XIAO147_TOUCH OR CONFIG_LISM_RESISTIVE_JOYSTICK OR CONFIG_LISM_CHARGE_TELEMETRY OR CONFIG_LISM_BLE_HID_BRIDGE)
  zephyr_library()
  if(CONFIG_LISM_SPLIT_ROLE_TAG)
    include("${CMAKE_CURRENT_LIST_DIR}/cmake/lism_hid_central.cmake")
    include("${CMAKE_CURRENT_LIST_DIR}/cmake/lism_split_role.cmake")
    set(_role_src "${APPLICATION_SOURCE_DIR}/src/split/bluetooth/peripheral.c")
    set(_role_copy "${CMAKE_CURRENT_BINARY_DIR}/lism_role_peripheral.c")
    lism_split_patch_role("${_role_src}" "${_role_copy}" "${CMAKE_CURRENT_LIST_DIR}/src/lism_split_role.h")
    set_source_files_properties("${_role_src}"
      DIRECTORY "${APPLICATION_SOURCE_DIR}" PROPERTIES HEADER_FILE_ONLY ON)
    zephyr_library_sources("${_role_copy}")
    zephyr_library_include_directories("${APPLICATION_SOURCE_DIR}/src/split/bluetooth")
  endif()
  if(CONFIG_LISM_BLE_HID_BRIDGE)
    include("${CMAKE_CURRENT_LIST_DIR}/cmake/lism_hid_central.cmake")
    set(_hid_central_src "${APPLICATION_SOURCE_DIR}/src/split/bluetooth/central.c")
    set(_hid_ble_src "${APPLICATION_SOURCE_DIR}/src/ble.c")
    set(_hid_endpoints_src "${APPLICATION_SOURCE_DIR}/src/endpoints.c")
    set(_hid_central_copy "${CMAKE_CURRENT_BINARY_DIR}/lism_hid_zmk_central.c")
    set(_hid_ble_copy "${CMAKE_CURRENT_BINARY_DIR}/lism_hid_zmk_ble.c")
    set(_hid_endpoints_copy "${CMAKE_CURRENT_BINARY_DIR}/lism_hid_zmk_endpoints.c")
    lism_hid_patch_central("${_hid_central_src}" "${_hid_central_copy}"
      "${CMAKE_CURRENT_LIST_DIR}/src/lism_hid_host.h")
    lism_hid_patch_ble("${_hid_ble_src}" "${_hid_ble_copy}")
    lism_hid_patch_endpoints("${_hid_endpoints_src}" "${_hid_endpoints_copy}")
    if(CONFIG_LISM_BLE_INPUT_BRIDGE)
      if(CONFIG_LISM_INPUT_LEGACY_COMPAT AND CONFIG_BT_SMP_SC_PAIR_ONLY)
        message(FATAL_ERROR "Legacy compatibility requires the pinned generated KCONFIG_ROOT")
      endif()
      if(NOT CONFIG_LISM_INPUT_LEGACY_COMPAT AND NOT CONFIG_BT_SMP_SC_PAIR_ONLY)
        message(FATAL_ERROR "SC-only policy required when explicit legacy compatibility is disabled")
      endif()
      include("${CMAKE_CURRENT_LIST_DIR}/cmake/lism_input_security.cmake")
      set(_input_smp_src "${ZEPHYR_BASE}/subsys/bluetooth/host/smp.c")
      set(_input_smp_copy "${CMAKE_CURRENT_BINARY_DIR}/lism_input_zephyr_smp.c")
      lism_input_patch_smp("${_input_smp_src}" "${_input_smp_copy}"
        "${CMAKE_CURRENT_LIST_DIR}/src/lism_input_security.h")
      set_source_files_properties("${_input_smp_src}"
        DIRECTORY "${ZEPHYR_BASE}/subsys/bluetooth/host" PROPERTIES HEADER_FILE_ONLY ON)
      zephyr_library_sources("${_input_smp_copy}")
      zephyr_library_link_libraries(subsys__bluetooth)
      zephyr_library_include_directories("${ZEPHYR_BASE}/subsys/bluetooth/host")
      if(CONFIG_LISM_INPUT_LEGACY_COMPAT)
        include("${CMAKE_CURRENT_LIST_DIR}/cmake/lism_input_keys.cmake")
        set(_input_keys_src "${ZEPHYR_BASE}/subsys/bluetooth/host/keys.c")
        set(_input_keys_copy "${CMAKE_CURRENT_BINARY_DIR}/lism_input_zephyr_keys.c")
        lism_input_patch_keys("${_input_keys_src}" "${_input_keys_copy}"
          "${CMAKE_CURRENT_LIST_DIR}/src/lism_input_key_storage.h")
        set_source_files_properties("${_input_keys_src}"
          DIRECTORY "${ZEPHYR_BASE}/subsys/bluetooth/host" PROPERTIES HEADER_FILE_ONLY ON)
        zephyr_library_sources("${_input_keys_copy}")
      endif()
      include("${CMAKE_CURRENT_LIST_DIR}/cmake/lism_input_output.cmake")
      include("${CMAKE_CURRENT_LIST_DIR}/cmake/lism_input_profiles.cmake")
      set(_inputs_header "${CMAKE_CURRENT_LIST_DIR}/src/lism_input_merge.h")
      lism_input_patch_endpoints("${_hid_endpoints_copy}" "${_hid_endpoints_copy}" "${_inputs_header}")
      foreach(_kind usb_hid hog)
        set(_input_${_kind}_src "${APPLICATION_SOURCE_DIR}/src/${_kind}.c")
        set(_input_${_kind}_copy "${CMAKE_CURRENT_BINARY_DIR}/lism_input_zmk_${_kind}.c")
      endforeach()
      lism_input_patch_usb("${_input_usb_hid_src}" "${_input_usb_hid_copy}" "${_inputs_header}")
      lism_input_patch_hog("${_input_hog_src}" "${_input_hog_copy}" "${_inputs_header}")
      set_source_files_properties("${_input_usb_hid_src}" "${_input_hog_src}"
        DIRECTORY "${APPLICATION_SOURCE_DIR}" PROPERTIES HEADER_FILE_ONLY ON)
      zephyr_library_sources("${_input_usb_hid_copy}" "${_input_hog_copy}")
      set(_inputs_include "${CMAKE_CURRENT_BINARY_DIR}/input_include")
      lism_input_profile_header("${APPLICATION_SOURCE_DIR}/include/zmk/ble.h" "${_inputs_include}")
      target_include_directories(zephyr_interface BEFORE INTERFACE "${_inputs_include}")
      file(APPEND "${_hid_ble_copy}" "\nbool lism_input_split_identity(const bt_addr_le_t *addr) {\n    if (!addr) { return false; }\n    for (unsigned i = 0; i < ZMK_SPLIT_BLE_PERIPHERAL_COUNT; i++) {\n        if (bt_addr_le_cmp(addr, &peripheral_addrs[i]) == 0) { return true; }\n    }\n    return false;\n}\n")
    endif()
    if(CONFIG_LISM_TWO_KEYBOARD_SETS)
      include("${CMAKE_CURRENT_LIST_DIR}/cmake/lism_split_sets.cmake")
      set(_sets_header "${CMAKE_CURRENT_LIST_DIR}/src/lism_split_sets.h")
      lism_split_sets_patch_ble("${_hid_ble_copy}" "${_hid_ble_copy}" "${_sets_header}")
      lism_split_sets_patch_central("${_hid_central_copy}" "${_hid_central_copy}" "${_sets_header}")
      set(_sets_input_split "${APPLICATION_SOURCE_DIR}/src/pointing/input_split.c")
      set(_sets_input_core "${ZEPHYR_BASE}/subsys/input/input.c")
      lism_split_sets_patch_input_split("${_sets_input_split}" "${CMAKE_CURRENT_BINARY_DIR}/lism_sets_input_split.c")
      lism_split_sets_patch_input_core("${_sets_input_core}" "${CMAKE_CURRENT_BINARY_DIR}/lism_sets_input_core.c")
      set_source_files_properties("${_sets_input_split}"
        DIRECTORY "${APPLICATION_SOURCE_DIR}" PROPERTIES HEADER_FILE_ONLY ON)
      set_source_files_properties("${_sets_input_core}"
        DIRECTORY "${ZEPHYR_BASE}/subsys/input" PROPERTIES HEADER_FILE_ONLY ON)
      zephyr_library_sources("${CMAKE_CURRENT_BINARY_DIR}/lism_sets_input_split.c"
        "${CMAKE_CURRENT_BINARY_DIR}/lism_sets_input_core.c" src/lism_split_sets.c)
      zephyr_library_sources_ifdef(CONFIG_LISM_AUTO_SPLIT_SETS src/lism_split_auto.c)
    endif()
    if(CONFIG_LISM_INPUT_LEGACY_COMPAT)
      if(NOT CONFIG_LISM_TWO_KEYBOARD_SETS)
        message(FATAL_ERROR "Legacy compatibility requires the synchronized split registry")
      endif()
      include("${CMAKE_CURRENT_LIST_DIR}/cmake/lism_input_identity.cmake")
      lism_input_patch_identity("${_hid_ble_copy}" "${_hid_ble_copy}")
    endif()
    set_source_files_properties("${_hid_central_src}" "${_hid_ble_src}" "${_hid_endpoints_src}"
      DIRECTORY "${APPLICATION_SOURCE_DIR}" PROPERTIES HEADER_FILE_ONLY ON)
    zephyr_library_sources("${_hid_central_copy}" "${_hid_ble_copy}" "${_hid_endpoints_copy}")
    if(CONFIG_SHIELD_PROSPECTOR_ADAPTER)
      include("${CMAKE_CURRENT_LIST_DIR}/cmake/lism_hid_observer.cmake")
      get_filename_component(_hid_west_root "${ZEPHYR_BASE}" DIRECTORY)
      set(_hid_observer_module "${_hid_west_root}/prospector-zmk-module")
      set(_hid_observer_src
          "${_hid_observer_module}/src/split/bluetooth/central_status_changed_observer.c")
      set(_hid_observer_copy "${CMAKE_CURRENT_BINARY_DIR}/lism_hid_prospector_observer.c")
      lism_hid_patch_observer("${_hid_observer_src}" "${_hid_observer_copy}")
      # This config module is configured before Prospector. Set the original
      # source's directory-scoped property only after that module exists.
      set_property(GLOBAL PROPERTY LISM_HID_OBSERVER_SOURCE "${_hid_observer_src}")
      set_property(GLOBAL PROPERTY LISM_HID_OBSERVER_MODULE "${_hid_observer_module}")
      function(lism_hid_finish_observer_override)
        get_property(_source GLOBAL PROPERTY LISM_HID_OBSERVER_SOURCE)
        get_property(_module GLOBAL PROPERTY LISM_HID_OBSERVER_MODULE)
        set_source_files_properties("${_source}"
          DIRECTORY "${_module}" PROPERTIES HEADER_FILE_ONLY ON)
      endfunction()
      cmake_language(DEFER DIRECTORY "${APPLICATION_SOURCE_DIR}"
        CALL lism_hid_finish_observer_override)
      zephyr_library_sources("${_hid_observer_copy}")
    endif()
  endif()
  zephyr_library_sources_ifdef(CONFIG_LISM_CHARGE_TELEMETRY src/lism_charge.c)
  if(CONFIG_LISM_CHARGE_TELEMETRY)
    # Add a completion observer to the pinned, sole ZMK battery sampling worker.
    # Never run a second ADC sampler alongside it or infer freshness from cached get.
    set(_battery_src "${APPLICATION_SOURCE_DIR}/src/battery.c")
    include("${CMAKE_CURRENT_LIST_DIR}/cmake/lism_battery_telemetry.cmake")
    lism_patch_battery_telemetry("${_battery_src}" "${CMAKE_CURRENT_BINARY_DIR}/lism_zmk_battery.c")
    set_source_files_properties("${_battery_src}"
      DIRECTORY "${APPLICATION_SOURCE_DIR}" PROPERTIES HEADER_FILE_ONLY ON)
    zephyr_library_sources("${CMAKE_CURRENT_BINARY_DIR}/lism_zmk_battery.c")
    if(CONFIG_CHARGE_INDICATOR)
      # The pinned RGB indicator sleeps inside a GPIO ISR. Defer its unchanged
      # LED policy to a debounced work item before shipping charging support.
      get_filename_component(_charge_west_root "${ZEPHYR_BASE}" DIRECTORY)
      set(_charge_dir "${_charge_west_root}/zmk-feature-charge-indicator")
      set(_charge_src "${_charge_dir}/src/charge_indicator.c")
      file(READ "${_charge_src}" _charge_code)
      set(_charge_old [=[static void chg_handler(const struct device *port, struct gpio_callback *cb, uint32_t pins)
{
    k_sleep(K_MSEC(8));
    bool charging = read_charging();
    atomic_set(&is_charging, charging);
    apply_charging_color(charging);
}]=])
      set(_charge_new [=[static void chg_deferred(struct k_work *work)
{
    ARG_UNUSED(work);
    bool charging = read_charging();
    atomic_set(&is_charging, charging);
    apply_charging_color(charging);
}
K_WORK_DELAYABLE_DEFINE(chg_deferred_work, chg_deferred);
static void chg_handler(const struct device *port, struct gpio_callback *cb, uint32_t pins)
{
    ARG_UNUSED(port); ARG_UNUSED(cb); ARG_UNUSED(pins);
    k_work_reschedule(&chg_deferred_work, K_MSEC(8));
}]=])
      string(FIND "${_charge_code}" "${_charge_old}" _charge_match)
      if(_charge_match EQUAL -1)
        message(FATAL_ERROR "Pinned charge indicator IRQ hook moved")
      endif()
      string(REPLACE "${_charge_old}" "${_charge_new}" _charge_code "${_charge_code}")
      file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/lism_charge_indicator.c" "${_charge_code}")
      # This module uses target_sources(app), not a module-local library. Source
      # properties must be set in the directory where app was created.
      set_source_files_properties("${_charge_src}" TARGET_DIRECTORY app
        PROPERTIES HEADER_FILE_ONLY ON)
      zephyr_library_sources("${CMAKE_CURRENT_BINARY_DIR}/lism_charge_indicator.c")
    endif()
  endif()
  zephyr_library_sources_ifdef(CONFIG_LISM_PROSPECTOR_LOCK_INDICATORS src/prospector_lock_indicators.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/axs5106l_touchpad.c)
  if(CONFIG_LISM_XIAO147_RAW_CAPTURE OR CONFIG_LISM_XIAO147_AUTO_TOUCH)
    zephyr_library_sources(src/xiao147_raw_capture.c)
  endif()
  if(CONFIG_LISM_XIAO147_GESTURE_SHADOW OR CONFIG_LISM_XIAO147_AUTO_TOUCH)
    zephyr_library_sources(src/xiao147_raw_gestures.c)
  endif()
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_AUTO_TOUCH src/xiao147_auto_touch.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/lsm6ds3_air_mouse.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_rotation.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/display_jd9853a.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_display_test.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_battery_rings.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_rssi.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_hid_trace.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_ui.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_palette.c)
  if(CONFIG_LISM_BLE_INPUT_BRIDGE)
    zephyr_library_sources(src/lism_input_host.c src/lism_input_parser.c src/lism_input_merge.c
                           src/xiao147_inputs_ui.c src/xiao147_usr1_click.c)
  else()
    zephyr_library_sources_ifdef(CONFIG_LISM_BLE_HID_BRIDGE src/lism_hid_host.c src/lism_hid_parser.c)
  endif()
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/fonts/xiao_menu_bold.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_wpm_meter.c src/xiao147_output.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_RESISTIVE_JOYSTICK src/kscan_resistive_joystick.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_JOYSTICK_STATUS src/joystick_status.c)
endif()

# Prospector's default SYS_INIT rotates the original square display by 270
# degrees. The XIAO 1.47-inch LCD is already configured as 320x172 landscape
# by its ST7789-compatible devicetree MADCTL and offsets. Running the second
# rotation swaps its dimensions and breaks both widgets and touch coordinates.
# Only suppress that one Prospector source for this new dongle shield.
if(CONFIG_LISM_XIAO147_TOUCH)
  zephyr_link_libraries(-Wl,--wrap=zmk_endpoint_send_mouse_report)
  get_filename_component(_west_workspace_root "${ZEPHYR_BASE}" DIRECTORY)
  set(_prospector_shield_dir
      "${_west_workspace_root}/prospector-zmk-module/boards/shields/prospector_adapter")
  set(_prospector_rotate_src
      "${_prospector_shield_dir}/src/display_rotate_init.c")
  if(NOT EXISTS "${_prospector_rotate_src}")
    message(FATAL_ERROR "Prospector rotation source moved: ${_prospector_rotate_src}")
  endif()
  set_source_files_properties("${_prospector_rotate_src}"
      DIRECTORY "${_prospector_shield_dir}"
      PROPERTIES HEADER_FILE_ONLY ON)
  # Preserve OPERATOR geometry but fix widget startup and LVGL thread ownership.
  zephyr_library_include_directories("${_prospector_shield_dir}/include"
                                    "${_prospector_shield_dir}/src/layouts/operator")
  # Recompile these pinned widgets with an XIAO-only palette. Keep upstream
  # files intact, and replace precisely one include rather than relying on
  # include-path precedence (quoted includes prefer the source directory).
  foreach(_widget modifier_indicator layer_display)
    set(_color_src "${_prospector_shield_dir}/src/layouts/operator/${_widget}.c")
    file(READ "${_color_src}" _color_code)
    string(REGEX MATCHALL "#include \"display_colors.h\"" _color_matches "${_color_code}")
    list(LENGTH _color_matches _color_match_count)
    if(NOT _color_match_count EQUAL 1)
      message(FATAL_ERROR "Pinned OPERATOR ${_widget} color include moved")
    endif()
    string(REPLACE "#include \"display_colors.h\""
      "#include \"${CMAKE_CURRENT_LIST_DIR}/src/xiao147_operator_colors.h\""
      _color_code "${_color_code}")
    if(_widget STREQUAL "modifier_indicator")
      string(APPEND _color_code "\nvoid xiao147_modifiers_refresh_colors(void) {\n    modifier_indicator_update_cb(modifier_indicator_get_state(NULL));\n}\n")
    endif()
    set(_color_copy "${CMAKE_CURRENT_BINARY_DIR}/xiao147_${_widget}.c")
    file(WRITE "${_color_copy}" "${_color_code}")
    set_source_files_properties("${_color_src}"
      DIRECTORY "${_prospector_shield_dir}" PROPERTIES HEADER_FILE_ONLY ON)
    zephyr_library_sources("${_color_copy}")
  endforeach()
  foreach(_widget wpm_meter output)
    set(_widget_src "${_prospector_shield_dir}/src/layouts/operator/${_widget}.c")
    if(NOT EXISTS "${_widget_src}")
      message(FATAL_ERROR "Prospector widget source moved: ${_widget_src}")
    endif()
    set_source_files_properties("${_widget_src}"
        DIRECTORY "${_prospector_shield_dir}" PROPERTIES HEADER_FILE_ONLY ON)
  endforeach()
  # Prospector already removes Zephyr's original ST7789 source. Replace its
  # fork only for this shield: exactly one DEVICE_DT_INST_DEFINE remains.
  set(_prospector_driver
      "${_west_workspace_root}/prospector-zmk-module/drivers/display/display_st7789v.c")
  if(NOT EXISTS "${_prospector_driver}" OR NOT TARGET drivers__display)
    message(FATAL_ERROR "Prospector display driver target/source moved")
  endif()
  set_source_files_properties("${_prospector_driver}"
      TARGET_DIRECTORY drivers__display PROPERTIES HEADER_FILE_ONLY ON)
    set_source_files_properties("${_prospector_shield_dir}/src/custom_status_screen.c"
        DIRECTORY "${_prospector_shield_dir}"
        PROPERTIES COMPILE_DEFINITIONS
        "zmk_display_status_screen=xiao147_stock_status_screen")
endif()
