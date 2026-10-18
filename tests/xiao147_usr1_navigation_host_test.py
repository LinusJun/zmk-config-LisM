"""Execute the real UI USR1 integration on fake display/input work queues."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
ui = (root / "src/xiao147_ui.c").read_text()
def function(signature):
    start = ui.index(signature + "\n{")
    return ui[start:ui.index("\n}\n", start) + 3]

begin = ui.index("struct usr1_edge {")
end = ui.index("\n#endif", ui.index("static void usr1_queue_edge(bool pressed)\n{", begin))
navigation = ui[begin:end]
touch_struct = ui[ui.index("struct touch_sample {"):ui.index("struct peripheral_state {")]
orientation = ui[ui.index("    enum xiao147_page page = atomic_get(&page_atomic);", ui.index("static void redraw(struct k_work *work)\n{")):
                 ui.index("    xiao147_rotation_set(orientation);", ui.index("static void redraw(struct k_work *work)\n{"))]
callbacks = "\n".join(function(signature) for signature in (
    "enum xiao147_page xiao147_ui_page(void)",
    "bool xiao147_ui_wake_on_touch(void)",
    "void xiao147_ui_toggle_imu(void)",
    "void xiao147_ui_next_page(void)",
    "void xiao147_ui_set_auto_orientation(enum xiao147_orientation orientation)",
    "void xiao147_ui_touch(uint8_t fingers, uint16_t x, uint16_t y,\n                       enum xiao147_orientation orientation)",
    "static void menu_button_input(struct input_event *evt, void *user_data)",
    "static void imu_button_input(struct input_event *evt, void *user_data)"))
assert "xiao147_inputs_focus_set_context_callback(usr1_invalidate_context);" in ui

prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include "xiao147_usr1_click.h"
#include "xiao147_orientation.h"
#include "xiao147_settings_touch.h"
static struct xiao147_settings_gesture settings_gesture={.armed=-1};
#define CONFIG_LISM_BLE_INPUT_BRIDGE 1
#define CONFIG_LISM_BLE_HID_BRIDGE 1
#define LOG_INF(...)
#define ARG_UNUSED(x) (void)(x)
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
#define TOUCH_FRAME_MS 16
#define XIAO147_TOUCH_CANCEL UINT8_MAX
#define INPUT_EV_KEY 1
#define INPUT_BTN_0 256
#define INPUT_BTN_1 257
typedef long atomic_t;
typedef long atomic_val_t;
#define ATOMIC_INIT(x) (x)
static long atomic_get(const atomic_t*p){return *p;}
static long atomic_set(atomic_t*p,long v){long old=*p;*p=v;return old;}
static long atomic_inc(atomic_t*p){return (*p)++;}
static bool atomic_cas(atomic_t*p,long old,long v){if(*p!=old)return false;*p=v;return true;}
enum xiao147_page {XIAO147_PAGE_STATUS,XIAO147_PAGE_SETTINGS,XIAO147_PAGE_DEVICES,XIAO147_PAGE_BLE_MOUSE,XIAO147_PAGE_COUNT};
static atomic_t page_atomic,usr1_down,imu_mode,auto_rotate_mode,restore_landscape,sleeping,auto_orientation;
static atomic_t menu_contact_down;
static uint32_t now,last_activity_ms;
static bool initialized=true,display_context,redraw_pending;
static enum xiao147_orientation rotation;
struct input_event {int type,code,value;};
struct k_work {void(*handler)(struct k_work*);};
struct k_work_delayable {struct k_work work;bool pending;uint32_t due;};
static struct k_work redraw_work;
static struct k_work_delayable touch_work;
static int display_queue;
#define K_WORK_DELAYABLE_DEFINE(name,fn) static struct k_work_delayable name={.work={.handler=fn}}
static uint32_t k_uptime_get_32(void){return now;}
static bool zmk_display_is_initialized(void){return initialized;}
static void*zmk_display_work_q(void){return &display_queue;}
static void k_work_submit_to_queue(void*q,struct k_work*w){assert(q==&display_queue&&w==&redraw_work);redraw_pending=true;}
static int k_work_reschedule_for_queue(void*q,struct k_work_delayable*w,int delay){
 assert(q==&display_queue);w->pending=true;w->due=now+delay;return 0;
}
static int k_work_schedule_for_queue(void*q,struct k_work_delayable*w,int delay){
 assert(q==&display_queue);if(!w->pending){w->pending=true;w->due=now+delay;}return 0;
}
struct queue {size_t size;unsigned capacity,n,read;uint8_t rows[32][64];};
#define K_MSGQ_DEFINE(name,size_,count_,alignment) static struct queue name={.size=size_,.capacity=count_}
static int maybe_inject(void*q);
static int k_msgq_put(struct queue*q,const void*p,int wait){
 (void)wait;if(q->n-q->read==q->capacity)return -ENOMSG;
 memcpy(q->rows[q->n++%32],p,q->size);return 0;
}
static int k_msgq_get(struct queue*q,void*p,int wait){
 (void)wait;if(q->n==q->read){(void)maybe_inject(q);return -ENOMSG;}
 memcpy(p,q->rows[q->read++%32],q->size);return 0;
}
static void k_msgq_purge(struct queue*q){q->n=q->read=0;}
static enum xiao147_orientation xiao147_rotation_get(void){return rotation;}
static bool xiao147_rotation_is_portrait(void){return xiao147_orientation_portrait(rotation);}
static void xiao147_rotation_set(enum xiao147_orientation o){assert(display_context);rotation=o;}
static unsigned moves,activations,captures,cancels,focus,activated_focus,gui_calls;
static uint32_t focus_sequence,focus_serial;
static unsigned captured_focus;
static bool capture_allowed=true;
static bool semantic_pending,stable_countdown_pending,change_inside_next,change_inside_capture;
static uint32_t semantic_sequence;
static void(*semantic_callback)(void);
static void gui(void){assert(display_context);gui_calls++;}
static void xiao147_inputs_focus_set_context_callback(void(*changed)(void)){semantic_callback=changed;}
static void xiao147_inputs_draw(bool portrait){
 (void)portrait;gui();
 if(semantic_pending){
  semantic_pending=false;semantic_sequence++;focus_serial=0;focus=4;
  assert(semantic_callback);semantic_callback();
 }
 /* A changed countdown label is visual, not a new command/auth identity. */
 stable_countdown_pending=false;
}
static void xiao147_inputs_focus_cancel_pending(void){gui();focus_serial=0;cancels++;}
static void xiao147_inputs_focus_next(void){
 gui();uint32_t before=semantic_sequence;focus_serial=0;
 if(change_inside_next){change_inside_next=false;semantic_pending=true;}
 xiao147_inputs_draw(false);if(before!=semantic_sequence)return;
 moves++;focus=(focus+1)%5;
}
static uint32_t xiao147_inputs_focus_capture(void){
 gui();uint32_t before=semantic_sequence;
 if(change_inside_capture){change_inside_capture=false;semantic_pending=true;}
 xiao147_inputs_draw(false);if(before!=semantic_sequence)return 0;
 captures++;if(!capture_allowed)return 0;
 captured_focus=focus;focus_serial=++focus_sequence;return focus_serial;
}
static void xiao147_inputs_focus_activate(uint32_t serial){
 gui();if(serial&&serial==focus_serial&&focus==captured_focus){
  activations++;activated_focus=focus;focus_serial=0;
 }
}
static void xiao147_inputs_cancel(void){gui();focus_serial=0;}
static void xiao147_inputs_press(uint16_t x,uint16_t y){(void)x;(void)y;gui();focus_serial=0;}
enum xiao147_page xiao147_ui_page(void);
bool xiao147_ui_wake_on_touch(void);
static void imu_button_input(struct input_event*,void*);
'''
postlude = r'''
K_MSGQ_DEFINE(touch_queue,sizeof(struct touch_sample),32,4);
static int previous_touch_fingers;
static bool slider_drag;
static struct touch_sample last_touch;
static bool inject_late_press;
static int maybe_inject(void*q){
 if(inject_late_press&&q==&usr1_edges){
  inject_late_press=false;now=319;
  struct input_event e={INPUT_EV_KEY,INPUT_BTN_0,1};imu_button_input(&e,NULL);
  now=321;return 1;
 }return 0;
}
static void orientation_redraw(enum xiao147_orientation orientation){
 assert(display_context);
 bool portrait=xiao147_orientation_portrait(orientation);
''' + orientation + r'''
 xiao147_rotation_set(orientation);
}
'''
tests = r'''
static void reset(void){
 usr1_down=imu_mode=auto_rotate_mode=restore_landscape=sleeping=0;
 usr1_context=1;usr1_press_context=usr1_worker_context=usr1_capture=0;
 usr1_navigation_press=false;xiao147_usr1_click_init(&usr1_click);
 k_msgq_purge(&usr1_edges);k_msgq_purge(&touch_queue);
 usr1_navigation.pending=touch_work.pending=redraw_pending=false;
 page_atomic=XIAO147_PAGE_BLE_MOUSE;rotation=XIAO147_USB_LEFT;auto_orientation=rotation;
 now=0;moves=activations=captures=cancels=focus=activated_focus=gui_calls=0;
 focus_serial=0;capture_allowed=initialized=true;display_context=false;inject_late_press=false;
 semantic_pending=stable_countdown_pending=change_inside_next=change_inside_capture=false;
 semantic_sequence=0;xiao147_inputs_focus_set_context_callback(usr1_invalidate_context);
}
static void button(int value,uint32_t time){
 now=time;struct input_event e={INPUT_EV_KEY,INPUT_BTN_0,value};
 unsigned before=gui_calls;imu_button_input(&e,NULL);assert(gui_calls==before);
}
static void worker(uint32_t time){
 now=time;usr1_navigation.pending=false;display_context=true;
 usr1_navigation.work.handler(&usr1_navigation.work);display_context=false;
}
static void redraw(void){
 assert(redraw_pending);redraw_pending=false;display_context=true;
 orientation_redraw((enum xiao147_orientation)auto_orientation);display_context=false;
}
int main(void){
 reset();page_atomic=XIAO147_PAGE_STATUS;
 button(1,10);assert(imu_mode==1&&redraw_pending&&!gui_calls);
 button(1,11);button(2,12);assert(imu_mode==1);button(0,20);
 assert(imu_mode==1&&!usr1_navigation.pending&&!moves&&!activations);
 for(int p=XIAO147_PAGE_SETTINGS;p<=XIAO147_PAGE_DEVICES;p++){
  reset();page_atomic=p;button(1,10);button(2,11);button(0,20);
  assert(!imu_mode&&!usr1_navigation.pending&&!gui_calls);
 }
 /* A status-held button cannot turn its release into a navigation click. */
 reset();page_atomic=XIAO147_PAGE_STATUS;button(1,10);page_atomic=XIAO147_PAGE_BLE_MOUSE;
 button(0,20);worker(1000);assert(!moves&&!activations&&!captures);
 /* Single is delayed once, all focus calls run only on display queue. */
 reset();button(1,10);button(1,11);button(2,12);button(0,20);button(0,21);
 worker(20);assert(captures==1&&!moves&&!activations&&usr1_navigation.due==320);
 worker(319);assert(!moves);worker(320);assert(moves==1&&focus==1&&!activations);
 worker(1000);assert(moves==1);
 /* Slow worker drains original timestamps before ticking processing time. */
 reset();button(1,10);button(0,20);button(1,200);button(0,210);
 worker(10000);assert(!moves&&activations==1&&activated_focus==0&&captures==1);
 /* Holding the second press across the first deadline does not MOVE first. */
 reset();button(1,10);button(0,20);button(1,319);worker(330);
 assert(!moves&&!activations&&usr1_navigation.due==820);
 button(0,700);worker(700);assert(activations==1&&!moves&&activated_focus==0);
 /* A new edge queued after drain but before the fresh clock cannot be
  * overtaken by timeout: watermark is captured at worker entry. */
 reset();button(1,10);button(0,20);worker(20);inject_late_press=true;
 worker(310);assert(!moves&&!activations&&usr1_navigation.pending);
 worker(321);assert(!moves&&!activations);button(0,330);worker(330);
 assert(activations==1&&!moves);
 /* Page change, USR2, touch and redraw-orientation changes each invalidate
  * already queued double edges, without touching focus from input context. */
 for(unsigned reason=0;reason<4;reason++){
  reset();button(1,10);button(0,20);button(1,100);button(0,110);
  unsigned before=gui_calls;
  if(reason==0)xiao147_ui_next_page();
  if(reason==1){struct input_event menu={INPUT_EV_KEY,INPUT_BTN_1,1};menu_button_input(&menu,NULL);}
  if(reason==2)xiao147_ui_touch(1,10,10,XIAO147_USB_LEFT);
  if(reason==3){xiao147_ui_set_auto_orientation(XIAO147_BUTTONS_BOTTOM);redraw();}
  if(reason!=3)assert(gui_calls==before);
  worker(1000);assert(!moves&&!activations);
 }
 /* Orientation mismatch also cancels a previously captured pending single
  * even before the normal redraw's context invalidation runs. */
 reset();button(1,10);button(0,20);worker(20);
 button(1,100);rotation=XIAO147_USB_RIGHT;button(0,110);worker(110);worker(1000);
 assert(!moves&&!activations);
 /* Overflow revokes the whole generation. A dropped edge cannot confirm. */
 reset();for(unsigned i=0;i<17;i++)button((i&1)?0:1,10+i);
 assert(usr1_context>1);button(0,40);worker(1000);assert(!moves&&!activations);
 /* Serial changed by a focus redraw/candidate expiry must not activate the
  * command that happens to occupy the same screen position later. */
 reset();button(1,10);button(0,20);worker(20);assert(focus_serial);
 focus_serial=0;button(1,100);button(0,110);worker(110);assert(!activations&&!moves);
 reset();capture_allowed=false;button(1,10);button(0,20);button(1,100);button(0,110);
 worker(110);assert(!activations);
 /* A command/auth semantic change before the first queued press is drained
  * revokes both that capture AND its possible delayed single navigation. */
 for(unsigned double_click=0;double_click<2;double_click++){
  reset();button(1,10);button(0,20);semantic_pending=true;
  if(double_click){button(1,100);button(0,110);}
  worker(1000);worker(2000);
  assert(!moves&&!activations&&!captures&&focus==4);
 }
 /* The same protection applies after an old first capture was already made. */
 reset();button(1,10);button(0,20);worker(20);assert(captures==1);
 semantic_pending=true;button(1,100);button(0,110);worker(110);
 assert(!moves&&!activations&&focus==4);
 /* Stable labels/countdown ticks do not revoke a semantically identical item. */
 reset();button(1,10);button(0,20);stable_countdown_pending=true;
 button(1,100);button(0,110);worker(1000);
 assert(!moves&&activations==1&&activated_focus==0);
 /* MOVE|CAPTURE must not capture a new auth command if focus_next's internal
  * refresh changed semantic context. Capture's own refresh also returns zero. */
 reset();change_inside_next=true;display_context=true;
 usr1_apply_action(XIAO147_USR1_MOVE|XIAO147_USR1_CAPTURE,usr1_context);
 display_context=false;assert(!moves&&!captures&&!usr1_capture&&focus==4);
 reset();change_inside_capture=true;display_context=true;
 usr1_apply_action(XIAO147_USR1_CAPTURE,usr1_context);
 display_context=false;assert(!captures&&!usr1_capture&&focus==4);
 /* A sleeping BLE page consumes the first press as wake, not navigation. */
 reset();sleeping=1;button(1,10);button(0,20);worker(1000);
 assert(!sleeping&&!moves&&!activations&&!captures);
 puts("PASS: real USR1 navigation integration display ownership, page gating, delayed single/original-focus double, watermark, context/serial revocation and overflow");
}
'''
with tempfile.TemporaryDirectory(prefix="xiao147-usr1-nav-") as tmp:
    out = Path(tmp)
    # Declarations for touch queue must precede the actual touch function; the
    # fake redraw includes the unchanged production orientation invalidation.
    harness = prelude + touch_struct + navigation + postlude + callbacks + tests
    (out / "test.c").write_text(harness)
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                    "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I", str(root / "src"),
                    str(root / "src/xiao147_usr1_click.c"), str(out / "test.c"),
                    "-o", str(out / "test")], check=True)
    subprocess.run([str(out / "test")], check=True)
