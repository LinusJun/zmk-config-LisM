/* SPDX-License-Identifier: MIT
 * Nordic ESB + badjeff app_esb API. PTX owns its radio continuously; the BLE
 * host receiver owns ESB only inside MPSL slots. IRQ0 copies to an SPSC ring;
 * callbacks, queue operations, clocks, logs and retries run on this thread.
 */
#include "app_esb.h"
#include "common.h"
#include "link.h"
#include <esb.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/crc.h>
#include <zephyr/random/random.h>
#include <zephyr/drivers/clock_control/nrf_clock_control.h>
#include <hal/nrf_timer.h>
#include <hal/nrf_radio.h>
#include <zephyr/logging/log.h>
#if IS_ENABLED(CONFIG_ZMK_BLE)
#include <mpsl_timeslot.h>
extern void *__ptr__radio_dynamic_irq_handler;
#endif
LOG_MODULE_REGISTER(lism_radio,LOG_LEVEL_INF);
#if IS_ENABLED(CONFIG_MPSL_ASSERT_HANDLER)
/* A fatal MPSL assertion cannot safely return to a damaged radio scheduler. */
void mpsl_assert_handle(const char *file,int line) { ARG_UNUSED(file);ARG_UNUSED(line);k_panic(); }
#endif
#define NODE DT_COMPAT_GET_ANY_STATUS_OKAY(zmk_esb_split)
static const uint8_t base0[]=DT_PROP(NODE,base_addr_0),base1[]=DT_PROP(NODE,base_addr_1);
static const uint8_t prefixes[]=DT_PROP(NODE,addr_prefix);
#define SLOT_US 4000
#define WIRE_HEADER LISM_WIRE_HEADER
struct tx_item { struct esb_payload payload;uint8_t retries;uint32_t target_boot; };
K_MSGQ_DEFINE(tx_queue,sizeof(struct tx_item),32,4);
static app_esb_callback_t callback;
static app_esb_mode_t mode;
static atomic_t enabled,ready,irq_error,tx_result,ring_overflow;
static bool initialized,have_tx;
static struct tx_item current;
static uint32_t boot_id,next_seq=1;
static struct esb_payload receive_ring[64];
static atomic_t head,tail,idle=ATOMIC_INIT(1);
static atomic_t last_boot[2];
static uint32_t previous_boot[2],last_seq[2];
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
    if(!rc)rc=esb_enable_pipes(BIT(0)|BIT(1));
    if(!rc)rc=esb_set_rf_channel(37);
    if(!rc && have_tx)rc=esb_write_payload(&current.payload);
    if(!rc && mode==APP_ESB_MODE_PRX)rc=esb_start_rx();
    return rc;
}
#if IS_ENABLED(CONFIG_ZMK_BLE)
BUILD_ASSERT(IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL));
static mpsl_timeslot_signal_return_param_t response;
static mpsl_timeslot_request_t request={.request_type=MPSL_TIMESLOT_REQ_TYPE_EARLIEST,
    .params.earliest={.hfclk=MPSL_TIMESLOT_HFCLK_CFG_XTAL_GUARANTEED,
    .priority=MPSL_TIMESLOT_PRIORITY_NORMAL,.length_us=SLOT_US,.timeout_us=100000}};
