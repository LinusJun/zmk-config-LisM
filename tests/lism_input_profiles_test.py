"""Keep bond storage capacity independent of saved computer profile indices."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
app = Path(os.environ.get("LISM_ZMK_APP", "/private/tmp/lism-trackpad-build/zmk/app"))
original = (app / "include/zmk/ble.h").read_text()
old = "#define ZMK_BLE_PROFILE_COUNT (CONFIG_BT_MAX_PAIRED - CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS)"
new = "#define ZMK_BLE_PROFILE_COUNT CONFIG_LISM_BLE_HOST_PROFILES"
with tempfile.TemporaryDirectory(prefix="lism-input-profiles-") as tmp:
    out = Path(tmp)
    script = out / "profiles.cmake"
    script.write_text(f'include("{root}/cmake/lism_input_profiles.cmake")\n'
                      f'lism_input_profile_header("{app}/include/zmk/ble.h" "{out}/include")\n')
    subprocess.run(["cmake", "-P", str(script)], check=True)
    generated = (out / "include/zmk/ble.h").read_text()
    assert generated == original.replace(old, new)
    assert "#define ZMK_BLE_PROFILE_COUNT CONFIG_BT_MAX_PAIRED" in generated  # noncentral untouched
    assert "#define ZMK_SPLIT_BLE_PERIPHERAL_COUNT CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS" in generated
    # Compile the actual generated header in both roles, preserving branch semantics.
    (out / "include/zmk/keys.h").write_text('#pragma once\n#include <stdint.h>\n#include <stdbool.h>\n#define IS_ENABLED(x) x\n')
    (out / "include/zmk/ble").mkdir()
    (out / "include/zmk/ble/profile.h").write_text('#pragma once\ntypedef struct { int dummy; } bt_addr_le_t;\nstruct bt_conn;\n')
    test = out / "test.c"
    test.write_text('#include <zmk/ble.h>\n_Static_assert(ZMK_BLE_PROFILE_COUNT==EXPECTED,"host profiles");\nint main(void){return 0;}\n')
    for central, expected in ((1, 6), (0, 14)):
        subprocess.run(["cc", "-std=c11", "-Wall", "-Werror", "-I", str(out / "include"),
                        "-DCONFIG_ZMK_SPLIT_BLE=1", f"-DCONFIG_ZMK_SPLIT_ROLE_CENTRAL={central}",
                        "-DCONFIG_BT_MAX_PAIRED=14", "-DCONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS=6",
                        "-DCONFIG_LISM_BLE_HOST_PROFILES=6", f"-DEXPECTED={expected}",
                        str(test), "-o", str(out / "test")], check=True)
    for altered in (original.replace(old, old + "_CHANGED"), original + "\n" + old + "\n"):
        bad = out / "bad.h"
        bad.write_text(altered)
        script.write_text(f'include("{root}/cmake/lism_input_profiles.cmake")\n'
                          f'lism_input_profile_header("{bad}" "{out}/bad")\n')
        result = subprocess.run(["cmake", "-P", str(script)], capture_output=True)
        assert result.returncode != 0, "missing/duplicated profile hook must fail closed"
print("PASS: six preserved computer profiles, fourteen independent bond slots, noncentral unchanged, header drift rejected")
