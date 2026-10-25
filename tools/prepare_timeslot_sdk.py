"""Pinned nRF52840 PRX adaptation, reserve resources in thread context once.

No SDK event/TIMER2 ISR is enabled. Hardware ACK uses RADIO/PPI; drain RX at
slot end. Application framing retains dedup/command ACK across FIFO resets.
"""
from pathlib import Path
import hashlib, sys
from prepare_upstream_esb import replace

SDK_SHA='0edd4a985f5edc62267e87d1427f790ef81e691a563437aaafc6b02599c732b5'

def prepare(source, destination):
    data=Path(source).read_bytes()
    assert hashlib.sha256(data).hexdigest()==SDK_SHA
    t=data.decode()
    begin=t.index('\terr = sys_timer_init();',t.index('int esb_init('))
    end=t.index('\n\tdisable_event.event.generic.event',begin)
    resources=t[begin:end]
    t=t[:begin]+t[end:]
    t=replace(t,'int esb_init(const struct esb_config *config)\n{\n\tint err;',
                'int esb_init(const struct esb_config *config)\n{')
    begin=t.index('#if IS_ENABLED(CONFIG_ESB_DYNAMIC_INTERRUPTS)',t.index('int esb_init('))
    end=t.index('\n\tesb_state = ESB_STATE_IDLE;',begin)
    t=t[:begin]+'''/* MPSL owns RADIO. SWI0/TIMER2 stay disabled for this PRX.
     * No interrupt registration or resource allocation from IRQ0. */
'''+t[end:]
    t=replace(t,'\tirq_disable(ESB_RADIO_IRQ_NUMBER);',
                '\t/* Never disable the MPSL RADIO vector. */')
    t=replace(t,'\tsys_timer_deinit();\n\tesb_ppi_deinit();', '''\t/* Keep preallocated TIMER2/PPI resources for the next slot. */
    nrf_timer_task_trigger(ESB_NRF_TIMER_INSTANCE, NRF_TIMER_TASK_STOP);
    nrf_timer_task_trigger(ESB_NRF_TIMER_INSTANCE, NRF_TIMER_TASK_CLEAR);
    nrf_timer_int_disable(ESB_NRF_TIMER_INSTANCE, 0xFFFFFFFF);
    NVIC_ClearPendingIRQ(ESB_EVT_IRQ_NUMBER);
    NVIC_ClearPendingIRQ(ESB_TIMER_IRQ);''')
    # Disable ESB PPI before POWER=0, preventing DISABLED from ramping up RX.
    # Bounded teardown discards an in-progress packet; unchanged PTX retries.
    begin=t.index('\tnrf_radio_event_clear(NRF_RADIO, NRF_RADIO_EVENT_DISABLED);',t.index('void esb_disable('))
    end=t.index('\n\tesb_fem_reset();',begin)
    t=t[:begin]+'''    esb_ppi_disable_all();
    nrf_radio_power_set(NRF_RADIO, false);
    nrf_radio_event_clear(NRF_RADIO, NRF_RADIO_EVENT_DISABLED);
'''+t[end:]
    # Bad CRC / full RX FIFO can arrive while automatic RX/ACK shorts are
    # ramping the radio. Never busy-wait at MPSL priority 0: stop this slot's
    # PRX hardware, retain queued RX data, and reinitialize on the next grant.
    begin=t.index('static void clear_events_restart_rx(void)')
    end=t.index('static void on_radio_disabled_rx_dpl(',begin)
    t=t[:begin]+'''static void clear_events_restart_rx(void)
{
    /* Resume on next MPSL grant, after end_slot drains the existing FIFO. */
    esb_ppi_disable_all();
    nrf_radio_shorts_disable(NRF_RADIO, 0xFFFFFFFF);
    nrf_radio_int_disable(NRF_RADIO, 0xFFFFFFFF);
    nrf_radio_power_set(NRF_RADIO, false);
}

'''+t[end:]
    t+='''
int lism_esb_resources_init(void) {
    int err;
'''+resources+'''
    irq_disable(ESB_EVT_IRQ_NUMBER);
    irq_disable(ESB_TIMER_IRQ);
    NVIC_ClearPendingIRQ(ESB_EVT_IRQ_NUMBER);
    NVIC_ClearPendingIRQ(ESB_TIMER_IRQ);
    return 0;
}
void lism_esb_radio_irq(void) { radio_irq_handler(); }
'''
    # The right hub's PTX path is explicitly serviced from granted MPSL IRQs.
    # SWI/TIMER2 NVIC vectors stay disabled. No timer object is allocated in IRQ0.
    t+='''
void lism_esb_slot_service(void) {
    if (nrf_timer_int_enable_check(esb_timer.p_reg, NRF_TIMER_INT_COMPARE1_MASK) &&
        nrf_timer_event_check(esb_timer.p_reg, NRF_TIMER_EVENT_COMPARE1))
        esb_timer_handler(NRF_TIMER_EVENT_COMPARE1, NULL);
    if (nrf_timer_int_enable_check(esb_timer.p_reg, NRF_TIMER_INT_COMPARE3_MASK) &&
        nrf_timer_event_check(esb_timer.p_reg, NRF_TIMER_EVENT_COMPARE3)) {
        nrf_timer_event_clear(esb_timer.p_reg, NRF_TIMER_EVENT_COMPARE3);
        esb_timer_handler(NRF_TIMER_EVENT_COMPARE3, NULL);
    }
    esb_evt_irq_handler();
    NVIC_ClearPendingIRQ(ESB_EVT_IRQ_NUMBER);
}
'''
    # Read-only right-hand trace of the actual SDK ACK state machine. Other
    # targets compile these calls away; no radio/clock/lease setting changes.
    t='''#include <stdint.h>
#if defined(CONFIG_SHIELD_LISM_R)
extern void lism_esb_prx_trace(unsigned event, const uint8_t *rx, unsigned rx_len,
                             const uint8_t *tx, unsigned tx_len, unsigned detail);
#define LISM_PRX_TRACE(...) lism_esb_prx_trace(__VA_ARGS__)
#else
#define LISM_PRX_TRACE(...) do {} while (0)
#endif
'''+t
    t=replace(t, '\ttx_pdu->type.dpl_pdu.no_ack = rx_pdu->type.dpl_pdu.no_ack;',
                '''\ttx_pdu->type.dpl_pdu.no_ack = rx_pdu->type.dpl_pdu.no_ack;
    LISM_PRX_TRACE(1, rx_pdu->data, rx_pdu->type.dpl_pdu.length,
                   tx_pdu->data, tx_pdu->type.dpl_pdu.length,
                   rx_pdu->type.dpl_pdu.pid | (retransmit_payload ? 4U : 0U));''')
    t=replace(t, 'static void on_radio_disabled_rx_ack(void)\n{',
                '''static void on_radio_disabled_rx_ack(void)
{
    struct esb_radio_pdu *trace_tx = (struct esb_radio_pdu *)tx_payload_buffer;
    LISM_PRX_TRACE(2, NULL, 0, trace_tx->data,
                   trace_tx->type.dpl_pdu.length, trace_tx->type.dpl_pdu.pid);''')
    # These anchors are the real PRX error paths, before bounded recovery.
    t=replace(t, '''\tif (!nrf_radio_crc_status_check(NRF_RADIO)) {
\t\tclear_events_restart_rx();''', '''\tif (!nrf_radio_crc_status_check(NRF_RADIO)) {
        LISM_PRX_TRACE(4, NULL, 0, NULL, 0, 0);
\t\tclear_events_restart_rx();''')
    t=replace(t, '''\tif (rx_fifo.count >= CONFIG_ESB_RX_FIFO_SIZE) {
\t\tclear_events_restart_rx();''', '''\tif (rx_fifo.count >= CONFIG_ESB_RX_FIFO_SIZE) {
        LISM_PRX_TRACE(5, NULL, 0, NULL, 0, 0);
\t\tclear_events_restart_rx();''')
    t+='''
void lism_esb_prx_slot_end_trace(void)
{
    if (esb_cfg.mode == ESB_MODE_PRX) {
        struct esb_radio_pdu *trace_tx = (struct esb_radio_pdu *)tx_payload_buffer;
        /* Snapshot before esb_disable clears state/POWER. A pending END may
         * mean the packet finished before its callback; retain that distinction. */
        LISM_PRX_TRACE(3, NULL, 0, trace_tx->data,
                       esb_state == ESB_STATE_PRX_SEND_ACK ? trace_tx->type.dpl_pdu.length : 0,
                       (esb_state == ESB_STATE_PRX_SEND_ACK ? 1U : 0U) |
                       (nrf_radio_event_check(NRF_RADIO, ESB_RADIO_EVENT_END) ? 2U : 0U));
    }
}
'''
    Path(destination).write_text(t)

