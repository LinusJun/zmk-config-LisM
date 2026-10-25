/* SPDX-License-Identifier: MIT
 * Left has three distinct PTX networks. A bounded private control exchange
 * cannot be blocked by retries to a powered-off data target. */
#include "app_esb.h"
#include <kb_route/radio_channels.h>
#include "common.h"
#include "link.h"
#include <kb_route/left_protocol.h>
#include <esb.h>
#include <zephyr/random/random.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/crc.h>
#include <zephyr/logging/log.h>
#include <zephyr/drivers/clock_control/nrf_clock_control.h>
#include <hal/nrf_clock.h>
#include <hal/nrf_rng.h>
#include <hal/nrf_power.h>
#include <left_boot_trace.h>
LOG_MODULE_REGISTER(lism_left_radio,LOG_LEVEL_INF);
#define NODE DT_COMPAT_GET_ANY_STATUS_OKAY(zmk_esb_split)
static const uint8_t dongle_base0[]=DT_PROP(NODE,base_addr_0),dongle_base1[]=DT_PROP(NODE,base_addr_1);
static const uint8_t private_base0[]={0xb7,0x63,0xa9,0xe7},private_base1[]={0xc9,0x57,0x8d,0xf1};
static const uint8_t prefixes[]=DT_PROP(NODE,addr_prefix);
static app_esb_callback_t callback;
static atomic_t ready,enabled,tx_result;
enum left_boot_stage {LEFT_BOOT_ENTERED=1,LEFT_BOOT_HFCLK,LEFT_BOOT_RNG,LEFT_BOOT_ESB,LEFT_BOOT_READY,LEFT_BOOT_FAILED};
static atomic_t left_boot_stage,left_boot_result;
static struct k_spinlock left_lock;
static struct kb_left_policy policy={.target=1,.station=KB_STATION_NONE};
static struct lism_snapshot physical;
struct left_edge {struct kb_left_packet packet;uint32_t generation;};
K_MSGQ_DEFINE(edges,sizeof(struct left_edge),64,4);
static uint32_t route_generation,boot_id,data_seq[KB_NET_COUNT]={1,1,1},control_seq=1,command_boot[KB_NET_COUNT],previous_boot[KB_NET_COUNT],command_seq[KB_NET_COUNT];
static int64_t coordinator_seen;
static uint32_t route_received,route_rejected,route_duplicate,lease_expired,data_acks,control_acks,max_route_gap;
static uint32_t retained_retry,retry_start_fail;
static atomic_t edge_fault;
struct left_rx {struct esb_payload p;unsigned net;};
static struct left_rx received[8];static atomic_t rx_head,rx_tail;
static unsigned current_net;
static atomic_t control_urgent;
/* v5.0.4.1 DIAG: thread-owned, mutually exclusive receive outcomes. */
struct left_rx_diag {uint32_t frames,header,target,crc,body,old_boot,sequence,policy,dispatch,accepted,last_target;};
static struct left_rx_diag rx_diag[KB_NET_COUNT];
static atomic_t rx_ring_dropped;
static void put32(uint8_t*p,uint32_t v){for(unsigned i=0;i<4;i++)p[i]=v>>(i*8);}
static uint32_t get32(const uint8_t*p){return p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static bool all_up(void){return !physical.keys && !physical.buttons;}
static void packet(struct kb_left_packet*p){*p=(struct kb_left_packet){.authority=policy.authority,.epoch=policy.epoch,.receiver_boot=policy.receiver_boot,.station=policy.station,.buttons=physical.buttons,.battery=physical.battery};kb_left_pack_keys(p,physical.keys);}
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
    if(e->evt_id==ESB_EVENT_RX_RECEIVED){struct esb_payload p;while(!esb_read_rx_payload(&p)){unsigned h=atomic_get(&rx_head),n=(h+1)%ARRAY_SIZE(received);if(n!=(unsigned)atomic_get(&rx_tail)){received[h]=(struct left_rx){.p=p,.net=current_net};atomic_set(&rx_head,n);}else atomic_inc(&rx_ring_dropped);}}
}
static int configure(unsigned net){
    struct esb_config c=ESB_DEFAULT_CONFIG;c.protocol=ESB_PROTOCOL_ESB_DPL;c.bitrate=ESB_BITRATE_2MBPS;c.mode=ESB_MODE_PTX;c.tx_mode=ESB_TXMODE_MANUAL_START;c.selective_auto_ack=true;c.event_handler=event;c.retransmit_delay=600;c.retransmit_count=0;c.tx_output_power=ESB_TX_POWER_8DBM;c.use_fast_ramp_up=true;
    if(net>=KB_NET_COUNT)return -EINVAL;
    unsigned station=net==KB_NET_DONGLE_B?1:0;
    int rc=esb_init(&c);if(!rc)rc=esb_set_base_address_0(net==KB_NET_PRIVATE?private_base0:kb_station_base0[station]);if(!rc)rc=esb_set_base_address_1(net==KB_NET_PRIVATE?private_base1:kb_station_base1[station]);if(!rc)rc=esb_set_prefixes(prefixes,ARRAY_SIZE(prefixes));if(!rc)rc=esb_enable_pipes(BIT(0));if(!rc)rc=esb_set_rf_channel(kb_radio_channel(net));current_net=net;return rc;
}
static void receive(void){
    unsigned t;while((t=atomic_get(&rx_tail))!=(unsigned)atomic_get(&rx_head)){
        struct left_rx item=received[t];struct esb_payload p=item.p;atomic_set(&rx_tail,(t+1)%ARRAY_SIZE(received));
        unsigned net=item.net;struct left_rx_diag *d=&rx_diag[net];d->frames++;
        if(p.pipe || p.length<LISM_WIRE_HEADER+sizeof(struct esb_msg_prefix)+2+sizeof(struct esb_msg_postfix) || p.length>64 || memcmp(p.data,"LsE3",4)){d->header++;continue;}
        if(get32(p.data+12)!=boot_id){d->target++;d->last_target=get32(p.data+12);continue;}
        size_t n=p.length-LISM_WIRE_HEADER;struct esb_command_envelope env={0};if(n-sizeof(struct esb_msg_postfix)>sizeof(env)){d->body++;continue;}
        struct esb_msg_postfix crc;memcpy(&crc,p.data+p.length-sizeof(crc),sizeof(crc));
        if(crc32_ieee(p.data+LISM_WIRE_HEADER,n-sizeof(crc))!=crc.crc){d->crc++;continue;}
        if(!lism_wire_valid(0,p.data+LISM_WIRE_HEADER,n)){d->body++;continue;}
        memcpy(&env,p.data+LISM_WIRE_HEADER,n-sizeof(crc));if(memcmp(env.prefix.magic_prefix,"ZmKe",4)){d->body++;continue;}
        uint32_t b=get32(p.data+4),s=get32(p.data+8);if(!b || !s){d->body++;continue;}
        if(b!=command_boot[net]){if(b==previous_boot[net]){d->old_boot++;continue;}previous_boot[net]=command_boot[net];command_boot[net]=b;command_seq[net]=0;}
        if(s<=command_seq[net]){if(net==KB_NET_PRIVATE)route_duplicate++;d->sequence++;continue;}
        const struct zmk_split_transport_central_command*c=&env.payload.cmd;bool accepted=false;
        if(net==KB_NET_PRIVATE && c->type==ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR && !strcmp(c->data.invoke_behavior.behavior_dev,"KB_LEFT_ROUTE3")){
            if(c->data.invoke_behavior.state!=1){d->policy++;continue;}
            unsigned v=c->data.invoke_behavior.param1;uint32_t ep=c->data.invoke_behavior.param2;
            k_spinlock_key_t k=k_spin_lock(&left_lock);struct kb_left_policy old=policy;
            unsigned station=(v&3)==2?KB_STATION_NONE:(v&3);
            accepted=kb_left_policy_command(&policy,b,ep,v>>4,(v>>2)&3,station,c->data.invoke_behavior.position,all_up());
            if(accepted){int64_t at=k_uptime_get();uint32_t gap=coordinator_seen?(uint32_t)(at-coordinator_seen):0;if(gap>max_route_gap)max_route_gap=gap;coordinator_seen=at;route_received++;}else route_rejected++;
            if(accepted && memcmp(&old,&policy,sizeof(old))){atomic_set(&control_urgent,1);route_generation++;k_msgq_purge(&edges);if(policy.phase==KB_LEFT_APPLIED)queue_snapshot();}
            k_spin_unlock(&left_lock,k);
            if(!accepted)d->policy++;
        }else{
            k_spinlock_key_t k=k_spin_lock(&left_lock);bool allowed=policy.phase==KB_LEFT_APPLIED && kb_left_network(policy.target,policy.station)==net && get32(p.data+16)==policy.epoch && (net==KB_NET_PRIVATE || b==policy.receiver_boot);k_spin_unlock(&left_lock,k);
            if(allowed){app_esb_event_t e={.evt_type=APP_ESB_EVT_RX,.pipe=0,.buf=p.data+LISM_WIRE_HEADER,.data_length=n};callback(&e);accepted=e.accepted;if(!accepted)d->dispatch++;}else d->policy++;
        }
        if(accepted){command_seq[net]=s;d->accepted++;}
    }
}
static void frame(struct esb_payload*p,unsigned net,unsigned type,const void*data,size_t bytes){
    *p=(struct esb_payload){.pipe=0,.length=LISM_WIRE_HEADER+sizeof(struct esb_msg_prefix)+1+sizeof(enum zmk_split_transport_peripheral_event_type)+bytes+sizeof(struct esb_msg_postfix)};
    memcpy(p->data,"LsE3",4);put32(p->data+4,boot_id);put32(p->data+8,type==KB_LEFT_CONTROL?control_seq++:data_seq[net]++);put32(p->data+12,net==KB_NET_PRIVATE?command_boot[net]:((const struct kb_left_packet*)data)->receiver_boot);put32(p->data+16,command_seq[net]);
    struct esb_event_envelope env={.prefix={.magic_prefix=ZMK_SPLIT_ESB_ENVELOPE_MAGIC_PREFIX,.payload_size=1+sizeof(env.payload.event.type)+bytes},.payload={.source=0,.event={.type=type}}};memcpy(&env.payload.event.data,data,bytes);size_t n=sizeof(env.prefix)+env.prefix.payload_size;memcpy(p->data+LISM_WIRE_HEADER,&env,n);struct esb_msg_postfix crc={.crc=crc32_ieee(p->data+LISM_WIRE_HEADER,n)};memcpy(p->data+LISM_WIRE_HEADER+n,&crc,sizeof(crc));
}
extern int lism_left_hfclk_finish_pending(void);
static int clocks_start(void){
    struct onoff_manager*m=z_nrf_clock_control_get_onoff(CLOCK_CONTROL_NRF_SUBSYS_HF);
    struct onoff_client c;int result,rc;if(!m)return -ENXIO;
    sys_notify_init_spinwait(&c.notify);rc=onoff_request(m,&c);lism_left_clock_request_state=rc;if(rc<0)return rc;
    int64_t since=k_uptime_get();bool checked=false;
    while(sys_notify_fetch_result(&c.notify,&result)){
        if(!checked && k_uptime_get()-since>=100){
            checked=true;
            int completed=lism_left_hfclk_finish_pending();
            LOG_INF("Left HFCLK pending request_state=%d handoff_completed=%d",rc,completed);
        }
        k_sleep(K_MSEC(1));
    }
    return result;
}
static void radio_thread(void*a,void*b,void*c){
    ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);atomic_set(&left_boot_stage,LEFT_BOOT_ENTERED);LOG_INF("Left INIT41 thread entered");while(!callback)k_sleep(K_MSEC(1));
    atomic_set(&left_boot_stage,LEFT_BOOT_HFCLK);LOG_INF("Left INIT41 callback ready; HFCLK request");int rc=clocks_start();atomic_set(&left_boot_result,rc);LOG_INF("Left INIT41 HFCLK rc=%d",rc);if(rc)goto fail;
    atomic_set(&left_boot_stage,LEFT_BOOT_RNG);do{boot_id=sys_rand32_get();}while(!boot_id);atomic_set(&left_boot_stage,LEFT_BOOT_ESB);rc=configure(1);atomic_set(&left_boot_result,rc);LOG_INF("Left INIT41 boot=%u configure rc=%d",boot_id,rc);if(rc)goto fail;lism_left_trace_configured();atomic_set(&ready,1);atomic_set(&left_boot_stage,LEFT_BOOT_READY);lism_notify_status();
    struct esb_payload tx,data_tx;struct left_edge edge;bool data_pending=false;int64_t data_since=0,control_seen=0;unsigned probe_rounds=0;bool have=false,in_flight=false,retry_queued=false,is_control=false,control_yield_data=false;int64_t next_control=0,since=0;uint32_t selected_generation=0;unsigned net=1,attempts=0;int64_t next_diag=0;
    bool expiry_reported=false;
    for(;;){
        receive();int result=atomic_set(&tx_result,0);int64_t now=k_uptime_get();
        if(atomic_set(&control_urgent,0)){next_control=0;control_yield_data=false;}
        /* The coordinator is a lease, not a hardware ACK. In asymmetric RF
         * loss the left must stop renewing an abandoned station by itself. */
        bool report_expiry=false;uint32_t expiry_epoch=0,expiry_age=0;
        k_spinlock_key_t lease=k_spin_lock(&left_lock);
        if(policy.phase==KB_LEFT_APPLIED && (!coordinator_seen || now-coordinator_seen>=KB_LEFT_CONTROL_LEASE_MS)){
            lease_expired++;policy.phase=KB_LEFT_FREEZE;policy.prepared=false;policy.needs_freeze=true;route_generation++;k_msgq_purge(&edges);
            if(!expiry_reported){expiry_reported=true;report_expiry=true;expiry_epoch=policy.epoch;expiry_age=coordinator_seen?(uint32_t)(now-coordinator_seen):UINT32_MAX;}
        }
        k_spin_unlock(&left_lock,lease);
        if(report_expiry)lism_left_trace_expiry(boot_id,expiry_epoch,current_net,expiry_age,control_seen?(uint32_t)(now-control_seen):UINT32_MAX);
        k_spinlock_key_t k=k_spin_lock(&left_lock);bool invalid=!is_control && (selected_generation!=route_generation || policy.phase!=KB_LEFT_APPLIED);k_spin_unlock(&left_lock,k);
        if(have && (result==1 || invalid || now-since>=(is_control?3:12))){
            if(in_flight){esb_disable();if((rc=configure(current_net)))goto fail;}else if(retry_queued)esb_flush_tx();have=in_flight=retry_queued=false;
            if(is_control)control_yield_data=true;
            if(!is_control && (result==1 || invalid))data_pending=false;
            if(result==1){if(is_control){control_seen=now;control_acks++;}else data_acks++;lism_ack_seen();app_esb_event_t e={.evt_type=APP_ESB_EVT_TX_SUCCESS};callback(&e);}
        }else if(have && result<0){
            in_flight=false;
            if(++attempts>=2 || (is_control && k_msgq_num_used_get(&edges))){
                esb_flush_tx();have=retry_queued=false;if(is_control)control_yield_data=true;
            }else{
                /* TX_FAILED leaves the original SDK FIFO head queued. Start
                 * that exact packet again: flush+write would increment its
                 * PID and make PRX consume an ACK payload lost on attempt 1. */
                /* Defer the start to the original enabled gate below, after
                 * data expiry and quarantine have had their normal priority. */
                retry_queued=true;
            }
        }
        if(data_pending && now-data_since>=500){
            atomic_set(&edge_fault,edge.generation+1);data_pending=false;
            if(have && !is_control){
                /* A retained retry can already be on air when its total data
                 * deadline arrives. Flush alone does not stop that RADIO. */
                if(in_flight){esb_disable();if((rc=configure(current_net)))goto fail;}
                else esb_flush_tx();
                have=in_flight=retry_queued=false;
            }
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
                struct kb_left_control ctrl;k=k_spin_lock(&left_lock);packet(&ctrl.physical);ctrl.phase=policy.phase;ctrl.target=policy.target;ctrl.prepared=policy.prepared;k_spin_unlock(&left_lock,k);
                net=1;is_control=true;frame(&tx,net,KB_LEFT_CONTROL,&ctrl,sizeof(ctrl));have=true;
                /* Noncommensurate bounded jitter avoids a permanent phase
                 * miss against the right's periodic private receive grant. */
                /* Empty hardware ACKs do not renew the route. Keep fast bounded
                 * probing until a fresh, accepted route command arrives. */
                next_control=now+((coordinator_seen && now-coordinator_seen<200)?17+(sys_rand32_get()%15):7+(probe_rounds++&1));
            }else{
                k=k_spin_lock(&left_lock);bool live=data_pending && edge.generation==route_generation && policy.phase==KB_LEFT_APPLIED;k_spin_unlock(&left_lock,k);
                if(data_pending && !live)data_pending=false;
                if(!data_pending)for(unsigned n=0;n<8 && !k_msgq_get(&edges,&edge,K_NO_WAIT);n++){
                    k=k_spin_lock(&left_lock);bool valid=edge.generation==route_generation && policy.phase==KB_LEFT_APPLIED;net=kb_left_network(policy.target,policy.station);selected_generation=route_generation;k_spin_unlock(&left_lock,k);
                    if(!valid)continue;frame(&data_tx,net,KB_LEFT_DATA,&edge.packet,sizeof(edge.packet));data_pending=true;data_since=now;break;
                }
                if(data_pending){k=k_spin_lock(&left_lock);net=kb_left_network(policy.target,policy.station);selected_generation=route_generation;k_spin_unlock(&left_lock,k);tx=data_tx;is_control=false;have=true;control_yield_data=false;}
            }
            if(have){since=now;attempts=0;retry_queued=false;if(net!=current_net){esb_disable();if((rc=configure(net)))goto fail;}atomic_clear(&tx_result);}
        }
        if(have && !in_flight && atomic_get(&enabled)){
            rc=retry_queued?0:esb_write_payload(&tx);if(!rc)rc=esb_start_tx();
            if(!rc){in_flight=true;if(retry_queued)retained_retry++;}
            else if(retry_queued){
                retry_start_fail++;
                /* Busy is an active SDK transaction, not a safe cancellation.
                 * Keep tracking it until its event or the existing deadline. */
                if(rc==-EBUSY)in_flight=true;
                else{esb_flush_tx();have=retry_queued=false;if(is_control)control_yield_data=true;}
            }else if(rc!=-EBUSY){esb_flush_tx();have=false;}
        }
        if(now>=next_diag){
            next_diag=now+5000;k=k_spin_lock(&left_lock);struct kb_left_policy d=policy;int64_t last=coordinator_seen;k_spin_unlock(&left_lock,k);
            LOG_INF("Left route auth=%u epoch=%u station=%u phase=%u prepared=%u age_ms=%u max_gap=%u rx=%u reject=%u duplicate=%u expiry=%u data_ack=%u ctrl_ack=%u hw_age=%u queue=%u",
                d.authority,d.epoch,d.station,d.phase,d.prepared,last?(unsigned)(now-last):UINT32_MAX,max_route_gap,route_received,route_rejected,route_duplicate,lease_expired,data_acks,control_acks,control_seen?(unsigned)(now-control_seen):UINT32_MAX,k_msgq_num_used_get(&edges));
            struct left_rx_diag *r=&rx_diag[KB_NET_PRIVATE];
            LOG_INF("Left RX41 boot=%u raw=%u/%u/%u ring_drop=%u target=%u last_target=%u hdr=%u crc=%u body=%u old=%u seq=%u policy=%u dispatch=%u accept=%u",
                boot_id,rx_diag[0].frames,rx_diag[1].frames,rx_diag[2].frames,(unsigned)atomic_get(&rx_ring_dropped),r->target,r->last_target,r->header,r->crc,r->body,r->old_boot,r->sequence,r->policy,r->dispatch,r->accepted);
            LOG_INF("Left RETRY48 retained=%u start_fail=%u",retained_retry,retry_start_fail);
        }
        k_sleep(K_MSEC(1));
    }
