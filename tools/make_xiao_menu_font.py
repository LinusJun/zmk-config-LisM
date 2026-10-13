"""Generate uncompressed LVGL 4bpp fonts; print an apply_patch, never edit inputs.

Input: LiberationSans-Bold.ttf from pdfjs-dist/standard_fonts (SIL OFL 1.1).
Renamed derivative XiaoMenu_Bold; ASCII 32..126 includes all menu characters.
Requires Pillow; usage: python3 tools/make_xiao_menu_font.py FONT LICENSE
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def font_source(path, size):
    font = ImageFont.truetype(path, size)
    ascent, descent = font.getmetrics()
    bitmap, descriptors = [], ["    {0}"]
    for code in range(32, 127):
        char = chr(code)
        left, top, right, bottom = font.getbbox(char, anchor="ls")
        width, height = right-left, bottom-top
        image = Image.new("L", (max(1,width), max(1,height)))
        ImageDraw.Draw(image).text((-left,-top), char, font=font, fill=255, anchor="ls")
        levels = [min(15,(value+8)//17) for value in image.getdata()] if height else []
        if len(levels) & 1:
            levels.append(0)
        descriptors.append(f"    {{.bitmap_index={len(bitmap)}, .adv_w={round(font.getlength(char)*16)}, "
                           f".box_w={width}, .box_h={height}, .ofs_x={left}, .ofs_y={-bottom}}}")
        bitmap.extend((levels[i]<<4)|levels[i+1] for i in range(0,len(levels),2))
    name = f"XiaoMenu_Bold_{size}"
    pixels = "\n".join("    "+", ".join(f"0x{x:02x}" for x in bitmap[i:i+16])+"," for i in range(0,len(bitmap),16))
    glyphs = ",\n".join(descriptors)
    return f"""
static const uint8_t bitmap_{size}[] = {{
{pixels}
}};
static const lv_font_fmt_txt_glyph_dsc_t glyphs_{size}[] = {{
{glyphs}
}};
static const lv_font_fmt_txt_cmap_t cmaps_{size}[] = {{
    {{.range_start=32, .range_length=95, .glyph_id_start=1,
      .type=LV_FONT_FMT_TXT_CMAP_FORMAT0_TINY}}
}};
static const lv_font_fmt_txt_dsc_t dsc_{size} = {{
    .glyph_bitmap=bitmap_{size}, .glyph_dsc=glyphs_{size}, .cmaps=cmaps_{size},
    .cmap_num=1, .bpp=4, .bitmap_format=0
}};
const lv_font_t {name} = {{
    .get_glyph_dsc=lv_font_get_glyph_dsc_fmt_txt,
    .get_glyph_bitmap=lv_font_get_bitmap_fmt_txt,
    .line_height={ascent+descent}, .base_line={descent}, .dsc=&dsc_{size},
    .underline_position=-2, .underline_thickness=1
}};
"""

source = ('/* XiaoMenu_Bold: generated from Liberation Sans Bold; SIL OFL 1.1.\n'
          ' * Digitized data copyright (c) 2010 Google Corporation.\n'
          ' * Copyright (c) 2012 Red Hat, Inc. See XIAO_MENU_FONT_LICENSE.txt.\n'
          ' * Regenerate with tools/make_xiao_menu_font.py. */\n#include <lvgl.h>\n')
source += font_source(sys.argv[1],16)+font_source(sys.argv[1],12)
print('*** Begin Patch')
for name, text in [('src/fonts/xiao_menu_bold.c',source),
                   ('XIAO_MENU_FONT_LICENSE.txt',Path(sys.argv[2]).read_text())]:
    print('*** Add File: '+name)
    print('\n'.join('+'+line.rstrip() for line in text.rstrip().splitlines()))
print('*** End Patch')
