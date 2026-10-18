# Keep measurement validity independent from downstream event/BAS delivery.
# This edits a generated copy only, against the verified pinned ZMK source.
function(lism_patch_battery_telemetry source output)
  file(SHA256 "${source}" digest)
  if(NOT digest STREQUAL "152753e9646ba980f33f79184f4c7b08b79716ff3b2da3a61f4c9f78d23a1fc4")
    message(FATAL_ERROR "Pinned ZMK battery source changed; review sampling hook")
  endif()
  file(READ "${source}" code)
  string(REPLACE "static int zmk_battery_update(const struct device *battery) {"
    "static int zmk_battery_update(const struct device *battery, int *sample_result) {\n    *sample_result = -EIO;" code "${code}")
  # Both supported sensor modes have now completed their fresh channel read.
  string(REPLACE "    if (last_state_of_charge != state_of_charge.val1) {"
    "    *sample_result = 0; /* Measurement succeeded; publishing may still fail. */\n    if (last_state_of_charge != state_of_charge.val1) {" code "${code}")
  # Preserve the real ADC/SOC errno, but never relabel a later publish error.
  string(FIND "${code}" "    *sample_result = 0;" measured_end)
  string(SUBSTRING "${code}" 0 ${measured_end} measured)
  string(SUBSTRING "${code}" ${measured_end} -1 publishing)
  string(REPLACE "        return rc;" "        *sample_result = rc;\n        return rc;" measured "${measured}")
  set(code "${measured}${publishing}")
  string(REPLACE "    int rc = zmk_battery_update(battery);"
    "    extern void lism_charge_begin_battery_sample(void);\n    extern void lism_charge_note_battery_sample(int result);\n    lism_charge_begin_battery_sample();\n    int sample_result;\n    int rc = zmk_battery_update(battery, &sample_result);\n    lism_charge_note_battery_sample(sample_result);" code "${code}")
  string(REPLACE "K_WORK_DEFINE(battery_work, zmk_battery_work);"
    "K_WORK_DEFINE(battery_work, zmk_battery_work);\nvoid lism_charge_request_battery_sample(void) {\n    k_work_submit_to_queue(zmk_workqueue_lowprio_work_q(), &battery_work);\n}\n" code "${code}")
  string(PREPEND code "#include <errno.h>\n")
  file(WRITE "${output}" "${code}")
endfunction()
