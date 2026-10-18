/* SPDX-License-Identifier: MIT */
#include "mode.h"
#include <drivers/input_processor.h>
#define NODE DT_NODELABEL(trackball_split_input_right)
static const struct zmk_input_processor_entry processors[]={
    LISTIFY(DT_PROP_LEN(NODE,input_processors),ZMK_INPUT_PROCESSOR_ENTRY_AT_IDX,(,),NODE)};
static void pointer(struct input_event *event,void *arg) {
    ARG_UNUSED(arg);
    if(!lism_standalone_running())return;
    for(size_t i=0;i<ARRAY_SIZE(processors);i++)
        if(zmk_input_processor_handle_event(processors[i].dev,event,processors[i].param1,
           processors[i].param2,NULL)!=ZMK_INPUT_PROC_CONTINUE)return;
    lism_standalone_route_input(event);
}
INPUT_CALLBACK_DEFINE(DEVICE_DT_GET(DT_PHANDLE(NODE,device)),pointer,NULL);

void lism_standalone_pointer_reset(void) {
    for(unsigned axis=0;axis<2;axis++) {
        struct input_event event={.dev=DEVICE_DT_GET(DT_PHANDLE(NODE,device)),
            .type=INPUT_EV_REL,.code=axis?INPUT_REL_Y:INPUT_REL_X,.value=0,.sync=axis!=0};
        for(size_t i=0;i<ARRAY_SIZE(processors);i++)
            if(zmk_input_processor_handle_event(processors[i].dev,&event,processors[i].param1,
               processors[i].param2,NULL)!=ZMK_INPUT_PROC_CONTINUE)break;
    }
}
