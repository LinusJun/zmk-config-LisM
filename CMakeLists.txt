if(CONFIG_LISM_PROSPECTOR_LOCK_INDICATORS OR CONFIG_LISM_XIAO147_TOUCH OR CONFIG_LISM_RESISTIVE_JOYSTICK)
  zephyr_library()
  zephyr_library_sources_ifdef(CONFIG_LISM_PROSPECTOR_LOCK_INDICATORS src/prospector_lock_indicators.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/axs5106l_touchpad.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/lsm6ds3_air_mouse.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_rotation.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/display_jd9853a.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_DISPLAY_SELFTEST src/xiao147_display_test.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_battery_rings.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_rssi.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/xiao147_ui.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_RESISTIVE_JOYSTICK src/kscan_resistive_joystick.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_JOYSTICK_STATUS src/joystick_status.c)
endif()

# Prospector's default SYS_INIT rotates the original square display by 270
# degrees. The XIAO 1.47-inch LCD is already configured as 320x172 landscape
# by its ST7789-compatible devicetree MADCTL and offsets. Running the second
# rotation swaps its dimensions and breaks both widgets and touch coordinates.
# Only suppress that one Prospector source for this new dongle shield.
if(CONFIG_LISM_XIAO147_TOUCH)
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
  # Prospector already removes Zephyr's original ST7789 source. Replace its
  # fork only for this shield: exactly one DEVICE_DT_INST_DEFINE remains.
  set(_prospector_driver
      "${_west_workspace_root}/prospector-zmk-module/drivers/display/display_st7789v.c")
  if(NOT EXISTS "${_prospector_driver}" OR NOT TARGET drivers__display)
    message(FATAL_ERROR "Prospector display driver target/source moved")
  endif()
  set_source_files_properties("${_prospector_driver}"
      TARGET_DIRECTORY drivers__display PROPERTIES HEADER_FILE_ONLY ON)
  if(CONFIG_LISM_XIAO147_DISPLAY_SELFTEST)
    set_source_files_properties("${_prospector_shield_dir}/src/custom_status_screen.c"
        DIRECTORY "${_prospector_shield_dir}"
        PROPERTIES COMPILE_DEFINITIONS
        "zmk_display_status_screen=xiao147_stock_status_screen")
  endif()
endif()
