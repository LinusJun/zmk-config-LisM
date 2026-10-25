/* SPDX-License-Identifier: MIT
 * Read-only snapshots. PRE_KERNEL_1/0 is an initialization checkpoint, not reset entry.
 */
#include <zephyr/kernel.h>
#include <zephyr/init.h>
#include <zephyr/logging/log.h>
#include <hal/nrf_clock.h>
#include <hal/nrf_power.h>
#include <hal/nrf_radio.h>
#include <hal/nrf_timer.h>
#include <hal/nrf_ppi.h>
#include <hal/nrf_usbd.h>
#include "left_boot_trace.h"
LOG_MODULE_REGISTER(lism_boot45,LOG_LEVEL_INF);
struct snapshot {
    uint32_t reset,gpregret,gpregret2,hf,event,clock_int,usb,ppi;
    uint32_t timer_mode,timer_bits,timer_prescaler,timer_shorts,timer_int,cc[4];
    uint32_t radio_power,radio_state,radio_shorts,radio_int,radio_mode,radio_frequency;
};
static struct snapshot early,configured;
static bool configured_valid;
int lism_left_clock_request_state=-1;
extern void lism_left_clock_snapshot(uint32_t out[16]);
static void capture(struct snapshot *s){
    unsigned key=irq_lock();
    *s=(struct snapshot){.reset=NRF_POWER->RESETREAS,.gpregret=NRF_POWER->GPREGRET,
        .gpregret2=NRF_POWER->GPREGRET2,.hf=NRF_CLOCK->HFCLKSTAT,
        .event=NRF_CLOCK->EVENTS_HFCLKSTARTED,.clock_int=NRF_CLOCK->INTENSET,
        .usb=NRF_USBD->ENABLE,.ppi=NRF_PPI->CHEN,
        .timer_mode=NRF_TIMER2->MODE,.timer_bits=NRF_TIMER2->BITMODE,
        .timer_prescaler=NRF_TIMER2->PRESCALER,.timer_shorts=NRF_TIMER2->SHORTS,
        .timer_int=NRF_TIMER2->INTENSET,
        .cc={NRF_TIMER2->CC[0],NRF_TIMER2->CC[1],NRF_TIMER2->CC[2],NRF_TIMER2->CC[3]},
        .radio_power=NRF_RADIO->POWER,.radio_state=NRF_RADIO->STATE,
        .radio_shorts=NRF_RADIO->SHORTS,.radio_int=NRF_RADIO->INTENSET,
        .radio_mode=NRF_RADIO->MODE,.radio_frequency=NRF_RADIO->FREQUENCY};
    irq_unlock(key);
}
static int capture_early(void){capture(&early);return 0;}
SYS_INIT(capture_early,PRE_KERNEL_1,0);
void lism_left_trace_configured(void){capture(&configured);configured_valid=true;}
static void log_snapshot(const char *label,const struct snapshot *s){
    LOG_INF("Left SNAP45 %s reset=%x gp=%x/%x hf=%x evt=%x cint=%x usb=%x ppi=%x",
        label,s->reset,s->gpregret,s->gpregret2,s->hf,s->event,s->clock_int,s->usb,s->ppi);
    LOG_INF("Left TIMER45 %s mode=%x bits=%x pre=%x shorts=%x int=%x cc=%x/%x/%x/%x",
        label,s->timer_mode,s->timer_bits,s->timer_prescaler,s->timer_shorts,s->timer_int,
        s->cc[0],s->cc[1],s->cc[2],s->cc[3]);
    LOG_INF("Left RADIO45 %s power=%x state=%x shorts=%x int=%x mode=%x freq=%x",
        label,s->radio_power,s->radio_state,s->radio_shorts,s->radio_int,s->radio_mode,s->radio_frequency);
}
static void log_clock(void){
    uint32_t s[16];lism_left_clock_snapshot(s);
    LOG_INF("Left CLOCK45 request=%d flags=%x mgr=%x refs=%u users=%x hf=%x starts=%u events=%u irq_done=%u",
        lism_left_clock_request_state,s[0],s[1],s[2],s[3],s[4],s[5],s[6],s[7]);
    LOG_INF("Left HANDOFF45 entry=%x/%u/%u checked=%u flags=%x mgr=%x refs=%u recovered=%u",
        s[8],s[9],s[10],s[11],s[12],s[13],s[14],s[15]);
}
void lism_left_trace_boot_log(void){
    log_snapshot("pre_kernel_1_0",&early);
    if(configured_valid)log_snapshot("configured_pre_tx",&configured);
    log_clock();
}
void lism_left_trace_expiry(uint32_t boot,uint32_t epoch,unsigned net,uint32_t control_age,uint32_t ack_age){
    struct snapshot live;capture(&live);
    LOG_INF("Left EXPIRY45 boot=%u epoch=%u net=%u ctrl_age=%u ack_age=%u",boot,epoch,net,control_age,ack_age);
    log_snapshot("first_expiry_approx",&live);log_clock();
}
