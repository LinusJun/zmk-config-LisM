#if defined(CONFIG_LISM_KB_HUB)
#include "hub_radio.inc"
#elif defined(CONFIG_SHIELD_LISM_L)
#include "left_radio.inc"
#else
/* SPDX-License-Identifier: MIT
 * Nordic ESB + badjeff app_esb API. PTX owns its radio continuously; the BLE
 * host receiver owns ESB only inside MPSL slots. IRQ0 copies to an SPSC ring;
 * callbacks, queue operations, clocks, logs and retries run on this thread.
 */
#include "app_esb.h"
#include "common.h"
#if IS_ENABLED(CONFIG_LISM_BLE_INPUT_BRIDGE)
#include <lism_screen.h>
#endif
#include "link.h"
#include <kb_route/route.h>
#include <kb_route/signal.h>
#include <esb.h>
#include <kb_route/owner_policy.h>
#include <zephyr/sys/reboot.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/crc.h>
#include <zephyr/random/random.h>
#include <zephyr/drivers/clock_control/nrf_clock_control.h>
#include <hal/nrf_timer.h>
#include <hal/nrf_radio.h>
#include <zephyr/logging/log.h>
#if IS_ENABLED(CONFIG_ZMK_BLE)
#include <mpsl_timeslot.h>
#include <sdc_soc.h>
#include <mpsl/mpsl_work.h>
extern int lism_esb_resources_init(void);
extern void lism_esb_radio_irq(void);
extern void lism_esb_ppi_channels_get(uint8_t *channels);
#endif
LOG_MODULE_REGISTER(lism_radio,LOG_LEVEL_INF);
#if IS_ENABLED(CONFIG_MPSL_ASSERT_HANDLER)
/* A fatal MPSL assertion cannot safely return to a damaged radio scheduler. */
void mpsl_assert_handle(const char *file,int line) { ARG_UNUSED(file);ARG_UNUSED(line);k_panic(); }
#endif
#define NODE DT_COMPAT_GET_ANY_STATUS_OKAY(zmk_esb_split)
#if defined(CONFIG_SHIELD_LISM_L)
static const uint8_t base0[]={0xB7,0x63,0xA9,0xE7},base1[]={0xC9,0x57,0x8D,0xF1};
#else
static const uint8_t base0[]= {0xb7,0x63,0xa9,CONFIG_LISM_DONGLE_STATION==0?0x51:0x52};
static const uint8_t base1[]= {0xb7,0x63,0xa9,CONFIG_LISM_DONGLE_STATION==0?0x51:0x52};
#endif
static const uint8_t prefixes[]={0xa7,0xd3,0x69,0xb5};
#define SLOT_US 4000
#define WIRE_HEADER LISM_WIRE_HEADER
struct tx_item { struct esb_payload payload;uint8_t retries;uint32_t target_boot,generation;int64_t expires;bool discovery; };
static atomic_t command_generation;
static struct k_spinlock signal_lock;
static struct {int8_t dbm;int64_t seen;} signals[2];
bool kb_esb_signal(unsigned source,int8_t*dbm){
    if(source>=2)return false;k_spinlock_key_t k=k_spin_lock(&signal_lock);
    bool valid=signals[source].seen && k_uptime_get()-signals[source].seen<3000;
    *dbm=signals[source].dbm;k_spin_unlock(&signal_lock,k);return valid;
}
void kb_receiver_fence_commands(void){atomic_inc(&command_generation);}
K_MSGQ_DEFINE(tx_queue,sizeof(struct tx_item),32,4);
static app_esb_callback_t callback;
static app_esb_mode_t mode;
static atomic_t enabled,ready,irq_error,tx_result,ring_overflow;
static atomic_t diag_tx_ok,diag_tx_fail,diag_rx,diag_valid,diag_accepted;
#if IS_ENABLED(CONFIG_ZMK_BLE)
static atomic_t diag_slots,diag_blocked,diag_timer,diag_idle,diag_cancel,diag_radio;
static atomic_t diag_signal,diag_requests,diag_request_rc,diag_handoffs;
static atomic_t diag_power,diag_state,diag_shorts,diag_interrupts,diag_mpsl_work;
static void mpsl_probe(struct k_work *work) {
    ARG_UNUSED(work);atomic_inc(&diag_mpsl_work);
}
K_WORK_DEFINE(mpsl_probe_work,mpsl_probe);
#endif
static bool initialized,have_tx;
static struct tx_item current;
static uint32_t boot_id,next_seq=1;
uint32_t kb_receiver_boot(void){return boot_id;}
static struct esb_payload receive_ring[64];
static atomic_t head,tail,idle=ATOMIC_INIT(1);
static atomic_t last_boot[4];
static struct kb_owner_policy owner;
static K_MUTEX_DEFINE(owner_mutex);
static atomic_t retiring,published_owner;
static int64_t retired_at;
K_MSGQ_DEFINE(probe_queue,sizeof(struct tx_item),4,4);
bool kb_receiver_admitting(void){return !atomic_get(&retiring);}
uint8_t kb_receiver_owner_set(void){unsigned n=atomic_get(&published_owner);return n?n-1:KB_OWNER_NONE;}
uint32_t kb_receiver_peer_boot(unsigned source){return source<2 && owner.bound?(uint32_t)atomic_get(&last_boot[owner.set*2+source]):0;}
static uint32_t previous_boot[4],last_seq[4];
static atomic_t command_ack_boot,command_ack_seq;
static K_MUTEX_DEFINE(send_mutex);
static void put32(uint8_t *p,uint32_t v) { for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i)); }
static uint32_t get32(const uint8_t *p) { return p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24); }
static void collect_rx(void) {
    struct esb_payload p;
    while (!esb_read_rx_payload(&p)) {
        unsigned h=atomic_get(&head),n=(h+1)%ARRAY_SIZE(receive_ring);
        if(n==(unsigned)atomic_get(&tail)) { atomic_inc(&ring_overflow);continue; }
        receive_ring[h]=p;atomic_set(&head,n);
    }
}
static void radio_event(const struct esb_evt *e) {
    if(e->evt_id==ESB_EVENT_TX_SUCCESS)atomic_inc(&diag_tx_ok);
    if(e->evt_id==ESB_EVENT_TX_FAILED)atomic_inc(&diag_tx_fail);
    if(e->evt_id==ESB_EVENT_TX_SUCCESS && mode==APP_ESB_MODE_PTX)atomic_set(&tx_result,1);
    else if(e->evt_id==ESB_EVENT_TX_FAILED)atomic_set(&tx_result,-1);
    /* One producer: direct mode uses this IRQ; MPSL mode drains at slot end. */
#if !IS_ENABLED(CONFIG_ZMK_BLE)
    if(e->evt_id==ESB_EVENT_RX_RECEIVED)collect_rx();
#endif
}
static int configure_radio(void) {
    struct esb_config c=ESB_DEFAULT_CONFIG;
    c.protocol=ESB_PROTOCOL_ESB_DPL;c.bitrate=ESB_BITRATE_2MBPS;
    c.mode=mode==APP_ESB_MODE_PRX?ESB_MODE_PRX:ESB_MODE_PTX;
    c.tx_mode=ESB_TXMODE_MANUAL_START;c.selective_auto_ack=true;
    c.event_handler=radio_event;c.retransmit_delay=600;c.retransmit_count=3;
    c.tx_output_power=ESB_TX_POWER_8DBM;c.use_fast_ramp_up=true;
    int rc=esb_init(&c);initialized=!rc;
    if(!rc)rc=esb_set_base_address_0(base0);
    if(!rc)rc=esb_set_base_address_1(base1);
    if(!rc)rc=esb_set_prefixes(prefixes,ARRAY_SIZE(prefixes));
    if(!rc)rc=esb_enable_pipes(BIT(0)|BIT(1)|BIT(2)|BIT(3));
    if(!rc)rc=esb_set_rf_channel(37);
    if(!rc && have_tx && current.generation==(uint32_t)atomic_get(&command_generation))rc=esb_write_payload(&current.payload);
    if(!rc && mode==APP_ESB_MODE_PRX)rc=esb_start_rx();
    return rc;
}
#if IS_ENABLED(CONFIG_ZMK_BLE)
BUILD_ASSERT(IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL));
BUILD_ASSERT(!IS_ENABLED(CONFIG_MPSL_FEM),"Slot adapter assumes XIAO has no external FEM");
static mpsl_timeslot_signal_return_param_t response;
static mpsl_timeslot_request_t request={.request_type=MPSL_TIMESLOT_REQ_TYPE_EARLIEST,
    .params.earliest={.hfclk=MPSL_TIMESLOT_HFCLK_CFG_XTAL_GUARANTEED,
    .priority=MPSL_TIMESLOT_PRIORITY_NORMAL,.length_us=SLOT_US,.timeout_us=100000}};
