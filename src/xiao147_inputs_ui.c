#include "xiao147_inputs_ui.h"
#include "lism_input_host.h"
#include "xiao147_operator_colors.h"
#if defined(CONFIG_LISM_TWO_KEYBOARD_SETS)
#include "lism_split_sets.h"
#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
#include "lism_split_auto.h"
#endif
#endif
#include <stdio.h>
#include <string.h>
#include <zephyr/kernel.h>

LV_FONT_DECLARE(XiaoMenu_Bold_12);
#define BUTTONS 13
#define ROWS 3
#if defined(CONFIG_LISM_INPUT_AUTO_PAIR) && CONFIG_LISM_INPUT_AUTO_PAIR
#define INPUT_AUTO_PAIR 1
#else
#define INPUT_AUTO_PAIR 0
#endif
enum action { NONE, TAB_INPUT, TAB_SET, SLOT, SEARCH, RETRY, FORGET,
    FILTER, MORE, PICK, SET_SLOT, SET_BEGIN, SET_CANCEL, SET_PICK,
    LEGACY, LEGACY_ALLOW, LEGACY_CANCEL,
    AUTH_ACCEPT, AUTH_CANCEL, DIGIT, BACK, ENTER };
struct command { int16_t x,y,w,h; uint8_t action, value; uint32_t token; };
static lv_obj_t *group,*title,*message,*hint,*buttons[BUTTONS],*labels[BUTTONS],*icons[2];
static struct command commands[BUTTONS],pressed;
static struct lism_input_status drawn;
static bool portrait,sets,show_other,press_valid,forget_armed;
static uint8_t selected,page,set_slot;
static uint32_t forget_until,auth_token,pin;
static uint8_t digits;
static char pressed_peer_name[24],forget_peer_name[24];
static uint8_t forget_slot;
static bool legacy_armed;
static uint8_t legacy_slot;
static uint32_t legacy_token,legacy_until;
static int8_t focused=-1;
static struct command focus_target,previous_commands[BUTTONS];
static uint32_t focus_serial,focus_sequence;
static uint32_t context_sequence;
static void (*context_changed_callback)(void);
static bool previous_valid;
struct focus_context {
    uint32_t auth_token,passkey;
    uint32_t legacy_token;
    uint8_t selected,page,set_slot,auth_type,auth_slot;
    uint8_t legacy_slot;
    bool portrait,sets,show_other,searching,forget_armed,legacy_armed;
    struct {bool saved,connected,ready,legacy_available,legacy_allowed;
        uint32_t legacy_token;char name[24];} peers[2];
};
static struct focus_context previous_context;
#if defined(CONFIG_LISM_TWO_KEYBOARD_SETS)
static bool enrolling;
#endif
static bool same_command(const struct command *a,const struct command *b) {
    return a->action==b->action && a->value==b->value && a->token==b->token;
}
void xiao147_inputs_focus_cancel_pending(void) {focus_serial=0;}
void xiao147_inputs_focus_set_context_callback(void (*changed)(void)) {
    context_changed_callback=changed;
}
static bool visual_before(unsigned a,unsigned b) {
    return commands[a].y<commands[b].y ||
        (commands[a].y==commands[b].y && commands[a].x<commands[b].x);
}
static int first_focus(void) {
    int first=-1;
    for(unsigned i=0;i<BUTTONS;i++)if(commands[i].action &&
        (first<0 || visual_before(i,first)))first=i;
    return first;
}
static void focus_style(void) {
    for(unsigned i=0;i<BUTTONS;i++) {
        bool active=focused==(int)i && commands[i].action;
        lv_obj_set_style_border_width(buttons[i],active?2:1,LV_PART_MAIN);
        lv_obj_set_style_border_color(buttons[i],lv_color_hex(active?0xffffff:XIAO147_ACCENT_BLUE),LV_PART_MAIN);
        lv_obj_set_style_bg_color(buttons[i],lv_color_hex(active?0x355570:0x1a2432),LV_PART_MAIN);
    }
}
static void focus_finish(void) {
    struct focus_context context;
    memset(&context,0,sizeof(context));
    context.auth_token=drawn.auth_token;context.passkey=drawn.passkey;
    context.selected=selected;context.page=page;context.set_slot=set_slot;
    context.auth_type=drawn.auth_type;context.auth_slot=drawn.auth_slot;
    context.portrait=portrait;context.sets=sets;context.show_other=show_other;
    context.searching=drawn.searching;context.forget_armed=forget_armed;
    context.legacy_armed=legacy_armed;context.legacy_slot=legacy_slot;context.legacy_token=legacy_token;
    for(unsigned i=0;i<2;i++) {
        context.peers[i].saved=drawn.peers[i].saved;
        context.peers[i].connected=drawn.peers[i].connected;
        context.peers[i].ready=drawn.peers[i].ready;
        context.peers[i].legacy_available=drawn.peers[i].legacy_available;
        context.peers[i].legacy_allowed=drawn.peers[i].legacy_allowed;
        context.peers[i].legacy_token=drawn.peers[i].legacy_token;
        memcpy(context.peers[i].name,drawn.peers[i].name,sizeof(context.peers[i].name));
    }
    bool changed=!previous_valid || memcmp(&context,&previous_context,sizeof(context));
    for(unsigned i=0;i<BUTTONS;i++) {
        const struct command *a=&commands[i],*b=&previous_commands[i];
        if(!same_command(a,b) || a->x!=b->x || a->y!=b->y || a->w!=b->w || a->h!=b->h)changed=true;
    }
    if(changed)xiao147_inputs_focus_cancel_pending();
    previous_context=context;memcpy(previous_commands,commands,sizeof(commands));previous_valid=true;
    int preserved=-1;
    for(unsigned i=0;i<BUTTONS;i++)if(commands[i].action && same_command(&focus_target,&commands[i])) {preserved=i;break;}
    focused=preserved>=0?preserved:first_focus();
    if(focused>=0)focus_target=commands[focused];else memset(&focus_target,0,sizeof(focus_target));
    focus_style();
    if(changed) {
        if(++context_sequence==0)++context_sequence;
        /* All widgets, context snapshots and focus are now consistent. The
         * owner may invalidate queued physical-button gestures, not re-enter. */
        if(context_changed_callback)context_changed_callback();
    }
}
static bool contains(const struct command *c,uint16_t x,uint16_t y) {
    return c->action && x>=c->x && x<c->x+c->w && y>=c->y && y<c->y+c->h;
}
static lv_obj_t *label(lv_obj_t *parent) {
    lv_obj_t *o=lv_label_create(parent);
    lv_obj_set_style_text_font(o,&XiaoMenu_Bold_12,LV_PART_MAIN);
    lv_obj_set_style_text_color(o,lv_color_hex(0xffffff),LV_PART_MAIN);
    lv_label_set_long_mode(o,LV_LABEL_LONG_CLIP);return o;
}
static void text(lv_obj_t *o,int x,int y,int w,int h,const char *s) {
    lv_obj_set_pos(o,x,y);lv_obj_set_size(o,w,h);lv_label_set_text(o,s);
}
static void button(unsigned i,int x,int y,int w,int h,const char *s,
    enum action action,unsigned value,uint32_t token) {
    if(i>=BUTTONS)return;
    commands[i]=(struct command){x,y,w,h,action,value,token};
    lv_obj_set_pos(buttons[i],x,y);lv_obj_set_size(buttons[i],w,h);
    lv_obj_clear_flag(buttons[i],LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_width(labels[i],w-4);lv_label_set_text(labels[i],s);lv_obj_center(labels[i]);
}
lv_obj_t *xiao147_inputs_init(lv_obj_t *parent) {
    focused=-1;previous_valid=false;focus_serial=0;memset(&focus_target,0,sizeof(focus_target));
    legacy_armed=false;legacy_token=legacy_until=0;
    group=lv_obj_create(parent);lv_obj_remove_style_all(group);
    lv_obj_set_pos(group,0,0);lv_obj_clear_flag(group,LV_OBJ_FLAG_SCROLLABLE);
    title=label(group);message=label(group);hint=label(group);
    lv_obj_set_style_text_color(hint,lv_color_hex(0x8295ae),LV_PART_MAIN);
    for(unsigned i=0;i<BUTTONS;i++) {
        buttons[i]=lv_obj_create(group);lv_obj_remove_style_all(buttons[i]);
        lv_obj_set_style_bg_color(buttons[i],lv_color_hex(0x1a2432),LV_PART_MAIN);
        lv_obj_set_style_bg_opa(buttons[i],LV_OPA_COVER,LV_PART_MAIN);
        lv_obj_set_style_radius(buttons[i],3,LV_PART_MAIN);
        lv_obj_set_style_border_color(buttons[i],lv_color_hex(XIAO147_ACCENT_BLUE),LV_PART_MAIN);
        lv_obj_set_style_border_width(buttons[i],1,LV_PART_MAIN);
        lv_obj_clear_flag(buttons[i],LV_OBJ_FLAG_SCROLLABLE|LV_OBJ_FLAG_CLICKABLE);
        labels[i]=label(buttons[i]);
        lv_obj_set_style_text_align(labels[i],LV_TEXT_ALIGN_CENTER,LV_PART_MAIN);
    }
    lv_obj_add_flag(group,LV_OBJ_FLAG_HIDDEN);return group;
}
static void auth_draw(int w) {
    char buf[72];
    if(auth_token!=drawn.auth_token) {auth_token=drawn.auth_token;pin=0;digits=0;press_valid=false;}
    text(title,6,3,w-12,18,"Bluetooth pairing");
    if(drawn.auth_type==LISM_INPUT_AUTH_ENTRY) {
        snprintf(buf,sizeof(buf),"PIN: %0*u",digits?digits:1,(unsigned)pin);
        text(message,6,portrait?33:25,w-92,24,buf);
        button(12,w-80,portrait?33:25,74,22,"Cancel",AUTH_CANCEL,0,auth_token);
        int top=portrait?70:53, h=portrait?44:24,gap=4,bw=(w-20)/3;
        for(unsigned n=0;n<12;n++) {
            const char *s=n==9?"Back":n==10?"0":n==11?"Pair":NULL;
            char digit[2]={(char)('1'+n),0};
            button(n,6+(n%3)*(bw+4),top+(n/3)*(h+gap),bw,h,s?s:digit,
                n==9?BACK:n==11?ENTER:DIGIT,n>=9?0:n+1,auth_token);
        }
        text(hint,6,portrait?270:162,w-12,16,"Enter code, then Pair");
    } else {
        snprintf(buf,sizeof(buf),"%s %06u\nOn: %.23s",drawn.auth_type==LISM_INPUT_AUTH_DISPLAY?
            "Type on keyboard:":"Confirm code:",(unsigned)drawn.passkey,
            drawn.auth_slot<LISM_INPUT_PEERS?drawn.peers[drawn.auth_slot].name:"BLE input");
        text(message,6,portrait?48:35,w-12,48,buf);
        if(drawn.auth_type==LISM_INPUT_AUTH_CONFIRM)
            button(0,6,portrait?145:95,(w-18)/2,32,"Confirm",AUTH_ACCEPT,0,auth_token);
        button(1,drawn.auth_type==LISM_INPUT_AUTH_CONFIRM?w/2+3:6,portrait?145:95,
            drawn.auth_type==LISM_INPUT_AUTH_CONFIRM?(w-18)/2:w-12,32,"Cancel",AUTH_CANCEL,0,auth_token);
        text(hint,6,portrait?250:145,w-12,24,"Check code before confirming");
    }
}
static void legacy_draw(int w) {
    char buf[144];
    text(title,6,3,w-12,18,"Legacy BLE pairing");
    snprintf(buf,sizeof(buf),portrait?
        "Allow older pairing for:\n%.23s\nWeaker security.\nOther devices\nstay SC only.":
        "Allow older pairing for:\n%.23s\nWeaker pairing security.\nOther devices stay SC only.",drawn.peers[legacy_slot].name);
    text(message,6,portrait?48:28,w-12,portrait?110:72,buf);
    /* Cancel is the first physical-button focus. A second independent
     * action is required to authorize the captured device token. */
    button(0,6,portrait?190:110,(w-18)/2,32,"Cancel",LEGACY_CANCEL,legacy_slot,legacy_token);
    button(1,w/2+3,portrait?190:110,(w-18)/2,32,"Allow",LEGACY_ALLOW,legacy_slot,legacy_token);
    text(hint,6,portrait?256:150,w-12,portrait?42:18,"No saved pairings erased");
}
void xiao147_inputs_draw(bool p) {
    if(!group)return;
    if(portrait!=p)press_valid=false;
    portrait=p;int w=p?172:320;
    lv_obj_set_size(group,w,p?320:172);
    memset(commands,0,sizeof(commands));
    for(unsigned i=0;i<BUTTONS;i++)lv_obj_add_flag(buttons[i],LV_OBJ_FLAG_HIDDEN);
    lism_input_host_snapshot(&drawn);
    if(drawn.auth_type!=LISM_INPUT_AUTH_NONE) {legacy_armed=false;auth_draw(w);focus_finish();return;}
    auth_token=0;pin=0;digits=0;
    if(legacy_armed) {
        const struct lism_input_peer_status *peer=&drawn.peers[legacy_slot];
        if(!legacy_token || peer->legacy_token!=legacy_token || !peer->legacy_available ||
            peer->legacy_allowed || (int32_t)(legacy_until-k_uptime_get_32())<=0)legacy_armed=false;
        else {legacy_draw(w);focus_finish();return;}
    }
    text(title,6,2,w-12,17,"BLE Inputs");
    button(0,6,21,(w-18)/2,18,sets?"Inputs":"[Inputs]",TAB_INPUT,0,0);
#if defined(CONFIG_LISM_TWO_KEYBOARD_SETS)
    button(1,w/2+3,21,(w-18)/2,18,sets?"[LisM sets]":"LisM sets",TAB_SET,0,0);
    if(sets) {
        static const char *const names[]={"A-L","A-R","JP19","Circle","B-L","B-R"};
        int bw=(w-22)/3;
        for(unsigned i=0;i<6;i++) {
            char s[16];snprintf(s,sizeof(s),"%s%s",i==set_slot?">":"",names[i]);
            button(2+i,6+(i%3)*(bw+5),44+(i/3)*22,bw,20,s,SET_SLOT,i,0);
        }
        char status[64];lism_split_sets_get_status(status,sizeof(status));
        text(message,6,90,w-12,p?28:15,status);
        bool cancellable=enrolling;
#if defined(CONFIG_LISM_AUTO_SPLIT_SETS)
        cancellable |= lism_split_auto_pending();
#endif
        button(8,6,p?122:108,(w-18)/2,22,cancellable?"Cancel":"Register",cancellable?SET_CANCEL:SET_BEGIN,set_slot,0);
        button(9,w/2+3,p?122:108,(w-18)/2,22,"Next device",MORE,0,0);
        struct lism_split_set_candidate candidates[LISM_SPLIT_SET_CANDIDATES];
        size_t count=lism_split_sets_candidates(candidates,LISM_SPLIT_SET_CANDIDATES);
        if(count) {
            unsigned index=page%count;
            char s[64];snprintf(s,sizeof(s),"%.31s (%ddBm)",candidates[index].name,candidates[index].rssi);
            button(10,6,p?154:134,w-12,p?48:22,s,SET_PICK,index,candidates[index].token);
        }
        text(hint,6,p?265:158,w-12,p?42:14,p?"USR1: next\nDouble: select\nOther halves OFF":"USR1: next/double select; others OFF");
        focus_finish();return;
    }
#endif
    for(unsigned i=0;i<2;i++) {
        char s[40];snprintf(s,sizeof(s),"%c%u %.23s",selected==i?'>':' ',i+1,
            drawn.peers[i].name[0]?drawn.peers[i].name:"Empty");
        button(2+i,6+i*(w/2-3),44,(w-18)/2,p?28:20,s,SLOT,i,0);
    }
    char s[72];
    snprintf(s,sizeof(s),"%.47s",drawn.searching?drawn.message:
        drawn.peers[selected].message[0]?drawn.peers[selected].message:drawn.message);
    text(message,6,p?77:66,w-12,p?29:14,s);
    bool armed=forget_armed && (int32_t)(forget_until-k_uptime_get_32())>0;
    const char *controls[]={drawn.searching?"Cancel":"Search","Retry",armed?"Confirm forget":"Forget",
        show_other?"All BLE":"HID only","More"};
    const enum action actions[]={SEARCH,RETRY,FORGET,FILTER,MORE};
    for(unsigned i=0;i<5;i++) {
        int bw=p?(w-16)/2:(w-32)/(INPUT_AUTO_PAIR?5:6);
        button(4+i,6+(p?i%2:i)*(bw+4),(p?111:83)+(p?i/2:0)*24,bw,21,controls[i],actions[i],selected,0);
    }
    if (!INPUT_AUTO_PAIR) {
        const struct lism_input_peer_status *peer=&drawn.peers[selected];
        int legacy_bw=p?(w-16)/2:(w-32)/6;
        button(12,6+(p?1:5)*(legacy_bw+4),p?159:83,legacy_bw,21,
            peer->legacy_allowed?"SC only":peer->legacy_available?"Legacy":"SC only",
            peer->legacy_allowed || peer->legacy_available?LEGACY:NONE,selected,peer->legacy_token);
    }
    int top=p?187:108,stride=p?32:17,height=p?29:16;
    for(unsigned row=0;row<ROWS;row++) {
        unsigned index=page*ROWS+row;
        const struct lism_hid_candidate *c=&drawn.candidates[index];
        snprintf(s,sizeof(s),p?"%.23s\n%.17s":"%.23s %.17s",
            c->name[0]?c->name:"Unnamed",c->address);
        button(9+row,6,top+row*stride,w-12,height,c->valid?s:"--",c->valid&&drawn.searching?PICK:NONE,index,c->token);
    }
    text(hint,6,p?289:160,w-12,p?28:12,p?"USR1: next\nDouble: select":"USR1: next / double: select");
    focus_finish();
}
void xiao147_inputs_press(uint16_t x,uint16_t y) {
    xiao147_inputs_focus_cancel_pending();
    press_valid=false;
    for(unsigned i=0;i<BUTTONS;i++)if(contains(&commands[i],x,y)) {
        pressed=commands[i];press_valid=true;
        focused=i;focus_target=commands[i];focus_style();
        memcpy(pressed_peer_name,drawn.peers[selected].name,sizeof(pressed_peer_name));break;
    }
}
void xiao147_inputs_cancel(void) {
    xiao147_inputs_focus_cancel_pending();
    press_valid=false;forget_armed=false;legacy_armed=false;pin=0;digits=0;
    struct lism_input_status now;lism_input_host_snapshot(&now);
    if(now.auth_type!=LISM_INPUT_AUTH_NONE)lism_input_host_auth(now.auth_token,false,0);
#if defined(CONFIG_LISM_TWO_KEYBOARD_SETS)
    if(enrolling) {lism_split_sets_register_cancel();enrolling=false;}
#endif
}
void xiao147_inputs_release(uint16_t x,uint16_t y) {
    xiao147_inputs_focus_cancel_pending();
    if(!press_valid)return;
    press_valid=false;
    bool match=false;
    for(unsigned i=0;i<BUTTONS;i++)if(contains(&commands[i],x,y) &&
        commands[i].action==pressed.action && commands[i].value==pressed.value &&
        commands[i].token==pressed.token)match=true;
    if(!match)return;
    struct lism_input_status now;lism_input_host_snapshot(&now);
    enum action a=pressed.action;unsigned v=pressed.value;
    if(a==LEGACY_ALLOW || a==LEGACY_CANCEL) {
        if(!legacy_armed || v>=LISM_INPUT_PEERS || v!=legacy_slot || pressed.token!=legacy_token ||
            now.auth_type!=LISM_INPUT_AUTH_NONE || now.peers[v].legacy_token!=legacy_token ||
            !now.peers[v].legacy_available || now.peers[v].legacy_allowed ||
            (int32_t)(legacy_until-k_uptime_get_32())<=0) {legacy_armed=false;return;}
        legacy_armed=false;
        if(a==LEGACY_ALLOW)lism_input_host_legacy(v,pressed.token,true);
        xiao147_inputs_draw(portrait);return;
    }
    if(a>=AUTH_ACCEPT) {
        if(now.auth_token!=pressed.token || now.auth_type==LISM_INPUT_AUTH_NONE)return;
        if(a==AUTH_CANCEL)lism_input_host_auth(pressed.token,false,0);
        else if(a==AUTH_ACCEPT && now.auth_type==LISM_INPUT_AUTH_CONFIRM)lism_input_host_auth(pressed.token,true,0);
        else if(now.auth_type==LISM_INPUT_AUTH_ENTRY) {
            if(a==DIGIT && digits<6) {pin=pin*10+v;digits++;}
            else if(a==BACK && digits) {pin/=10;digits--;}
            else if(a==ENTER && digits) {lism_input_host_auth(pressed.token,true,pin);pin=0;digits=0;}
        }
        xiao147_inputs_draw(portrait);return;
    }
    if(now.auth_type!=LISM_INPUT_AUTH_NONE)return;
    if(a==TAB_INPUT || a==TAB_SET) {
        sets=a==TAB_SET;page=0;forget_armed=false;
        if(sets && now.searching)lism_input_host_cancel();
#if defined(CONFIG_LISM_TWO_KEYBOARD_SETS)
        if(!sets && enrolling) {lism_split_sets_register_cancel();enrolling=false;}
#endif
    }
    else if(a==SLOT && v<2) {selected=v;forget_armed=false;page=0;}
    else if(a==SEARCH) {forget_armed=false;if(now.searching)lism_input_host_cancel();else lism_input_host_search(selected,show_other);}
    else if(a==RETRY)lism_input_host_retry(selected);
    else if(a==LEGACY && v<LISM_INPUT_PEERS && pressed.token && now.peers[v].legacy_token==pressed.token) {
        forget_armed=false;
        if(now.peers[v].legacy_allowed)lism_input_host_legacy(v,pressed.token,false);
        else if(now.peers[v].legacy_available) {
            legacy_slot=v;legacy_token=pressed.token;legacy_until=k_uptime_get_32()+20000;legacy_armed=true;
        }
    }
    else if(a==FORGET) {
        if(strcmp(now.peers[selected].name,pressed_peer_name))return;
        uint32_t tick=k_uptime_get_32();
        if(forget_armed && forget_slot==selected && !strcmp(forget_peer_name,pressed_peer_name) &&
            (int32_t)(forget_until-tick)>0) {forget_armed=false;lism_input_host_forget(selected);}
        else {forget_armed=true;forget_until=tick+5000;forget_slot=selected;
            memcpy(forget_peer_name,pressed_peer_name,sizeof(forget_peer_name));}
    } else if(a==FILTER) {show_other=!show_other;page=0;if(now.searching)lism_input_host_search(selected,show_other);}
    else if(a==MORE)page=(page+1)%(sets?8:LISM_HID_CANDIDATES/ROWS);
    else if(a==PICK && v<LISM_HID_CANDIDATES && now.searching && now.candidates[v].valid &&
        now.candidates[v].token==pressed.token && pressed.token)lism_input_host_select(v,pressed.token);
#if defined(CONFIG_LISM_TWO_KEYBOARD_SETS)
    else if(a==SET_SLOT && v<LISM_SPLIT_SET_SLOTS) {
        if(enrolling && set_slot!=v) {lism_split_sets_register_cancel();enrolling=false;}
        set_slot=v;page=0;
    }
    else if(a==SET_BEGIN) {enrolling=lism_split_sets_register_begin(set_slot)==0;page=0;}
    else if(a==SET_CANCEL) {lism_split_sets_register_cancel();enrolling=false;}
    else if(a==SET_PICK && pressed.token) {
        struct lism_split_set_candidate c[LISM_SPLIT_SET_CANDIDATES];
        size_t n=lism_split_sets_candidates(c,LISM_SPLIT_SET_CANDIDATES);
        for(unsigned i=0;i<n;i++)if(c[i].token==pressed.token) {
            if(lism_split_sets_register_select(pressed.token)==0)enrolling=false;
            break;
        }
    }
#endif
    xiao147_inputs_draw(portrait);
}

uint32_t xiao147_inputs_focus_capture(void) {
    if(!group)return 0;
    uint32_t before=context_sequence;
    xiao147_inputs_draw(portrait);
    if(before!=context_sequence)return 0;
    if(focused<0 || !commands[focused].action)return 0;
    if(++focus_sequence==0)++focus_sequence;
    focus_serial=focus_sequence;return focus_serial;
}
void xiao147_inputs_focus_next(void) {
    if(!group)return;
    uint32_t before=context_sequence;
    xiao147_inputs_focus_cancel_pending();xiao147_inputs_draw(portrait);
    if(before!=context_sequence)return;
    int next=-1;
    for(unsigned i=0;i<BUTTONS;i++)if(commands[i].action &&
        (focused<0 || visual_before(focused,i)) && (next<0 || visual_before(i,next)))next=i;
    focused=next>=0?next:first_focus();
    if(focused>=0)focus_target=commands[focused];
    focus_style();
}
void xiao147_inputs_focus_activate(uint32_t serial) {
    if(!serial || !group || serial!=focus_serial)return;
    xiao147_inputs_draw(portrait); /* Revalidate tokens even without a periodic redraw. */
    if(serial!=focus_serial || focused<0)return;
    struct command command=commands[focused];
    xiao147_inputs_focus_cancel_pending();
    xiao147_inputs_press(command.x+command.w/2,command.y+command.h/2);
    xiao147_inputs_release(command.x+command.w/2,command.y+command.h/2);
}

/* Code-native silhouettes; no font glyph dependence and no new profile slot. */
static void icon_line(lv_layer_t *ctx,int x,int y,int x2,int y2,lv_color_t color) {
    lv_draw_line_dsc_t d;lv_draw_line_dsc_init(&d);d.color=color;d.width=1;
    d.p1.x=x;d.p1.y=y;d.p2.x=x2;d.p2.y=y2;lv_draw_line(ctx,&d);
}
static void icon_draw(lv_event_t *event) {
    lv_obj_t *o=lv_event_get_target(event);lv_area_t area;lv_obj_get_coords(o,&area);
    unsigned type=(uintptr_t)lv_event_get_user_data(event);
    lv_layer_t *ctx=lv_event_get_layer(event);int x=area.x1,y=area.y1;
    lv_color_t c=lv_color_hex(XIAO147_ACCENT_BLUE);
    if(type==LISM_INPUT_DEVICE_MOUSE) {
        icon_line(ctx,x+4,y+1,x+10,y+1,c);icon_line(ctx,x+2,y+3,x+2,y+10,c);
        icon_line(ctx,x+12,y+3,x+12,y+10,c);icon_line(ctx,x+4,y+12,x+10,y+12,c);
        icon_line(ctx,x+2,y+3,x+4,y+1,c);icon_line(ctx,x+10,y+1,x+12,y+3,c);
        icon_line(ctx,x+2,y+10,x+4,y+12,c);icon_line(ctx,x+10,y+12,x+12,y+10,c);
        icon_line(ctx,x+7,y+1,x+7,y+5,c);icon_line(ctx,x+2,y+5,x+12,y+5,c);
    } else {
        icon_line(ctx,x+1,y+2,x+13,y+2,c);icon_line(ctx,x+1,y+11,x+13,y+11,c);
        icon_line(ctx,x+1,y+2,x+1,y+11,c);icon_line(ctx,x+13,y+2,x+13,y+11,c);
        if(type==LISM_INPUT_DEVICE_KEYBOARD) {
            for(int k=3;k<12;k+=3)icon_line(ctx,x+k,y+4,x+k,y+7,c);
            icon_line(ctx,x+3,y+9,x+11,y+9,c);
        } else if(type==LISM_INPUT_DEVICE_GAMEPAD) {
            icon_line(ctx,x+3,y+6,x+7,y+6,c);icon_line(ctx,x+5,y+4,x+5,y+8,c);
            icon_line(ctx,x+10,y+5,x+10,y+6,c);icon_line(ctx,x+11,y+8,x+12,y+8,c);
        } else if(type==LISM_INPUT_DEVICE_TOUCHPAD)icon_line(ctx,x+1,y+9,x+13,y+9,c);
        else {icon_line(ctx,x+4,y+4,x+10,y+9,c);icon_line(ctx,x+10,y+4,x+4,y+9,c);}
    }
}
void xiao147_inputs_status_init(lv_obj_t *screen) {
    for(unsigned i=0;i<2;i++) {
        icons[i]=lv_obj_create(screen);lv_obj_remove_style_all(icons[i]);lv_obj_set_size(icons[i],15,14);
        lv_obj_clear_flag(icons[i],LV_OBJ_FLAG_CLICKABLE|LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(icons[i],LV_OBJ_FLAG_HIDDEN);
    }
}
void xiao147_inputs_status_draw(bool p,bool visible) {
    struct lism_input_status s;lism_input_host_snapshot(&s);
    for(unsigned i=0;i<2;i++) {
        if(!icons[i])continue;
        lv_obj_remove_event_cb(icons[i],icon_draw);
        if(!visible || !s.peers[i].ready || !s.peers[i].connected) {lv_obj_add_flag(icons[i],LV_OBJ_FLAG_HIDDEN);continue;}
        lv_obj_set_pos(icons[i],p?69+i*20:178,p?274:113+i*21);
        lv_obj_add_event_cb(icons[i],icon_draw,LV_EVENT_DRAW_MAIN,(void *)(uintptr_t)s.peers[i].type);
        lv_obj_clear_flag(icons[i],LV_OBJ_FLAG_HIDDEN);lv_obj_invalidate(icons[i]);
    }
}
