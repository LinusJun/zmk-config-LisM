# Pinned behavior fix: changing the output is a press-only action, whose
# matching release is nevertheless a successfully consumed key event.
function(lism_patch_output_behavior input output)
  file(READ "${input}" code)
  set(old "static const struct behavior_driver_api behavior_outputs_driver_api = {\n    .binding_pressed = on_keymap_binding_pressed,")
  string(FIND "${code}" "${old}" where)
  if(where LESS 0)
    message(FATAL_ERROR "Pinned output behavior moved; refusing an unverified patch")
  endif()
  set(new [=[static int lism_output_released(struct zmk_behavior_binding *binding,
                                struct zmk_behavior_binding_event event) {
    (void)binding; (void)event;
    return 0;
}

static const struct behavior_driver_api behavior_outputs_driver_api = {
    .binding_released = lism_output_released,
    .binding_pressed = on_keymap_binding_pressed,]=])
  string(REPLACE "${old}" "${new}" code "${code}")
  file(WRITE "${output}" "${code}")
endfunction()