static void end_slot(void) {
    if(initialized) { collect_rx();esb_disable();initialized=false; }
    /* The bounded SDK cleanup powers RADIO off. Return an enabled peripheral
     * in DISABLED state to MPSL/SDC, never a powered-off radio. Valid grant only. */
    nrf_radio_power_set(NRF_RADIO,false);
    nrf_radio_power_set(NRF_RADIO,true);
    __DSB();
    nrf_radio_shorts_disable(NRF_RADIO,0xFFFFFFFF);
    nrf_radio_int_disable(NRF_RADIO,0xFFFFFFFF);
    nrf_timer_int_disable(NRF_TIMER0,NRF_TIMER_INT_COMPARE0_MASK);
    nrf_timer_event_clear(NRF_TIMER0,NRF_TIMER_EVENT_COMPARE0);
    NVIC_ClearPendingIRQ(RADIO_IRQn);
    /* Sample only while owning the slot. Thread diagnostics read this cache. */
    atomic_set(&diag_power,NRF_RADIO->POWER);
    atomic_set(&diag_state,NRF_RADIO->STATE);
    atomic_set(&diag_shorts,NRF_RADIO->SHORTS);
    atomic_set(&diag_interrupts,NRF_RADIO->INTENSET);
    atomic_inc(&diag_handoffs);
}
static mpsl_timeslot_signal_return_param_t *slot(mpsl_timeslot_session_id_t id,uint32_t signal) {
    ARG_UNUSED(id);response.callback_action=MPSL_TIMESLOT_SIGNAL_ACTION_NONE;
    atomic_set(&diag_signal,signal);
    switch(signal) {
    case MPSL_TIMESLOT_SIGNAL_START: {
        atomic_inc(&diag_slots);
#if IS_ENABLED(CONFIG_LISM_BLE_INPUT_BRIDGE)
        lism_coex_grant();
#endif
        if(!atomic_get(&enabled)) {end_slot();response.callback_action=MPSL_TIMESLOT_SIGNAL_ACTION_END;return &response;}
        NVIC_ClearPendingIRQ(RADIO_IRQn);
        nrf_radio_power_set(NRF_RADIO,false);nrf_radio_power_set(NRF_RADIO,true);
        nrf_timer_bit_width_set(NRF_TIMER0,NRF_TIMER_BIT_WIDTH_32);
        nrf_timer_cc_set(NRF_TIMER0,NRF_TIMER_CC_CHANNEL0,SLOT_US-300);
        nrf_timer_event_clear(NRF_TIMER0,NRF_TIMER_EVENT_COMPARE0);
        nrf_timer_int_enable(NRF_TIMER0,NRF_TIMER_INT_COMPARE0_MASK);
        int rc=configure_radio();
        if(rc) { atomic_set(&irq_error,rc);end_slot();response.callback_action=MPSL_TIMESLOT_SIGNAL_ACTION_END; }
        return &response;
    }
    case MPSL_TIMESLOT_SIGNAL_RADIO:
        atomic_inc(&diag_radio);
        if(initialized)lism_esb_radio_irq();
        return &response;
    case MPSL_TIMESLOT_SIGNAL_TIMER0:
        atomic_inc(&diag_timer);
        if(nrf_timer_event_check(NRF_TIMER0,NRF_TIMER_EVENT_COMPARE0)) {
            nrf_timer_event_clear(NRF_TIMER0,NRF_TIMER_EVENT_COMPARE0);end_slot();
            response.callback_action=MPSL_TIMESLOT_SIGNAL_ACTION_END;
        }
        return &response;
    case MPSL_TIMESLOT_SIGNAL_SESSION_IDLE:
        atomic_inc(&diag_idle);
        atomic_set(&idle,1);return NULL;
    case MPSL_TIMESLOT_SIGNAL_BLOCKED:
        atomic_inc(&diag_blocked);
        atomic_set(&idle,1);return NULL;
    case MPSL_TIMESLOT_SIGNAL_CANCELLED:
        atomic_inc(&diag_cancel);
        atomic_set(&idle,1);return NULL;
    case MPSL_TIMESLOT_SIGNAL_SESSION_CLOSED:
        atomic_set(&irq_error,-ESHUTDOWN);return NULL;
    case MPSL_TIMESLOT_SIGNAL_INVALID_RETURN:
    case MPSL_TIMESLOT_SIGNAL_OVERSTAYED:
        atomic_set(&irq_error,-EIO);return NULL;
    default:return NULL;
    }
}
#endif
static int clocks_start(void) {
    struct onoff_manager *manager=z_nrf_clock_control_get_onoff(CLOCK_CONTROL_NRF_SUBSYS_HF);
    struct onoff_client client;int result,rc;
    if(!manager)return -ENXIO;
    sys_notify_init_spinwait(&client.notify);rc=onoff_request(manager,&client);
    if(rc<0)return rc;
    /* Yielding a cooperative thread does not let lower-priority USB/log
     * workers run. Sleep while keeping this pending client's storage alive. */
    while(sys_notify_fetch_result(&client.notify,&result))k_sleep(K_MSEC(1));
    return result;
}
static void notify(app_esb_event_type_t type) {
    app_esb_event_t e={.evt_type=type};callback(&e);
}
static void retire_owner(void) {
    if(!atomic_cas(&retiring,0,1))return;
    owner.retired=true;retired_at=k_uptime_get();kb_receiver_fence_commands();
    /* No new tuple is admitted under this receiver boot, even if old workers
     * are still executing. link_tick actively releases held state, then the
     * hardware reset discards ALL old radio/behavior/USB/SDC work. */
}
static bool tx_owner_live(const struct tx_item *t){
    if(atomic_get(&retiring) || t->generation!=(uint32_t)atomic_get(&command_generation) || k_uptime_get()>t->expires)return false;
    if(t->discovery)return !owner.bound || kb_owner_same(&owner,t->payload.pipe/2,t->target_boot);
    return owner.bound && t->payload.pipe/2==owner.set;
}
/* Owner commands always precede bounded best-effort presence replies. Stale
 * replies are never handed to a new tuple; a live probe has <=250ms lifetime. */
