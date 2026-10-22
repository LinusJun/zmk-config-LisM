/* All LVGL allocation, updates and destruction run on the display thread. */
#include <lvgl.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/logging/log.h>
#include "lism_screen.h"
#include "lism_input_host.h"
#include "xiao147_ui.h"
LOG_MODULE_REGISTER(lism_screen,LOG_LEVEL_INF);
static atomic_t toggles,active,generation,requested_active=ATOMIC_INIT(-1);
struct navigation {uint32_t generation;uint8_t position;};
K_MSGQ_DEFINE(screen_nav,sizeof(struct navigation),16,4);
void lism_screen_request_next(void) {
#ifdef CONFIG_LISM_XIAO147_TOUCH
    xiao147_ui_next_page();
#else
    atomic_inc(&toggles);
#endif
}
bool lism_screen_take_toggle(void) {return atomic_set(&toggles,0)&1;}
void lism_screen_request_active(bool value){atomic_set(&requested_active,value?1:0);}
bool lism_screen_take_request(bool *value){int v=atomic_set(&requested_active,-1);if(v<0)return false;*value=v!=0;return true;}
bool lism_screen_active(void) {return atomic_get(&active);}
void lism_screen_commit(bool value) {atomic_inc(&generation);atomic_set(&active,value);}
void lism_screen_nav(unsigned position) {
    struct navigation n={.generation=atomic_get(&generation),.position=position};
    k_msgq_put(&screen_nav,&n,K_NO_WAIT);
}
#define WHITE 0xffffff
#define ACCENT 0xb1e5f0
#define MUTED 0x7b7d93
static lv_obj_t *parent_screen,*root,*title,*pager,*tiles[4],*status_label;
static lv_obj_t *modal,*modal_title,*modal_text,*modal_value,*modal_hint;
static bool visible;
static bool touch_armed,touching,touch_blocked;static int touch_target=-1;static uint32_t touch_generation;static uint16_t touch_x,touch_y;
static lv_obj_t *touch_keys[5];
static uint64_t originally_hidden;
enum view {DEVICES,SEARCH};
enum dialog {NONE,DEVICE,CONNECT,FORGET};
static enum view view;
static enum dialog dialog;
static int layout=-1;
static uint8_t focus,slot,page,choice=1,pin_cursor;
static uint32_t auth_token,peer_token;
static uint8_t pin[6];
static struct lism_hid_candidate chosen;
static uint8_t chosen_index,chosen_slot;
static char peer_name[24],notice[48];
static struct lism_input_status shown;
/* Avoid reallocating label text and invalidating the same area every poll. */
static void set_text(lv_obj_t *o,const char *s) {
    if(strcmp(lv_label_get_text(o),s))lv_label_set_text(o,s);
}
static lv_obj_t *text(lv_obj_t *p,int x,int y,int w,const lv_font_t *font) {
    lv_obj_t *o=lv_label_create(p);lv_obj_set_pos(o,x,y);lv_obj_set_width(o,w);
    lv_obj_set_style_text_font(o,font,0);lv_obj_set_style_text_color(o,lv_color_hex(WHITE),0);
    lv_label_set_long_mode(o,LV_LABEL_LONG_CLIP);return o;
}
static lv_obj_t *panel(lv_obj_t *p,int x,int y,int w,int h) {
    lv_obj_t *o=lv_obj_create(p);lv_obj_set_pos(o,x,y);lv_obj_set_size(o,w,h);
    lv_obj_clear_flag(o,LV_OBJ_FLAG_SCROLLABLE);lv_obj_set_style_pad_all(o,0,0);
    lv_obj_set_style_radius(o,7,0);lv_obj_set_style_bg_color(o,lv_color_hex(0x181d23),0);
    lv_obj_set_style_bg_opa(o,LV_OPA_COVER,0);lv_obj_set_style_border_width(o,1,0);
    lv_obj_set_style_border_color(o,lv_color_hex(0x353640),0);return o;
}
static const char *peer_state(const struct lism_input_peer_status *p) {
    return p->ready?"Connected":p->connected?"Connecting...":p->saved?"Disconnected":"Add a device";
}
static bool selectable(unsigned i) {
    if(view==DEVICES)return i>=2 || shown.enabled || shown.peers[i].saved || shown.peers[i].connected;
    return i==3 || shown.candidates[page*3+i].valid;
}
static unsigned result_pages(void) {
    for(unsigned i=LISM_HID_CANDIDATES;i>0;i--)if(shown.candidates[i-1].valid)return (i-1)/3+1;
    return 1;
}
static void normalize_focus(void) {
    if(view==SEARCH)page%=result_pages();
    if(focus<4 && selectable(focus))return;
    for(unsigned i=0;i<4;i++)if(selectable(i)){focus=i;return;}
}
static void create_modal(void) {
    if(modal)return;
    modal=panel(root,4,4,312,164);lv_obj_set_style_border_color(modal,lv_color_hex(0x569fa7),0);
    modal_title=text(modal,10,4,290,&lv_font_montserrat_20);
    modal_text=text(modal,10,28,290,&lv_font_montserrat_14);lv_label_set_long_mode(modal_text,LV_LABEL_LONG_SCROLL_CIRCULAR);
    modal_value=text(modal,10,62,290,&lv_font_montserrat_20);lv_obj_set_height(modal_value,24);
    modal_hint=text(modal,10,88,290,&lv_font_montserrat_14);lv_label_set_long_mode(modal_hint,LV_LABEL_LONG_CLIP);lv_obj_set_height(modal_hint,48);
    static const char *const names[]={"Left","Right","Up","Down","OK"};
    for(unsigned i=0;i<5;i++) {
        touch_keys[i]=text(modal,4+i*60,138,56,&lv_font_montserrat_18);
        lv_label_set_text(touch_keys[i],names[i]);lv_obj_set_height(touch_keys[i],24);
        lv_obj_set_style_bg_color(touch_keys[i],lv_color_hex(0x303d46),0);
        lv_obj_set_style_bg_opa(touch_keys[i],LV_OPA_COVER,0);lv_obj_set_style_text_align(touch_keys[i],LV_TEXT_ALIGN_CENTER,0);
    }
}
static void render_modal(void) {
    char s[160];
    if(!shown.auth_type && dialog==NONE) {if(modal)lv_obj_add_flag(modal,LV_OBJ_FLAG_HIDDEN);return;}
    create_modal();lv_obj_clear_flag(modal,LV_OBJ_FLAG_HIDDEN);lv_obj_move_foreground(modal);
    lv_obj_set_height(modal_text,shown.auth_type?32:56);
    if(!shown.auth_type)set_text(modal_value,"");
    if(shown.auth_type) {
        if(auth_token!=shown.auth_token){auth_token=shown.auth_token;memset(pin,0,sizeof(pin));pin_cursor=0;choice=1;}
        set_text(modal_title,"PAIR DEVICE");
        if(shown.auth_type==LISM_INPUT_AUTH_ENTRY) {
            set_text(modal_text,"Enter the code shown by your device");
            snprintf(s,sizeof(s),"%u %u %u %u %u %u",pin[0],pin[1],pin[2],pin[3],pin[4],pin[5]);set_text(modal_value,s);
            if(pin_cursor<6)snprintf(s,sizeof(s),"Digit %u / 6\nLeft/right: digit  Up/down: value\nEnter: next",pin_cursor+1);
            else snprintf(s,sizeof(s),"%s\nLeft/right then Enter",pin_cursor==6?"> Confirm code":"> Cancel pairing");
            set_text(modal_hint,s);
        } else {
            snprintf(s,sizeof(s),"%06u",shown.passkey);set_text(modal_value,s);
            set_text(modal_text,shown.auth_type==LISM_INPUT_AUTH_DISPLAY?"Type this code on the BLE keyboard":"Does your device show this code?");
            set_text(modal_hint,shown.auth_type==LISM_INPUT_AUTH_DISPLAY?"Waiting for device...\nEnter: cancel":choice?"Connect     > Cancel\nLeft/right then Enter":"> Connect     Cancel\nLeft/right then Enter");
        }
    } else if(dialog==DEVICE) {
        if((shown.peers[slot].ready || !shown.enabled) && choice==0)choice=2;
        set_text(modal_title,"DEVICE OPTIONS");
        snprintf(s,sizeof(s),"%s\n%s",peer_name,shown.peers[slot].message[0]?shown.peers[slot].message:peer_state(&shown.peers[slot]));set_text(modal_text,s);
        if(shown.peers[slot].ready || !shown.enabled)snprintf(s,sizeof(s),"%sForget device\n%sBack",choice==1?"> ":"  ",choice==2?"> ":"  ");
        else snprintf(s,sizeof(s),"%sRetry\n%sForget device\n%sBack",choice==0?"> ":"  ",choice==1?"> ":"  ",choice==2?"> ":"  ");set_text(modal_hint,s);
    } else if(dialog==FORGET) {
        set_text(modal_title,"FORGET DEVICE?");set_text(modal_text,peer_name);
        set_text(modal_hint,choice?"Forget     > Cancel\nLeft/right then Enter":"> Forget     Cancel\nLeft/right then Enter");
    } else {
        set_text(modal_title,"CONNECT DEVICE?");
        snprintf(s,sizeof(s),"%s\n%s",chosen.name,chosen.address);set_text(modal_text,s);
        set_text(modal_hint,choice?"Connect     > Back\nLeft/right then Enter":"> Connect     Back\nLeft/right then Enter");
    }
}
static void render(void) {
    char s[128];normalize_focus();
    int next_layout=(int)view;
    if(layout!=next_layout) {
        layout=next_layout;
        for(unsigned i=0;i<4;i++) {
            int x=8,y,w=304,h;
            if(view==DEVICES) {y=i<2?26+i*50:125;w=i<2?304:148;h=i<2?48:28;if(i==3)x=164;}
            else {y=i<3?28+i*36:140;w=i<3?304:106;h=i<3?32:30;}
            lv_obj_set_pos(tiles[i],x,y);lv_obj_set_size(tiles[i],w,h);
            lv_obj_set_style_pad_top(tiles[i],view==DEVICES?(i<2?0:1):3,0);
            lv_obj_set_style_text_font(tiles[i],&lv_font_montserrat_18,0);
        }
        if(view==SEARCH){
            lv_obj_set_pos(status_label,126,142);lv_obj_set_size(status_label,186,28);
            lv_obj_set_style_text_font(status_label,&lv_font_montserrat_20,0);
        }else{
            lv_obj_set_pos(status_label,8,154);lv_obj_set_size(status_label,304,18);
            lv_obj_set_style_text_font(status_label,&lv_font_montserrat_14,0);
        }
    }
    set_text(title,view==DEVICES?"BLE DEVICES":"ADD DEVICE");
    if(view==SEARCH && result_pages()>1){snprintf(s,sizeof(s),"< %u/%u >",page+1,result_pages());set_text(pager,s);}else set_text(pager,"");
    for(unsigned i=0;i<4;i++) {
        bool show=true;
        if(view==DEVICES) {
            if(i<2){const struct lism_input_peer_status *p=&shown.peers[i];snprintf(s,sizeof(s),"%u  %s\n%s",i+1,p->name[0]?p->name:"Empty slot",shown.enabled?peer_state(p):"BLE inputs off");}
            else snprintf(s,sizeof(s),"%s",i==2?(shown.enabled?"BLE: ON":"BLE: OFF"):"Back");
        } else if(i<3) {
            const struct lism_hid_candidate *c=&shown.candidates[page*3+i];
            if(c->valid)snprintf(s,sizeof(s),"%s",c->name);
            else {snprintf(s,sizeof(s),"%s",shown.searching?"Searching...":"No devices found");show=i==0;}
        } else snprintf(s,sizeof(s),"Back");
        set_text(tiles[i],s);
        if(show)lv_obj_clear_flag(tiles[i],LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(tiles[i],LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_text_color(tiles[i],lv_color_hex(selectable(i)?WHITE:MUTED),0);
        lv_obj_set_style_border_width(tiles[i],focus==i?2:1,0);
        lv_obj_set_style_border_color(tiles[i],lv_color_hex(focus==i?ACCENT:0x353640),0);
    }
    const char *hint=view==SEARCH?(result_pages()>1?"Left/right: more devices":"Select a device"):(shown.enabled?"Select a slot to add / manage":"Switch BLE on to add devices");
    if(notice[0])hint=notice;
    else if(view==SEARCH && shown.searching){snprintf(s,sizeof(s),"Search: %us",shown.seconds_left);hint=s;}
    else if(view==SEARCH && !shown.searching)hint="Search ended";
    else if(strstr(shown.message,"fail") || strstr(shown.message,"timeout") || strstr(shown.message,"paused"))hint=shown.message;
    set_text(status_label,dialog!=NONE || shown.auth_type?"":hint);render_modal();
}
static void start_search(unsigned target) {
    slot=target;page=0;focus=3;view=SEARCH;dialog=NONE;notice[0]=0;
    LOG_INF("XIAO BLE search requested slot=%u ui=%u",slot,(unsigned)atomic_get(&generation));
    lism_input_host_search(slot,false); /* Preserve HID-only default. */
}
static bool same_peer(void) {
    struct lism_input_status latest;lism_input_host_snapshot(&latest);
    return latest.peers[slot].legacy_token==peer_token && !strcmp(latest.peers[slot].name,peer_name) &&
        (latest.peers[slot].saved || latest.peers[slot].connected);
}
static void action(unsigned position) {
    touch_armed=false;
    bool enter=position==37;bool direction=position>=42 && position<=45;
    if(shown.auth_type) {
        if(shown.auth_type==LISM_INPUT_AUTH_ENTRY) {
            if(position==43)pin_cursor=(pin_cursor+1)%8;
            if(position==45)pin_cursor=(pin_cursor+7)%8;
            if(pin_cursor<6 && (position==42 || position==44))pin[pin_cursor]=(pin[pin_cursor]+(position==42?1:9))%10;
            if(enter){if(pin_cursor<6)pin_cursor++;else {unsigned value=0;for(unsigned i=0;i<6;i++)value=value*10+pin[i];lism_input_host_auth(shown.auth_token,pin_cursor==6,value);}}
        } else if(shown.auth_type==LISM_INPUT_AUTH_DISPLAY) {if(enter)lism_input_host_auth(shown.auth_token,false,0);}
        else {if(direction)choice=!choice;if(enter)lism_input_host_auth(shown.auth_token,!choice,0);}
        return;
    }
    if(dialog!=NONE) {
        if(dialog==DEVICE) {
            struct lism_input_status latest;lism_input_host_snapshot(&latest);
            if((latest.peers[slot].ready || !latest.enabled) && choice==0)choice=2;
            if(direction) {
                do {choice=(choice+((position==42 || position==45)?2:1))%3;}
                while((shown.peers[slot].ready || !shown.enabled) && choice==0);
            }
            if(enter) {
                if(choice==2)dialog=NONE;
                else if(!same_peer()){dialog=NONE;snprintf(notice,sizeof(notice),"Device changed; select again");}
                else if(!choice){lism_input_host_retry(slot);dialog=NONE;}
                else {dialog=FORGET;choice=1;}
            }
        } else {
            if(direction)choice=!choice;
            if(enter) {
                if(!choice && dialog==FORGET && same_peer())lism_input_host_forget(slot);
                if(!choice && dialog==CONNECT) {
                    struct lism_input_status latest;lism_input_host_snapshot(&latest);
                    const struct lism_hid_candidate *c=&latest.candidates[chosen_index];
                    if(latest.searching && latest.selected==chosen_slot && c->valid && c->token==chosen.token && !strcmp(c->address,chosen.address)) {
                        lism_input_host_select(chosen_index,chosen.token);view=DEVICES;focus=chosen_slot;
                    } else snprintf(notice,sizeof(notice),"Selection expired; search again");
                }
                dialog=NONE;
            }
        }
        return;
    }
    notice[0]=0;
    if(view==SEARCH && focus<3 && (position==43 || position==45)){unsigned pages=result_pages();page=(page+(position==43?1:pages-1))%pages;normalize_focus();return;}
    if(direction) {
        for(unsigned n=0;n<4;n++){focus=(focus+((position==42 || position==45)?3:1))%4;if(selectable(focus))break;}
    }
    if(!enter)return;
    if(view==SEARCH) {
        if(focus==3){lism_input_host_cancel();view=DEVICES;focus=slot;}
        else if(shown.candidates[page*3+focus].valid) {
            chosen_index=page*3+focus;chosen=shown.candidates[chosen_index];chosen_slot=slot;dialog=CONNECT;choice=1;
        }
    } else if(focus<2) {
        slot=focus;
        if(!shown.peers[slot].saved && !shown.peers[slot].connected){if(shown.enabled)start_search(slot);}
        else {peer_token=shown.peers[slot].legacy_token;snprintf(peer_name,sizeof(peer_name),"%s",shown.peers[slot].name);dialog=DEVICE;choice=2;}
    } else if(focus==2) {
        lism_input_host_set_enabled(!shown.enabled);
    } else lism_screen_request_next();
}
static void create_view(void) {
    root=panel(parent_screen,0,0,320,172);lv_obj_set_style_radius(root,0,0);lv_obj_set_style_border_width(root,0,0);
    lv_obj_set_style_bg_color(root,lv_color_hex(0),0);
    title=text(root,8,2,220,&lv_font_montserrat_20);lv_obj_set_style_text_color(title,lv_color_hex(ACCENT),0);
    pager=text(root,224,6,90,&lv_font_montserrat_14);
    for(unsigned i=0;i<4;i++) {
        tiles[i]=lv_label_create(root);lv_label_set_long_mode(tiles[i],LV_LABEL_LONG_CLIP);
        lv_obj_set_style_bg_color(tiles[i],lv_color_hex(0x181d23),0);lv_obj_set_style_bg_opa(tiles[i],LV_OPA_COVER,0);
        lv_obj_set_style_radius(tiles[i],7,0);lv_obj_set_style_text_font(tiles[i],&lv_font_montserrat_18,0);
        lv_obj_set_style_pad_hor(tiles[i],10,0);lv_obj_set_style_text_line_space(tiles[i],2,0);
    }
    status_label=text(root,8,154,304,&lv_font_montserrat_14);layout=-1;
}
static void set_home_visibility(bool hide) {
    for(unsigned i=0;i<lv_obj_get_child_count(parent_screen) && i<64;i++) {
        lv_obj_t *child=lv_obj_get_child(parent_screen,i);if(child==root)continue;
        if(hide){if(lv_obj_has_flag(child,LV_OBJ_FLAG_HIDDEN))originally_hidden|=UINT64_C(1)<<i;else originally_hidden&=~(UINT64_C(1)<<i);lv_obj_add_flag(child,LV_OBJ_FLAG_HIDDEN);}
        else if(!(originally_hidden&(UINT64_C(1)<<i)))lv_obj_clear_flag(child,LV_OBJ_FLAG_HIDDEN);
    }
}
static void poll(lv_timer_t *timer) {
    ARG_UNUSED(timer);bool value=lism_screen_active(),dirty=false;
    if(value!=visible) {
        touch_armed=false;visible=value;dialog=NONE;auth_token=0;focus=0;page=0;view=DEVICES;notice[0]=0;dirty=true;
        if(visible){set_home_visibility(true);create_view();lism_input_host_snapshot(&shown);}
        else {
            set_home_visibility(false);lv_obj_delete(root);root=NULL;modal=NULL;
            lism_input_host_cancel();
        }
        lv_mem_monitor_t m;lv_mem_monitor(&m);LOG_INF("UI page=%u LVGL used_pct=%u peak_bytes=%u",visible,m.used_pct,(unsigned)m.max_used);
    }
    struct navigation n;
    for(unsigned budget=0;budget<16 && !k_msgq_get(&screen_nav,&n,K_NO_WAIT);budget++) {
        if(visible && n.generation==(uint32_t)atomic_get(&generation)){action(n.position);dirty=true;}
    }
    if(visible) {
        struct lism_input_status next;lism_input_host_snapshot(&next);
        if(memcmp(&next,&shown,sizeof(shown))){shown=next;dirty=true;}
        if(dirty)render();
    }
}
void lism_screen_create(lv_obj_t *screen) {
    /* Home has no hidden device widgets. The sole timer survives page closes. */
    parent_screen=screen;lv_timer_create(poll,100,NULL);
}

uint32_t lism_screen_generation(void){return atomic_get(&generation);}

/* Called only by the XIAO display-thread controller. */
void lism_screen_present(void) {poll(NULL);} /* Reflect input-thread commit, never bypass its release barrier. */
static bool inside(lv_obj_t *o,uint16_t x,uint16_t y) {
    if(!o || lv_obj_has_flag(o,LV_OBJ_FLAG_HIDDEN))return false;
    lv_area_t a;lv_obj_get_coords(o,&a);return x>=a.x1 && x<=a.x2 && y>=a.y1 && y<=a.y2;
}
static int touch_at(uint16_t x,uint16_t y) {
    if(!visible || !root)return -1;
    if(dialog!=NONE || shown.auth_type) {for(unsigned i=0;i<5;i++)if(inside(touch_keys[i],x,y))return 10+i;return -1;}
    if(view==SEARCH && result_pages()>1 && inside(pager,x,y))return x<276?20:21;
    for(unsigned i=0;i<4;i++)if(inside(tiles[i],x,y) && selectable(i))return i;
    return -1;
}
/* Capture only the identity and action being touched. Countdown/RSSI/name
 * completion on unrelated rows must not cancel a tap, but replacement peers,
 * new auth prompts, changed menus and page generations must remain inert. */
struct touch_context {
    uint32_t generation,auth_token,identity_token;
    uint8_t target,view,dialog,page,slot,choice,pin_cursor,auth_type,selected;
    bool enabled,saved,connected,ready,valid,searching;
    char identity[24];
};
static struct touch_context pressed_context;
static struct touch_context touch_context_get(unsigned target,const struct lism_input_status *state){
    struct touch_context c;memset(&c,0,sizeof(c));
    c.generation=atomic_get(&generation);c.target=target;c.view=view;c.dialog=dialog;
    c.page=page;c.slot=slot;c.choice=choice;c.pin_cursor=pin_cursor;
    c.auth_type=state->auth_type;c.auth_token=state->auth_type?state->auth_token:0;
    if(state->auth_type)return c;
    int peer=-1,candidate=-1;
    if(dialog==DEVICE || dialog==FORGET)peer=slot;
    else if(dialog==CONNECT)candidate=chosen_index;
    else if(target<4){
        if(view==DEVICES){if(target<2)peer=target;else if(target==2)c.enabled=state->enabled;}
        else if(target<3)candidate=page*3+target;
    }
    if(peer>=0 && peer<LISM_INPUT_PEERS){
        const struct lism_input_peer_status*p=&state->peers[peer];
        c.enabled=state->enabled;c.saved=p->saved;c.connected=p->connected;c.ready=p->ready;
        c.identity_token=p->legacy_token;snprintf(c.identity,sizeof(c.identity),"%s",p->name);
    }
    if(candidate>=0 && candidate<LISM_HID_CANDIDATES){
        const struct lism_hid_candidate*p=&state->candidates[candidate];
        c.selected=state->selected;c.valid=p->valid;c.searching=state->searching;
        c.identity_token=p->token;snprintf(c.identity,sizeof(c.identity),"%s",p->address);
    }
    return c;
}
void lism_screen_touch(uint8_t fingers,uint16_t x,uint16_t y) {
    if(!visible || fingers>1){touch_armed=false;touching=fingers!=0;touch_blocked=fingers!=0;return;}
    if(fingers==1) {
        int target=touch_at(x,y);
        if(!touching){touch_target=target;touch_x=x;touch_y=y;touch_generation=atomic_get(&generation);touch_armed=target>=0 && !touch_blocked;if(touch_armed)pressed_context=touch_context_get(target,&shown);}
        else if(touch_armed && (target!=touch_target || abs((int)x-touch_x)>12 || abs((int)y-touch_y)>12))touch_armed=false;
        touching=true;
        return;
    }
    touching=false;if(touch_blocked){touch_blocked=false;touch_armed=false;return;}
    if(!touch_armed)return;
    int target=touch_target;touch_armed=false;
    if(touch_generation!=(uint32_t)atomic_get(&generation) || !visible)return;
    struct lism_input_status latest;lism_input_host_snapshot(&latest);
    struct touch_context current=touch_context_get(target,&latest);
    if(memcmp(&current,&pressed_context,sizeof(current)))return;
    shown=latest; /* Dispatch against fresh state after identity validation. */
    if(target<4){focus=target;action(37);}
    else if(target<15){static const unsigned actions[]={45,43,42,44,37};action(actions[target-10]);}
    else action(target==20?45:43);
    render();
}
