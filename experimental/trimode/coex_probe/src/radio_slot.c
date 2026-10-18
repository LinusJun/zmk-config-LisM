/* SPDX-License-Identifier: MIT
 * Bench probe only. Not a LisM transport or a keyboard firmware.
 * No kernel queue, logging or sleep is called from a high-priority timeslot.
 */
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <hal/nrf_timer.h>
#include <esb.h>
#include <mpsl_timeslot.h>

/* Exposed by the pinned badjeff SDK fork; do not silently use upstream SDK. */
extern void *__ptr__radio_dynamic_irq_handler;
static atomic_t last_error, successes, failures, slot_active;
static atomic_t request_pending;
static mpsl_timeslot_signal_return_param_t response;
static bool initialized;
static const uint8_t base0[4] = {0x93, 0x71, 0x45, 0xb6};
static const uint8_t base1[4] = {0xa8, 0x26, 0x5b, 0xe1};
static const uint8_t prefix[1] = {0xd3};
static mpsl_timeslot_request_t request = {
    .request_type = MPSL_TIMESLOT_REQ_TYPE_EARLIEST,
    .params.earliest = {
        .hfclk = MPSL_TIMESLOT_HFCLK_CFG_XTAL_GUARANTEED,
        .priority = MPSL_TIMESLOT_PRIORITY_NORMAL,
        .length_us = 1500,
        .timeout_us = 100000,
    },
};
static void esb_event(const struct esb_evt *event) {
    if (event->evt_id == ESB_EVENT_TX_SUCCESS) atomic_inc(&successes);
    if (event->evt_id == ESB_EVENT_TX_FAILED) atomic_inc(&failures);
}
static void stop_slot(void) {
    if (initialized) {
        esb_disable();
        initialized = false;
    }
    nrf_timer_int_disable(NRF_TIMER0, NRF_TIMER_INT_COMPARE0_MASK);
    atomic_clear(&slot_active);
}
static mpsl_timeslot_signal_return_param_t *signal_handler(
    mpsl_timeslot_session_id_t session, uint32_t signal) {
    ARG_UNUSED(session);
    response.callback_action = MPSL_TIMESLOT_SIGNAL_ACTION_NONE;
    switch (signal) {
    case MPSL_TIMESLOT_SIGNAL_START: {
        atomic_set(&slot_active, 1);
        nrf_timer_cc_set(NRF_TIMER0, NRF_TIMER_CC_CHANNEL0, 1200);
        nrf_timer_event_clear(NRF_TIMER0, NRF_TIMER_EVENT_COMPARE0);
        nrf_timer_int_enable(NRF_TIMER0, NRF_TIMER_INT_COMPARE0_MASK);
        struct esb_config config = ESB_DEFAULT_CONFIG;
        config.event_handler = esb_event;
        config.retransmit_count = 0;
        config.use_fast_ramp_up = true;
        int rc = esb_init(&config);
        initialized = !rc;
        if (!rc) rc = esb_set_base_address_0(base0);
        if (!rc) rc = esb_set_base_address_1(base1);
        if (!rc) rc = esb_set_prefixes(prefix, 1);
        if (!rc) rc = esb_set_rf_channel(37);
        struct esb_payload payload = {
            .length = 8, .pipe = 0, .noack = false,
            .data = {'L', 'i', 's', 'M', 'P', 'R', 'O', 'B'},
        };
        if (!rc) rc = esb_write_payload(&payload);
        if (rc) {
            atomic_set(&last_error, rc);
            stop_slot();
            response.callback_action = MPSL_TIMESLOT_SIGNAL_ACTION_END;
        }
        return &response;
    }
    case MPSL_TIMESLOT_SIGNAL_RADIO:
        if (initialized)
            ((void (*)(const void *))__ptr__radio_dynamic_irq_handler)(NULL);
        return &response;
    case MPSL_TIMESLOT_SIGNAL_TIMER0:
        if (nrf_timer_event_check(NRF_TIMER0, NRF_TIMER_EVENT_COMPARE0)) {
            nrf_timer_event_clear(NRF_TIMER0, NRF_TIMER_EVENT_COMPARE0);
            stop_slot();
            response.callback_action = MPSL_TIMESLOT_SIGNAL_ACTION_END;
        }
        return &response;
    case MPSL_TIMESLOT_SIGNAL_SESSION_IDLE:
    case MPSL_TIMESLOT_SIGNAL_BLOCKED:
    case MPSL_TIMESLOT_SIGNAL_CANCELLED:
        atomic_set(&request_pending, 1);
        return NULL;
    case MPSL_TIMESLOT_SIGNAL_INVALID_RETURN:
    case MPSL_TIMESLOT_SIGNAL_OVERSTAYED:
        atomic_set(&last_error, -EIO);
        return NULL;
    default:
        return NULL;
    }
}
/* Match Nordic's requirement: serialize MPSL public APIs on a cooperative
 * thread. This probe intentionally requests one short window every 20 ms;
 * it does not assert that this schedule is suitable for a trackball. */
static void slot_thread_fn(void *a, void *b, void *c) {
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    mpsl_timeslot_session_id_t session;
    int rc = mpsl_timeslot_session_open(signal_handler, &session);
    if (rc) { atomic_set(&last_error, rc); return; }
    atomic_set(&request_pending, 1);
    while (!atomic_get(&last_error)) {
        if (atomic_cas(&request_pending, 1, 0)) {
            rc = mpsl_timeslot_request(session, &request);
            if (rc) { atomic_set(&last_error, rc); break; }
        }
        k_sleep(K_MSEC(20));
    }
    mpsl_timeslot_session_close(session);
}
K_THREAD_DEFINE(probe_slot_thread, 1536, slot_thread_fn, NULL, NULL, NULL,
                K_PRIO_COOP(8), 0, SYS_FOREVER_MS);
void lism_probe_slots_start(void) { k_thread_start(probe_slot_thread); }
