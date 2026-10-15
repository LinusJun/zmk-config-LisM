if(CONFIG_LISM_PROSPECTOR_LOCK_INDICATORS OR CONFIG_LISM_XIAO147_TOUCH OR CONFIG_LISM_RESISTIVE_JOYSTICK OR CONFIG_LISM_CHARGE_TELEMETRY)
  zephyr_library()
  zephyr_library_sources_ifdef(CONFIG_LISM_CHARGE_TELEMETRY src/lism_charge.c)
  if(CONFIG_LISM_CHARGE_TELEMETRY)
    # Add a completion observer to the pinned, sole ZMK battery sampling worker.
    # Never run a second ADC sampler alongside it or infer freshness from cached get.
    set(_battery_src "${APPLICATION_SOURCE_DIR}/src/battery.c")
    file(READ "${_battery_src}" _battery_code)
    set(_battery_hook "    int rc = zmk_battery_update(battery);")
    string(FIND "${_battery_code}" "${_battery_hook}" _battery_hook_pos)
    if(_battery_hook_pos EQUAL -1)
      message(FATAL_ERROR "Pinned ZMK battery completion hook moved")
    endif()
    string(REPLACE "${_battery_hook}"
      "${_battery_hook}\n    extern void lism_charge_note_battery_sample(int result);\n    lism_charge_note_battery_sample(rc);"
      _battery_code "${_battery_code}")
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/lism_zmk_battery.c" "${_battery_code}")
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
      set_source_files_properties("${_charge_src}" DIRECTORY "${_charge_dir}"
        PROPERTIES HEADER_FILE_ONLY ON)
      zephyr_library_sources("${CMAKE_CURRENT_BINARY_DIR}/lism_charge_indicator.c")
    endif()
  endif()
  zephyr_library_sources_ifdef(CONFIG_LISM_PROSPECTOR_LOCK_INDICATORS src/prospector_lock_indicators.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/axs5106l_touchpad.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_RAW_CAPTURE src/xiao147_raw_capture.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_GESTURE_SHADOW src/xiao147_raw_gestures.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/lsm6ds3_air_mouse.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_rotation.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/display_jd9853a.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_display_test.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_battery_rings.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_rssi.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_hid_trace.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_ui.c)
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
