/* Touch is an independent mouse-button owner. All HID sends run in the sole
 * input thread with the existing endpoint transition lock, never touch I2C work.
 */
#include <errno.h>
#include <string.h>
#include <zephyr/device.h>
#include <zephyr/init.h>
#include <zephyr/input/input.h>
#include <zephyr/dt-bindings/input/input-event-codes.h>
#include <zmk/hid.h>
#include <zmk/endpoints.h>
#include <zmk/usb.h>
#include "kb_route/route.h"
#include "lism_input_merge.h"
#include "xiao147_ui.h"
#include "xiao147_pointer.h"
#define TOUCH_KICK 0x7ffb
struct context {uint32_t epoch,route,ui;int endpoint;bool allowed;};
struct sample {struct context context;uint32_t stream;int token;int16_t dx,dy,pan,wheel;uint8_t buttons;int64_t at;};
K_MSGQ_DEFINE(touch_queue,sizeof(struct sample),16,4);
static struct k_spinlock producer_lock;
static struct context produced,observed;
static uint8_t queued_buttons;
static uint32_t serial;
static uint32_t stream=1;
static uint32_t stream_get(void){k_spinlock_key_t key=k_spin_lock(&producer_lock);uint32_t s=stream;k_spin_unlock(&producer_lock,key);return s;}
static uint32_t stream_cancel(void){k_spinlock_key_t key=k_spin_lock(&producer_lock);queued_buttons=0;if(!++stream)stream=1;uint32_t s=stream;k_spin_unlock(&producer_lock,key);return s;}
static bool same(struct context a,struct context b) {return a.epoch==b.epoch && a.route==b.route && a.ui==b.ui && a.endpoint==b.endpoint && a.allowed==b.allowed;}
extern uint32_t kb_receiver_route_generation(void);
static struct context context(void) {
    struct context c={.epoch=lism_input_merge_generation(),.route=kb_receiver_route_generation(),.ui=xiao147_ui_pointer_generation(),.endpoint=xiao147_pointer_endpoint()};
    c.allowed=kb_route_dongle_key_allowed() && xiao147_ui_page()==XIAO147_PAGE_STATUS && !xiao147_ui_is_sleeping() && c.endpoint>=0;
    if(c.endpoint==0 && !zmk_usb_is_hid_ready())c.allowed=false;
    return c;
}
static int context_token(struct context c) {
    k_spinlock_key_t key=k_spin_lock(&producer_lock);
    if(!same(c,observed) || !serial){observed=c;serial=(serial+1)&0x7fffffff;if(!serial)serial=1;}
    int result=c.allowed?(int)serial:-1;k_spin_unlock(&producer_lock,key);return result;
}
int xiao147_touch_endpoint(void) {return context_token(context());}
DEVICE_DEFINE(xiao147_touch_device,"LisM touch source",NULL,NULL,NULL,NULL,POST_KERNEL,CONFIG_INPUT_INIT_PRIORITY,NULL);
static void kick(void) {input_report(DEVICE_GET(xiao147_touch_device),INPUT_EV_MSC,TOUCH_KICK,0,true,K_NO_WAIT);}
static int submit(uint16_t code,int32_t value,bool button) {
    struct context c=context();if(!c.allowed)return button && !value?0:-ECANCELED;
    struct sample s={.context=c,.token=context_token(c),.at=k_uptime_get()};
    k_spinlock_key_t key=k_spin_lock(&producer_lock);
    if(!same(c,produced)){produced=c;queued_buttons=0;}
    s.stream=stream;
    s.buttons=queued_buttons;
    if(button){if(value)s.buttons|=BIT(code-INPUT_BTN_0);else s.buttons&=~BIT(code-INPUT_BTN_0);}
    else switch(code){case INPUT_REL_X:s.dx=value;break;case INPUT_REL_Y:s.dy=value;break;case INPUT_REL_HWHEEL:s.pan=value;break;case INPUT_REL_WHEEL:s.wheel=value;break;default:k_spin_unlock(&producer_lock,key);return -EINVAL;}
    int rc=k_msgq_put(&touch_queue,&s,K_NO_WAIT);if(!rc)queued_buttons=s.buttons;
    k_spin_unlock(&producer_lock,key);if(!rc)kick();return rc;
}
int xiao147_touch_key(const struct device*d,uint16_t code,int32_t value,bool sync,k_timeout_t t) {
    ARG_UNUSED(d);ARG_UNUSED(sync);ARG_UNUSED(t);if(code<INPUT_BTN_0 || code>INPUT_BTN_4 || value<0 || value>1)return -EINVAL;return submit(code,value,true);
}
int xiao147_touch_rel(const struct device*d,uint16_t code,int32_t value,bool sync,k_timeout_t t) {
    ARG_UNUSED(d);ARG_UNUSED(sync);ARG_UNUSED(t);if(value<INT16_MIN || value>INT16_MAX)return -ERANGE;return submit(code,value,false);
}
static void retry(struct k_work*w);K_WORK_DELAYABLE_DEFINE(retry_work,retry);
static int send(const struct sample*s) {
    int rc=lism_input_merge_touch(s->context.epoch,s->token,s->buttons);if(rc)return rc;
    zmk_hid_mouse_movement_set(s->dx,s->dy);zmk_hid_mouse_scroll_set(s->pan,s->wheel);
    lism_input_usb_bridge(true);rc=zmk_endpoint_send_mouse_report();lism_input_usb_bridge(false);
    zmk_hid_mouse_movement_set(0,0);zmk_hid_mouse_scroll_set(0,0);return rc;
}
static void process(struct input_event*e,void*unused) {
    ARG_UNUSED(unused);if(e->type!=INPUT_EV_MSC || e->code!=TOUCH_KICK)return;
    static struct context owner;static uint32_t owner_stream;static struct sample pending;static bool have;static uint8_t held;
    lism_input_endpoint_transition_begin();struct context c=context();
    uint32_t current_stream=stream_get();
    if(!same(owner,c) || owner_stream!=current_stream) {
        /* Endpoint reset already neutralizes the old host. A mode/page/sleep
         * change on the same host still needs this source's neutral report. */
        bool neutral=held && owner.endpoint==c.endpoint && owner.epoch==c.epoch;
        owner=c;owner_stream=current_stream;have=neutral;
        pending=(struct sample){.context=c,.token=context_token(c),.stream=current_stream,.at=k_uptime_get()};
        lism_input_merge_touch(c.epoch,pending.token,0);if(!neutral)held=0;
    }
    for(unsigned budget=0;budget<8;budget++) {
        if(!have) {
            if(k_msgq_get(&touch_queue,&pending,K_NO_WAIT))break;
            if(!same(pending.context,c) || pending.stream!=current_stream || !c.allowed)continue;
            have=true;
        }
        if(k_uptime_get()-pending.at>150){
            /* Expiry cancels this source, never discards its release. Invalidate
             * the producer's button snapshot so later motion cannot re-press. */
            current_stream=owner_stream=stream_cancel();pending.stream=current_stream;
            pending.dx=pending.dy=pending.pan=pending.wheel=0;pending.buttons=0;
            pending.at=k_uptime_get();
        }
        /* Recheck while holding the endpoint fence, immediately before send. */
        struct context latest=context();
        if(!same(c,latest))break;
        int rc=send(&pending);
        if(rc!=-ESTALE)held|=pending.buttons; /* Another sender can expose a merged press even when this send was busy. */
        if(rc && rc!=-ENOTCONN && rc!=-ENODEV)break;
        held=pending.buttons;have=false;
    }
    lism_input_endpoint_transition_end();
    k_work_reschedule(&retry_work,K_MSEC(10));
}
INPUT_CALLBACK_DEFINE(DEVICE_GET(xiao147_touch_device),process,NULL);
static void retry(struct k_work*w){ARG_UNUSED(w);kick();k_work_reschedule(&retry_work,K_MSEC(10));}
static int init(void){k_work_reschedule(&retry_work,K_MSEC(100));return 0;}
SYS_INIT(init,APPLICATION,CONFIG_APPLICATION_INIT_PRIORITY);
