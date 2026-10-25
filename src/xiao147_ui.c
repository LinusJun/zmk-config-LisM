/* Fixed landscape two-page controller. Touch/I2C and buttons only enqueue;
 * LVGL allocation and deletion stay on its display thread. No IMU driver.
 */
#include <lvgl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/device.h>
#include <zephyr/input/input.h>
#include <zephyr/dt-bindings/input/input-event-codes.h>
#include <zephyr/drivers/led.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include "xiao147_ui.h"
#include "xiao147_rotation.h"
#include "lism_screen.h"
#include "kb_route/view.h"
#include "kb_route/layer_label.h"
LV_FONT_DECLARE(FG_Medium_20);
LV_FONT_DECLARE(FR_Medium_32);
LV_FONT_DECLARE(DINishExpanded_Light_36);
static atomic_t current_page,requests,page_epoch,touch_epoch,pointer_epoch,cancel_touch,confirm;
struct touch {uint32_t epoch,gesture;uint16_t x,y;uint8_t fingers;};
K_MSGQ_DEFINE(touches,sizeof(struct touch),24,4);
static lv_obj_t *screen;
static bool backlight_applied;
static int pending_page=-1;
static uint8_t xiao147_output_profile_count(void){return 5;}
enum xiao147_page xiao147_ui_page(void){return atomic_get(&current_page);}
bool xiao147_ui_imu_enabled(void){return false;}
bool xiao147_ui_is_sleeping(void){return false;}
bool xiao147_ui_wake_on_motion(void){return false;}
uint32_t xiao147_ui_pointer_generation(void){return atomic_get(&pointer_epoch);}
bool xiao147_ui_wake_on_touch(void){return false;}
void xiao147_ui_next_page(void){xiao147_ui_wake_on_touch();atomic_inc(&requests);}
void xiao147_ui_previous_page(void){xiao147_ui_wake_on_touch();atomic_inc(&requests);}
enum xiao147_orientation xiao147_rotation_get(void){return XIAO147_USB_LEFT;}
bool xiao147_rotation_is_portrait(void){return false;}
void xiao147_rotation_install(void){}
void xiao147_rotation_set(enum xiao147_orientation o){ARG_UNUSED(o);}
void xiao147_ui_touch(uint8_t fingers,uint16_t x,uint16_t y,enum xiao147_orientation orientation){
    if(orientation!=XIAO147_USB_LEFT || fingers==XIAO147_TOUCH_CANCEL){atomic_inc(&touch_epoch);atomic_set(&cancel_touch,1);return;}
    struct touch t={.epoch=atomic_get(&page_epoch),.gesture=atomic_get(&touch_epoch),.x=x,.y=y,.fingers=fingers};
    if(k_msgq_put(&touches,&t,K_NO_WAIT)){atomic_inc(&touch_epoch);atomic_set(&cancel_touch,1);}
}
static void main_visibility(bool visible){
    for(unsigned i=0;i<lv_obj_get_child_count(screen);i++){
        lv_obj_t *o=lv_obj_get_child(screen,i);
        if(visible)lv_obj_remove_flag(o,LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(o,LV_OBJ_FLAG_HIDDEN);
    }
}
static void page_set(unsigned next){
    lism_screen_touch(XIAO147_TOUCH_CANCEL,0,0);
    k_msgq_purge(&touches);atomic_inc(&pointer_epoch);atomic_inc(&page_epoch);atomic_inc(&touch_epoch);atomic_set(&current_page,next);
    lism_screen_present();
    if(next!=XIAO147_PAGE_DEVICES)main_visibility(true);
}
static void layout_status(void)
{
    /* Native 320x172 landscape geometry: separate graph, icon and footer
     * rows. No transform surfaces or scaling allocations are needed. */
    if (lv_obj_get_child_cnt(screen) < 5) { return; }
    lv_obj_t *modifiers = lv_obj_get_child(screen, 0);
    lv_obj_set_layout(modifiers, LV_LAYOUT_NONE);
    lv_obj_set_style_pad_all(modifiers, 0, LV_PART_MAIN);
    lv_obj_set_pos(modifiers, 4, 0);
    lv_obj_set_size(modifiers, 312, 25); /* FG_Medium_20 line height is 25. */
    lv_obj_remove_flag(modifiers, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(modifiers, LV_SCROLLBAR_MODE_OFF);
    lv_obj_set_style_bg_color(modifiers,lv_color_black(),LV_PART_MAIN);
    lv_obj_set_style_bg_opa(modifiers,LV_OPA_COVER,LV_PART_MAIN);
    /* Measure proportional labels and distribute equal divider gaps. */
    if (lv_obj_get_child_cnt(modifiers) >= 11) {
        int widths[6], total = 0;
        for (int column = 0; column < 6; column++) {
            lv_obj_t *label = lv_obj_get_child(modifiers, column * 2);
            lv_point_t size;
            lv_text_get_size(&size, lv_label_get_text(label), &FG_Medium_20,
                            0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
            widths[column] = size.x;
            total += size.x;
        }
        int spare = 312 - total - 10;
        if (spare < 0) { spare = 0; }
        int used = 0;
        for (int column = 0; column < 6; column++) {
            lv_obj_t *label = lv_obj_get_child(modifiers, column * 2);
            lv_obj_set_pos(label, used + column * 2 + column * 2 * spare / 10, 0);
            lv_obj_set_width(label, widths[column]);
            lv_obj_set_style_pad_all(label, 0, LV_PART_MAIN);
            lv_obj_set_style_text_letter_space(label, 0, LV_PART_MAIN);
            lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_LEFT, LV_PART_MAIN);
            lv_label_set_long_mode(label, LV_LABEL_LONG_CLIP);
            lv_obj_set_style_text_font(label, &FG_Medium_20, LV_PART_MAIN);
            used += widths[column];
            if (column < 5) {
                lv_obj_t *divider = lv_obj_get_child(modifiers, column * 2 + 1);
                lv_obj_set_pos(divider, used + column * 2 + (column * 2 + 1) * spare / 10, 4);
                lv_obj_set_size(divider, 2, 16);
                lv_obj_clear_flag(divider, LV_OBJ_FLAG_HIDDEN);
            }
        }
    }

    lv_obj_t *wpm = lv_obj_get_child(screen, 1);
    lv_obj_set_pos(wpm, 4, 27);
    lv_obj_set_size(wpm, 312, 51);
    lv_obj_set_style_pad_all(wpm, 0, LV_PART_MAIN);
    lv_obj_remove_flag(wpm, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(wpm, LV_SCROLLBAR_MODE_OFF);
    uint32_t wpm_children = lv_obj_get_child_cnt(wpm);
    if (wpm_children > 3) {
        uint32_t bars = wpm_children - 3;
        for (uint32_t i = 0; i <= bars; i++) {
            lv_obj_t *bar = lv_obj_get_child(wpm, i); /* Last is the peak. */
            if (i < bars) {
                int x = i * 314 / bars;
                int end = (i + 1) * 314 / bars - 2;
                lv_obj_set_pos(bar, x, 0);
                lv_obj_set_width(bar, end - x);
            }
            lv_obj_set_height(bar, 51);
            lv_obj_set_style_pad_all(bar, 0, LV_PART_MAIN);
            lv_obj_remove_flag(bar, LV_OBJ_FLAG_SCROLLABLE);
            lv_obj_set_scrollbar_mode(bar, LV_SCROLLBAR_MODE_OFF);
        }
        lv_obj_t *number = lv_obj_get_child(wpm, wpm_children - 2);
        lv_obj_t *name = lv_obj_get_child(wpm, wpm_children - 1);
        lv_obj_set_style_text_font(number, &FR_Medium_32, LV_PART_MAIN);
        kb_layer_label_update(name,lv_label_get_text(name));
        lv_obj_align(number, LV_ALIGN_TOP_LEFT, 0, 0);
        lv_obj_align(name, LV_ALIGN_BOTTOM_RIGHT, 0, 0);
    }

    lv_obj_t *layers = lv_obj_get_child(screen, 2);
    lv_obj_set_pos(layers, 4, 82);
    lv_obj_set_size(layers, 312, 6);
    lv_obj_set_style_pad_all(layers, 0, LV_PART_MAIN);
    lv_obj_remove_flag(layers, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(layers, LV_SCROLLBAR_MODE_OFF);
    uint32_t layer_count = lv_obj_get_child_cnt(layers);
    for (uint32_t i = 0; i < layer_count; i++) {
        int x = i * 315 / layer_count;
        int end = (i + 1) * 315 / layer_count - 3;
        lv_obj_t *dot = lv_obj_get_child(layers, i);
        lv_obj_set_pos(dot, x, 0);
        lv_obj_set_size(dot, end - x, 6);
    }

    lv_obj_t *battery = lv_obj_get_child(screen, 3);
    lv_obj_set_pos(battery, 4, 118);
    lv_obj_set_size(battery, 132, 52);
    lv_obj_set_style_pad_all(battery, 0, LV_PART_MAIN);
    lv_obj_remove_flag(battery, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(battery, LV_SCROLLBAR_MODE_OFF);
    /* Only two ESB halves are present; keep their native circles and labels. */
    for (unsigned i = 0; i < lv_obj_get_child_cnt(battery) && i < 2; i++) {
        lv_obj_t *arc = lv_obj_get_child(battery, i);
        lv_obj_set_pos(arc, 2 + (int)i * 72, 1);
        lv_obj_set_size(arc, 50, 50);
        lv_obj_set_style_pad_all(arc, 0, LV_PART_MAIN);
        lv_obj_remove_flag(arc, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_scrollbar_mode(arc, LV_SCROLLBAR_MODE_OFF);
    }

    lv_obj_t *output = lv_obj_get_child(screen, 4);
    lv_obj_set_pos(output, 144, 118);
    lv_obj_set_size(output, 172, 52);
    lv_obj_set_style_pad_all(output, 0, LV_PART_MAIN);
    lv_obj_remove_flag(output, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(output, LV_SCROLLBAR_MODE_OFF);
    if (lv_obj_get_child_cnt(output) >= 7) {
        for (unsigned i = 0; i < 7; i++) {
            lv_obj_t *button = lv_obj_get_child(output, i);
            if (i < 2) {
                lv_obj_set_pos(button, (int)i * 87, 0);
                lv_obj_set_size(button, 85, 25);
            } else {
                unsigned slot = i - 2, count = xiao147_output_profile_count();
                int x = slot * 174 / count;
                int end = (slot + 1) * 174 / count - 2;
                lv_obj_set_pos(button, x, 27);
                lv_obj_set_size(button, end - x, 25);
            }
            lv_obj_remove_flag(button, LV_OBJ_FLAG_SCROLLABLE);
            lv_obj_set_scrollbar_mode(button, LV_SCROLLBAR_MODE_OFF);
            if (lv_obj_get_child_cnt(button)) {
                lv_obj_t *label = lv_obj_get_child(button, 0);
                lv_obj_set_style_translate_y(label, 0, LV_PART_MAIN);
                lv_obj_center(label);
            }
        }
    }
}

static void refresh(lv_timer_t*timer){
    ARG_UNUSED(timer);unsigned next=atomic_set(&requests,0);
    if(next){pending_page=((pending_page<0?(int)xiao147_ui_page():pending_page)+next)%XIAO147_PAGE_COUNT;lism_screen_request_active(pending_page==XIAO147_PAGE_DEVICES);}
    if(pending_page>=0 && lism_screen_active()==(pending_page==XIAO147_PAGE_DEVICES)){unsigned target=pending_page;pending_page=-1;page_set(target);}
    if(xiao147_ui_page()==XIAO147_PAGE_DEVICES && !lism_screen_active())page_set(XIAO147_PAGE_STATUS);
    if(atomic_set(&cancel_touch,0)){lism_screen_touch(XIAO147_TOUCH_CANCEL,0,0);}
    struct touch t;
    for(unsigned budget=0;budget<12 && !k_msgq_get(&touches,&t,K_NO_WAIT);budget++){
        if(t.epoch!=(uint32_t)atomic_get(&page_epoch) || t.gesture!=(uint32_t)atomic_get(&touch_epoch))continue;
        if(xiao147_ui_page()==XIAO147_PAGE_DEVICES)lism_screen_touch(t.fingers,t.x,t.y);
    }
    if(atomic_set(&confirm,0) && xiao147_ui_page()==XIAO147_PAGE_DEVICES)lism_screen_nav(37);
    static unsigned labels;
    if(lv_obj_get_child_count(screen)>=5){unsigned count=lv_obj_get_child_count(lv_obj_get_child(screen,0));if(count>=11 && count!=labels){layout_status();labels=count;}}
    const struct device *leds=DEVICE_DT_GET_ONE(pwm_leds);
    if(!backlight_applied && device_is_ready(leds) &&
       !led_set_brightness(leds,DT_NODE_CHILD_IDX(DT_NODELABEL(disp_bl)),100))backlight_applied=true;

}
static void buttons(struct input_event*e,void*unused){
    ARG_UNUSED(unused);if(e->type!=INPUT_EV_KEY||!e->value)return;
    if(e->code==INPUT_BTN_1)xiao147_ui_next_page();
    else {xiao147_ui_wake_on_touch();if(xiao147_ui_page()==XIAO147_PAGE_DEVICES)atomic_set(&confirm,1);else xiao147_ui_previous_page();}
}
static void menu_input(struct input_event*e,void*u){buttons(e,u);}
static void usr1_input(struct input_event*e,void*u){buttons(e,u);}
INPUT_CALLBACK_DEFINE(DEVICE_DT_GET(DT_NODELABEL(xiao_touch_menu_button)),menu_input,NULL);
INPUT_CALLBACK_DEFINE(DEVICE_DT_GET(DT_NODELABEL(xiao_touch_left_button)),usr1_input,NULL);
void xiao147_ui_start(lv_obj_t *s){
    screen=s;
    lv_obj_set_size(screen,320,172);lv_obj_set_style_pad_all(screen,0,0);
    lv_obj_set_style_border_width(screen,0,0);lv_obj_remove_flag(screen,LV_OBJ_FLAG_SCROLLABLE);
    lv_timer_create(refresh,33,NULL);
}
