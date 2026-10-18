if(CONFIG_SHIELD_LISM_R AND NOT CONFIG_LISM_STANDALONE_RIGHT)
  message(FATAL_ERROR "Standalone right must compile the ZMK central role")
endif()
if(CONFIG_LISM_STANDALONE_RIGHT)
  if(NOT CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS EQUAL 1 OR CONFIG_BT_MAX_PAIRED LESS 6)
    message(FATAL_ERROR "Standalone right needs exactly one left slot and space for four hosts, left and dongle")
  endif()
elseif(CONFIG_BT_ID_MAX LESS 2 OR CONFIG_BT_MAX_CONN LESS 2 OR CONFIG_BT_CTLR_SDC_PERIPHERAL_COUNT LESS 2)
  message(FATAL_ERROR "Standalone left needs two identities and two peripheral connection slots")
endif()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  "${CMAKE_CURRENT_LIST_DIR}/../tools/prepare_standalone.py")
# Opt-in copies of pinned ZMK sources, isolated from ordinary firmware builds.
set(_standalone "${CMAKE_CURRENT_BINARY_DIR}/standalone")
set(_upstream_peripheral "${APPLICATION_SOURCE_DIR}/src/split/bluetooth/peripheral.c")
set_source_files_properties("${_upstream_peripheral}" DIRECTORY "${APPLICATION_SOURCE_DIR}" PROPERTIES HEADER_FILE_ONLY ON)
zephyr_library_sources(src/standalone/link.c)
zephyr_library_include_directories("${APPLICATION_SOURCE_DIR}/src/split/bluetooth")
execute_process(COMMAND ${PYTHON_EXECUTABLE} "${CMAKE_CURRENT_LIST_DIR}/../tools/prepare_standalone.py"
    "${APPLICATION_SOURCE_DIR}" "${_standalone}" "${CMAKE_CURRENT_LIST_DIR}/.."
    RESULT_VARIABLE _standalone_result)
if(NOT _standalone_result EQUAL 0)
  message(FATAL_ERROR "Pinned standalone source generation failed")
endif()
set_source_files_properties("${APPLICATION_SOURCE_DIR}/src/split/bluetooth/service.c"
  DIRECTORY "${APPLICATION_SOURCE_DIR}" PROPERTIES HEADER_FILE_ONLY ON)
zephyr_library_sources("${_standalone}/service.c")
if(CONFIG_LISM_STANDALONE_RIGHT)
  foreach(_file event_manager ble hog endpoints activity)
    set_source_files_properties("${APPLICATION_SOURCE_DIR}/src/${_file}.c"
      DIRECTORY "${APPLICATION_SOURCE_DIR}" PROPERTIES HEADER_FILE_ONLY ON)
    zephyr_library_sources("${_standalone}/${_file}.c")
  endforeach()
  set_source_files_properties("${APPLICATION_SOURCE_DIR}/src/pointing/input_listener.c"
    DIRECTORY "${APPLICATION_SOURCE_DIR}" PROPERTIES HEADER_FILE_ONLY ON)
  zephyr_library_sources("${_standalone}/input_listener.c")
  set_source_files_properties("${APPLICATION_SOURCE_DIR}/src/split/bluetooth/central.c"
    DIRECTORY "${APPLICATION_SOURCE_DIR}" PROPERTIES HEADER_FILE_ONLY ON)
  zephyr_library_sources("${_standalone}/central.c" "${_standalone}/command.c"
    src/standalone/mode.c src/standalone/chord.c src/standalone/pointer.c)
  target_include_directories(zephyr_interface BEFORE INTERFACE "${_standalone}/include")
  zephyr_linker_sources(SECTIONS "${APPLICATION_SOURCE_DIR}/include/linker/zmk-split-transport-peripheral.ld")
endif()
