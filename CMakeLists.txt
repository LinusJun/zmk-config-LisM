if(CONFIG_LISM_PROSPECTOR_LOCK_INDICATORS)
  zephyr_library()
  zephyr_library_sources(src/prospector_lock_indicators.c)
endif()