static bool take_next_tx(struct tx_item *out){
    for(unsigned q=0;q<2;q++){
        for(unsigned n=0;n<(q?4:32);n++){
            if(k_msgq_get(q?&probe_queue:&tx_queue,out,K_NO_WAIT))break;
            if(!tx_owner_live(out))continue;
            if(!out->discovery && out->target_boot!=(uint32_t)atomic_get(&last_boot[out->payload.pipe]))continue;
            return true;
        }
    }
    return false;
}
static bool probe_preemptible(void){return have_tx && current.discovery && k_msgq_num_used_get(&tx_queue)>0;}
static void offer_probe(unsigned physical,uint32_t peer,const struct kb_discovery *probe){
    struct esb_command_envelope env={.prefix={.magic_prefix=ZMK_SPLIT_ESB_ENVELOPE_MAGIC_PREFIX},.payload={.source=1,.cmd={.type=ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR}}};

    env.payload.cmd.data.invoke_behavior.param1=probe->cookie;
    env.payload.cmd.data.invoke_behavior.param2=boot_id;
    env.payload.cmd.data.invoke_behavior.event_source=1;
    env.payload.cmd.data.invoke_behavior.position=CONFIG_LISM_DONGLE_STATION;
    env.payload.cmd.data.invoke_behavior.state=1;
    memcpy(env.payload.cmd.data.invoke_behavior.behavior_dev,"KB_PROBE3",10);
    env.prefix.payload_size=1+sizeof(env.payload.cmd.type)+sizeof(env.payload.cmd.data.invoke_behavior);
    size_t n=sizeof(env.prefix)+env.prefix.payload_size;
    k_mutex_lock(&send_mutex,K_FOREVER);
    struct tx_item item={.target_boot=peer,.generation=atomic_get(&command_generation),.expires=k_uptime_get()+250,.discovery=true};
    item.payload=(struct esb_payload){.pipe=physical,.length=WIRE_HEADER+n+sizeof(struct esb_msg_postfix)};
    memcpy(item.payload.data,kb_set_magic(physical/2),4);put32(item.payload.data+4,boot_id);put32(item.payload.data+8,next_seq++);put32(item.payload.data+12,peer);
    memcpy(item.payload.data+WIRE_HEADER,&env,n);
    struct esb_msg_postfix crc={.crc=crc32_ieee(item.payload.data+WIRE_HEADER,n)};memcpy(item.payload.data+WIRE_HEADER+n,&crc,sizeof(crc));
    (void)k_msgq_put(&probe_queue,&item,K_NO_WAIT);k_mutex_unlock(&send_mutex);
}
static void receive_frames(void) {
    unsigned t;
    while((t=(unsigned)atomic_get(&tail))!=(unsigned)atomic_get(&head)) {
        struct esb_payload p=receive_ring[t];atomic_set(&tail,(t+1)%ARRAY_SIZE(receive_ring));
        atomic_inc(&diag_rx);
        if(p.pipe>=4 || p.length<WIRE_HEADER+sizeof(struct esb_msg_prefix)+2+sizeof(struct esb_msg_postfix) ||
            p.length>CONFIG_ESB_MAX_PAYLOAD_LENGTH || memcmp(p.data,kb_set_magic(p.pipe/2),4))continue;
        struct esb_msg_prefix prefix;memcpy(&prefix,p.data+WIRE_HEADER,sizeof(prefix));
        size_t body_len=p.length-WIRE_HEADER,frame_len=sizeof(prefix)+prefix.payload_size;
        if(memcmp(prefix.magic_prefix,"ZmKe",4) || prefix.payload_size<2 ||
            frame_len+sizeof(struct esb_msg_postfix)!=body_len || p.data[WIRE_HEADER+sizeof(prefix)]!=(p.pipe%2))continue;
        struct esb_msg_postfix crc;memcpy(&crc,p.data+WIRE_HEADER+frame_len,sizeof(crc));
        if(crc32_ieee(p.data+WIRE_HEADER,frame_len)!=crc.crc)continue;
        if(!lism_wire_valid(p.pipe%2,p.data+WIRE_HEADER,body_len))continue;
        struct esb_event_envelope body={0};
        if(mode==APP_ESB_MODE_PRX)memcpy(&body,p.data+WIRE_HEADER,body_len-sizeof(struct esb_msg_postfix));
        bool probe=mode==APP_ESB_MODE_PRX && (int)body.payload.event.type==KB_PROBE_EVENT;
        /* Only discovery may bootstrap without the current receiver boot.
         * Ordinary input from before a receiver reboot is never re-admitted. */
        if(mode==APP_ESB_MODE_PRX && !probe && get32(p.data+12)!=boot_id)continue;
        atomic_inc(&diag_valid);
        /* A delayed reset/bootloader command for a previous half boot must
         * never execute again after that half restarts. */
        if(mode==APP_ESB_MODE_PTX && get32(p.data+12)!=boot_id)continue;
        uint32_t b=get32(p.data+4),s=get32(p.data+8);if(!b || !s)continue;
        unsigned logical=p.pipe%2,set=p.pipe/2;
        k_mutex_lock(&owner_mutex,K_FOREVER);
        if(kb_owner_expire(&owner,k_uptime_get()))retire_owner();
        if(atomic_get(&retiring)){k_mutex_unlock(&owner_mutex);continue;}
        if(probe){
            struct kb_discovery d;memcpy(&d,&body.payload.event.data,sizeof(d));
            if(logical==1 && d.version==KB_PROTOCOL_VERSION && d.station==CONFIG_LISM_DONGLE_STATION && d.authority && d.cookie && kb_owner_probe_allowed(&owner,set,b,k_uptime_get()))offer_probe(p.pipe,b,&d);
            if(owner.retired)retire_owner();
            k_mutex_unlock(&owner_mutex);continue; /* never changes owner peer caches */
        }
        struct kb_owner_policy candidate=owner;
        bool status=(int)body.payload.event.type==KB_ROUTE_EVENT;
        if(status){
            struct kb_route_status rs;memcpy(&rs,&body.payload.event.data,sizeof(rs));
            if(rs.version!=KB_ROUTE_VERSION || rs.profile>=5 || rs.station!=CONFIG_LISM_DONGLE_STATION || rs.receiver_boot!=boot_id || (rs.target==0?rs.owner_station!=rs.station:rs.owner_station!=KB_STATION_NONE) || !kb_owner_status(&candidate,set,b,rs.authority,rs.epoch,rs.phase,rs.target,rs.mode,k_uptime_get())){if(candidate.retired)retire_owner();k_mutex_unlock(&owner_mutex);continue;}
        }else if(!owner.bound || owner.set!=set || (logical==1 && owner.wire_boot!=b)){k_mutex_unlock(&owner_mutex);continue;}
        if(b!=(uint32_t)atomic_get(&last_boot[p.pipe])) {
            if(b==previous_boot[p.pipe]){k_mutex_unlock(&owner_mutex);continue;}
            previous_boot[p.pipe]=atomic_get(&last_boot[p.pipe]);atomic_set(&last_boot[p.pipe],b);last_seq[p.pipe]=0;
        }
        if(s<=last_seq[p.pipe]){k_mutex_unlock(&owner_mutex);continue;}
        if(mode==APP_ESB_MODE_PRX && have_tx && current.payload.pipe==p.pipe &&
            get32(p.data+12)==boot_id && get32(p.data+16)==get32(current.payload.data+8))
            atomic_set(&tx_result,1); /* peer confirmed valid command queue insertion */
        /* Publish the immutable owner before native workers can consume the claim.
         * If queue insertion fails, its reservation simply expires; never
         * reuse this receiver boot for an alternate tuple. */
        if(status){owner=candidate;atomic_set(&published_owner,set+1);}
        app_esb_event_t e={.evt_type=APP_ESB_EVT_RX,.pipe=logical,
            .buf=p.data+WIRE_HEADER,.data_length=p.length-WIRE_HEADER};callback(&e);
        if(e.accepted) {
            if(!probe && p.rssi && p.rssi<127){k_spinlock_key_t k=k_spin_lock(&signal_lock);signals[logical].dbm=-(int)p.rssi;signals[logical].seen=k_uptime_get();k_spin_unlock(&signal_lock,k);}
            atomic_inc(&diag_accepted);
            last_seq[p.pipe]=s;
            if(mode==APP_ESB_MODE_PTX) {
                k_mutex_lock(&send_mutex,K_FOREVER);
                atomic_set(&command_ack_boot,b);atomic_set(&command_ack_seq,s);
                k_mutex_unlock(&send_mutex);
            }
        }
        k_mutex_unlock(&owner_mutex);
    }
}
/* PRX puts a command in the ACK FIFO while RX remains running. esb_start_tx()
 * belongs to PTX and returns an error on a continuously listening receiver. */
