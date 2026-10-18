"""Check the combined matrix without changing the already-tested 42 keyboard keys."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def clean(text):
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))


def read(path):
    return clean((ROOT / path).read_text())


baseline = json.loads((ROOT / "tests/lism_keyboard_baseline.json").read_text())
keymap = read("tests/fixtures/keymap_editor_baseline.keymap").split("keymap {", 1)[1]
layers = {}
for match in re.finditer(r"(default_layer|layer_\d+)\s*\{.*?bindings\s*=\s*<(.*?)>;", keymap, re.S):
    layers[match[1]] = [" ".join(value.split()) for value in
                        re.findall(r"&[A-Za-z_]\w*[^&]*", match[2])]
assert layers.keys() == baseline["layers"].keys()
for name, bindings in layers.items():
    assert len(bindings) == 58, (name, len(bindings))
    original = baseline["layers"][name].copy()
    if name == "layer_4":
        original[1] = "&to 5"
    elif name == "layer_6":
        original[1] = "&to 0"
    assert bindings[:42] == original, name
assert layers["default_layer"][42:54] == [
    "&kp W", "&kp S", "&kp A", "&kp D", "&kp SPACE", "&kp LEFT_SHIFT",
    "&kp W", "&kp S", "&kp A", "&kp D", "&none", "&kp LEFT_SHIFT",
]
for name in layers.keys() - {"default_layer"}:
    assert layers[name][42:54] == ["&trans"] * 12

matrix_text = read("boards/shields/lism/lism.dtsi")
matrix_body = re.search(r"transform_42keymap:.*?map\s*=\s*<(.*?)>", matrix_text, re.S)[1]
matrix = [list(map(int, match)) for match in re.findall(r"RC\((\d+),\s*(\d+)\)", matrix_body)]
assert matrix[:42] == baseline["matrix"]
assert len(matrix) == len(set(map(tuple, matrix))) == 58
assert matrix[42:54] == [[row, col] for row in (4, 5) for col in (0, 1, 2, 3, 4, 6)]
assert [4, 5] in matrix[:42] and [4, 10] in matrix[:42]
assert re.search(r"transform_42keymap:.*?rows\s*=\s*<7>", matrix_text, re.S)

layout = read("boards/shields/lism/lism-layouts.dtsi")
attributes_body = re.search(r"physical_42key_layout:.*?keys.*?=(.*?);", layout, re.S)[1]
attributes = [list(map(int, match.split())) for match in
              re.findall(r"<&key_physical_attrs\s+([^>]+)>", attributes_body)]
assert len(attributes) == 58 and attributes[:42] == baseline["attributes"]
draw_layout = json.loads((ROOT / "config/lism.json").read_text())["layouts"]["LisM"]["layout"]
assert len(draw_layout) == 58, "Keymap drawing must include both joystick virtual groups"
for key, attrs in zip(draw_layout, attributes):
    assert [key.get("w", 1), key.get("h", 1), key["x"], key["y"]] == [
        value / 100 for value in attrs[:4]
    ], (key, attrs)
position_body = re.search(r"physical_42key_layout\s*\{\s*physical-layout.*?positions\s*=(.*?);", layout, re.S)[1]
assert list(map(int, re.findall(r"\d+", position_body))) == list(range(58))
legacy_map = re.search(r"physical_40key_layout\s*\{\s*physical-layout.*?positions\s*=(.*?);", layout, re.S)[1]
legacy_positions = list(map(int, re.findall(r"\d+", legacy_map)))
assert legacy_positions[:42] == [*range(33), 40, 33, 34, 35, 36, 41, 37, 38, 39]
assert legacy_positions[42:] == list(range(42, 58))
assert len(legacy_positions) == 58, "ZMK requires equal entry counts even for inactive maps"
assert sorted(legacy_positions) == list(range(58)), "Legacy map must have no duplicates or omissions"

jp19 = read("boards/shields/lism/lism_joystick.overlay")
circle = read("boards/shields/lism/lism_circlepad.overlay")
assert "row-offset = <4>" in jp19 and "row-offset = <5>" in circle
assert "rows = <6>" in circle
assert "swap-axes;" in jp19 and "invert-x;" in jp19 and "eight-sector;" not in jp19
assert "swap-axes;" in circle and "eight-sector;" in circle and "invert-x;" not in circle
for overlay in (jp19, circle):
    for setting in ("preserve-calibrated-center;", "poll-period-ms = <10>",
                    "idle-timeout-ms = <300000>", "idle-poll-period-ms = <50>",
                    "sprint-full-radius = <1400>", "sprint-enter-percent = <50>",
                    "sprint-exit-percent = <45>"):
        assert setting in overlay, setting
assert "press-gpios" in jp19 and "press-gpios" not in circle
assert "activation-threshold = <150>" in jp19 and "release-threshold = <90>" in jp19
assert "activation-threshold = <300>" in circle and "release-threshold = <240>" in circle

build = (ROOT / "build.yaml").read_text()
names = re.findall(r"artifact-name:\s*(\S+)", build)
assert len(names) == len(set(names)) == 7, names
assert {"lism_jp19_peripheral_wasd", "lism_circlepad_peripheral_wasd",
        "lism_dongle_xiao_plus_147_touch", "lism_dongle_prospector_operator"} <= set(names)
assert not any(re.search(r"logging|serial|diagnostic", name) for name in names)
for path in ("config/lism_dongle.conf", "config/xiao_plus_147_touch.conf"):
    assert "CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS=4" in (ROOT / path).read_text()
defaults = read("boards/shields/lism/Kconfig.defconfig")
for option in ("BT_MAX_CONN", "BT_MAX_PAIRED"):
    assert re.search(r"config " + option + r"\s+default 9", defaults)
assert "codex/xiao147-all-peripherals" in (ROOT / ".github/workflows/build.yml").read_text()

print("PASS: 7 normal UF2 targets, both dongles, original layout and existing device mappings preserved,")
print("58 independent bindings and positions (including left 4waystick), tested joystick parameters, four saved BLE slots")
