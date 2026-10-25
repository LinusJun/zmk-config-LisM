"""Recognize a running HFXO left by the UF2 loader; keep SDK checkout intact."""
from pathlib import Path
import hashlib
import sys

PIN = 'c85ade09a43bd70a0b3b8b9714a1f6f9985dbb78e08d5b670b9f7495e5caa6ca'

def prepare(source, destination):
    raw = Path(source).read_bytes()
    if hashlib.sha256(raw).hexdigest() != PIN:
        raise ValueError('Pinned nRF clock driver changed; review handoff patch')
    old = '''\thfclk_users |= HF_USER_GENERIC;
\tif (hfclk_users & HF_USER_BT) {
\t\t(void)nrfx_clock_is_running(NRF_CLOCK_DOMAIN_HFCLK, &type);
\t\tif (type == NRF_CLOCK_HFCLK_HIGH_ACCURACY) {'''
    new = '''\thfclk_users |= HF_USER_GENERIC;
\t/* UF2 can hand over with HFXO running and its STARTED event consumed.
\t * Adopt it through the normal driver callback, exactly as for BT's
\t * existing HFXO. Do not stop a clock also used by USB. */
\t{
\t\tbool running = nrfx_clock_is_running(NRF_CLOCK_DOMAIN_HFCLK, &type);
\t\tif (running && type == NRF_CLOCK_HFCLK_HIGH_ACCURACY) {'''
    text = raw.decode()
    if text.count(old) != 1:
        raise ValueError('Clock handoff anchor moved')
    text = text.replace(old, new).replace(
        '/* Clock already started by z_nrf_clock_bt_ctlr_hf_request */',
        '/* Clock already started by BT or the UF2 bootloader. */')
    text = text.replace('static void generic_hfclk_start(void)',
        'static unsigned lism_handoff[5];\n'
        'static unsigned lism_hf_starts, lism_hf_events, lism_hf_completed;\n'
        'static unsigned lism_hf_entry_flags, lism_hf_entry_running, lism_hf_entry_type;\n'
        'static void generic_hfclk_start(void)')
    text = text.replace('bool running = nrfx_clock_is_running(NRF_CLOCK_DOMAIN_HFCLK, &type);',
        'bool running = nrfx_clock_is_running(NRF_CLOCK_DOMAIN_HFCLK, &type);\n'
        '\t\tlism_hf_starts++; lism_hf_entry_flags = *get_hf_flags();\n'
        '\t\tlism_hf_entry_running = running; lism_hf_entry_type = type;')
    text = text.replace('case NRFX_CLOCK_EVT_HFCLK_STARTED:\n\t{',
        'case NRFX_CLOCK_EVT_HFCLK_STARTED:\n\t{\n\t\tlism_hf_events++;')
    text = text.replace('clkstarted_handle(dev, CLOCK_CONTROL_NRF_TYPE_HFCLK);',
        'lism_hf_completed++;\n\t\t\tclkstarted_handle(dev, CLOCK_CONTROL_NRF_TYPE_HFCLK);')
    # No application onoff client exists yet. A stale bootloader HF event
    # must not seed nrfx's anomaly-201 duplicate-event state before our start.
    before = '\tnrfx_clock_enable();\n\n\tfor (enum clock_control_nrf_type i = 0;'
    after = '\tlism_left_clock_discard_boot_event();\n' + before
    if text.count(before) != 1:
        raise ValueError('Clock enable/init anchor moved')
    text = text.replace(before, after)
    declaration = '''static void lism_left_clock_discard_boot_event(void)
{
    nrf_clock_event_clear(NRF_CLOCK, NRF_CLOCK_EVENT_HFCLKSTARTED);
}

'''
    text = text.replace('static int clk_init(const struct device *dev)',
                        declaration + 'static int clk_init(const struct device *dev)')
    # A USB request can already be waiting before the radio joins its manager.
    # Check physical readiness from the driver's own context, never by altering
    # the manager's state/refcounts or synthesizing a hardware interrupt.
    text += '''
int lism_left_hfclk_finish_pending(void)
{
    unsigned int key = irq_lock();
    struct nrf_clock_control_sub_data *data =
        get_sub_data(CLOCK_DEVICE, CLOCK_CONTROL_NRF_TYPE_HFCLK);
    struct onoff_manager *mgr =
        get_onoff_manager(CLOCK_DEVICE, CLOCK_CONTROL_NRF_TYPE_HFCLK);
    nrf_clock_hfclk_t type;
    bool running = nrfx_clock_is_running(NRF_CLOCK_DOMAIN_HFCLK, &type);
    unsigned flags = data->flags, manager_flags = mgr->flags, users = hfclk_users;
    bool complete = GET_STATUS(data->flags) == CLOCK_CONTROL_STATUS_STARTING &&
        GET_CTX(data->flags) == CTX_ONOFF &&
        data->cb == onoff_started_callback &&
        (mgr->flags & ONOFF_STATE_MASK) == ONOFF_STATE_TO_ON &&
        (hfclk_users & HF_USER_GENERIC) && running &&
        type == NRF_CLOCK_HFCLK_HIGH_ACCURACY;
    lism_handoff[0]=1; lism_handoff[1]=flags; lism_handoff[2]=manager_flags;
    lism_handoff[3]=mgr->refs; lism_handoff[4]=complete;
    if (complete) {
        set_on_state(&data->flags);
    }
    irq_unlock(key);
    LOG_WRN("Left HFCLK handoff flags=%x manager=%x users=%x running=%u type=%u starts=%u entry=%x/%u/%u events=%u completed=%u recover=%u",
        flags, manager_flags, users, running, type, lism_hf_starts,
        lism_hf_entry_flags, lism_hf_entry_running, lism_hf_entry_type,
        lism_hf_events, lism_hf_completed, complete);
    if (complete) {
        clkstarted_handle(CLOCK_DEVICE, CLOCK_CONTROL_NRF_TYPE_HFCLK);
        return 1;
    }
    return 0;
}

void lism_left_clock_snapshot(uint32_t out[16])
{
    unsigned key=irq_lock();
    struct nrf_clock_control_sub_data *d=get_sub_data(CLOCK_DEVICE,CLOCK_CONTROL_NRF_TYPE_HFCLK);
    struct onoff_manager *m=get_onoff_manager(CLOCK_DEVICE,CLOCK_CONTROL_NRF_TYPE_HFCLK);
    out[0]=d->flags;out[1]=m->flags;out[2]=m->refs;out[3]=hfclk_users;
    out[4]=NRF_CLOCK->HFCLKSTAT;out[5]=lism_hf_starts;out[6]=lism_hf_events;
    out[7]=lism_hf_completed;out[8]=lism_hf_entry_flags;
    out[9]=lism_hf_entry_running;out[10]=lism_hf_entry_type;
    for(unsigned i=0;i<5;i++)out[11+i]=lism_handoff[i];
    irq_unlock(key);
}
'''
    Path(destination).write_text(text)

if __name__ == '__main__':
    prepare(*sys.argv[1:])