def prepare_ppi(source, destination):
    data=Path(source).read_bytes()
    assert hashlib.sha256(data).hexdigest()=='f560e59b15467c9701bb1f9e35aacec87aab180167c28282636f8a89892dd95f'
    t=data.decode()
    t=replace(t, '''BIT(radio_end_timer_start) |
\t\t\t\t  (IS_ENABLED(CONFIG_ESB_NEVER_DISABLE_TX) ?
\t\t\t\t\tBIT(timer_compare1_radio_txen) : 0)''', '''BIT(timer_compare1_radio_txen) |
                  (IS_ENABLED(CONFIG_ESB_NEVER_DISABLE_TX) ?
                      BIT(radio_end_timer_start) : 0)''')
    t+='''
/* Read only our allocated channel IDs, safe outside a radio timeslot. */
void lism_esb_ppi_channels_get(uint8_t *channels)
{
    channels[0]=egu_ramp_up;
    channels[1]=disabled_egu;
    channels[2]=egu_timer_start;
    channels[3]=radio_address_timer_stop;
    channels[4]=timer_compare0_radio_disable;
    channels[5]=timer_compare1_radio_txen;
}
'''
    Path(destination).write_text(t)

if __name__=='__main__':
    prepare(sys.argv[1],sys.argv[2])
    prepare_ppi(sys.argv[3],sys.argv[4])