fail:atomic_set(&left_boot_result,rc);atomic_set(&left_boot_stage,LEFT_BOOT_FAILED);atomic_clear(&ready);lism_notify_status();LOG_ERR("Left radio stopped %d",rc);
}
K_THREAD_DEFINE(lism_radio_thread,3072,radio_thread,NULL,NULL,NULL,K_PRIO_COOP(8),0,0);
/* Early CDC output can be lost during UF2 handoff. This one-shot observer
 * never touches clock/radio state and exits once transport is enabled. */
struct zmk_split_transport_peripheral;
extern const struct zmk_split_transport_peripheral *active_transport;
static void left_boot_observer(void *a,void *b,void *c){
    ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
    lism_left_trace_boot_log();
    for(unsigned sample=0;sample<15;sample++){
        LOG_INF("Left BOOT42 stage=%u rc=%d cb=%u ready=%u enabled=%u transport=%u boot=%u reset=%x",
            (unsigned)atomic_get(&left_boot_stage),(int)atomic_get(&left_boot_result),callback!=NULL,(unsigned)atomic_get(&ready),(unsigned)atomic_get(&enabled),active_transport!=NULL,boot_id,(unsigned)NRF_POWER->RESETREAS);
        if(atomic_get(&ready) && atomic_get(&enabled) && active_transport)return;
        LOG_INF("Left HW42 hfstat=%x hfevent=%u clock_int=%x clock_irq=%u/%u rng_event=%u rng_int=%x rng_irq=%u/%u",
            (unsigned)NRF_CLOCK->HFCLKSTAT,(unsigned)NRF_CLOCK->EVENTS_HFCLKSTARTED,(unsigned)NRF_CLOCK->INTENSET,
            (unsigned)NVIC_GetEnableIRQ(POWER_CLOCK_IRQn),(unsigned)NVIC_GetPendingIRQ(POWER_CLOCK_IRQn),
            (unsigned)NRF_RNG->EVENTS_VALRDY,(unsigned)NRF_RNG->INTENSET,(unsigned)NVIC_GetEnableIRQ(RNG_IRQn),(unsigned)NVIC_GetPendingIRQ(RNG_IRQn));
        k_sleep(K_SECONDS(2));
    }
}
K_THREAD_DEFINE(lism_left_boot_observer,1024,left_boot_observer,NULL,NULL,NULL,K_LOWEST_APPLICATION_THREAD_PRIO,0,1500);
bool lism_radio_ready(void){return atomic_get(&ready);}
int zmk_split_esb_init(app_esb_mode_t mode,app_esb_callback_t cb){ARG_UNUSED(mode);callback=cb;return 0;}
int zmk_split_esb_set_enable(bool v){atomic_set(&enabled,v);return 0;}
int zmk_split_esb_send(app_esb_data_t*p){ARG_UNUSED(p);return -ENOTSUP;}
