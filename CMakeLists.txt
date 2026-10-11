if(CONFIG_LISM_PROSPECTOR_LOCK_INDICATORS OR CONFIG_LISM_XIAO147_TOUCH)
  zephyr_library()
  zephyr_library_sources_ifdef(CONFIG_LISM_PROSPECTOR_LOCK_INDICATORS src/prospector_lock_indicators.c)
  zephyr_library_sources_ifdef(CONFIG_LISM_XIAO147_TOUCH src/axs5106l_touchpad.c)
endif()

# Prospector's default SYS_INIT rotates the original square display by 270
# degrees. The XIAO 1.47-inch LCD is already configured as 320x172 landscape
# by its ST7789-compatible devicetree MADCTL and offsets. Running the second
# rotation swaps its dimensions and breaks both widgets and touch coordinates.
# Only suppress that one Prospector source for this new dongle shield.
if(CONFIG_LISM_XIAO147_TOUCH)
  if(NOT DEFINED ZEPHYR_PROSPECTOR_ZMK_MODULE_DIR)
    message(FATAL_ERROR "Prospector module path unavailable; cannot safely disable its display rotation")
  endif()
  set(_prospector_shield_dir
      "${ZEPHYR_PROSPECTOR_ZMK_MODULE_DIR}/boards/shields/prospector_adapter")
  set(_prospector_rotate_src
      "${_prospector_shield_dir}/src/display_rotate_init.c")
  if(NOT EXISTS "${_prospector_rotate_src}")
    message(FATAL_ERROR "Prospector rotation source moved: ${_prospector_rotate_src}")
  endif()
  set_source_files_properties("${_prospector_rotate_src}"
      DIRECTORY "${_prospector_shield_dir}"
      PROPERTIES HEADER_FILE_ONLY ON)
endif()
