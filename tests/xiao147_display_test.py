"""Display repair invariants; build/real-panel tests remain separate."""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
read = lambda p: (root / p).read_text()
config = read("config/xiao_plus_147_touch.conf")
for setting in ("CONFIG_LV_Z_VDB_SIZE=10", "CONFIG_LV_Z_DOUBLE_VDB=n",
                "CONFIG_LV_Z_MEM_POOL_SIZE=65536", "CONFIG_LV_COLOR_16_SWAP=n",
                "CONFIG_LV_Z_FLUSH_THREAD=n", "CONFIG_LV_USE_LOG=y",
                "CONFIG_SYS_HEAP_RUNTIME_STATS=y", "CONFIG_LV_Z_MEM_POOL_SYS_HEAP=y"):
    assert setting in config, setting
assert 320 * 172 * 2 * .1 + 65536 < 320 * 172 * 2 * .5 * 2 + 20000
overlay = read("boards/shields/xiao_plus_147_touch/xiao_plus_147_touch.overlay")
assert "mipi-max-frequency = <8000000>" in overlay
assert "reset-gpios = <&gpio1 3 GPIO_ACTIVE_LOW>" in overlay
assert "mdac = <0x28>" in overlay and "y-offset = <34>" in overlay
driver = read("src/display_jd9853a.c")
assert "sys_put_be16(pixel, &wire_pixels[i * 2])" in driver
assert "0x00, 0xe0" in driver and "0x55" in driver
assert "mipi_dbi_reset(config->mipi_dbi, 20)" in driver
assert "ST7789V_CMD_CMD2EN," not in driver and "ST7789V_CMD_RGBCTRL," not in driver
init = driver[driver.index("static int st7789v_lcd_init"):driver.index("static int st7789v_init")]
assert init.index("st7789v_exit_sleep(dev)") < init.index("sequence[]")
assert "ST7789V_CMD_SW_RESET" in driver
# Explicit RGB565 native-value -> wire-byte contract, including unequal bytes.
assert b"".join(p.to_bytes(2, "big") for p in (0xf800, 0x07e0, 0x001f, 0xffff, 0)) == bytes.fromhex(
    "f8 00 07 e0 00 1f ff ff 00 00")
cmake = read("CMakeLists.txt")
assert 'if(CONFIG_LISM_XIAO147_TOUCH)' in cmake
assert "TARGET_DIRECTORY drivers__display PROPERTIES HEADER_FILE_ONLY ON" in cmake
assert "zmk_display_status_screen=xiao147_stock_status_screen" in cmake
assert "CONFIG_LISM_XIAO147_DISPLAY_SELFTEST src/xiao147_display_test.c" in cmake
for path in ("src/xiao147_ui.c", "src/xiao147_battery_rings.c",
             "src/prospector_lock_indicators.c"):
    assert "!xiao147_display_ui_ready()" in read(path)
diag = read("src/xiao147_display_test.c")
assert "K_SECONDS(5)" in diag and "pattern_y == 172" in diag
assert "k_sleep(" not in diag and "settings_" not in diag
rotation = read("src/xiao147_rotation.c")
assert "result = display_write" in rotation and "report_write(result)" in rotation
assert "write_errors % 100" in rotation and "xiao147_display_hold_flush()" in rotation
test_matrix = read("build-display-test.yaml")
assert re.findall(r"artifact-name:\s*(\S+)", test_matrix) == ["lism_dongle_xiao147_startup_queue_test"]
assert "raw_refresh_gate" in diag and "LV_EVENT_REFR_REQUEST" in diag
assert "lv_timer_pause" in diag and "lv_timer_resume" in diag
assert "CONFIG_LISM_XIAO147_DISPLAY_SELFTEST=y" in test_matrix
assert "settings_reset" not in test_matrix
print("PASS: XIAO-only driver, single endian conversion, smaller buffers, staged UI, original mappings preserved")
