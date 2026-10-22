/* Fixed landscape three-page controller. Touch/I2C and buttons only enqueue;
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
#include <zmk/battery.h>
#include <zmk/usb.h>
#include "xiao147_ui.h"
#include "xiao147_rotation.h"
#include "lism_screen.h"
#include "kb_route/view.h"
#include "kb_route/layer_label.h"
LV_FONT_DECLARE(FG_Medium_20);
LV_FONT_DECLARE(FR_Medium_32);
LV_FONT_DECLARE(DINishExpanded_Light_36);
static atomic_t current_page,requests,page_epoch,touch_epoch,pointer_epoch,sleeping,activity,cancel_touch,confirm;
struct touch {uint32_t epoch,gesture;uint16_t x,y;uint8_t fingers;};
K_MSGQ_DEFINE(touches,sizeof(struct touch),24,4);
static lv_obj_t *screen,*settings,*brightness_label,*slider,*sleep_label;
static lv_obj_t *dongle_battery_bar,*dongle_battery_label;
static uint8_t brightness=90,applied=255;
static bool auto_sleep=true;
static int pending_page=-1;
static int settings_armed=-1;static uint16_t press_x,press_y;static bool contact,blocked;
static uint8_t xiao147_output_profile_count(void){return 5;}
enum xiao147_page xiao147_ui_page(void){return atomic_get(&current_page);}
bool xiao147_ui_imu_enabled(void){return false;}
bool xiao147_ui_is_sleeping(void){return atomic_get(&sleeping);}
bool xiao147_ui_wake_on_motion(void){return false;}
uint32_t xiao147_ui_pointer_generation(void){return atomic_get(&pointer_epoch);}
bool xiao147_ui_wake_on_touch(void){atomic_set(&activity,k_uptime_get_32());bool was=atomic_set(&sleeping,0)!=0;if(was)atomic_inc(&pointer_epoch);return was;}
void xiao147_ui_next_page(void){xiao147_ui_wake_on_touch();atomic_inc(&requests);}
void xiao147_ui_previous_page(void){xiao147_ui_wake_on_touch();atomic_add(&requests,2);}
enum xiao147_orientation xiao147_rotation_get(void){return XIAO147_USB_LEFT;}
bool xiao147_rotation_is_portrait(void){return false;}
void xiao147_rotation_install(void){}
void xiao147_rotation_set(enum xiao147_orientation o){ARG_UNUSED(o);}
void xiao147_ui_touch(uint8_t fingers,uint16_t x,uint16_t y,enum xiao147_orientation orientation){
    if(orientation!=XIAO147_USB_LEFT || fingers==XIAO147_TOUCH_CANCEL){atomic_inc(&touch_epoch);atomic_set(&cancel_touch,1);return;}
    struct touch t={.epoch=atomic_get(&page_epoch),.gesture=atomic_get(&touch_epoch),.x=x,.y=y,.fingers=fingers};
    if(k_msgq_put(&touches,&t,K_NO_WAIT)){atomic_inc(&touch_epoch);atomic_set(&cancel_touch,1);}
}
static lv_obj_t *label(lv_obj_t*p,int x,int y,int w,const char*text,const lv_font_t*font){
    lv_obj_t *o=lv_label_create(p);lv_obj_set_pos(o,x,y);lv_obj_set_width(o,w);lv_label_set_text(o,text);
    lv_obj_set_style_text_color(o,lv_color_hex(0xffffff),0);lv_obj_set_style_text_font(o,font,0);return o;
}
static void settings_draw(void){
    char text[40];snprintf(text,sizeof(text),"Brightness   %u%%",brightness);lv_label_set_text(brightness_label,text);
    lv_bar_set_value(slider,brightness,LV_ANIM_OFF);lv_label_set_text(sleep_label,auto_sleep?"Screen sleep: 5 min":"Screen sleep: Off");
}
static void settings_create(void){
    settings=lv_obj_create(screen);lv_obj_set_pos(settings,0,0);lv_obj_set_size(settings,320,172);lv_obj_set_style_pad_all(settings,0,0);
    lv_obj_set_style_border_width(settings,0,0);lv_obj_set_style_bg_color(settings,lv_color_black(),0);lv_obj_remove_flag(settings,LV_OBJ_FLAG_SCROLLABLE);
    label(settings,10,5,280,"DISPLAY",&lv_font_montserrat_18);
    brightness_label=label(settings,16,38,288,"",&lv_font_montserrat_14);
    slider=lv_bar_create(settings);lv_obj_set_pos(slider,16,66);lv_obj_set_size(slider,288,12);lv_bar_set_range(slider,8,100);
    lv_obj_set_style_bg_color(slider,lv_color_hex(0x569fa7),LV_PART_INDICATOR);
    sleep_label=label(settings,16,95,288,"",&lv_font_montserrat_14);
    label(settings,16,134,288,"Back to status",&lv_font_montserrat_14);settings_draw();
}
static void main_visibility(bool visible){
    for(unsigned i=0;i<lv_obj_get_child_count(screen);i++){
        lv_obj_t *o=lv_obj_get_child(screen,i);if(o==settings)continue;
        if(visible)lv_obj_remove_flag(o,LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(o,LV_OBJ_FLAG_HIDDEN);
    }
}
static void page_set(unsigned next){
    lism_screen_touch(XIAO147_TOUCH_CANCEL,0,0);settings_armed=-1;contact=false;blocked=true;
    k_msgq_purge(&touches);atomic_inc(&pointer_epoch);atomic_inc(&page_epoch);atomic_inc(&touch_epoch);atomic_set(&current_page,next);
    if(settings){lv_obj_delete(settings);settings=NULL;}
    lism_screen_present();
    if(next!=XIAO147_PAGE_DEVICES){main_visibility(next==XIAO147_PAGE_STATUS);if(next==XIAO147_PAGE_SETTINGS)settings_create();}
}
static int settings_hit(uint16_t x,uint16_t y){if(x<8||x>=312)return -1;return y>=32&&y<86?0:y>=90&&y<123?1:y>=129&&y<168?2:-1;}
static void settings_touch(struct touch t){
    if(t.fingers>1){settings_armed=-1;blocked=t.fingers!=0;contact=t.fingers!=0;return;}
    if(t.fingers==1){
        if(!contact){settings_armed=blocked?-1:settings_hit(t.x,t.y);press_x=t.x;press_y=t.y;contact=true;}
        if(settings_armed==0){brightness=CLAMP(8+(int)(t.x-16)*92/288,8,100);settings_draw();}
        else if(settings_hit(t.x,t.y)!=settings_armed || abs((int)t.x-press_x)>12 || abs((int)t.y-press_y)>12)settings_armed=-1;
    }else{
        int action=blocked?-1:settings_armed;contact=false;blocked=false;settings_armed=-1;
        if(action==1){auto_sleep=!auto_sleep;settings_draw();}else if(action==2)page_set(XIAO147_PAGE_STATUS);
    }
}

static void layout_status(void)
{
    const bool portrait=false;
    /* The pinned OPERATOR layout creates these five children in this order. */
    if (lv_obj_get_child_cnt(screen) < 5) { return; }
    lv_obj_t *modifiers = lv_obj_get_child(screen, 0);
    lv_obj_set_layout(modifiers, LV_LAYOUT_NONE);
    lv_obj_set_style_pad_all(modifiers, 0, LV_PART_MAIN);
    lv_obj_set_pos(modifiers, portrait ? 6 : 10, portrait ? 5 : 2);
    lv_obj_set_size(modifiers, portrait ? 162 : 300, portrait ? 50 : 24);
    /* Measure the real font: equal-width cells do not give equal gaps around
     * proportional labels such as GUI and NUM. Distribute remainder pixels
     * cumulatively so the spaces on either side of a divider differ by <=1. */
    if (lv_obj_get_child_cnt(modifiers) >= 11) {
        const int columns = portrait ? 3 : 6;
        const int row_width = portrait ? 162 : 300;
        for (int row = 0; row < (portrait ? 2 : 1); row++) {
            int widths[6], total = 0;
            for (int column = 0; column < columns; column++) {
                lv_obj_t *label = lv_obj_get_child(modifiers, (row * columns + column) * 2);
                lv_point_t size;
                lv_text_get_size(&size, lv_label_get_text(label), &FG_Medium_20,
                                0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
                widths[column] = size.x;
                total += size.x;
            }
            const int spaces = 2 * (columns - 1);
            int spare = row_width - total - 2 * (columns - 1);
            if (spare < 0) { spare = 0; }
            int used = 0;
            for (int column = 0; column < columns; column++) {
                int index = (row * columns + column) * 2;
                lv_obj_t *label = lv_obj_get_child(modifiers, index);
                lv_obj_set_pos(label, used + column * 2 + (column * 2 * spare) / spaces,
                               row * 25);
                lv_obj_set_width(label, widths[column]);
                lv_obj_set_style_pad_all(label, 0, LV_PART_MAIN);
                lv_obj_set_style_text_letter_space(label, 0, LV_PART_MAIN);
                lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_LEFT, LV_PART_MAIN);
                lv_label_set_long_mode(label, LV_LABEL_LONG_CLIP);
                lv_obj_set_style_text_font(label, &FG_Medium_20, LV_PART_MAIN);
                used += widths[column];
                if (index < 10) {
                    lv_obj_t *divider = lv_obj_get_child(modifiers, index + 1);
                    lv_obj_set_pos(divider, used + column * 2 +
                                   ((column * 2 + 1) * spare) / spaces, row * 25);
                    lv_obj_set_size(divider, 2, 16);
                    if (portrait && index == 4) { lv_obj_add_flag(divider, LV_OBJ_FLAG_HIDDEN); }
                    else { lv_obj_clear_flag(divider, LV_OBJ_FLAG_HIDDEN); }
                }
            }
        }
    }

    lv_obj_t *wpm = lv_obj_get_child(screen, 1);
    lv_obj_set_pos(wpm, portrait ? 4 : 10, portrait ? 61 : 28);
    /* Native geometry avoids the ARGB transform layer (260*65*4 = 67600
     * bytes before widgets, already larger than our entire 64 KiB LVGL heap).
     */
    int meter_width = portrait ? 164 : 300;
    int meter_height = portrait ? 55 : 65;
    lv_obj_set_size(wpm, meter_width, meter_height);
    uint32_t wpm_children = lv_obj_get_child_cnt(wpm);
    if (wpm_children > 3) {
        uint32_t bars = wpm_children - 3; /* bars, peak, WPM label, layer label */
        for (uint32_t i = 0; i < bars; i++) {
            int gap = portrait ? 1 : 2;
            int x = i * (meter_width + gap) / bars;
            int end = (i + 1) * (meter_width + gap) / bars - gap;
            lv_obj_t *bar = lv_obj_get_child(wpm, i);
            lv_obj_set_pos(bar, x, 0);
            lv_obj_set_size(bar, end - x, meter_height);
        }
        lv_obj_set_height(lv_obj_get_child(wpm, bars), meter_height);
        lv_obj_t *number = lv_obj_get_child(wpm, wpm_children - 2);
        lv_obj_t *name = lv_obj_get_child(wpm, wpm_children - 1);
        lv_obj_set_style_text_font(number, portrait ? &FG_Medium_20 : &FR_Medium_32, LV_PART_MAIN);
        kb_layer_label_update(name,lv_label_get_text(name));
        lv_obj_align(number, LV_ALIGN_TOP_LEFT, 0, 0);
        lv_obj_align(name, LV_ALIGN_BOTTOM_RIGHT, 0, 0);
    }

    lv_obj_t *layers = lv_obj_get_child(screen, 2);
    lv_obj_set_pos(layers, portrait ? 7 : 10, portrait ? 128 : 98);
    int layer_width = portrait ? 158 : 300;
    lv_obj_set_size(layers, layer_width, 6);
    uint32_t layer_count = lv_obj_get_child_cnt(layers);
    for (uint32_t i = 0; i < layer_count; i++) {
        int x = i * (layer_width + 3) / layer_count;
        int end = (i + 1) * (layer_width + 3) / layer_count - 3;
        lv_obj_t *dot = lv_obj_get_child(layers, i);
        lv_obj_set_pos(dot, x, 0);
        lv_obj_set_size(dot, end - x, 6);
    }

    /* The three-peripheral stock widget uses vertical bars; our matching
     * compact arcs live in their own status-only object. */
    lv_obj_set_pos(lv_obj_get_child(screen,3),10,109);
    lv_obj_t *output = lv_obj_get_child(screen, 4);
    lv_obj_set_pos(output, portrait ? 8 : 202, portrait ? 144 : 109);
    lv_obj_set_size(output, portrait ? 156 : 116, portrait ? 65 : 62);
    uint32_t output_children = lv_obj_get_child_cnt(output);
    if (output_children >= 3) {
        for (uint32_t i = 0; i < 2; i++) {
            lv_obj_t *button = lv_obj_get_child(output, i);
            lv_obj_set_pos(button, portrait ? (int)i * 79 : (int)i * 58, 0);
            lv_obj_set_size(button, portrait ? 77 : 56, 29);
        }
        uint32_t profile_count = xiao147_output_profile_count();
        int row_width = portrait ? 156 : 116;
        for (uint32_t i = 0; i < profile_count; i++) {
            lv_obj_t *slot = lv_obj_get_child(output, i + 2);
            int x = (int)i * (row_width + 2) / (int)profile_count;
            int end = (int)(i + 1) * (row_width + 2) / (int)profile_count - 2;
            lv_obj_set_pos(slot, x, portrait ? 36 : 33);
            lv_obj_set_size(slot, end - x, 29);
        }
    }
    lv_obj_set_style_transform_pivot_x(output, 0, LV_PART_MAIN);
    lv_obj_set_style_transform_pivot_y(output, 0, LV_PART_MAIN);
    lv_obj_set_style_transform_scale_x(output, 256, LV_PART_MAIN);
    lv_obj_set_style_transform_scale_y(output, 256, LV_PART_MAIN);


    lv_obj_set_pos(dongle_battery_bar, 150, 115);
    lv_obj_set_size(dongle_battery_bar, 42, 6);
    const int battery_label_height = lv_font_montserrat_12.line_height;
    lv_obj_set_height(dongle_battery_label, battery_label_height);
    lv_obj_set_style_pad_all(dongle_battery_label, 0, LV_PART_MAIN);
    lv_label_set_long_mode(dongle_battery_label, LV_LABEL_LONG_CLIP);
    lv_obj_set_pos(dongle_battery_label, 150, 128);
    lv_obj_set_width(dongle_battery_label, portrait ? 53 : 49);
    lv_obj_set_style_text_align(dongle_battery_label,
                                portrait ? LV_TEXT_ALIGN_RIGHT : LV_TEXT_ALIGN_LEFT,
                                LV_PART_MAIN);
}
static void refresh(lv_timer_t*timer){
    ARG_UNUSED(timer);unsigned next=atomic_set(&requests,0);
    if(next){pending_page=((pending_page<0?(int)xiao147_ui_page():pending_page)+next)%XIAO147_PAGE_COUNT;lism_screen_request_active(pending_page==XIAO147_PAGE_DEVICES);}
    if(pending_page>=0 && lism_screen_active()==(pending_page==XIAO147_PAGE_DEVICES)){unsigned target=pending_page;pending_page=-1;page_set(target);}
    if(xiao147_ui_page()==XIAO147_PAGE_DEVICES && !lism_screen_active())page_set(XIAO147_PAGE_STATUS);
    if(atomic_set(&cancel_touch,0)){lism_screen_touch(XIAO147_TOUCH_CANCEL,0,0);settings_armed=-1;blocked=true;}
    struct touch t;
    for(unsigned budget=0;budget<12 && !k_msgq_get(&touches,&t,K_NO_WAIT);budget++){
        if(t.epoch!=(uint32_t)atomic_get(&page_epoch) || t.gesture!=(uint32_t)atomic_get(&touch_epoch))continue;
        if(xiao147_ui_page()==XIAO147_PAGE_DEVICES)lism_screen_touch(t.fingers,t.x,t.y);
        else if(xiao147_ui_page()==XIAO147_PAGE_SETTINGS)settings_touch(t);
    }
    if(atomic_set(&confirm,0) && xiao147_ui_page()==XIAO147_PAGE_DEVICES)lism_screen_nav(37);
    static unsigned labels;
    if(lv_obj_get_child_count(screen)>=5){unsigned count=lv_obj_get_child_count(lv_obj_get_child(screen,0));if(count>=11 && count!=labels){layout_status();labels=count;}}
    if(auto_sleep && k_uptime_get_32()-(uint32_t)atomic_get(&activity)>300000 && !atomic_set(&sleeping,1))atomic_inc(&pointer_epoch);
    uint8_t level=atomic_get(&sleeping)?0:brightness;
    const struct device *leds=DEVICE_DT_GET_ONE(pwm_leds);
    if(level!=applied && device_is_ready(leds) && !led_set_brightness(leds,DT_NODE_CHILD_IDX(DT_NODELABEL(disp_bl)),level))applied=level;
    uint8_t battery=zmk_battery_state_of_charge();char text[24];
    if(zmk_usb_is_powered())snprintf(text,sizeof(text),"USB");else snprintf(text,sizeof(text),"%u%%",battery);
    if(strcmp(lv_label_get_text(dongle_battery_label),text))lv_label_set_text(dongle_battery_label,text);
    lv_bar_set_value(dongle_battery_bar,battery,LV_ANIM_OFF);
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
    screen=s;atomic_set(&activity,k_uptime_get_32());
    dongle_battery_bar=lv_bar_create(screen);lv_obj_set_pos(dongle_battery_bar,10,160);lv_obj_set_size(dongle_battery_bar,140,6);lv_bar_set_range(dongle_battery_bar,0,100);
    lv_obj_set_style_bg_color(dongle_battery_bar,lv_color_hex(0x4d9c76),LV_PART_INDICATOR);
    dongle_battery_label=label(screen,153,156,49,"USB",&lv_font_montserrat_10);
    lv_timer_create(refresh,33,NULL);
}
