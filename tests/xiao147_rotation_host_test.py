"""Exercise actual flush/rotation C with padded buffers and physical LCD RAM."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = re.sub(r"^#include .*$", "", (root / "src/xiao147_rotation.c").read_text(), flags=re.M)
prelude = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef int atomic_t;
#define ATOMIC_INIT(x) (x)
static int atomic_get(atomic_t *x) { return *x; }
static void atomic_set(atomic_t *x,int y) { *x=y; }
#define LOG_MODULE_REGISTER(...)
#define LOG_INF(...) ((void)0)
#define LOG_ERR(...) ((void)0)
#define DEVICE_DT_GET(x) (&dev)
#define DT_CHOSEN(x) 0
#define LV_COLOR_FORMAT_RGB565 16
#define LV_DISPLAY_ROTATION_0 0
#define LV_DISPLAY_ROTATION_90 1
struct device { int unused; };
static struct device dev;
struct display_buffer_descriptor { uint16_t width,height,pitch; size_t buf_size; bool frame_incomplete; };
typedef struct { uint32_t stride; } header_t;
typedef struct { header_t header; size_t data_size; } lv_draw_buf_t;
typedef struct { int x1,y1,x2,y2; } lv_area_t;
typedef struct { int unused; } lv_display_t;
static lv_display_t disp;
static lv_draw_buf_t draw_buf;
static int format=16,rotation,ready_calls,flush_notes,last_result,writes,invalidations;
static bool hold,last=true;
static uint16_t panel[172][320];
static bool device_is_ready(const struct device *d) { assert(d==&dev);return true; }
static lv_display_t *lv_display_get_default(void) { return &disp; }
static lv_draw_buf_t *lv_display_get_buf_active(lv_display_t *d) { assert(d==&disp);return &draw_buf; }
static int lv_display_get_color_format(lv_display_t *d) { assert(d==&disp);return format; }
static void lv_display_flush_ready(lv_display_t *d) { assert(d==&disp);ready_calls++; }
static bool lv_display_flush_is_last(lv_display_t *d) { assert(d==&disp);return last; }
static void lv_display_set_flush_cb(lv_display_t *d,void (*cb)(lv_display_t *,const lv_area_t *,uint8_t *)) { assert(d==&disp && cb); }
static void lv_display_set_rotation(lv_display_t *d,int r) { assert(d==&disp);rotation=r; }
static void *lv_scr_act(void) { return &disp; }
static void lv_obj_invalidate(void *o) { assert(o==&disp);invalidations++; }
static bool xiao147_display_hold_flush(void) { return hold; }
static void xiao147_display_note_flush(bool entering,int result) { flush_notes++;if(!entering)last_result=result; }
static int display_write(const struct device *d,uint16_t x,uint16_t y,
                         const struct display_buffer_descriptor *b,const void *data) {
    assert(d==&dev && x+b->width<=320 && y+b->height<=172);
    assert(b->pitch>=b->width && b->buf_size >= (size_t)b->pitch*b->height*2);
    const uint16_t *p=data;
    for(unsigned row=0;row<b->height;row++) for(unsigned col=0;col<b->width;col++)
        panel[y+row][x+col]=p[row*b->pitch+col];
    writes++;return 0;
}
"""
test = r"""
int main(void) {
    xiao147_rotation_install();
    uint16_t pixels[]={0xf800,0x07e0,0x001f,0xdead,0xffff,0x1234,0x5678,0xbeef};
    draw_buf=(lv_draw_buf_t){.header={.stride=8},.data_size=sizeof(pixels)};
    lv_area_t area={2,3,4,4};
    flush_cb(&disp,&area,(uint8_t *)pixels);
    assert(last_result==0 && ready_calls==1 && panel[3][2]==0xf800 && panel[4][2]==0xffff);
    assert(panel[4][4]==0x5678); /* padding poison must never become a pixel */
    memset(panel,0x55,sizeof(panel));
    xiao147_rotation_set(true);
    assert(rotation==1 && xiao147_rotation_is_portrait() && invalidations==1);
    for(int y=0;y<172;y++)for(int x=0;x<320;x++)assert(panel[y][x]==0);
    flush_cb(&disp,&area,(uint8_t *)pixels);
    for(int y=0;y<2;y++)for(int x=0;x<3;x++)assert(panel[2+x][319-(3+y)]==pixels[y*4+x]);
    uint16_t large[172*40];
    for(int y=0;y<40;y++)for(int x=0;x<172;x++)large[y*172+x]=x==171?0xdead:(uint16_t)(1+y*171+x);
    draw_buf=(lv_draw_buf_t){.header={.stride=344},.data_size=sizeof(large)};
    lv_area_t wide={0,0,170,39};
    flush_cb(&disp,&wide,(uint8_t *)large);
    for(int y=0;y<40;y++)for(int x=0;x<171;x++)assert(panel[x][319-y]==large[y*172+x]);
    draw_buf=(lv_draw_buf_t){.header={.stride=8},.data_size=sizeof(pixels)};
    memset(panel,0x55,sizeof(panel));
    xiao147_rotation_set(false);
    assert(rotation==0 && !xiao147_rotation_is_portrait() && invalidations==2);
    for(int y=0;y<172;y++)for(int x=0;x<320;x++)assert(panel[y][x]==0);
    int before=writes;
    hold=true;flush_cb(&disp,&area,(uint8_t *)pixels);assert(writes==before && last_result==1);
    hold=false;draw_buf.header.stride=5;
    flush_cb(&disp,&area,(uint8_t *)pixels);assert(writes==before && last_result==-EINVAL);
    draw_buf.header.stride=8;draw_buf.data_size=8;
    flush_cb(&disp,&area,(uint8_t *)pixels);assert(writes==before && last_result==-EINVAL);
    draw_buf.data_size=sizeof(pixels);format=24;
    flush_cb(&disp,&area,(uint8_t *)pixels);assert(writes==before && last_result==-EINVAL);
    assert(ready_calls==7 && flush_notes==14);
    puts("PASS: actual RGB565 stride, odd-width padded partials, clockwise rotation, physical clearing, bad-buffer guards and flush completion");
}
"""
with tempfile.TemporaryDirectory(prefix="xiao147-rotation-") as directory:
    folder = Path(directory)
    c = folder / "test.c"
    c.write_text(prelude + source + test)
    binary = folder / "test"
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", str(c), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
