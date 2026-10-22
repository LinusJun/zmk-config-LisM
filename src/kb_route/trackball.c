/* SPDX-License-Identifier: MIT */
#include "route.h"
#include <zephyr/input/input.h>
#include <drivers/input_processor.h>
#include <zmk/pointing/input_split.h>
#define BALL_NODE DT_NODELABEL(trackball_split_input_right)
static const struct zmk_input_processor_entry processors[]={LISTIFY(DT_PROP_LEN(BALL_NODE,input_processors),ZMK_INPUT_PROCESSOR_ENTRY_AT_IDX,(,),BALL_NODE)};
static void ball(struct input_event *e,void *arg) {
    ARG_UNUSED(arg);
    /* Physical button state must be tracked even across a closing gate.
     * Its release cannot be discarded while a route waits for all-up. */
    if(e->type==INPUT_EV_KEY) {
        struct zmk_split_transport_peripheral_event button={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT,.data.input_event={.reg=1,.type=e->type,.code=e->code,.value=e->value,.sync=e->sync}};
        if(!kb_route_input(1,&button))zmk_input_split_report_peripheral_event(1,e->type,e->code,e->value,e->sync);
        return;
    }
    if(!kb_route_accept_pointer())return;
    for(size_t i=0;i<ARRAY_SIZE(processors);i++)if(zmk_input_processor_handle_event(processors[i].dev,e,processors[i].param1,processors[i].param2,NULL)!=ZMK_INPUT_PROC_CONTINUE)return;
    struct zmk_split_transport_peripheral_event ev={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT,.data.input_event={.reg=1,.type=e->type,.code=e->code,.value=e->value,.sync=e->sync}};
    if(!kb_route_input(1,&ev))zmk_input_split_report_peripheral_event(1,e->type,e->code,e->value,e->sync);
}
INPUT_CALLBACK_DEFINE(DEVICE_DT_GET(DT_PHANDLE(BALL_NODE,device)),ball,NULL);

void kb_route_pointer_reset(void) {
    /* Pinned xyz data consists solely of these int16 accumulators. Runs in
     * the input thread fence, never concurrently with this producer. */
    memset(DEVICE_DT_GET(DT_NODELABEL(zip_xyz))->data,0,CONFIG_ZMK_INPUT_PROCESSOR_XYZ_CODES_MAX_LEN*sizeof(int16_t));
    memset(DEVICE_DT_GET(DT_NODELABEL(zip_zxy))->data,0,CONFIG_ZMK_INPUT_PROCESSOR_XYZ_CODES_MAX_LEN*sizeof(int16_t));
}
