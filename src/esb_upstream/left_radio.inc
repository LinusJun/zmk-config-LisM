/* SPDX-License-Identifier: MIT
 * Left has two DISTINCT PTX addresses. A bounded private control exchange
 * cannot be blocked by retries to a powered-off data target. */
#include "app_esb.h"
#include "common.h"
#include "link.h"
#include <kb_route/left_protocol.h>
#include <esb.h>
#include <zephyr/random/random.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/crc.h>
#include <zephyr/logging/log.h>
#include <zephyr/drivers/clock_control/nrf_clock_control.h>
LOG_MODULE_REGISTER(lism_left_radio,LOG_LEVEL_INF);
#define NODE DT_COMPAT_GET_ANY_STATUS_OKAY(zmk_esb_split)
static const uint8_t dongle_base0[]=DT_PROP(NODE,base_addr_0),dongle_base1[]=DT_PROP(NODE,base_addr_1);
static const uint8_t private_base0[]={0xb7,0x63,0xa9,0xe7},private_base1[]={0xc9,0x57,0x8d,0xf1};
static const uint8_t prefixes[]=DT_PROP(NODE,addr_prefix);
static app_esb_callback_t callback;
static atomic_t ready,enabled,tx_result;
static struct k_spinlock left_lock;
static struct kb_left_policy policy;
static struct lism_snapshot physical;
struct left_edge {struct kb_left_packet packet;uint32_t generation;};
K_MSGQ_DEFINE(edges,sizeof(struct left_edge),64,4);
static uint32_t route_generation,boot_id,data_seq[2]={1,1},control_seq=1,command_boot[2],previous_boot[2],command_seq[2];
static atomic_t edge_fault;
struct left_rx {struct esb_payload p;unsigned net;};
static struct left_rx received[8];static atomic_t rx_head,rx_tail;
static unsigned current_net;
static void put32(uint8_t*p,uint32_t v){for(unsigned i=0;i<4;i++)p[i]=v>>(i*8);}
static uint32_t get32(const uint8_t*p){return p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static bool all_up(void){return !physical.keys && !physical.buttons;}
static void packet(struct kb_left_packet*p){*p=(struct kb_left_packet){.authority=policy.authority,.epoch=policy.epoch,.buttons=physical.buttons,.battery=physical.battery};kb_left_pack_keys(p,physical.keys);}
static void queue_snapshot(void){struct left_edge e={.generation=route_generation};packet(&e.packet);if(k_msgq_put(&edges,&e,K_NO_WAIT))atomic_set(&edge_fault,route_generation+1);}
void kb_left_track(const struct zmk_split_transport_peripheral_event*e){
    k_spinlock_key_t k=k_spin_lock(&left_lock);
    if(e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT){
        unsigned pos=e->data.key_position_event.position;if(pos<LISM_KEYS){uint64_t bit=UINT64_C(1)<<pos;if(e->data.key_position_event.pressed)physical.keys|=bit;else physical.keys&=~bit;}
    }else if(e->type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT)physical.battery=e->data.battery_event.level;
    else if((int)e->type==LISM_SNAPSHOT_TYPE){struct lism_snapshot s;memcpy(&s,&e->data,sizeof(s));physical.battery=s.battery;}
    unsigned phase=policy.phase;kb_left_observe(&policy,all_up());
    if(policy.phase==KB_LEFT_APPLIED){if(phase!=KB_LEFT_APPLIED)route_generation++;queue_snapshot();}
    k_spin_unlock(&left_lock,k);
}
static void event(const struct esb_evt*e){
    if(e->evt_id==ESB_EVENT_TX_SUCCESS)atomic_set(&tx_result,1);
    if(e->evt_id==ESB_EVENT_TX_FAILED)atomic_set(&tx_result,-1);
    if(e->evt_id==ESB_EVENT_RX_RECEIVED){struct esb_payload p;while(!esb_read_rx_payload(&p)){unsigned h=atomic_get(&rx_head),n=(h+1)%ARRAY_SIZE(received);if(n!=(unsigned)atomic_get(&rx_tail)){received[h]=(struct left_rx){.p=p,.net=current_net};atomic_set(&rx_head,n);}}}
}
static int configure(unsigned net){
    struct esb_config c=ESB_DEFAULT_CONFIG;c.protocol=ESB_PROTOCOL_ESB_DPL;c.bitrate=ESB_BITRATE_2MBPS;c.mode=ESB_MODE_PTX;c.tx_mode=ESB_TXMODE_MANUAL_START;c.selective_auto_ack=true;c.event_handler=event;c.retransmit_delay=600;c.retransmit_count=0;c.tx_output_power=ESB_TX_POWER_8DBM;c.use_fast_ramp_up=true;
    int rc=esb_init(&c);if(!rc)rc=esb_set_base_address_0(net?private_base0:dongle_base0);if(!rc)rc=esb_set_base_address_1(net?private_base1:dongle_base1);if(!rc)rc=esb_set_prefixes(prefixes,ARRAY_SIZE(prefixes));if(!rc)rc=esb_enable_pipes(BIT(0));if(!rc)rc=esb_set_rf_channel(37);current_net=net;return rc;
}
static void receive(void){
    unsigned t;while((t=atomic_get(&rx_tail))!=(unsigned)atomic_get(&rx_head)){
        struct left_rx item=received[t];struct esb_payload p=item.p;atomic_set(&rx_tail,(t+1)%ARRAY_SIZE(received));
        if(p.pipe || p.length<LISM_WIRE_HEADER+sizeof(struct esb_msg_prefix)+2+sizeof(struct esb_msg_postfix) || p.length>64 || memcmp(p.data,"LsE2",4) || get32(p.data+12)!=boot_id)continue;
        size_t n=p.length-LISM_WIRE_HEADER;struct esb_command_envelope env={0};if(n-sizeof(struct esb_msg_postfix)>sizeof(env))continue;
        struct esb_msg_postfix crc;memcpy(&crc,p.data+p.length-sizeof(crc),sizeof(crc));
        if(crc32_ieee(p.data+LISM_WIRE_HEADER,n-sizeof(crc))!=crc.crc || !lism_wire_valid(0,p.data+LISM_WIRE_HEADER,n))continue;
        memcpy(&env,p.data+LISM_WIRE_HEADER,n-sizeof(crc));if(memcmp(env.prefix.magic_prefix,"ZmKe",4))continue;
        uint32_t b=get32(p.data+4),s=get32(p.data+8);if(!b || !s)continue;
        unsigned net=item.net;if(b!=command_boot[net]){if(b==previous_boot[net])continue;previous_boot[net]=command_boot[net];command_boot[net]=b;command_seq[net]=0;}
        if(s<=command_seq[net])continue;
        const struct zmk_split_transport_central_command*c=&env.payload.cmd;bool accepted=false;
        if(net && c->type==ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR && !strcmp(c->data.invoke_behavior.behavior_dev,"KB_LEFT_ROUTE")){
            if(c->data.invoke_behavior.state!=1)continue;
            unsigned v=c->data.invoke_behavior.param1;uint32_t ep=c->data.invoke_behavior.param2;
            k_spinlock_key_t k=k_spin_lock(&left_lock);struct kb_left_policy old=policy;
            accepted=kb_left_policy_command(&policy,b,ep,v>>1,v&1,all_up());
            if(accepted && memcmp(&old,&policy,sizeof(old))){route_generation++;k_msgq_purge(&edges);if(policy.phase==KB_LEFT_APPLIED)queue_snapshot();}
            k_spin_unlock(&left_lock,k);
        }else{
            k_spinlock_key_t k=k_spin_lock(&left_lock);bool allowed=policy.phase==KB_LEFT_APPLIED && policy.target==net && get32(p.data+16)==policy.epoch;k_spin_unlock(&left_lock,k);
            if(allowed){app_esb_event_t e={.evt_type=APP_ESB_EVT_RX,.pipe=0,.buf=p.data+LISM_WIRE_HEADER,.data_length=n};callback(&e);accepted=e.accepted;}
        }
        if(accepted)command_seq[net]=s;
    }
}
static void frame(struct esb_payload*p,unsigned net,unsigned type,const void*data,size_t bytes){
    *p=(struct esb_payload){.pipe=0,.length=LISM_WIRE_HEADER+sizeof(struct esb_msg_prefix)+1+sizeof(enum zmk_split_transport_peripheral_event_type)+bytes+sizeof(struct esb_msg_postfix)};
    memcpy(p->data,"LsE2",4);put32(p->data+4,boot_id);put32(p->data+8,type==KB_LEFT_CONTROL?control_seq++:data_seq[net]++);put32(p->data+12,command_boot[net]);put32(p->data+16,command_seq[net]);
    struct esb_event_envelope env={.prefix={.magic_prefix=ZMK_SPLIT_ESB_ENVELOPE_MAGIC_PREFIX,.payload_size=1+sizeof(env.payload.event.type)+bytes},.payload={.source=0,.event={.type=type}}};memcpy(&env.payload.event.data,data,bytes);size_t n=sizeof(env.prefix)+env.prefix.payload_size;memcpy(p->data+LISM_WIRE_HEADER,&env,n);struct esb_msg_postfix crc={.crc=crc32_ieee(p->data+LISM_WIRE_HEADER,n)};memcpy(p->data+LISM_WIRE_HEADER+n,&crc,sizeof(crc));
}
static int clocks_start(void){struct onoff_manager*m=z_nrf_clock_control_get_onoff(CLOCK_CONTROL_NRF_SUBSYS_HF);struct onoff_client c;int result,rc;if(!m)return -ENXIO;sys_notify_init_spinwait(&c.notify);rc=onoff_request(m,&c);if(rc<0)return rc;while(sys_notify_fetch_result(&c.notify,&result))k_sleep(K_MSEC(1));return result;}
static void radio_thread(void*a,void*b,void*c){
    ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);while(!callback)k_sleep(K_MSEC(1));int rc=clocks_start();if(rc)goto fail;
    do{boot_id=sys_rand32_get();}while(!boot_id);if((rc=configure(1)))goto fail;atomic_set(&ready,1);lism_notify_status();
    struct esb_payload tx,data_tx;struct left_edge edge;bool data_pending=false;int64_t data_since=0,control_seen=0;unsigned probe_rounds=0;bool have=false,in_flight=false,is_control=false,control_yield_data=false;int64_t next_control=0,since=0;uint32_t selected_generation=0;unsigned net=1,attempts=0;
    for(;;){
        receive();int result=atomic_set(&tx_result,0);int64_t now=k_uptime_get();
        k_spinlock_key_t k=k_spin_lock(&left_lock);bool invalid=!is_control && (selected_generation!=route_generation || policy.phase!=KB_LEFT_APPLIED);k_spin_unlock(&left_lock,k);
        if(have && (result==1 || invalid || now-since>=(is_control?3:12))){
            if(in_flight){esb_disable();if((rc=configure(current_net)))goto fail;}have=in_flight=false;
            if(is_control)control_yield_data=true;
            if(!is_control && (result==1 || invalid))data_pending=false;
            if(result==1){if(is_control)control_seen=now;lism_ack_seen();app_esb_event_t e={.evt_type=APP_ESB_EVT_TX_SUCCESS};callback(&e);}
        }else if(have && result<0){esb_flush_tx();in_flight=false;if(++attempts>=2 || (is_control && k_msgq_num_used_get(&edges))){have=false;if(is_control)control_yield_data=true;}}
        if(data_pending && now-data_since>=500){
            atomic_set(&edge_fault,edge.generation+1);data_pending=false;
            if(have && !is_control){esb_flush_tx();have=in_flight=false;}
            LOG_WRN("Left data acknowledgement timeout; input quarantined until release");
        }
        uint32_t fault=atomic_set(&edge_fault,0);
        if(fault){
            /* Never replay a queue truncated across a lost edge. Quarantine
             * until real all-up, then recover with an authoritative snapshot. */
            k=k_spin_lock(&left_lock);if(kb_left_fault(&policy,fault-1==route_generation,all_up())){route_generation++;k_msgq_purge(&edges);if(policy.phase==KB_LEFT_APPLIED)queue_snapshot();}k_spin_unlock(&left_lock,k);
        }
        if(!have && atomic_get(&enabled)){
            if(now>=next_control && !(control_yield_data && (data_pending || k_msgq_num_used_get(&edges)))){
                struct kb_left_control ctrl;k=k_spin_lock(&left_lock);packet(&ctrl.physical);ctrl.phase=policy.phase;ctrl.target=policy.target;k_spin_unlock(&left_lock,k);
                net=1;is_control=true;frame(&tx,net,KB_LEFT_CONTROL,&ctrl,sizeof(ctrl));have=true;
                /* Noncommensurate bounded jitter avoids a permanent phase
                 * miss against the right's periodic private receive grant. */
                next_control=now+((control_seen && now-control_seen<200)?17+(sys_rand32_get()%15):7+(probe_rounds++&1));
            }else{
                k=k_spin_lock(&left_lock);bool live=data_pending && edge.generation==route_generation && policy.phase==KB_LEFT_APPLIED;k_spin_unlock(&left_lock,k);
                if(data_pending && !live)data_pending=false;
                if(!data_pending)for(unsigned n=0;n<8 && !k_msgq_get(&edges,&edge,K_NO_WAIT);n++){
                    k=k_spin_lock(&left_lock);bool valid=edge.generation==route_generation && policy.phase==KB_LEFT_APPLIED;net=policy.target;selected_generation=route_generation;k_spin_unlock(&left_lock,k);
                    if(!valid)continue;frame(&data_tx,net,KB_LEFT_DATA,&edge.packet,sizeof(edge.packet));data_pending=true;data_since=now;break;
                }
                if(data_pending){k=k_spin_lock(&left_lock);net=policy.target;selected_generation=route_generation;k_spin_unlock(&left_lock,k);tx=data_tx;is_control=false;have=true;control_yield_data=false;}
            }
            if(have){since=now;attempts=0;if(net!=current_net){esb_disable();if((rc=configure(net)))goto fail;}atomic_clear(&tx_result);}
        }
        if(have && !in_flight && atomic_get(&enabled)){rc=esb_write_payload(&tx);if(!rc)rc=esb_start_tx();if(!rc)in_flight=true;else if(rc!=-EBUSY){esb_flush_tx();have=false;}}
        k_sleep(K_MSEC(1));
    }
fail:atomic_clear(&ready);lism_notify_status();LOG_ERR("Left radio stopped %d",rc);
}
K_THREAD_DEFINE(lism_radio_thread,3072,radio_thread,NULL,NULL,NULL,K_PRIO_COOP(8),0,0);
bool lism_radio_ready(void){return atomic_get(&ready);}
int zmk_split_esb_init(app_esb_mode_t mode,app_esb_callback_t cb){ARG_UNUSED(mode);callback=cb;return 0;}
int zmk_split_esb_set_enable(bool v){atomic_set(&enabled,v);return 0;}
int zmk_split_esb_send(app_esb_data_t*p){ARG_UNUSED(p);return -ENOTSUP;}
