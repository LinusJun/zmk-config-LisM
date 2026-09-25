if(CONFIG_LISM_RESISTIVE_JOYSTICK)
  zephyr_library()
  zephyr_library_sources(src/kscan_resistive_joystick.c)
endif()
