/* SPDX-License-Identifier: MIT
 * Two independent, fixed ESB links inside MPSL grants. Native split central
 * runs on link 0 (left->right). Link 1 is a raw PTX uplink to Prospector.
 * No live SDK object, queued frame or receive provenance is shared across links.
 */
#include "app_esb.h"
#include <zephyr/dt-bindings/input/input-event-codes.h>
#include "common.h"
#include "link.h"
#include <kb_route/route.h>
#include <kb_route/mirror.h>
#include <esb.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/crc.h>
#include <zephyr/random/random.h>
#include <hal/nrf_timer.h>
#include <hal/nrf_radio.h>
#include <zephyr/logging/log.h>
#include <mpsl_timeslot.h>
#include <sdc_soc.h>
LOG_MODULE_REGISTER(kb_hub_radio,LOG_LEVEL_INF);
extern int lism_esb_resources_init(void);
extern void lism_esb_ppi_channels_get(uint8_t *channels);
extern void lism_esb_radio_irq(void);
extern void lism_esb_slot_service(void);
void mpsl_assert_handle(const char *file,int line) {ARG_UNUSED(file);ARG_UNUSED(line);k_panic();}
#define HUB_SLOT_US 4000
#define NODE DT_COMPAT_GET_ANY_STATUS_OKAY(zmk_esb_split)
static const uint8_t dongle_base0[]=DT_PROP(NODE,base_addr_0),dongle_base1[]=DT_PROP(NODE,base_addr_1);
static const uint8_t prefixes[]={CONFIG_LISM_KEYBOARD_SET==0?0xa7:0x69,CONFIG_LISM_KEYBOARD_SET==0?0xd3:0xb5};
static const uint8_t keyboard_base0[]={0xB7,0x63,0xA9,(CONFIG_LISM_KEYBOARD_SET==0?0xe7:0xe9)},keyboard_base1[]={0xC9,0x57,0x8D,(CONFIG_LISM_KEYBOARD_SET==0?0xf1:0xf3)};
struct hub_tx {struct esb_payload p;uint32_t peer,generation,local_boot;uint8_t station;int64_t since;};
K_MSGQ_DEFINE(left_tx,sizeof(struct hub_tx),16,4);
K_MSGQ_DEFINE(dongle_tx,sizeof(struct hub_tx),64,4);
struct hub_link {
    uint32_t boot,next_seq,last_boot[2],previous_boot[2],last_seq[2],ack_boot[2],ack_seq[2];
    struct hub_tx tx;bool have;atomic_t result;uint32_t success,fail;
};
static struct hub_link links[2];
static struct hub_tx latest_mirror;static bool mirror_pending;
struct hub_rx {struct esb_payload p;uint8_t link,station;uint32_t boot;};
static struct hub_rx rx[64];static atomic_t head,tail,overflow;
static atomic_t idle=ATOMIC_INIT(1),ready,enabled,error;
static atomic_t uplink_generation;
static atomic_t station_requested,station_selected;
static atomic_t probe_transmitted;
void kb_uplink_station_request(uint8_t station){if(station<KB_STATION_COUNT)atomic_set(&station_requested,station);}
uint8_t kb_uplink_station_current(void){return atomic_get(&station_selected);}
void kb_uplink_reset(void) {atomic_inc(&uplink_generation);}
static app_esb_callback_t callback;
static unsigned grant_link,rounds;
static int64_t next_private;
static K_MUTEX_DEFINE(tx_lock);
static uint32_t left_route_epoch;
static uint8_t left_route_phase,left_route_target,left_route_station;
static uint32_t left_route_receiver_boot;
static int64_t next_left_control;
uint32_t kb_private_authority(void){return links[0].boot;}
void kb_left_set_route(uint32_t epoch,uint8_t phase,uint8_t target,uint8_t station,uint32_t receiver_boot){
    k_mutex_lock(&tx_lock,K_FOREVER);
    if(left_route_epoch!=epoch || left_route_phase!=phase || left_route_target!=target || left_route_station!=station || left_route_receiver_boot!=receiver_boot)next_left_control=0;
    left_route_epoch=epoch;left_route_phase=phase;left_route_target=target;left_route_station=station;left_route_receiver_boot=receiver_boot;
    k_mutex_unlock(&tx_lock);
}
static bool sdk_ready;
static int64_t uplink_seen;static uint32_t control_last_seq;
uint32_t kb_uplink_peer_boot(void){return links[1].last_boot[1];}
bool kb_uplink_connected(void) {return uplink_seen && k_uptime_get()-uplink_seen<1500;}
static uint32_t get32(const uint8_t *p) {return p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static void put32(uint8_t *p,uint32_t v) {for(unsigned i=0;i<4;i++)p[i]=v>>(i*8);}
static bool tx_live(unsigned net,const struct hub_tx *tx,int64_t now) {
    return now-tx->since<=500 && (net?(tx->generation==(uint32_t)atomic_get(&uplink_generation) && tx->station==kb_uplink_station_current() && tx->local_boot==links[1].boot):tx->peer==links[net].last_boot[tx->p.pipe] && tx->generation==left_route_epoch);
}
static void collect(void) {
    struct esb_payload p;while(!esb_read_rx_payload(&p)) {
        unsigned h=atomic_get(&head),n=(h+1)%ARRAY_SIZE(rx);if(n==(unsigned)atomic_get(&tail)){atomic_inc(&overflow);continue;}
        rx[h]=(struct hub_rx){.p=p,.link=grant_link,.station=kb_uplink_station_current(),.boot=links[grant_link].boot};atomic_set(&head,n);
    }
}
static void esb_event(const struct esb_evt *e) {
    struct hub_link *l=&links[grant_link];
    if(e->evt_id==ESB_EVENT_TX_SUCCESS && grant_link){atomic_set(&l->result,1);l->success++;}
    else if(e->evt_id==ESB_EVENT_TX_FAILED){atomic_set(&l->result,-1);l->fail++;}
}
static int setup(void) {
    struct esb_config c=ESB_DEFAULT_CONFIG;c.protocol=ESB_PROTOCOL_ESB_DPL;c.bitrate=ESB_BITRATE_2MBPS;
    c.mode=grant_link?ESB_MODE_PTX:ESB_MODE_PRX;c.tx_mode=ESB_TXMODE_MANUAL_START;c.selective_auto_ack=true;
    c.event_handler=esb_event;c.retransmit_delay=600;c.retransmit_count=0;
    c.tx_output_power=ESB_TX_POWER_8DBM;c.use_fast_ramp_up=true;
    int rc=esb_init(&c);sdk_ready=!rc;
    if(!rc)rc=esb_set_base_address_0(grant_link?kb_station_base0[kb_uplink_station_current()]:keyboard_base0);
    if(!rc)rc=esb_set_base_address_1(grant_link?kb_station_base1[kb_uplink_station_current()]:keyboard_base1);
    if(!rc)rc=esb_set_prefixes(prefixes,ARRAY_SIZE(prefixes));
    if(!rc)rc=esb_enable_pipes(grant_link?(BIT(0)|BIT(1)):BIT(0));
    if(!rc)rc=esb_set_rf_channel(37);
    /* A transition may happen after requesting a grant but before START.
     * Recheck its epoch in IRQ0 before loading/starting the old PTX frame. */
    bool transmit=links[grant_link].have && (links[grant_link].tx.generation==(grant_link?(uint32_t)atomic_get(&uplink_generation):left_route_epoch)) && (!grant_link || (links[1].tx.station==kb_uplink_station_current() && links[1].tx.local_boot==links[1].boot));
    if(!rc && transmit)rc=esb_write_payload(&links[grant_link].tx.p);
    if(!rc)rc=grant_link?(transmit?esb_start_tx():0):esb_start_rx();
    if(!rc && grant_link && transmit){
        struct esb_event_envelope body={0};memcpy(&body,links[1].tx.p.data+LISM_WIRE_HEADER,links[1].tx.p.length-LISM_WIRE_HEADER-sizeof(struct esb_msg_postfix));
        if((int)body.payload.event.type==KB_PROBE_EVENT){struct kb_discovery p;memcpy(&p,&body.payload.event.data,sizeof(p));atomic_set(&probe_transmitted,p.cookie);}
    }
    return rc;
}
static mpsl_timeslot_signal_return_param_t response;
static mpsl_timeslot_request_t request={.request_type=MPSL_TIMESLOT_REQ_TYPE_EARLIEST,.params.earliest={.hfclk=MPSL_TIMESLOT_HFCLK_CFG_XTAL_GUARANTEED,.priority=MPSL_TIMESLOT_PRIORITY_NORMAL,.length_us=HUB_SLOT_US,.timeout_us=100000}};
static void end_slot(void) {
    if(sdk_ready){lism_esb_slot_service();collect();esb_disable();sdk_ready=false;}
    nrf_radio_power_set(NRF_RADIO,false);nrf_radio_power_set(NRF_RADIO,true);__DSB();
    nrf_radio_shorts_disable(NRF_RADIO,0xffffffff);nrf_radio_int_disable(NRF_RADIO,0xffffffff);
    nrf_timer_int_disable(NRF_TIMER0,0xffffffff);nrf_timer_event_clear(NRF_TIMER0,NRF_TIMER_EVENT_COMPARE0);nrf_timer_event_clear(NRF_TIMER0,NRF_TIMER_EVENT_COMPARE1);NVIC_ClearPendingIRQ(RADIO_IRQn);
}
static mpsl_timeslot_signal_return_param_t *slot(mpsl_timeslot_session_id_t id,uint32_t signal) {
    ARG_UNUSED(id);response.callback_action=MPSL_TIMESLOT_SIGNAL_ACTION_NONE;
    switch(signal) {
    case MPSL_TIMESLOT_SIGNAL_START: {
        if(!atomic_get(&enabled)){end_slot();response.callback_action=MPSL_TIMESLOT_SIGNAL_ACTION_END;return &response;}
        nrf_radio_power_set(NRF_RADIO,false);nrf_radio_power_set(NRF_RADIO,true);NVIC_ClearPendingIRQ(RADIO_IRQn);
        nrf_timer_bit_width_set(NRF_TIMER0,NRF_TIMER_BIT_WIDTH_32);
        nrf_timer_cc_set(NRF_TIMER0,NRF_TIMER_CC_CHANNEL0,HUB_SLOT_US-300);
        nrf_timer_cc_set(NRF_TIMER0,NRF_TIMER_CC_CHANNEL1,50);
        nrf_timer_event_clear(NRF_TIMER0,NRF_TIMER_EVENT_COMPARE0);nrf_timer_event_clear(NRF_TIMER0,NRF_TIMER_EVENT_COMPARE1);
        nrf_timer_int_enable(NRF_TIMER0,NRF_TIMER_INT_COMPARE0_MASK|NRF_TIMER_INT_COMPARE1_MASK);
        int rc=setup();if(rc){atomic_set(&error,rc);end_slot();response.callback_action=MPSL_TIMESLOT_SIGNAL_ACTION_END;}return &response;
    }
    case MPSL_TIMESLOT_SIGNAL_RADIO:if(sdk_ready){lism_esb_radio_irq();lism_esb_slot_service();}return &response;
    case MPSL_TIMESLOT_SIGNAL_TIMER0:
        if(nrf_timer_event_check(NRF_TIMER0,NRF_TIMER_EVENT_COMPARE0)){end_slot();response.callback_action=MPSL_TIMESLOT_SIGNAL_ACTION_END;}
        else if(nrf_timer_event_check(NRF_TIMER0,NRF_TIMER_EVENT_COMPARE1)) {
            nrf_timer_event_clear(NRF_TIMER0,NRF_TIMER_EVENT_COMPARE1);
            if(sdk_ready)lism_esb_slot_service();
            /* TIMER2/SWI remain disabled. Service only their bounded pending
             * events inside this grant, never register a second radio IRQ. */
            uint32_t now; nrf_timer_task_trigger(NRF_TIMER0,NRF_TIMER_TASK_CAPTURE2);now=nrf_timer_cc_get(NRF_TIMER0,NRF_TIMER_CC_CHANNEL2);
            nrf_timer_cc_set(NRF_TIMER0,NRF_TIMER_CC_CHANNEL1,now+50);
        }return &response;
    case MPSL_TIMESLOT_SIGNAL_SESSION_IDLE:
    case MPSL_TIMESLOT_SIGNAL_BLOCKED:
    case MPSL_TIMESLOT_SIGNAL_CANCELLED:atomic_set(&idle,1);return NULL;
    case MPSL_TIMESLOT_SIGNAL_SESSION_CLOSED:
    case MPSL_TIMESLOT_SIGNAL_INVALID_RETURN:
    case MPSL_TIMESLOT_SIGNAL_OVERSTAYED:atomic_set(&error,-EIO);return NULL;
    default:return NULL;
    }
}
static bool valid_body(unsigned net,struct esb_payload *p) {
    if(p->pipe>=2 || p->length<LISM_WIRE_HEADER+sizeof(struct esb_msg_prefix)+2+sizeof(struct esb_msg_postfix) || p->length>64 || memcmp(p->data,KB_SET_MAGIC,4))return false;
    struct esb_msg_prefix h;memcpy(&h,p->data+LISM_WIRE_HEADER,sizeof(h));size_t body=p->length-LISM_WIRE_HEADER,n=sizeof(h)+h.payload_size;
    if(memcmp(h.magic_prefix,"ZmKe",4) || h.payload_size<2 || n+sizeof(struct esb_msg_postfix)!=body || p->data[LISM_WIRE_HEADER+sizeof(h)]!=p->pipe)return false;
    struct esb_msg_postfix crc;memcpy(&crc,p->data+LISM_WIRE_HEADER+n,sizeof(crc));if(crc.crc!=crc32_ieee(p->data+LISM_WIRE_HEADER,n))return false;
    return net?kb_uplink_valid(p->pipe,p->data+LISM_WIRE_HEADER,body):(p->pipe==0 && lism_wire_valid(p->pipe,p->data+LISM_WIRE_HEADER,body));
}
static void receive(void) {
    unsigned t;while((t=atomic_get(&tail))!=(unsigned)atomic_get(&head)) {
        struct hub_rx item=rx[t];atomic_set(&tail,(t+1)%ARRAY_SIZE(rx));struct esb_payload *p=&item.p;struct hub_link *l=&links[item.link];
        if(item.boot!=l->boot || (item.link && item.station!=kb_uplink_station_current()) || !valid_body(item.link,p))continue;
        unsigned pipe=p->pipe;uint32_t b=get32(p->data+4),s=get32(p->data+8);if(!b || !s || (item.link && get32(p->data+12)!=l->boot))continue;
        k_mutex_lock(&tx_lock,K_FOREVER);
        if(b!=l->last_boot[pipe]) {if(b==l->previous_boot[pipe]){k_mutex_unlock(&tx_lock);continue;}l->previous_boot[pipe]=l->last_boot[pipe];l->last_boot[pipe]=b;l->last_seq[pipe]=0;if(!item.link)control_last_seq=0;}
        struct esb_event_envelope body={0};bool control=false;if(!item.link){memcpy(&body,p->data+LISM_WIRE_HEADER,p->length-LISM_WIRE_HEADER-sizeof(struct esb_msg_postfix));control=(int)body.payload.event.type==KB_LEFT_CONTROL;}
        uint32_t *sequence=control?&control_last_seq:&l->last_seq[pipe];
        if(s<=*sequence){k_mutex_unlock(&tx_lock);continue;}
        if(!item.link && l->have && l->tx.p.pipe==pipe && get32(p->data+12)==l->boot && get32(p->data+16)==get32(l->tx.p.data+8))atomic_set(&l->result,1);
        k_mutex_unlock(&tx_lock);
        if(item.link && !kb_uplink_is_probe(p->data+LISM_WIRE_HEADER,p->length-LISM_WIRE_HEADER) && get32(p->data+16)!=kb_route_command_epoch())continue;
        bool accepted=false;
        if(item.link){accepted=kb_uplink_command(pipe,p->data+LISM_WIRE_HEADER,p->length-LISM_WIRE_HEADER,get32(p->data+16),b);}
        else {app_esb_event_t e={.evt_type=APP_ESB_EVT_RX,.pipe=pipe,.buf=p->data+LISM_WIRE_HEADER,.data_length=p->length-LISM_WIRE_HEADER};callback(&e);accepted=e.accepted;}
        if(accepted) {k_mutex_lock(&tx_lock,K_FOREVER);*sequence=s;if(item.link)l->ack_boot[pipe]=b,l->ack_seq[pipe]=s;k_mutex_unlock(&tx_lock);}
    }
}
#include "hub_motion.inc"
static int enqueue(unsigned net,app_esb_data_t *p,bool replaceable) {
    if(!p || p->pipe>=2 || !p->len || p->len>64-LISM_WIRE_HEADER)return -EINVAL;
    k_mutex_lock(&tx_lock,K_FOREVER);struct hub_link *l=&links[net];if(!l->boot || !l->next_seq){k_mutex_unlock(&tx_lock);return -EAGAIN;}
    struct hub_tx tx={.peer=l->last_boot[p->pipe],.generation=net?(uint32_t)atomic_get(&uplink_generation):left_route_epoch,.station=kb_uplink_station_current(),.local_boot=l->boot,.since=k_uptime_get()};tx.p.pipe=p->pipe;tx.p.length=LISM_WIRE_HEADER+p->len;
    memcpy(tx.p.data,KB_SET_MAGIC,4);put32(tx.p.data+4,l->boot);
    put32(tx.p.data+12,net?l->ack_boot[p->pipe]:tx.peer);put32(tx.p.data+16,net?l->ack_seq[p->pipe]:0);
    memcpy(tx.p.data+LISM_WIRE_HEADER,p->data,p->len);int rc;
    struct esb_event_envelope motion;
    if(net && motion_event(&tx,&motion)) {
        if(motion_pending && motion_old(&latest_motion)){motion_pending=false;motion_expired++;}
        if(motion_pending && merge_motion(&latest_motion,&tx)){k_mutex_unlock(&tx_lock);return 0;}
        rc=flush_motion();if(!rc){latest_motion=tx;motion_pending=true;}
    } else {
        rc=net?flush_motion():0;
        if(!rc){if(net && replaceable){latest_mirror=tx;mirror_pending=true;}else rc=k_msgq_put(net?&dongle_tx:&left_tx,&tx,K_NO_WAIT);}
    }
    k_mutex_unlock(&tx_lock);return rc;
}
int zmk_split_esb_send(app_esb_data_t *p) {return enqueue(0,p,false);}
int kb_uplink_send(unsigned source,const struct zmk_split_transport_peripheral_event *event) {
    size_t n;switch((int)event->type) {
    case KB_PROBE_EVENT:n=sizeof(struct kb_discovery);break;
    case LISM_SNAPSHOT_TYPE:n=sizeof(struct lism_snapshot);break;
    case KB_ROUTE_EVENT:n=sizeof(struct kb_route_status);break;
    case KB_MIRROR_EVENT:n=sizeof(struct kb_mirror);break;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT:n=sizeof(event->data.key_position_event);break;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT:n=sizeof(event->data.input_event);break;
    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT:n=sizeof(event->data.battery_event);break;
    default:return -EINVAL;
    }
    uint8_t frame[64]={0};struct esb_event_envelope env={.prefix={.magic_prefix=ZMK_SPLIT_ESB_ENVELOPE_MAGIC_PREFIX,.payload_size=1+sizeof(event->type)+n},.payload={.source=source,.event=*event}};
    size_t len=sizeof(env.prefix)+env.prefix.payload_size;memcpy(frame,&env,len);struct esb_msg_postfix crc={.crc=crc32_ieee(frame,len)};memcpy(frame+len,&crc,sizeof(crc));
    app_esb_data_t p={.pipe=source,.data=frame,.len=len+sizeof(crc)};return enqueue(1,&p,event->type==KB_MIRROR_EVENT);
}
static bool next_tx(unsigned net,struct hub_tx *out) {
    /* Assign wire sequence only after choosing the actual send order. A
     * replaceable mirror yields to reliable frames, including frames queued
     * after it. Enqueue-time numbers would make the receiver reject it as old.
     * Keep selection and numbering under the producer lock; retries reuse tx. */
    k_mutex_lock(&tx_lock,K_FOREVER);struct hub_link *l=&links[net];
    if(!l->next_seq){k_mutex_unlock(&tx_lock);return false;}
    bool have=false;
    if(!net && left_route_epoch && l->last_boot[0] && k_uptime_get()>=next_left_control){
        struct esb_command_envelope env={.prefix={.magic_prefix=ZMK_SPLIT_ESB_ENVELOPE_MAGIC_PREFIX},.payload={.source=0,.cmd={.type=ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR}}};
        __typeof__(env.payload.cmd.data.invoke_behavior) b={0};
        strcpy(b.behavior_dev,"KB_LEFT_ROUTE3");b.param1=(left_route_phase<<4)|(left_route_target<<2)|(left_route_station==KB_STATION_NONE?2:left_route_station);b.param2=left_route_epoch;b.position=left_route_receiver_boot;b.state=1;memcpy(&env.payload.cmd.data.invoke_behavior,&b,sizeof(b));
        env.prefix.payload_size=1+sizeof(env.payload.cmd.type)+sizeof(b);
        size_t n=sizeof(env.prefix)+env.prefix.payload_size;
        *out=(struct hub_tx){.peer=l->last_boot[0],.generation=left_route_epoch,.since=k_uptime_get()};out->p.pipe=0;out->p.length=LISM_WIRE_HEADER+n+sizeof(struct esb_msg_postfix);
        memcpy(out->p.data,KB_SET_MAGIC,4);put32(out->p.data+4,l->boot);memcpy(out->p.data+LISM_WIRE_HEADER,&env,n);
        struct esb_msg_postfix crc={.crc=crc32_ieee(out->p.data+LISM_WIRE_HEADER,n)};memcpy(out->p.data+LISM_WIRE_HEADER+n,&crc,sizeof(crc));
        next_left_control=k_uptime_get()+60;have=true;
    }
    while(!have && !k_msgq_get(net?&dongle_tx:&left_tx,out,K_NO_WAIT)) {
        if(net && motion_old(out)){motion_expired++;continue;}have=true;break;
    }
    if(!have && net && motion_pending){
        if(motion_old(&latest_motion))motion_expired++;
        else {*out=latest_motion;have=true;}motion_pending=false;
    }
    if(!have && net && mirror_pending){*out=latest_mirror;mirror_pending=false;have=true;}
    if(have){
        put32(out->p.data+8,l->next_seq++);
        /* ACK metadata is live transport state, not enqueue-time history.
         * Rebind once at selection; exact retries retain the selected bytes. */
        put32(out->p.data+12,net?l->ack_boot[out->p.pipe]:out->peer);
        put32(out->p.data+16,net?l->ack_seq[out->p.pipe]:left_route_epoch);
    }
    k_mutex_unlock(&tx_lock);return have;
}
static void apply_station_request(void){
    unsigned wanted=atomic_get(&station_requested);
    if(wanted==kb_uplink_station_current())return;
    /* Called only with MPSL idle. Producers share this lock, so an old queue,
     * selected retry, ACK or replaceable motion can never be retargeted. */
    k_mutex_lock(&tx_lock,K_FOREVER);
    atomic_inc(&uplink_generation);k_msgq_purge(&dongle_tx);motion_pending=mirror_pending=false;
    memset(&links[1],0,sizeof(links[1]));
    do{links[1].boot=sys_rand32_get();}while(!links[1].boot);
    links[1].next_seq=1;uplink_seen=0;atomic_set(&station_selected,wanted);
    k_mutex_unlock(&tx_lock);
}
static void radio_thread(void *a,void *b,void *c) {
    ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);while(!callback)k_sleep(K_MSEC(1));
    int rc=lism_esb_resources_init();if(rc)goto failed;uint8_t pp[6];uint32_t mask=0;lism_esb_ppi_channels_get(pp);for(unsigned i=0;i<6;i++)mask|=BIT(pp[i]);if(mask&SDC_PPI_CHANNELS_USED_MASK){rc=-EBUSY;goto failed;}
    for(unsigned i=0;i<2;i++){do{links[i].boot=sys_rand32_get();}while(!links[i].boot);links[i].next_seq=1;}
    mpsl_timeslot_session_id_t session;rc=mpsl_timeslot_session_open(slot,&session);if(rc)goto failed;
    atomic_set(&ready,1);lism_notify_status();app_esb_event_t initial={.evt_type=APP_ESB_EVT_TX_FAIL};callback(&initial);
    int64_t next_log=0;
    for(;;) {
        if((rc=atomic_set(&error,0)))break;receive();
        uint32_t transmitted=atomic_set(&probe_transmitted,0);if(transmitted)kb_route_probe_transmitted(kb_uplink_station_current(),transmitted);
        if(atomic_get(&idle)) {
            apply_station_request();
            for(unsigned i=0;i<2;i++) {
                struct hub_link *l=&links[i];int result=atomic_set(&l->result,0);
                bool expired=l->have && !tx_live(i,&l->tx,k_uptime_get());
                if(i && result==1)uplink_seen=k_uptime_get();
                if(l->have && (result==1 || expired || (!i && l->tx.peer!=l->last_boot[l->tx.p.pipe]))) {
                    l->have=false;if(!i){app_esb_event_t e={.evt_type=result==1?APP_ESB_EVT_TX_SUCCESS:APP_ESB_EVT_TX_FAIL};callback(&e);}
                }
                while(!l->have && next_tx(i,&l->tx)) {
                    if(!tx_live(i,&l->tx,k_uptime_get()))continue;
                    l->have=true;
                }
            }
            /* A blocked/unplugged dongle never starves left reception. */
            if(k_uptime_get()>=next_private || !links[1].have){grant_link=0;next_private=k_uptime_get()+19+(rounds++%5);}
            else grant_link=1;
            if(atomic_get(&enabled)){atomic_set(&idle,0);rc=mpsl_timeslot_request(session,&request);if(rc)atomic_set(&idle,1);}
        }
        if(k_uptime_get()>=next_log){next_log=k_uptime_get()+5000;LOG_INF("ESB hub TX=%u/%u fail=%u/%u q=%u/%u motion_merge=%u stale=%u inflight_ms=%u ack=%u,%u",links[0].success,links[1].success,links[0].fail,links[1].fail,k_msgq_num_used_get(&left_tx),k_msgq_num_used_get(&dongle_tx),motion_merged,motion_expired,links[1].have?(unsigned)(k_uptime_get()-links[1].tx.since):0,links[1].ack_seq[0],links[1].ack_seq[1]);}
        k_sleep(K_MSEC(1));
    }
    mpsl_timeslot_session_close(session);
failed:atomic_clear(&ready);lism_notify_status();LOG_ERR("Right ESB hub stopped rc=%d",rc);
}
K_THREAD_DEFINE(lism_radio_thread,4096,radio_thread,NULL,NULL,NULL,K_PRIO_COOP(CONFIG_MPSL_THREAD_COOP_PRIO),0,0);
bool lism_radio_ready(void) {return atomic_get(&ready);}
int zmk_split_esb_init(app_esb_mode_t mode,app_esb_callback_t cb) {ARG_UNUSED(mode);callback=cb;return 0;}
int zmk_split_esb_set_enable(bool v) {atomic_set(&enabled,v);return 0;}
