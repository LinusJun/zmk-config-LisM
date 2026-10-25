/* SPDX-License-Identifier: MIT
 * Drawn symbols avoid emoji/font fallbacks and retain the OPERATOR palette.
 */
#include <kb_route/icons.h>
#include "icon_input.h"
#include <string.h>
#include <kb_route/view.h>
#include <kb_route/signal.h>
#if defined(CONFIG_LISM_BLE_INPUT_BRIDGE)
#include "lism_input_host.h"
#endif
static lv_obj_t *icons;
static uint8_t shown=255;
static bool pad_ready,joystick_ready;
static lv_obj_t *signal_icons;
static uint8_t bars[4];
static void box(lv_layer_t *layer,int x,int y,int w,int h,int radius,uint32_t color,int border) {
    lv_draw_rect_dsc_t d;lv_draw_rect_dsc_init(&d);d.radius=radius;
    d.bg_color=lv_color_hex(border?0:color);d.bg_opa=border?LV_OPA_TRANSP:LV_OPA_COVER;
    d.border_width=border;d.border_color=lv_color_hex(color);d.border_opa=LV_OPA_COVER;
    lv_area_t a={x,y,x+w-1,y+h-1};lv_draw_rect(layer,&d,&a);
}
static void draw(lv_event_t *e) {
    lv_layer_t *layer=lv_event_get_layer(e);lv_area_t a;lv_obj_get_coords(lv_event_get_target(e),&a);
    int x=a.x1+3,y=a.y1+2;
    uint32_t joystick=joystick_ready?0x569fa7:0x353f40;
    uint32_t pad=pad_ready?0x569fa7:0x353f40;
    uint32_t keyboard=shown==KB_ROUTE_BLE?0x569fa7:0x353f40;
    uint32_t dongle=shown==KB_ROUTE_DONGLE?0xb9b9a7:0x4f4f40;
    if(shown==KB_ROUTE_SWITCHING)keyboard=dongle=0xa07840;
    /* Classic Lucide joystick: ball, stalk, left button and rounded base.
     * Raster adaptation of verification/icon-options/A-lucide-joystick.svg.
     * All four symbols have an 18px visible box and the same bottom baseline.
     */
    box(layer,x+7,y,7,7,LV_RADIUS_CIRCLE,joystick,2);
    box(layer,x+9,y+6,2,7,0,joystick,0);
    box(layer,x+4,y+10,2,3,0,joystick,0);
    box(layer,x,y+12,21,6,2,joystick,2);
    x+=28;
    box(layer,x,y,24,18,3,pad,2);box(layer,x+2,y+13,20,2,0,pad,0);
    box(layer,x+11,y+14,2,3,0,pad,0);
    x+=31; /* 24px touchpad ink + the shared 7px visible gap. */
    box(layer,x,y,24,18,3,keyboard,2);
    for(int row=0;row<2;row++)for(int col=0;col<5;col++)box(layer,x+3+col*4,y+4+row*4,2,2,0,keyboard,0);
    box(layer,x+7,y+13,10,2,0,keyboard,0);
    x+=28;
    box(layer,x+3,y+6,16,12,3,dongle,2);
    box(layer,x+7,y,8,8,0,dongle,2);box(layer,x+9,y+2,1,2,0,dongle,0);
    box(layer,x+12,y+2,1,2,0,dongle,0);

}
static void refresh(lv_timer_t *timer) {
    LV_UNUSED(timer);struct kb_route_view view;kb_route_snapshot(&view);
    bool connected=false,stick=false;
    uint8_t next[4]={0}; int8_t dbm=0;
    for(unsigned i=0;i<2;i++){bool valid=kb_esb_signal(i,&dbm);next[i]=kb_signal_bars(valid,dbm);}
#if defined(CONFIG_LISM_BLE_INPUT_BRIDGE)
    struct lism_input_status input;lism_input_host_snapshot(&input);
    for(unsigned i=0;i<LISM_INPUT_PEERS;i++) {
        const struct lism_input_peer_status *p=&input.peers[i];
        struct kb_icon_input_state state=kb_icon_input_classify(input.enabled,p->connected,p->ready,
            p->type==LISM_INPUT_DEVICE_MOUSE || p->type==LISM_INPUT_DEVICE_TOUCHPAD,
            p->name,sizeof(p->name));
        connected|=state.pad;stick|=state.joystick;
        bool valid=lism_input_host_slot_signal(i,&dbm);
        unsigned group=state.joystick?3:2;
        uint8_t strength=kb_signal_bars(valid,dbm);
        if(strength && (!next[group] || strength<next[group]))next[group]=strength;
    }
#endif
    if(shown!=view.mode || pad_ready!=connected || joystick_ready!=stick){shown=view.mode;pad_ready=connected;joystick_ready=stick;lv_obj_invalidate(icons);}
    if(memcmp(next,bars,sizeof(bars))){memcpy(bars,next,sizeof(bars));if(signal_icons)lv_obj_invalidate(signal_icons);}
}
static void draw_signals(lv_event_t*e){
    lv_layer_t*l=lv_event_get_layer(e);lv_area_t a;lv_obj_get_coords(lv_event_get_target(e),&a);
    static const uint8_t order[4]={0,1,3,2}; /* L/R/J/BLE; bars retain their source indexes. */
    for(unsigned column=0;column<4;column++){
        unsigned i=order[column];
        int x=a.x1+3+column*30,y=a.y1+4;uint32_t color=i==2?0x569fa7:0xb9b9a7;
        /* L/R radio antenna marks; Bluetooth rune for generic HID link. */
        if(i!=2){lv_draw_label_dsc_t d;lv_draw_label_dsc_init(&d);d.text=i==0?"L":i==1?"R":"J";d.font=&lv_font_montserrat_12;d.color=lv_color_hex(0x7b7d93);lv_area_t label={x,y,x+9,y+d.font->line_height-1};lv_draw_label(l,&d,&label);}
        else {
            /* Complete two-loop Bluetooth rune, with room below its baseline. */
            static const int8_t points[][2]={{0,3},{8,9},{4,12},{4,0},{8,3},{0,9}};
            lv_draw_line_dsc_t d;lv_draw_line_dsc_init(&d);d.width=1;d.color=lv_color_hex(0x569fa7);
            for(unsigned n=1;n<sizeof(points)/sizeof(points[0]);n++){
                d.p1=(lv_point_precise_t){x+points[n-1][0],y+points[n-1][1]};
                d.p2=(lv_point_precise_t){x+points[n][0],y+points[n][1]};lv_draw_line(l,&d);
            }
        }
        for(unsigned n=0;n<4;n++)box(l,x+10+n*3,y+14-n*2,2,2+n*2,0,n<bars[i]?color:0x353640,0);
    }
}
void kb_route_icons_create(lv_obj_t *parent) {
    icons=lv_obj_create(parent);lv_obj_remove_style_all(icons);lv_obj_set_size(icons,116,22);
#ifdef CONFIG_LISM_XIAO147_TOUCH
    lv_obj_set_size(icons,116,22);lv_obj_set_pos(icons,200,92);
#else
    lv_obj_set_pos(icons,148,142);
#endif
    lv_obj_set_style_bg_color(icons,lv_color_black(),0);lv_obj_set_style_bg_opa(icons,LV_OPA_COVER,0);lv_obj_set_style_radius(icons,4,0);
    lv_obj_remove_flag(icons,LV_OBJ_FLAG_SCROLLABLE|LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(icons,draw,LV_EVENT_DRAW_MAIN,NULL);refresh(NULL);lv_timer_create(refresh,200,NULL);
    signal_icons=lv_obj_create(parent);lv_obj_remove_style_all(signal_icons);
#ifdef CONFIG_LISM_XIAO147_TOUCH
    lv_obj_set_size(signal_icons,124,26);lv_obj_set_pos(signal_icons,4,90);
#else
    lv_obj_set_size(signal_icons,124,26);lv_obj_set_pos(signal_icons,10,140);
#endif
    
    lv_obj_set_style_bg_color(signal_icons,lv_color_black(),0);lv_obj_set_style_bg_opa(signal_icons,LV_OPA_COVER,0);lv_obj_set_style_radius(signal_icons,3,0);
    lv_obj_remove_flag(signal_icons,LV_OBJ_FLAG_SCROLLABLE|LV_OBJ_FLAG_CLICKABLE);lv_obj_add_event_cb(signal_icons,draw_signals,LV_EVENT_DRAW_MAIN,NULL);
}
