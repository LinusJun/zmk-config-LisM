"""Execute actual UI touch handler/producer with bounded queue/time mocks."""
from pathlib import Path
import re
import subprocess
import tempfile
root=Path(__file__).resolve().parents[1]
ui=(root/"src/xiao147_ui.c").read_text()
def function(signature):
    start=ui.index(signature)
    end=ui.index("\n}\n",start)+3
    return ui[start:end]
source=function("static void touch_handler(struct k_work *work)\n{")
source+=function("void xiao147_ui_touch(uint8_t fingers, uint16_t x, uint16_t y,")
prelude=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include "xiao147_orientation.h"
#define TOUCH_BATCH_LIMIT 8
#define TOUCH_FRAME_MS 33
#define SCREEN_W 320
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
#define XIAO147_TOUCH_CANCEL UINT8_MAX
enum rotation_mode {ROTATION_AUTO,ROTATION_LANDSCAPE,ROTATION_PORTRAIT};
enum xiao147_page {XIAO147_PAGE_STATUS,XIAO147_PAGE_SETTINGS,XIAO147_PAGE_DEVICES};
struct k_work {int unused;};
struct k_work_delayable {struct k_work work;bool pending;unsigned deadline;};
struct touch_sample {uint8_t fingers;uint16_t x,y;enum xiao147_orientation orientation;};
struct queue {struct touch_sample samples[32];int head,count;};
static struct queue touch_queue;
static struct k_work_delayable touch_work;
static struct touch_sample last_touch;
static uint8_t previous_touch_fingers,brightness=100;
static bool slider_drag,usb_powered=true,imu;
static enum rotation_mode rotation=ROTATION_LANDSCAPE;
static enum xiao147_orientation pose;
static int page_atomic=XIAO147_PAGE_SETTINGS,auto_rotate_mode;
static unsigned now,updates,pwm_calls,redraws,cancels;
static int atomic_get(int *p) {return *p;}
static void atomic_set(int *p,int v) {*p=v;}
static enum xiao147_orientation xiao147_rotation_get(void) {return pose;}
static bool xiao147_rotation_is_portrait(void) {return pose&1;}
static bool xiao147_ui_imu_enabled(void) {return imu;}
static void xiao147_ui_toggle_imu(void) {imu=!imu;}
static void xiao147_display_note_ui(int point) {if(point==101)cancels++;}
static bool zmk_display_is_initialized(void) {return true;}
static void *zmk_display_work_q(void) {return &touch_work;}
static int k_work_schedule_for_queue(void*q,struct k_work_delayable*w,int delay) {
    assert(q==&touch_work&&w==&touch_work&&delay==33);
    if(!w->pending){w->pending=true;w->deadline=now+delay;}return 0;
}
static int k_msgq_put(struct queue*q,const void*s,int wait) {
    (void)wait;if(q->count==32)return -1;q->samples[(q->head+q->count++)%32]=*(const struct touch_sample*)s;return 0;
}
static int k_msgq_get(struct queue*q,void*s,int wait) {
    (void)wait;if(!q->count)return -1;*(struct touch_sample*)s=q->samples[q->head];q->head=(q->head+1)%32;q->count--;return 0;
}
static int k_msgq_num_used_get(struct queue*q) {return q->count;}
static void apply_brightness(void) {pwm_calls++;}
static void update_brightness_widgets(void) {updates++;}
static void redraw(struct k_work*w) {(void)w;redraws++;}
'''
test=r'''
static void frame(void) {assert(touch_work.pending);now=touch_work.deadline;touch_work.pending=false;touch_handler(&touch_work.work);}
static void send(uint8_t f,uint16_t x,uint16_t y) {xiao147_ui_touch(f,x,y,pose);}
int main(void) {
    send(1,30,52);unsigned deadline=touch_work.deadline;
    now=16;send(1,50,52);assert(touch_work.deadline==deadline); /* no deadline starvation */
    for(int i=2;i<12;i++)send(1,14+i*20,52);
    send(0,0,0);frame();assert(touch_queue.count==5&&pwm_calls==1&&updates==1&&redraws==0);
    frame();assert(touch_queue.count==0&&pwm_calls==2&&updates==2&&redraws==0);
    assert(brightness==220*100/287&&!slider_drag&&previous_touch_fingers==0);
    /* A moving producer cannot keep a worker invocation alive forever. */
    unsigned frames=0;touch_queue=(struct queue){0};touch_work.pending=false;
    for(int i=0;i<180;i++) {
        now+=16;send(1,14+(i%100)*2,52);
        if(touch_work.pending&&now>=touch_work.deadline){frame();frames++;}
        assert(touch_queue.count<8);
    }
    send(0,0,0);while(touch_work.pending)frame();assert(frames>30&&!slider_drag);
    /* Crossing into Auto rotate during a slider drag must not toggle Auto. */
    send(1,120,52);send(1,150,80);send(0,0,0);frame();assert(rotation==ROTATION_LANDSCAPE&&redraws==0);
    /* Error cancellation is not a release/click; orientation cancels too. */
    send(1,80,80);send(XIAO147_TOUCH_CANCEL,0,0);send(0,0,0);frame();assert(redraws==0&&!slider_drag);
    send(1,80,52);frame();assert(slider_drag);
    xiao147_ui_touch(1,90,52,XIAO147_USB_RIGHT);frame();assert(!slider_drag&&previous_touch_fingers==0);
    /* Accidental second finger followed by lift must end, not latch, drag. */
    send(1,90,52);send(2,90,80);send(0,0,0);frame();
    assert(!slider_drag&&previous_touch_fingers==0&&redraws==0);
    /* Real release still activates one row exactly once. */
    send(1,80,80);send(0,0,0);frame();assert(rotation==ROTATION_AUTO&&redraws==1);
    send(0,0,0);frame();assert(redraws==1);
    /* Portrait brightness uses logical x and clamps both ends. */
    pose=XIAO147_BUTTONS_BOTTOM;send(1,200,77);send(0,0,0);frame();assert(brightness==100);
    send(1,0,77);send(0,0,0);frame();assert(brightness==1);
    /* The producer cancels a drag using its OLD orientation after rotation.
     * CANCEL must be honored before the handler's stale-orientation check. */
    send(1,80,77);frame();assert(slider_drag);
    unsigned prior_redraws=redraws;
    pose=XIAO147_USB_LEFT;
    xiao147_ui_touch(XIAO147_TOUCH_CANCEL,0,0,XIAO147_BUTTONS_BOTTOM);
    frame();assert(!slider_drag&&!previous_touch_fingers&&redraws==prior_redraws);
    send(1,301,52);send(0,0,0);frame();
    assert(brightness==100&&!slider_drag&&!previous_touch_fingers&&redraws==prior_redraws);
    assert(cancels>30);
    puts("PASS: actual slider handler: bounded batches, latest value, release/cancel, 33ms non-postponed schedule, no full redraw during drag");
}
'''
with tempfile.TemporaryDirectory(prefix="xiao147-slider-") as d:
    c=Path(d)/"test.c";binary=Path(d)/"test";c.write_text(prelude+source+test)
    subprocess.run(["cc","-std=gnu11","-Wall","-Wextra","-Werror","-Wno-unused-parameter",
                    "-fsanitize=address,undefined","-I",str(root/"src"),str(c),"-o",str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
