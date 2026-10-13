"""Execute the real diagnostic C callbacks with deterministic queue/LVGL mocks."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / "src/xiao147_display_test.c").read_text()
source = re.sub(r"^#include .*$", "", source, flags=re.M)
prelude = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>
#include <errno.h>
typedef int32_t atomic_t;
#define ATOMIC_INIT(x) (x)
static int32_t atomic_get(atomic_t *p) { return *p; }
static void atomic_set(atomic_t *p, int32_t v) { *p=v; }
static int32_t atomic_inc(atomic_t *p) { return (*p)++; }
static bool atomic_cas(atomic_t *p, int32_t old, int32_t n) {
    if (*p != old) return false;
    *p=n; return true;
}
#define LOG_MODULE_REGISTER(...)
static void fake_log(const char *fmt, ...) { (void)fmt; }
#define LOG_INF(...) fake_log(__VA_ARGS__)
#define LOG_ERR(...) fake_log(__VA_ARGS__)
#define K_MSEC(x) (x)
#define K_SECONDS(x) ((x)*1000)
struct k_work { int dummy; };
struct k_work_q { int dummy; };
struct k_work_delayable {
    struct k_work work;
    void (*handler)(struct k_work *);
    struct k_work_q *queue;
    bool pending;
    uint32_t deadline;
};
struct device { int dummy; };
struct display_buffer_descriptor {
    uint16_t width, height, pitch;
    size_t buf_size;
    bool frame_incomplete;
};
struct sys_memory_stats { size_t free_bytes, allocated_bytes, max_allocated_bytes; };
typedef struct { int id; bool deleted; } lv_obj_t;
typedef struct { bool paused; } lv_timer_t;
typedef struct { int dummy; } lv_display_t;
typedef struct { lv_display_t *target; } lv_event_t;
static lv_display_t mock_display;
static lv_timer_t refresh_timer;
static void (*refresh_gate)(lv_event_t *);
#define LV_EVENT_REFR_REQUEST 1
static lv_display_t *lv_display_get_default(void) { return &mock_display; }
static lv_timer_t *lv_display_get_refr_timer(lv_display_t *d) {
    assert(d==&mock_display); return &refresh_timer;
}
static lv_display_t *lv_event_get_target(lv_event_t *e) { return e->target; }
static void lv_timer_pause(lv_timer_t *t) { t->paused=true; }
static void lv_timer_resume(lv_timer_t *t) { t->paused=false; }
static void lv_display_add_event_cb(lv_display_t *d,void (*cb)(lv_event_t *),int event,void *data) {
    (void)data; assert(d==&mock_display && event==LV_EVENT_REFR_REQUEST); refresh_gate=cb;
}
static void request_refresh(void) {
    /* Actual f1db87ee dispatches callbacks in registration order:
     * the built-in callback resumes, then our later gate may pause again.
     */
    lv_timer_resume(&refresh_timer);
    if(refresh_gate) { lv_event_t e={.target=&mock_display}; refresh_gate(&e); }
}
static struct device mock_lcd;
static struct k_work_q display_queue;
static lv_obj_t objects[8], *active_screen;
static uint32_t clock_ms;
static unsigned object_count, raw_rows, markers[4], invalidations, refr_calls, stock_calls;
static int write_failure_row=-1;
static int last_render_hold=-1;
static int lv_font_montserrat_14;
#define DT_CHOSEN(x) 0
#define DEVICE_DT_GET(x) (&mock_lcd)
#define LV_PART_MAIN 0
#define LV_OPA_COVER 255
static uint32_t k_uptime_get_32(void) { return clock_ms; }
static struct k_work_q *zmk_display_work_q(void) { return &display_queue; }
static void k_work_init_delayable(struct k_work_delayable *w, void (*fn)(struct k_work *)) {
    w->handler=fn; w->pending=false;
}
static int k_work_reschedule_for_queue(struct k_work_q *q, struct k_work_delayable *w, int delay) {
    w->queue=q; w->deadline=clock_ms+delay; w->pending=true; return 1;
}
static int k_work_schedule_for_queue(struct k_work_q *q, struct k_work_delayable *w, int delay) {
    return k_work_reschedule_for_queue(q,w,delay);
}
static int k_work_reschedule(struct k_work_delayable *w,int delay) {
    return k_work_reschedule_for_queue(NULL,w,delay);
}
static int k_work_schedule(struct k_work_delayable *w,int delay) {
    return k_work_reschedule(w,delay);
}
static int display_write(const struct device *d, uint16_t x, uint16_t y,
                         const struct display_buffer_descriptor *desc, const void *buf) {
    assert(d==&mock_lcd && x==0 && buf);
    const uint16_t *p=buf;
    if (desc->width==320) {
        assert(desc->height==4 && desc->pitch==320 && desc->buf_size==2560);
        assert(y==raw_rows*4 && y+4<=172);
        assert(desc->frame_incomplete==(y+4<172));
        static const uint16_t colors[]={0xf800,0x07e0,0x001f,0xffff,0};
        for (unsigned row=0;row<4;row++) for(unsigned col=0;col<320;col++) {
            bool corner=(col<6 || col>=314) && (y+row<6 || y+row>=166);
            assert(p[row*320+col]==(corner?0xffff:colors[col/64]));
        }
        int n=raw_rows++;
        return n==write_failure_row ? -EIO : 0;
    }
    assert(y==0 && desc->width==20 && desc->height==20 && desc->pitch==20);
    assert(desc->buf_size==800 && !desc->frame_incomplete);
    /* 2 differs from 3 at the fifth glyph row: x=9,y=11 is white only for 2. */
    unsigned number=p[11*20+9] ? 2 : 3;
    markers[number]++;
    return 0;
}
static lv_obj_t *lv_obj_create(lv_obj_t *parent) {
    (void)parent; assert(object_count<8);
    lv_obj_t *o=&objects[object_count++]; o->deleted=false; return o;
}
static lv_obj_t *lv_label_create(lv_obj_t *p) { assert(p); return lv_obj_create(p); }
static unsigned lv_color_hex(unsigned x) { return x; }
static void lv_obj_set_style_bg_color(lv_obj_t *o,unsigned c,int p) {(void)o;(void)c;(void)p;}
static void lv_obj_set_style_bg_opa(lv_obj_t *o,int a,int p) {(void)o;(void)a;(void)p;}
static void lv_obj_set_style_text_color(lv_obj_t *o,unsigned c,int p) {(void)o;(void)c;(void)p;}
static void lv_obj_set_style_text_font(lv_obj_t *o,const int *f,int p) {(void)o;(void)f;(void)p;}
static void lv_label_set_text(lv_obj_t *o,const char *t) {(void)o;(void)t;}
static void lv_obj_center(lv_obj_t *o) {(void)o;}
static void lv_obj_invalidate(lv_obj_t *o) { assert(o && !o->deleted); invalidations++; request_refresh(); }
static void lv_screen_load(lv_obj_t *o) { assert(o && !o->deleted); active_screen=o; request_refresh(); }
static void lv_obj_delete(lv_obj_t *o) { assert(o && o!=active_screen); o->deleted=true; }
static void xiao147_rotation_install(void) {}
bool xiao147_display_hold_flush(void);
static void lv_refr_now(void *d) {
    (void)d; refr_calls++; last_render_hold=xiao147_display_hold_flush();
    assert(!last_render_hold);
    assert(!refresh_timer.paused);
    /* In the blocked case do not execute any subsequent display work.
     * The independent system heartbeat is still executable.
     */
}
void lvgl_heap_stats(struct sys_memory_stats *s) {
    s->free_bytes=50000; s->allocated_bytes=15000; s->max_allocated_bytes=15500;
}
lv_obj_t *xiao147_stock_status_screen(void) {
    stock_calls++; return lv_obj_create(NULL);
}
"""
test = r"""
static void run(struct k_work_delayable *w) {
    assert(w->pending);
    if(clock_ms<w->deadline) clock_ms=w->deadline;
    w->pending=false;
    w->handler(&w->work);
}
static void reset(void) {
    stage=progress=progress_time=flush_entered=flush_exited=last_flush_result=transmitted=0;
    pattern_y=failed_writes=clock_ms=object_count=raw_rows=invalidations=refr_calls=stock_calls=0;
    for(unsigned i=0;i<4;i++) markers[i]=0;
    active_screen=NULL;
    test_screen=NULL;
    refresh_timer.paused=false; refresh_gate=NULL;
}
static void pipeline(bool fail_one_write) {
    reset(); write_failure_row=fail_one_write ? 5 : -1;
    lv_obj_t *s=zmk_display_status_screen(); assert(s);
    lv_screen_load(s);
    assert(refresh_timer.paused);
    /* Screen loads and later invalidations must not re-enable raw rendering. */
    for(unsigned i=0;i<10;i++) { lv_obj_invalidate(s); assert(refresh_timer.paused); }
    assert(!xiao147_display_ui_ready() && xiao147_display_hold_flush());
    assert(phase_work.queue==&display_queue && heartbeat_work.queue==NULL);
    for(unsigned i=0;i<43;i++) run(&phase_work);
    assert(raw_rows==43 && pattern_y==172 && stage==0);
    assert(failed_writes==(unsigned)fail_one_write);
    uint32_t due=phase_work.deadline; assert(due-clock_ms==5000);
    run(&phase_work);
    assert(stage==1 && !xiao147_display_hold_flush() && !xiao147_display_ui_ready());
    assert(!refresh_timer.paused);
    assert(markers[2]==1 && refr_calls==1 && !last_render_hold);
    assert(phase_work.deadline-clock_ms==5000 && !stock_calls);
    run(&phase_work);
    assert(stage==2 && xiao147_display_ui_ready() && stock_calls==1);
    assert(markers[3]==1 && s->deleted && active_screen!=s);
    run(&phase_work); assert(stock_calls==1 && !phase_work.pending && progress==41);
    xiao147_display_note_flush(true,0); xiao147_display_note_flush(false,1);
    xiao147_display_note_flush(true,0); xiao147_display_note_flush(false,-EIO);
    assert(flush_entered==2 && flush_exited==2 && last_flush_result==-EIO);
}
int main(void) {
    pipeline(false); pipeline(true);
    reset(); write_failure_row=-1; active_screen=zmk_display_status_screen();
    for(unsigned i=0;i<43;i++) run(&phase_work);
    run(&phase_work);
    uint32_t phase_due=phase_work.deadline;
    /* Model a hung display worker: deliberately do not dispatch phase_work.
     * System heartbeat must never perform a display write or call LVGL/heap.
     */
    unsigned calls=refr_calls, rows=raw_rows, objects_before=object_count;
    for(unsigned i=0;i<5;i++) run(&heartbeat_work);
    assert(phase_work.pending && phase_work.deadline==phase_due);
    assert(!stock_calls && stage==1 && refr_calls==calls && raw_rows==rows);
    assert(object_count==objects_before && heartbeat_work.queue==NULL);
    puts("PASS: actual C phase scheduling/gates, raw colors/corners/bounds, write failure, markers, synchronous handoff, independent heartbeat");
}
"""
with tempfile.TemporaryDirectory(prefix="xiao147-phase-test-") as directory:
    folder = Path(directory)
    c = folder / "test.c"
    c.write_text(prelude + source + test)
    binary = folder / "test"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", "-fsanitize=address,undefined",
                    str(c), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
