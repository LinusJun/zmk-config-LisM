"""Generate a pinned ZMK Kconfig root; never modify the upstream checkout."""
from pathlib import Path
import argparse
import hashlib
import re

PINNED_SHA256 = "9393b6e9aa993e87e40657c7f58d517295d3546fb6709ca100b010e01dc9f050"
ANCHOR = "    select BT_SMP_SC_PAIR_ONLY\n"


def generate(source, destination, legacy_build_policy=None):
    source = Path(source).resolve()
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != PINNED_SHA256:
        raise ValueError("Unverified ZMK Kconfig: refusing compatibility policy override")
    text = data.decode()
    if text.count(ANCHOR) != 1:
        raise ValueError("SC policy anchor moved or duplicated")
    policy = "LISM_ESB_UNUSED_LEGACY"
    prefix = ""
    if legacy_build_policy is not None:
        # An independent build constant avoids the NCS BLE/I2C/UI dependency
        # cycle. CMake must verify it matches the actual per-device policy.
        policy = "LISM_ESB_UNUSED_LEGACY_BUILD_POLICY"
        prefix = ("config LISM_ESB_UNUSED_LEGACY_BUILD_POLICY\n    bool\n    default " +
                  ("y" if legacy_build_policy else "n") + "\n\n")
    text = prefix + text.replace(ANCHOR, f"    select BT_SMP_SC_PAIR_ONLY if !{policy}\n")
    def absolute(match):
        path = match.group(2)
        if Path(path).is_absolute() or "$" in path or ".." in Path(path).parts:
            raise ValueError("Unverified relative Kconfig source")
        return match.group(1) + 'source "' + str(source.parent / path) + '"'
    text, count = re.subn(r'(?m)^(\s*)rsource "([^"\n]+)"', absolute, text)
    if count != 11 or re.search(r'(?m)^\s*rsource\b', text):
        raise ValueError("Kconfig source anchors changed")
    Path(destination).write_text("# Generated pinned LisM legacy compatibility root.\n" + text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    generate(args.source, args.output)