static int submit_direct_payload(void) {
    int rc=esb_write_payload(&current.payload);
    if(!rc && mode==APP_ESB_MODE_PTX)rc=esb_start_tx();
    return rc;
}
static void radio_thread(void *a,void *b,void *c) {
    ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
    LOG_INF("ESB v4.7.2 radio thread entered, host BLE=%d",IS_ENABLED(CONFIG_ZMK_BLE));
    while(!callback)k_sleep(K_MSEC(1));
    int rc;
#if IS_ENABLED(CONFIG_ZMK_BLE)
    /* Own TIMER2/PPI for this app, but never access RADIO outside a slot.
     * This non-preemptible thread serializes MPSL API calls like its sample. */
    rc=lism_esb_resources_init();if(rc)goto failed;
    uint8_t channels[6];uint32_t ppi_mask=0;
    lism_esb_ppi_channels_get(channels);
    for(unsigned i=0;i<ARRAY_SIZE(channels);i++)ppi_mask|=BIT(channels[i]);
    LOG_INF("ESB PPI channels=%u,%u,%u,%u,%u,%u mask=%x",
        channels[0],channels[1],channels[2],channels[3],channels[4],channels[5],ppi_mask);
    if(ppi_mask & SDC_PPI_CHANNELS_USED_MASK) {rc=-EBUSY;goto failed;}

#else
    rc=clocks_start();if(rc)goto failed;
#endif
    do { boot_id=sys_rand32_get(); }while(!boot_id);
#if IS_ENABLED(CONFIG_ZMK_BLE)
    mpsl_timeslot_session_id_t session;
    rc=mpsl_timeslot_session_open(slot,&session);if(rc)goto failed;
#else
    rc=configure_radio();if(rc)goto failed;
#endif
    atomic_set(&ready,1);
    lism_notify_status();
    notify(APP_ESB_EVT_TX_FAIL); /* kick frames queued before the HF clock was ready */
    LOG_INF("ESB ready: %s, pipe %d, RF 2437 MHz, host BLE %d",
        mode==APP_ESB_MODE_PRX?"PRX":"PTX",CONFIG_ZMK_SPLIT_ESB_PERIPHERAL_ID,IS_ENABLED(CONFIG_ZMK_BLE));
    int64_t pending_since=0,next_diag=0;bool in_flight=false;
    for(;;) {
        if(k_uptime_get()>=next_diag) {
            next_diag=k_uptime_get()+5000;
            LOG_INF("ESB diag en=%d txOK=%d txFail=%d rx=%d valid=%d queued=%d",
                (int)atomic_get(&enabled),(int)atomic_get(&diag_tx_ok),(int)atomic_get(&diag_tx_fail),
                (int)atomic_get(&diag_rx),(int)atomic_get(&diag_valid),(int)atomic_get(&diag_accepted));
#if IS_ENABLED(CONFIG_ZMK_BLE)
            LOG_INF("ESB v4.7.2 slots=%d timer=%d idle_cb=%d blocked=%d cancel=%d radio=%d",
                (int)atomic_get(&diag_slots),(int)atomic_get(&diag_timer),(int)atomic_get(&diag_idle),
                (int)atomic_get(&diag_blocked),(int)atomic_get(&diag_cancel),(int)atomic_get(&diag_radio));
            LOG_INF("ESB sched idle=%d last=%d req=%d rc=%d mpsl_work=%d",
                (int)atomic_get(&idle),(int)atomic_get(&diag_signal),(int)atomic_get(&diag_requests),
                (int)atomic_get(&diag_request_rc),(int)atomic_get(&diag_mpsl_work));
            LOG_INF("ESB handoff n=%d POWER=%d STATE=%d SHORTS=%x INT=%x",
                (int)atomic_get(&diag_handoffs),(int)atomic_get(&diag_power),(int)atomic_get(&diag_state),
                (unsigned)atomic_get(&diag_shorts),(unsigned)atomic_get(&diag_interrupts));
#if IS_ENABLED(CONFIG_LISM_BLE_INPUT_BRIDGE)
            lism_coex_log();
            LOG_INF("ESB command q=%u pending=%u age_ms=%u wire_seq=%u target=%u",k_msgq_num_used_get(&tx_queue),have_tx,have_tx?(unsigned)(k_uptime_get()-pending_since):0,have_tx?get32(current.payload.data+8):0,have_tx?current.target_boot:0);
#endif
            mpsl_work_submit(&mpsl_probe_work); /* thread only, nonblocking probe */
#endif
        }
        if((rc=atomic_set(&irq_error,0)))break;
        if(atomic_set(&ring_overflow,0))LOG_WRN("ESB RX ring overflow; snapshot will repair held state");
        receive_frames();
        k_mutex_lock(&owner_mutex,K_FOREVER);
        if(kb_owner_expire(&owner,k_uptime_get()))retire_owner();
        k_mutex_unlock(&owner_mutex);
        if(atomic_get(&retiring) && k_uptime_get()-retired_at>=600)sys_reboot(SYS_REBOOT_COLD);
        if(atomic_get(&idle)) {
            int result=atomic_set(&tx_result,0);
            if(probe_preemptible()){have_tx=false;in_flight=false;result=0;}
            if(have_tx && mode==APP_ESB_MODE_PRX && !current.discovery && current.target_boot!=(uint32_t)atomic_get(&last_boot[current.payload.pipe])) {
                have_tx=false;in_flight=false;notify(APP_ESB_EVT_TX_FAIL);
            }
            if(have_tx && !tx_owner_live(&current)){have_tx=false;in_flight=false;}
            if(have_tx && result==1) {
                have_tx=false;in_flight=false;lism_ack_seen();notify(APP_ESB_EVT_TX_SUCCESS);
            } else if(have_tx && (result<0 || k_uptime_get()-pending_since>3000)) {
                in_flight=false;
                if(!current.retries-- || k_uptime_get()-pending_since>3000) {
                    have_tx=false;notify(APP_ESB_EVT_TX_FAIL);
                }
#if !IS_ENABLED(CONFIG_ZMK_BLE)
                esb_flush_tx();
#endif
            }
            if(!have_tx && take_next_tx(&current)) {
                have_tx=true;pending_since=k_uptime_get();in_flight=false;
                if(mode==APP_ESB_MODE_PRX && !current.discovery && current.target_boot!=(uint32_t)atomic_get(&last_boot[current.payload.pipe])) {
                    have_tx=false;notify(APP_ESB_EVT_TX_FAIL);
                }
                if(!tx_owner_live(&current))have_tx=false;
            }
        }
#if IS_ENABLED(CONFIG_ZMK_BLE)
        if(atomic_get(&enabled) && atomic_get(&idle)) {
            atomic_set(&idle,0);rc=mpsl_timeslot_request(session,&request);
            atomic_inc(&diag_requests);atomic_set(&diag_request_rc,rc);
            if(rc) { atomic_set(&idle,1);LOG_WRN("ESB slot request failed %d",rc);k_sleep(K_MSEC(10)); }
        }
#else
        if(have_tx && !in_flight && atomic_get(&enabled)) {
            rc=submit_direct_payload();
            if(!rc)in_flight=true;
            else if(rc!=-EBUSY) {LOG_WRN("ESB TX start %d",rc);esb_flush_tx();}
        }
#endif
        k_sleep(K_MSEC(1));
    }
#if IS_ENABLED(CONFIG_ZMK_BLE)
    mpsl_timeslot_session_close(session);
#endif
failed:
    atomic_clear(&ready);lism_notify_status();LOG_ERR("ESB radio stopped, error %d",rc);
}
/* Static threads are initialized AFTER APPLICATION SYS_INIT callbacks. A
 * k_thread_start() in that level runs too early and is ineffective; FOREVER
 * would then leave this thread asleep permanently. Let the kernel start it. */
