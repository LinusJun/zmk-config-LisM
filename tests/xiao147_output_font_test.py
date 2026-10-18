"""Check numbered-slot fit using the real, pinned OPERATOR font descriptors.

Pass --font when the Prospector module is checked out elsewhere. No substitute
font or assumed average digit width is used by this test.
"""
import argparse
import os
from pathlib import Path
import re

module = Path(os.environ.get("LISM_PROSPECTOR_MODULE",
                             "/private/tmp/lism-trackpad-build/prospector-zmk-module"))
parser = argparse.ArgumentParser()
parser.add_argument("--font", type=Path, default=module / (
    "boards/shields/prospector_adapter/src/fonts/FG_Medium_20.c"))
args = parser.parse_args()
font = args.font.read_text()
assert "const lv_font_t FG_Medium_20" in font
assert re.search(r"\.range_start\s*=\s*32,\s*\.range_length\s*=\s*65,\s*\.glyph_id_start\s*=\s*1", font)
descriptor = font.split("glyph_dsc[] = {", 1)[1].split("};", 1)[0]
glyphs = [dict((key, int(value)) for key, value in re.findall(
    r"\.(\w+)\s*=\s*(-?\d+)", row)) for row in re.findall(r"\{([^}]+)\}", descriptor)]
line_height = int(re.search(r"\.line_height\s*=\s*(\d+)", font)[1])
base_line = int(re.search(r"\.base_line\s*=\s*(\d+)", font)[1])
for count in (5, 6):
    for row_width in (116, 156):
        for index in range(count):
            start = index * (row_width + 2) // count
            end = (index + 1) * (row_width + 2) // count - 2
            width = end - start
            glyph = glyphs[ord(str(index + 1)) - 32 + 1]
            advance = (glyph["adv_w"] + 8) >> 4  # LVGL's 1/16 px descriptor.
            assert advance == 12 and glyph["box_w"] <= 10
            assert width >= 17 and width >= advance + 4
            # Centering uses advance width; check actual ink including offsets.
            ink_left = (width - advance) // 2 + glyph["ofs_x"]
            ink_right = ink_left + glyph["box_w"]
            assert 2 <= ink_left < ink_right <= width - 2
            # A centered 25 px line plus the original +1 px translation stays
            # inside the 29 px slot, including the actual baseline/ink box.
            line_top = (29 - line_height) // 2 + 1
            ink_top = line_top + line_height - base_line - glyph["box_h"] - glyph["ofs_y"]
            assert 0 <= line_top and line_top + line_height <= 29
            assert 0 <= ink_top and ink_top + glyph["box_h"] <= 29
print("PASS: actual FG_Medium_20 digits 1-6 fit five/six-slot 116/156 px rows with 2 px ink clearance")
