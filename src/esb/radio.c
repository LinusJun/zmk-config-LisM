/* SPDX-License-Identifier: MIT
 * Only MPSL owns RADIO/TIMER0. The generated Nordic ESB source deliberately
 * omits RADIO vector registration and never disables the MPSL RADIO IRQ.
 * The high priority callbacks perform no kernel queue, logging or crypto work.
 */
#include "radio.h"
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <hal/nrf_timer.h>
#include <hal/nrf_radio.h>
#include <mpsl_timeslot.h>

BUILD_ASSERT(CONFIG_ESB_MAX_PAYLOAD_LENGTH >= LISM_ESB_PACKET);
BUILD_ASSERT(CONFIG_MPSL_TIMESLOT_SESSION_COUNT >= 1);
BUILD_ASSERT(CONFIG_ESB_RADIO_IRQ_PRIORITY == 0);

extern void *__ptr__radio_dynamic_irq_handler;
static const uint8_t prefixes[6] = {0xc3, 0x5a, 0xb7, 0x69, 0xd2, 0x96};
static struct lism_esb_radio_plan scheduled;
static struct esb_payload received[16];
static atomic_t rx_head, rx_tail, error, idle = ATOMIC_INIT(1), overflow;
static mpsl_timeslot_signal_return_param_t response;
static bool initialized;
#define SLOT_US (IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL) ? 4000 : 3000)
static mpsl_timeslot_request_t request = {
    .request_type = MPSL_TIMESLOT_REQ_TYPE_EARLIEST,
    .params.earliest = {.hfclk = MPSL_TIMESLOT_HFCLK_CFG_XTAL_GUARANTEED,
        .priority = MPSL_TIMESLOT_PRIORITY_NORMAL, .length_us = SLOT_US, .timeout_us = 100000},
};
static void collect_rx(void) {
    struct esb_payload payload;
    while (!esb_read_rx_payload(&payload)) {
        unsigned head = (unsigned)atomic_get(&rx_head), next = (head + 1) % ARRAY_SIZE(received);
        if (next == (unsigned)atomic_get(&rx_tail)) { atomic_set(&overflow, 1); continue; }
        received[head] = payload;
        atomic_set(&rx_head, next);
    }
}
static void esb_event(const struct esb_evt *event) {
    ARG_UNUSED(event);
    /* One producer only: drain RX at the slot boundary (IRQ0). An IRQ2
     * producer here could be preempted midway by stop_slot's IRQ0 drain. */
}
static void stop_slot(void) {
    if (initialized) {
        /* Drain before esb_disable resets the FIFO. IRQ0 preempts ESB's IRQ2,
         * so this also covers a pending event interrupt at the slot boundary. */
        collect_rx(); esb_disable(); initialized = false;
    }
    nrf_timer_int_disable(NRF_TIMER0, NRF_TIMER_INT_COMPARE0_MASK);
    NVIC_ClearPendingIRQ(RADIO_IRQn);
    irq_enable(RADIO_IRQn);
}
static mpsl_timeslot_signal_return_param_t *signal_handler(
    mpsl_timeslot_session_id_t session, uint32_t signal) {
    ARG_UNUSED(session);
    response.callback_action = MPSL_TIMESLOT_SIGNAL_ACTION_NONE;
    switch (signal) {
    case MPSL_TIMESLOT_SIGNAL_START: {
        /* MPSL has granted ownership. Start from reset radio registers rather
         * than inheriting controller-specific BLE configuration. */
        NVIC_ClearPendingIRQ(RADIO_IRQn);
        nrf_radio_power_set(NRF_RADIO, false);
        nrf_radio_power_set(NRF_RADIO, true);
        nrf_timer_bit_width_set(NRF_TIMER0, NRF_TIMER_BIT_WIDTH_32);
        nrf_timer_cc_set(NRF_TIMER0, NRF_TIMER_CC_CHANNEL0, SLOT_US - 300);
        nrf_timer_event_clear(NRF_TIMER0, NRF_TIMER_EVENT_COMPARE0);
        nrf_timer_int_enable(NRF_TIMER0, NRF_TIMER_INT_COMPARE0_MASK);
        struct esb_config config = ESB_DEFAULT_CONFIG;
        config.mode = IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL) ? ESB_MODE_PRX : ESB_MODE_PTX;
        config.event_handler = esb_event; config.retransmit_count = 3;
        config.retransmit_delay = 600; config.use_fast_ramp_up = true;
        int rc = esb_init(&config); initialized = !rc;
        if (!rc) rc = esb_set_base_address_0(scheduled.base0);
        if (!rc) rc = esb_set_base_address_1(scheduled.base1);
        if (!rc) rc = esb_set_prefixes(prefixes, ARRAY_SIZE(prefixes));
        if (!rc) rc = esb_set_rf_channel(scheduled.channel);
        for (unsigned i = 0; !rc && i < scheduled.count; ++i)
            rc = esb_write_payload(&scheduled.packets[i]);
        if (!rc && IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)) rc = esb_start_rx();
        if (rc) {
            atomic_set(&error, rc); stop_slot();
            response.callback_action = MPSL_TIMESLOT_SIGNAL_ACTION_END;
        }
        return &response;
    }
    case MPSL_TIMESLOT_SIGNAL_RADIO:
        if (initialized) ((void (*)(const void *))__ptr__radio_dynamic_irq_handler)(NULL);
        return &response;
    case MPSL_TIMESLOT_SIGNAL_TIMER0:
        if (nrf_timer_event_check(NRF_TIMER0, NRF_TIMER_EVENT_COMPARE0)) {
            nrf_timer_event_clear(NRF_TIMER0, NRF_TIMER_EVENT_COMPARE0); stop_slot();
            response.callback_action = MPSL_TIMESLOT_SIGNAL_ACTION_END;
        }
        return &response;
    case MPSL_TIMESLOT_SIGNAL_SESSION_IDLE:
    case MPSL_TIMESLOT_SIGNAL_BLOCKED:
    case MPSL_TIMESLOT_SIGNAL_CANCELLED:
        atomic_set(&idle, 1); return NULL;
    case MPSL_TIMESLOT_SIGNAL_INVALID_RETURN:
    case MPSL_TIMESLOT_SIGNAL_OVERSTAYED:
        atomic_set(&error, -EIO); return NULL;
    default: return NULL;
    }
}
static void radio_thread(void *a, void *b, void *c) {
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    mpsl_timeslot_session_id_t session;
    int rc = mpsl_timeslot_session_open(signal_handler, &session);
    if (rc) { lism_esb_transport_error(rc); return; }
    for (;;) {
        int64_t now = k_uptime_get();
        if (atomic_set(&overflow, 0)) lism_esb_transport_error(-ENOBUFS);
        if ((rc = atomic_set(&error, 0))) {
            /* Do not recycle an overstayed/invalid session. Fail closed. */
            lism_esb_transport_error(rc); break;
        }
        unsigned tail;
        while ((tail = (unsigned)atomic_get(&rx_tail)) != (unsigned)atomic_get(&rx_head)) {
            struct esb_payload copy = received[tail];
            atomic_set(&rx_tail, (tail + 1) % ARRAY_SIZE(received));
            lism_esb_transport_receive(&copy, now);
        }
        lism_esb_transport_poll(now);
        if (atomic_get(&idle) && lism_esb_transport_plan(&scheduled, now)) {
            atomic_set(&idle, 0);
            rc = mpsl_timeslot_request(session, &request);
            if (rc) { lism_esb_transport_error(rc); break; }
        }
        k_sleep(K_MSEC(1));
    }
    mpsl_timeslot_session_close(session);
}
K_THREAD_DEFINE(lism_esb_radio_thread, 3072, radio_thread, NULL, NULL, NULL,
                K_PRIO_COOP(8), 0, SYS_FOREVER_MS);
static int start_radio(void) { k_thread_start(lism_esb_radio_thread); return 0; }
SYS_INIT(start_radio, APPLICATION, 99);