#if IS_ENABLED(CONFIG_ZMK_BLE)
#define LISM_RADIO_PRIORITY K_PRIO_COOP(CONFIG_MPSL_THREAD_COOP_PRIO)
#else
#define LISM_RADIO_PRIORITY K_PRIO_COOP(8)
#endif
K_THREAD_DEFINE(lism_radio_thread,4096,radio_thread,NULL,NULL,NULL,LISM_RADIO_PRIORITY,0,0);
bool lism_radio_ready(void) {return atomic_get(&ready);}
int zmk_split_esb_init(app_esb_mode_t selected,app_esb_callback_t cb) {mode=selected;callback=cb;return 0;}
int zmk_split_esb_set_enable(bool value) {atomic_set(&enabled,value);return 0;}
int zmk_split_esb_send(app_esb_data_t *p) {
    if(!p || p->pipe>=2 || !p->len || p->len>CONFIG_ESB_MAX_PAYLOAD_LENGTH-WIRE_HEADER)return -EINVAL;
    k_mutex_lock(&send_mutex,K_FOREVER);
    if(!boot_id || !next_seq) {k_mutex_unlock(&send_mutex);return -EAGAIN;}
    if(!owner.bound || !kb_receiver_admitting()){k_mutex_unlock(&send_mutex);return -EAGAIN;}
    unsigned physical=owner.set*2+p->pipe;
    struct tx_item item={.retries=p->max_retry,.generation=atomic_get(&command_generation),.expires=k_uptime_get()+3000};
    item.target_boot=atomic_get(&last_boot[physical]);
    item.payload.pipe=physical;item.payload.noack=false;item.payload.length=p->len+WIRE_HEADER;
    memcpy(item.payload.data,kb_set_magic(owner.set),4);put32(item.payload.data+4,boot_id);put32(item.payload.data+8,next_seq);
    if(mode==APP_ESB_MODE_PRX) {
        put32(item.payload.data+12,item.target_boot);put32(item.payload.data+16,kb_receiver_route_epoch());
    } else {
        put32(item.payload.data+12,atomic_get(&command_ack_boot));
        put32(item.payload.data+16,atomic_get(&command_ack_seq));
    }
    memcpy(item.payload.data+WIRE_HEADER,p->data,p->len);
    int rc=k_msgq_put(&tx_queue,&item,K_NO_WAIT);if(!rc)next_seq++;
    k_mutex_unlock(&send_mutex);return rc;
}

#endif