static void end_slot(void) {
    if(initialized) { collect_rx();esb_disable();initialized=false; }
    nrf_timer_int_disable(NRF_TIMER0,NRF_TIMER_INT_COMPARE0_MASK);
    NVIC_ClearPendingIRQ(RADIO_IRQn);irq_enable(RADIO_IRQn);
}
static mpsl_timeslot_signal_return_param_t *slot(mpsl_timeslot_session_id_t id,uint32_t signal) {
    ARG_UNUSED(id);response.callback_action=MPSL_TIMESLOT_SIGNAL_ACTION_NONE;
    switch(signal) {
    case MPSL_TIMESLOT_SIGNAL_START: {
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
        if(initialized)((void (*)(const void *))__ptr__radio_dynamic_irq_handler)(NULL);
        return &response;
    case MPSL_TIMESLOT_SIGNAL_TIMER0:
        if(nrf_timer_event_check(NRF_TIMER0,NRF_TIMER_EVENT_COMPARE0)) {
            nrf_timer_event_clear(NRF_TIMER0,NRF_TIMER_EVENT_COMPARE0);end_slot();
            response.callback_action=MPSL_TIMESLOT_SIGNAL_ACTION_END;
        }
        return &response;
    case MPSL_TIMESLOT_SIGNAL_SESSION_IDLE:
    case MPSL_TIMESLOT_SIGNAL_BLOCKED:
    case MPSL_TIMESLOT_SIGNAL_CANCELLED:
        atomic_set(&idle,1);return NULL;
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
    while(sys_notify_fetch_result(&client.notify,&result))k_yield();
    return result;
}
static void notify(app_esb_event_type_t type) {
    app_esb_event_t e={.evt_type=type};callback(&e);
}
static void receive_frames(void) {
    unsigned t;
    while((t=(unsigned)atomic_get(&tail))!=(unsigned)atomic_get(&head)) {
        struct esb_payload p=receive_ring[t];atomic_set(&tail,(t+1)%ARRAY_SIZE(receive_ring));
        if(p.pipe>=2 || p.length<WIRE_HEADER+sizeof(struct esb_msg_prefix)+2+sizeof(struct esb_msg_postfix) ||
            p.length>CONFIG_ESB_MAX_PAYLOAD_LENGTH || memcmp(p.data,"LsE2",4))continue;
        struct esb_msg_prefix prefix;memcpy(&prefix,p.data+WIRE_HEADER,sizeof(prefix));
        size_t body_len=p.length-WIRE_HEADER,frame_len=sizeof(prefix)+prefix.payload_size;
        if(memcmp(prefix.magic_prefix,"ZmKe",4) || prefix.payload_size<2 ||
            frame_len+sizeof(struct esb_msg_postfix)!=body_len || p.data[WIRE_HEADER+sizeof(prefix)]!=p.pipe)continue;
        struct esb_msg_postfix crc;memcpy(&crc,p.data+WIRE_HEADER+frame_len,sizeof(crc));
        if(crc32_ieee(p.data+WIRE_HEADER,frame_len)!=crc.crc)continue;
        if(!lism_wire_valid(p.pipe,p.data+WIRE_HEADER,body_len))continue;
        /* A delayed reset/bootloader command for a previous half boot must
         * never execute again after that half restarts. */
        if(mode==APP_ESB_MODE_PTX && get32(p.data+12)!=boot_id)continue;
        uint32_t b=get32(p.data+4),s=get32(p.data+8);if(!b || !s)continue;
        if(b!=(uint32_t)atomic_get(&last_boot[p.pipe])) {
            if(b==previous_boot[p.pipe])continue;
            previous_boot[p.pipe]=atomic_get(&last_boot[p.pipe]);atomic_set(&last_boot[p.pipe],b);last_seq[p.pipe]=0;
        }
        if(s<=last_seq[p.pipe])continue;
        if(mode==APP_ESB_MODE_PRX && have_tx && current.payload.pipe==p.pipe &&
            get32(p.data+12)==boot_id && get32(p.data+16)==get32(current.payload.data+8))
            atomic_set(&tx_result,1); /* peer confirmed valid command queue insertion */
        app_esb_event_t e={.evt_type=APP_ESB_EVT_RX,.pipe=p.pipe,
            .buf=p.data+WIRE_HEADER,.data_length=p.length-WIRE_HEADER};callback(&e);
        if(e.accepted) {
            last_seq[p.pipe]=s;
            if(mode==APP_ESB_MODE_PTX) {
                k_mutex_lock(&send_mutex,K_FOREVER);
                atomic_set(&command_ack_boot,b);atomic_set(&command_ack_seq,s);
                k_mutex_unlock(&send_mutex);
            }
        }
    }
}
static void radio_thread(void *a,void *b,void *c) {
    ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
    while(!callback)k_sleep(K_MSEC(1));
    int rc=clocks_start();if(rc)goto failed;
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
    int64_t pending_since=0;bool in_flight=false;
    for(;;) {
        if((rc=atomic_set(&irq_error,0)))break;
        if(atomic_set(&ring_overflow,0))LOG_WRN("ESB RX ring overflow; snapshot will repair held state");
        receive_frames();
        if(atomic_get(&idle)) {
            int result=atomic_set(&tx_result,0);
            if(have_tx && mode==APP_ESB_MODE_PRX && current.target_boot!=(uint32_t)atomic_get(&last_boot[current.payload.pipe])) {
                have_tx=false;in_flight=false;notify(APP_ESB_EVT_TX_FAIL);
            }
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
            if(!have_tx && !k_msgq_get(&tx_queue,&current,K_NO_WAIT)) {
                have_tx=true;pending_since=k_uptime_get();in_flight=false;
                if(mode==APP_ESB_MODE_PRX && current.target_boot!=(uint32_t)atomic_get(&last_boot[current.payload.pipe])) {
                    have_tx=false;notify(APP_ESB_EVT_TX_FAIL);
                }
            }
        }
#if IS_ENABLED(CONFIG_ZMK_BLE)
        if(atomic_get(&enabled) && atomic_get(&idle)) {
            atomic_set(&idle,0);rc=mpsl_timeslot_request(session,&request);
            if(rc) { atomic_set(&idle,1);LOG_WRN("ESB slot request failed %d",rc);k_sleep(K_MSEC(10)); }
        }
#else
        if(have_tx && !in_flight && atomic_get(&enabled)) {
            rc=esb_write_payload(&current.payload);
            if(!rc)rc=esb_start_tx();
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
K_THREAD_DEFINE(lism_radio_thread,4096,radio_thread,NULL,NULL,NULL,K_PRIO_COOP(8),0,SYS_FOREVER_MS);
static int start_radio(void) { k_thread_start(lism_radio_thread);return 0; }
SYS_INIT(start_radio,APPLICATION,99);
bool lism_radio_ready(void) {return atomic_get(&ready);}
int zmk_split_esb_init(app_esb_mode_t selected,app_esb_callback_t cb) {mode=selected;callback=cb;return 0;}
int zmk_split_esb_set_enable(bool value) {atomic_set(&enabled,value);return 0;}
int zmk_split_esb_send(app_esb_data_t *p) {
    if(!p || p->pipe>=2 || !p->len || p->len>CONFIG_ESB_MAX_PAYLOAD_LENGTH-WIRE_HEADER)return -EINVAL;
    k_mutex_lock(&send_mutex,K_FOREVER);
    if(!boot_id || !next_seq) {k_mutex_unlock(&send_mutex);return -EAGAIN;}
    struct tx_item item={.retries=p->max_retry};
    item.target_boot=atomic_get(&last_boot[p->pipe]);
    item.payload.pipe=p->pipe;item.payload.noack=false;item.payload.length=p->len+WIRE_HEADER;
    memcpy(item.payload.data,"LsE2",4);put32(item.payload.data+4,boot_id);put32(item.payload.data+8,next_seq);
    if(mode==APP_ESB_MODE_PRX) {
        put32(item.payload.data+12,item.target_boot);put32(item.payload.data+16,0);
    } else {
        put32(item.payload.data+12,atomic_get(&command_ack_boot));
        put32(item.payload.data+16,atomic_get(&command_ack_seq));
    }
    memcpy(item.payload.data+WIRE_HEADER,p->data,p->len);
    int rc=k_msgq_put(&tx_queue,&item,K_NO_WAIT);if(!rc)next_seq++;
    k_mutex_unlock(&send_mutex);return rc;
}
