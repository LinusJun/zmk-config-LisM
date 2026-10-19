if(CONFIG_CHARGE_INDICATOR)
      # The pinned RGB indicator sleeps inside a GPIO ISR. Defer its unchanged
      # LED policy to a debounced work item before shipping charging support.
      get_filename_component(_charge_west_root "${ZEPHYR_BASE}" DIRECTORY)
      set(_charge_dir "${_charge_west_root}/zmk-feature-charge-indicator")
      set(_charge_src "${_charge_dir}/src/charge_indicator.c")
      # Offline integration workspaces may symlink cached modules. CMake source
      # properties must name the exact canonical target_sources(app) path.
      get_filename_component(_charge_src "${_charge_src}" REALPATH)
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
