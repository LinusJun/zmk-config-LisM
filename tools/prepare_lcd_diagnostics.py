"""Instrument only the copied Prospector169 ST7789 driver; preserve all returns."""
from pathlib import Path
from prepare_upstream_esb import replace


def prepare(driver_path):
    p = Path(driver_path)
    text = p.read_text()
    text = replace(text, '#include "display_st7789v.h"',
                   '#include "display_st7789v.h"\n#include <lism_lcd_diag.h>')
    old = '''\treturn mipi_dbi_command_write(config->mipi_dbi, &config->dbi_config,
\t\t\t\t      cmd, tx_data, tx_count);'''
    new = '''\tint rc = mipi_dbi_command_write(config->mipi_dbi, &config->dbi_config,
\t\t\t\t      cmd, tx_data, tx_count);
\tlism_lcd_trace_command(cmd, tx_data, tx_count, rc);
\treturn rc;'''
    text = replace(text, old, new)
    text = replace(text, 'static int st7789v_write(const struct device *dev,',
                   'static int lism_st7789v_write_impl(const struct device *dev,')
    wrapper = '''static int st7789v_write(const struct device *dev, uint16_t x, uint16_t y,
                          const struct display_buffer_descriptor *desc, const void *buf)
{
    int rc = lism_st7789v_write_impl(dev, x, y, desc, buf);
    lism_lcd_trace_write(x, y, desc, buf, rc);
    return rc;
}

'''
    text = replace(text, 'static void st7789v_get_capabilities(',
                   wrapper + 'static void st7789v_get_capabilities(')
    p.write_text(text)
    (p.parent / 'lism_lcd_diag.c').write_text('#include <lism_lcd_diag.inc>\n')
    cmake = p.parent / 'CMakeLists.txt'
    cmake.write_text(cmake.read_text() + '\nzephyr_library_sources(lism_lcd_diag.c)\n')
