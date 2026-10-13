"""Execute XIAO's actual OPERATOR forks with queue and LVGL ownership assertions."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
def body(path):
    return re.sub(r"^#include .*$", "", (root/path).read_text(), flags=re.M)
prelude = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <ctype.h>
#include <string.h>
#define CONFIG_PROSPECTOR_LAYER_NAME_UPPERCASE 1
#define IS_ENABLED(x) (x)
#define WPM_BAR_COUNT 26
#define WPM_MAX 120
#define ZMK_BLE_PROFILE_COUNT 5
#define LV_PART_MAIN 0
#define LV_OPA_TRANSP 0
#define LV_OPA_COVER 255
#define LV_OBJ_FLAG_HIDDEN 1
#define LV_ALIGN_TOP_LEFT 0
#define LV_ALIGN_BOTTOM_RIGHT 1
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
#define ZMK_EV_EVENT_BUBBLE 0
#define ZMK_SUBSCRIPTION(...)
typedef struct sys_snode { struct sys_snode *next; } sys_snode_t;
typedef struct { sys_snode_t *head,*tail; } sys_slist_t;
#define SYS_SLIST_STATIC_INIT(x) {NULL,NULL}
#define CONTAINER_OF(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define SYS_SLIST_FOR_EACH_CONTAINER(l,v,m) \
 for(v=(l)->head?CONTAINER_OF((l)->head,__typeof__(*v),m):NULL;v; \
     v=v->m.next?CONTAINER_OF(v->m.next,__typeof__(*v),m):NULL)
static bool sys_slist_is_empty(sys_slist_t *l) { return !l->head; }
static void sys_slist_append(sys_slist_t *l,sys_snode_t *n) {
    n->next=NULL; if(l->tail) l->tail->next=n; else l->head=n; l->tail=n;
}
typedef struct lv_obj { struct lv_obj *child; char text[40]; int x; } lv_obj_t;
typedef unsigned lv_color_t;
static bool display_context;
static lv_obj_t objects[160];
static unsigned objects_used,gui_calls;
static void gui(void) { assert(display_context); gui_calls++; }
static lv_obj_t *lv_obj_create(lv_obj_t *p) {
    gui(); assert(objects_used<160); lv_obj_t *o=&objects[objects_used++];
    if(p && !p->child) p->child=o;
    return o;
}
static lv_obj_t *lv_label_create(lv_obj_t *p) { return lv_obj_create(p); }
static lv_obj_t *lv_obj_get_child(lv_obj_t *p,int i) { gui(); assert(i==0); return p->child; }
static lv_color_t lv_color_hex(unsigned c) { return c; }
#define STYLE2(name) static void name(lv_obj_t *o,int a,int part) {(void)o;(void)a;(void)part;gui();}
STYLE2(lv_obj_set_style_bg_color)
STYLE2(lv_obj_set_style_bg_opa)
STYLE2(lv_obj_set_style_border_width)
STYLE2(lv_obj_set_style_pad_all)
STYLE2(lv_obj_set_style_radius)
STYLE2(lv_obj_set_style_pad_hor)
STYLE2(lv_obj_set_style_pad_ver)
STYLE2(lv_obj_set_style_pad_top)
STYLE2(lv_obj_set_style_pad_bottom)
STYLE2(lv_obj_set_style_text_color)
STYLE2(lv_obj_set_style_border_color)
STYLE2(lv_obj_set_style_translate_y)
static void lv_obj_set_style_text_font(lv_obj_t *o,const int *f,int p) {(void)o;(void)f;(void)p;gui();}
static void lv_obj_set_pos(lv_obj_t *o,int x,int y) {o->x=x;(void)y;gui();}
static int lv_obj_get_x(lv_obj_t *o) { gui(); return o->x; }
static void lv_obj_set_size(lv_obj_t *o,int w,int h) {(void)o;(void)w;(void)h;gui();}
static void lv_obj_align(lv_obj_t *o,int a,int x,int y) {(void)o;(void)a;(void)x;(void)y;gui();}
static void lv_obj_add_flag(lv_obj_t *o,int f) {(void)o;(void)f;gui();}
static void lv_obj_clear_flag(lv_obj_t *o,int f) {(void)o;(void)f;gui();}
static void lv_obj_center(lv_obj_t *o) {(void)o;gui();}
static void lv_label_set_text(lv_obj_t *o,const char *s) {
    gui(); snprintf(o->text,sizeof(o->text),"%s",s);
}
static int FR_Medium_32,DINishExpanded_Light_36,FG_Medium_20;
#define DISPLAY_COLOR_WPM_BAR_ACTIVE 1
#define DISPLAY_COLOR_WPM_BAR_INACTIVE 2
#define DISPLAY_COLOR_WPM_TEXT 3
#define DISPLAY_COLOR_LAYER_TEXT 4
#define DISPLAY_COLOR_USB_ACTIVE_BG 5
#define DISPLAY_COLOR_USB_INACTIVE_BG 6
#define DISPLAY_COLOR_BLE_ACTIVE_BG 7
#define DISPLAY_COLOR_BLE_INACTIVE_BG 8
#define DISPLAY_COLOR_OUTPUT_ACTIVE_TEXT 9
#define DISPLAY_COLOR_SLOT_ACTIVE_BG 10
#define DISPLAY_COLOR_SLOT_INACTIVE_BG 11
struct zmk_widget_wpm_meter {
    sys_snode_t node; lv_obj_t *obj,*bars[WPM_BAR_COUNT],*peak_indicator,*wpm_label,*layer_label;
};
struct zmk_widget_output {
    sys_snode_t node; lv_obj_t *obj,*usb_btn,*ble_btn,*slots[ZMK_BLE_PROFILE_COUNT];
};
enum zmk_transport { ZMK_TRANSPORT_USB, ZMK_TRANSPORT_BLE };
struct zmk_endpoint_instance { enum zmk_transport transport; };
typedef struct { int kind; uint8_t index; } zmk_event_t;
struct zmk_ble_active_profile_changed { uint8_t index; };
static uint8_t mock_wpm,mock_profile;
static enum zmk_transport mock_transport=ZMK_TRANSPORT_USB;
static uint8_t zmk_wpm_get_state(void) { return mock_wpm; }
static uint8_t zmk_keymap_highest_layer_active(void) { return 0; }
static uint8_t zmk_keymap_layer_index_to_id(uint8_t i) { return i; }
static const char *zmk_keymap_layer_name(uint8_t i) { (void)i;return "Base"; }
static struct zmk_endpoint_instance zmk_endpoint_get_selected(void) {
    return (struct zmk_endpoint_instance){.transport=mock_transport};
}
static uint8_t zmk_ble_active_profile_index(void) { return mock_profile; }
static const struct zmk_ble_active_profile_changed *as_zmk_ble_active_profile_changed(const zmk_event_t *e) {
    return e && e->kind==1 ? (const void *)&e->index : NULL;
}
struct k_work { void (*handler)(struct k_work *); bool queued; };
struct k_work_delayable { struct k_work work; bool initialized,pending; unsigned delay; struct k_work_q *queue; };
struct k_work_q { int dummy; };
static struct k_work_q display_queue;
static unsigned delayable_inits,schedules;
static struct k_work_q *zmk_display_work_q(void) { return &display_queue; }
static void k_work_init_delayable(struct k_work_delayable *w,void (*cb)(struct k_work *)) {
    assert(!w->initialized); w->initialized=true; w->work.handler=cb; delayable_inits++;
}
static int k_work_schedule_for_queue(struct k_work_q *q,struct k_work_delayable *w,unsigned delay) {
    assert(q==&display_queue && w->initialized && w->work.handler && display_context);
    w->queue=q;w->delay=delay;w->pending=true;schedules++;return 1;
}
#define k_work_schedule(...) abort()
static void k_work_submit_to_queue(struct k_work_q *q,struct k_work *w) {
    assert(q==&display_queue && w->handler);w->queued=true;
}
#define ZMK_DISPLAY_WIDGET_LISTENER(name,type,cb,get) \
 static type name##_state; \
 static void name##_work_cb(struct k_work *work) {(void)work;assert(display_context);cb(name##_state);} \
 static struct k_work name##_work={.handler=name##_work_cb}; \
 static void name##_init(void) {name##_state=get(NULL);name##_work_cb(NULL);} \
 static int name##_cb(const zmk_event_t *e) {name##_state=get(e);k_work_submit_to_queue(zmk_display_work_q(),&name##_work);return 0;}
static void dispatch(struct k_work *w) {
    assert(w->queued);w->queued=false;display_context=true;w->handler(w);display_context=false;
}
"""
test = r"""
int main(void) {
    /* Raw startup WPM events queue valid listener work, never an uninitialized
     * smoothing item and never any LVGL object before a widget exists.
     */
    mock_wpm=40; display_context=false;
    unsigned before=gui_calls;
    widget_wpm_meter_cb(NULL); dispatch(&widget_wpm_meter_work);
    assert(!wpm_work_ready && !schedules && gui_calls==before && target_wpm==40);
    widget_wpm_meter_layer_cb(NULL); dispatch(&widget_wpm_meter_layer_work);
    struct zmk_widget_wpm_meter first={0},second={0};
    display_context=true;
    lv_obj_t *parent=lv_obj_create(NULL);
    assert(zmk_widget_wpm_meter_init(&first,parent)==0);
    assert(wpm_work_ready && delayable_inits==1 && wpm_smooth_work.pending);
    assert(wpm_smooth_work.queue==&display_queue);
    assert(zmk_widget_wpm_meter_init(&second,parent)==0);
    assert(delayable_inits==1); /* Do not reinitialize queued/running work. */
    assert(!strcmp(first.layer_label->text,"BASE"));
    wpm_smooth_work.pending=false;
    wpm_smooth_work.work.handler(&wpm_smooth_work.work);
    assert(wpm_smooth_work.pending && wpm_smooth_work.queue==&display_queue);
    assert(displayed_wpm>0 && displayed_wpm<target_wpm);
    display_context=false;
    /* Empty output list is safe during diagnostic stages. Event context never
     * mutates LVGL; endpoint/profile updates are deferred and coalesced.
     */
    before=gui_calls;
    mock_transport=ZMK_TRANSPORT_BLE;
    widget_output_async_cb(NULL); assert(gui_calls==before);
    dispatch(&widget_output_async_work); assert(gui_calls==before);
    struct zmk_widget_output output={0};
    display_context=true;assert(zmk_widget_output_init(&output,parent)==0);
    display_context=false;before=gui_calls;
    zmk_event_t profile={.kind=1,.index=2};
    widget_output_async_cb(&profile); assert(gui_calls==before);
    dispatch(&widget_output_async_work);
    assert(active_profile_index==2 && active_transport==ZMK_TRANSPORT_BLE && gui_calls>before);
    before=gui_calls;mock_transport=ZMK_TRANSPORT_USB;
    widget_output_async_cb(NULL); assert(gui_calls==before);
    dispatch(&widget_output_async_work); assert(active_transport==ZMK_TRANSPORT_USB);
    puts("PASS: actual OPERATOR WPM early-event safety, init-before-schedule, single work initialization, display-only animation and deferred USB/BLE GUI updates");
}
"""
joined = prelude + "\n#define widgets wpm_widgets\n" + body("src/xiao147_wpm_meter.c")
joined += "\n#undef widgets\n#define widgets output_widgets\n" + body("src/xiao147_output.c")
joined += "\n#undef widgets\n" + test
with tempfile.TemporaryDirectory(prefix="xiao147-widget-test-") as directory:
    folder=Path(directory)
    c=folder/"test.c"
    c.write_text(joined)
    binary=folder/"test"
    subprocess.run(["cc","-std=gnu11","-Wall","-Wextra","-Werror",
                    "-Wno-unused-parameter","-fsanitize=address,undefined",str(c),"-o",str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
