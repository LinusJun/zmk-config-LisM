"""Configuration regression for the stock LisM digital four-way stick."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def clean(text):
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))

def read(path):
    return clean((ROOT / path).read_text())

baseline = json.loads((ROOT / "tests/fourwaystick_baseline.json").read_text())
old_count = baseline["old_count"]
count = old_count + 4
row = baseline["row"]
layers = {}
keymap = read("config/lism.keymap")
mt = re.search(r"&mt\s*\{(.*?)\};", keymap, re.S)[1]
assert "tapping-term-ms = <200>;" in mt
assert "require-prior-idle-ms = <125>;" in mt
assert 'flavor = "balanced";' in mt
assert "&lt { quick-tap-ms = <300>; };" in keymap
for m in re.finditer(r"(default_layer|layer_\d+)\s*\{.*?bindings\s*=\s*<(.*?)>;", keymap, re.S):
    layers[m[1]] = [" ".join(x.split()) for x in re.findall(r"&[A-Za-z_]\w*[^&]*", m[2])]
expected = [
    ["&kp UP", "&kp RIGHT", "&kp DOWN", "&kp LEFT"],
    ["&kp LEFT_SHIFT", "&kp LEFT_GUI", "&kp LEFT_CONTROL", "&kp LEFT_ALT"],
    ["&stick_scroll MOVE_Y(-LISM_4WAY_SCROLL_SPEED)", "&stick_scroll MOVE_X(-LISM_4WAY_SCROLL_SPEED)",
     "&stick_scroll MOVE_Y(LISM_4WAY_SCROLL_SPEED)", "&stick_scroll MOVE_X(LISM_4WAY_SCROLL_SPEED)"],
    ["&kp LEFT_SHIFT", "&kp LEFT_GUI", "&kp LEFT_CONTROL", "&kp LEFT_ALT"],
    ["&trans"] * 4,
    ["&kp W", "&kp D", "&kp S", "&kp A"],
    ["&kp W", "&kp D", "&kp S", "&kp A"], ["&trans"] * 4,
]
assert layers.keys() == baseline["layers"].keys()
for index, (name, bindings) in enumerate(layers.items()):
    assert len(bindings) == count, (name, len(bindings), count)
    previous = baseline["layers"][name].copy()
    if name == "layer_4":
        assert previous[1] == "&none"
        previous[1] = "&to 5"
    elif name == "layer_6":
        assert previous[1] == "&trans"
        previous[1] = "&to 0"
    assert bindings[:old_count] == previous, name
    assert bindings[old_count:] == expected[index], (name, bindings[old_count:])
assert layers["layer_4"][10] == "&to 5"

matrix_text = read("boards/shields/lism/lism.dtsi")
body = re.search(r"transform_42keymap:.*?map\s*=\s*<(.*?)>", matrix_text, re.S)[1]
matrix = [list(map(int, m)) for m in re.findall(r"RC\((\d+),\s*(\d+)\)", body)]
assert matrix[:old_count] == baseline["matrix"]
assert matrix[old_count:] == [[row, col] for col in range(4)]
assert len(matrix) == len(set(map(tuple, matrix))) == count
assert re.search(r"transform_42keymap:.*?rows\s*=\s*<" + str(row + 1) + ">", matrix_text, re.S)
layout = read("boards/shields/lism/lism-layouts.dtsi")
body = re.search(r"physical_42key_layout:.*?keys.*?=(.*?);", layout, re.S)[1]
attributes = [list(map(int, m.split())) for m in re.findall(r"<&key_physical_attrs\s+([^>]+)>", body)]
assert len(attributes) == count
assert attributes[:old_count] == baseline["attributes"]
positions = re.findall(r"positions\s*=(.*?);", layout, re.S)
assert len(positions) == 2
for body in positions:
    values = list(map(int, re.findall(r"\d+", body)))
    assert len(values) == count and sorted(values) == list(range(count)), values
drawing = json.loads((ROOT / "config/lism.json").read_text())["layouts"]["LisM"]["layout"]
assert len(drawing) == count
for key, attrs in zip(drawing, attributes):
    assert [key.get("w", 1), key.get("h", 1), key["x"], key["y"]] == [v / 100 for v in attrs[:4]]

left = read("boards/shields/lism/lism_left.overlay")
gpios_body = re.search(r"left_4way:.*?input-gpios\s*=(.*?);", left, re.S)[1]
gpios = re.findall(r"<&(\w+)\s+(\d+)\s+\(([^)]*)\)>", gpios_body)
assert [(bank, int(pin)) for bank, pin, flags in gpios] == [("gpio0", 10), ("xiao_d", 6), ("xiao_d", 5), ("xiao_d", 4)]
assert all("GPIO_ACTIVE_LOW" in flags and "GPIO_PULL_UP" in flags for bank, pin, flags in gpios)
assert "zmk,kscan = &left_4way_composite;" in left
assert "row-offset = <" + str(row) + ">" in left
assert "paw3222" not in left and "trackball_split_input" not in left and 'status = "disabled"' in left
conf = (ROOT / "config/lism_left.conf").read_text()
assert "CONFIG_SPI=n" in conf and "CONFIG_PAW3222=n" in conf
assert re.search(r"if SHIELD_LISM_R\s+config PAW3222", read("boards/shields/lism/Kconfig.defconfig"))
assert 'status = "okay"' in re.search(r"layer_6\s*\{(.*?)bindings", keymap, re.S)[1]
assert "device = <&stick_scroll>;" in keymap
assert "x-input-code = <INPUT_REL_HWHEEL>" in keymap
assert "y-input-code = <INPUT_REL_WHEEL>" in keymap
assert "#define ZMK_POINTING_DEFAULT_SCRL_VAL 200" in keymap
assert "#define LISM_4WAY_SCROLL_SPEED 25" in keymap
assert 'compatible = "zmk,behavior-input-two-axis"' in keymap
assert "time-to-max-speed-ms = <0>" in keymap
assert "acceleration-exponent = <0>" in keymap
for path, digest in baseline["protected_files"].items():
    data = (ROOT / path).read_bytes()
    if path == "config/lism_right.conf":
        # Explicitly allow only additive charging telemetry; retain the original
        # protection hash for every trackball/input/radio setting.
        assert data.count(b"CONFIG_LISM_CHARGE_TELEMETRY=y\n") == 1
        data = data.replace(b"CONFIG_LISM_CHARGE_TELEMETRY=y\n", b"")
    if path in ("src/xiao147_ui.c", "src/xiao147_battery_rings.c"):
        # Normalize only the diagnostic-startup gate. UI's reviewed native-pixel
        # repair has a new protected hash; right-trackball/joystick hashes and
        # the unchanged battery/rssi geometry remain the original baseline.
        data = data.replace(b'#include "xiao147_display_test.h"\n', b'')
        data = data.replace(
            b'if (!zmk_display_is_initialized() || !xiao147_display_ui_ready()) {',
            b'if (!zmk_display_is_initialized()) {')
    if path == "src/xiao147_ui.c":
        # Read-only touch safety query: no change to protected UI/charging code.
        query = b'bool xiao147_ui_is_sleeping(void)\n{\n    return atomic_get(&sleeping) != 0;\n}\n\n'
        assert data.count(query) == 1
        data = data.replace(query, b'')
    assert hashlib.sha256(data).hexdigest() == digest, path + " unexpectedly changed"
build = (ROOT / "build.yaml").read_text()
assert "lism_left_peripheral_4waystick" in build and "lism_left_peripheral_trackball" not in build
assert "lism_right_peripheral_trackball" in build
assert not re.search(r"artifact-name:.*(?:adc_logging|diagnostic)", build)
dongle = read("boards/shields/lism/lism_dongle.overlay")
assert '&trackball_split_listener { status = "disabled"; };' in dongle
right = dongle.split('&trackball_split_listener_right {', 1)[1]
nav = re.search(r'\bscroller\s*\{(.*?)\};', right, re.S)[1]
device = re.search(r'\bdevice_scroll\s*\{(.*?)\};', right, re.S)[1]
mark = re.search(r'\bscrolloriginal\s*\{(.*?)\};', right, re.S)[1]
assert re.search(r'layers\s*=\s*<2>', nav)
assert '<&zip_scroll_x_scaler 0 1>' in nav
assert re.search(r'layers\s*=\s*<4>', device)
assert re.findall(r'<&([^>]+)>', device) == [
    'zip_zxy', 'zip_xy_to_scroll_mapper',
    'zip_scroll_transform INPUT_TRANSFORM_Y_INVERT',
    'zip_xy_scaler 1 16', 'zip_scroll_scaler 1 16']
assert re.search(r'layers\s*=\s*<3>', mark) and '<&zip_scroll_y_scaler 0 1>' in mark
print(f"PASS: {count} unique positions; Base arrows, NAV held natural scroll; DEVICE right trackball both axes; NAV/Mark trackball locks and remaining bindings preserved")
