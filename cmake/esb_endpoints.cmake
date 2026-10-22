# Keep original ZMK sources read-only and bind queued HOG reports to a host.
get_filename_component(_lism_endpoint_source "${APPLICATION_SOURCE_DIR}/src/endpoints.c" REALPATH)
get_filename_component(_lism_hog_source "${APPLICATION_SOURCE_DIR}/src/hog.c" REALPATH)
set(_lism_endpoint_copy "${CMAKE_CURRENT_BINARY_DIR}/lism_endpoints.c")
set(_lism_hog_copy "${CMAKE_CURRENT_BINARY_DIR}/lism_hog_routing.c")
execute_process(COMMAND "${PYTHON_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/../tools/prepare_hog_routing.py"
  "${_lism_hog_source}" "${_lism_hog_copy}" "${_lism_endpoint_source}" "${_lism_endpoint_copy}" RESULT_VARIABLE result)
if(NOT result EQUAL 0)
  message(FATAL_ERROR "Pinned HOG routing adaptation failed")
endif()
set_source_files_properties("${_lism_endpoint_source}" "${_lism_hog_source}" TARGET_DIRECTORY app PROPERTIES HEADER_FILE_ONLY ON)
target_sources(app PRIVATE "${_lism_endpoint_copy}" "${_lism_hog_copy}")
