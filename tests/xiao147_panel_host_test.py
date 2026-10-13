"""Actual panel serialization: skip padded pitch, cross rows, bounded 512B SPI."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / "src/display_jd9853a.c").read_text()
source = source[source.index("static uint8_t wire_pixels[512];"):
                source.index("static void st7789v_get_capabilities")]
prelude = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <errno.h>
#include <stdio.h>
#define MIN(a,b) ((a)<(b)?(a):(b))
#define ST7789V_CMD_RAMWR 0x2c
#define PIXEL_FORMAT_RGB_565 1
struct mipi_dbi_config {int unused;};
struct device {const void *config;};
struct st7789v_config {const struct device *mipi_dbi; struct mipi_dbi_config dbi_config;uint16_t width,height;};
struct display_buffer_descriptor {uint16_t width,height,pitch;size_t buf_size;bool frame_incomplete;};
static unsigned calls,area_calls,cmd_calls,received;
static int fail_at,area_error,command_error;
static bool final_incomplete;
static uint16_t expected[320*172];
static int st7789v_set_mem_area(const struct device *d,int x,int y,int w,int h) {
    (void)d;(void)x;(void)y;assert(w>0&&h>0);area_calls++;return area_error;
}
static int st7789v_transmit(const struct device*d,int c,const void*p,size_t n) {
    (void)d;assert(c==ST7789V_CMD_RAMWR&&!p&&!n);cmd_calls++;return command_error;
}
static void sys_put_be16(uint16_t v,uint8_t *p) {p[0]=v>>8;p[1]=v;}
static int mipi_dbi_write_display(const struct device*d,const struct mipi_dbi_config*c,
    const uint8_t *buf,const struct display_buffer_descriptor *desc,int format) {
    (void)d;(void)c;assert(format==PIXEL_FORMAT_RGB_565);
    calls++;assert(desc->buf_size<=512&&desc->height==1&&desc->pitch==desc->width);
    if((int)calls==fail_at)return -EIO;
    for(unsigned i=0;i<desc->buf_size/2;i++) {
        assert(((unsigned)buf[i*2]<<8|buf[i*2+1])==expected[received++]);
    }
    final_incomplete=desc->frame_incomplete;return 0;
}
'''
test = r'''
int main(void) {
    struct st7789v_config cfg={.width=320,.height=172};struct device dev={&cfg};
    uint16_t pixels[320*172];
    for(int w=1;w<=320;w+=7) for(int h=1;h<=172;h+=17) {
        int pitch=w==320?w:w+1,total=w*h;
        for(int y=0;y<h;y++)for(int x=0;x<pitch;x++) {
            pixels[y*pitch+x]=(uint16_t)(y*319+x+0x0701);
            if(x<w)expected[y*w+x]=pixels[y*pitch+x];
        }
        struct display_buffer_descriptor desc={w,h,pitch,(size_t)pitch*h*2,false};
        calls=received=area_calls=cmd_calls=0;fail_at=0;
        assert(st7789v_write(&dev,0,0,&desc,pixels)==0);
        assert(received==(unsigned)total&&calls==(unsigned)(total+255)/256);
        assert(area_calls==1&&cmd_calls==1&&!final_incomplete);
        desc.frame_incomplete=true;calls=received=0;
        assert(st7789v_write(&dev,0,0,&desc,pixels)==0&&final_incomplete);
    }
    struct display_buffer_descriptor desc={3,172,4,4*172*2,false};
    for(int y=0;y<172;y++)for(int x=0;x<4;x++) {
        pixels[y*4+x]=y*4+x;if(x<3)expected[y*3+x]=pixels[y*4+x];
    }
    calls=received=0;fail_at=2;assert(st7789v_write(&dev,0,0,&desc,pixels)==-EIO&&calls==2);
    desc.buf_size--;area_calls=0;assert(st7789v_write(&dev,0,0,&desc,pixels)==-EINVAL&&!area_calls);
    desc.buf_size++;assert(st7789v_write(&dev,319,0,&desc,pixels)==-EINVAL);
    area_error=-EIO;assert(st7789v_write(&dev,0,0,&desc,pixels)==-EIO);
    area_error=0;command_error=-EIO;calls=0;
    assert(st7789v_write(&dev,0,0,&desc,pixels)==-EIO&&!calls);
    puts("PASS: actual bounded RGB565 driver packer, padded odd widths, cross-row/packet, frame flags and write failures");
}
'''
with tempfile.TemporaryDirectory(prefix="xiao147-panel-") as directory:
    c = Path(directory) / "test.c"
    c.write_text(prelude + source + test)
    binary = Path(directory) / "test"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", str(c), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
