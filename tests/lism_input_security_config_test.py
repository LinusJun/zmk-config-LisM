"""Pinned copied Kconfig preserves default policy and resolves rsource paths."""
import importlib.util
import os
from pathlib import Path
import tempfile

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_input_security", root / "tools/prepare_input_security.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
source = Path(os.environ.get("LISM_ZMK_APP", "/private/tmp/lism-trackpad-build/zmk/app")) / "Kconfig"
original = source.read_bytes()
with tempfile.TemporaryDirectory() as directory:
    out = Path(directory) / "Kconfig"
    module.generate(source, out)
    text = out.read_text()
    assert "select BT_SMP_SC_PAIR_ONLY if !LISM_INPUT_LEGACY_COMPAT" in text
    assert 'rsource "' not in text
    assert text.count('source "' + str(source.parent) + '/') == 11
    assert source.read_bytes() == original
    drift = Path(directory) / "drift"
    drift.write_bytes(original + b"\n")
    try:
        module.generate(drift, out)
    except ValueError:
        pass
    else:
        raise AssertionError("Unpinned Kconfig accepted")
config = (root / "Kconfig").read_text().split("config LISM_INPUT_LEGACY_COMPAT", 1)[1].split("\nconfig ", 1)[0]
assert "depends on LISM_BLE_INPUT_BRIDGE" in config and "default n" in config
print("PASS: generated Kconfig pins SHA, preserves default SC policy, absolute includes and upstream")
