"""Generate an isolated compatibility copy of pinned badjeff ESB sources."""
from pathlib import Path
import shutil,hashlib,json
ROOT=Path(__file__).resolve().parents[1]
def replace(text,old,new):
    if text.count(old)!=1:raise ValueError('Pinned upstream anchor changed: '+old[:90])
    return text.replace(old,new)
def prepare(driver,dest):
    driver=Path(driver);dest=Path(dest)
    expected=json.loads((ROOT/'tools/upstream-esb-sha256.json').read_text())
    for name,digest in expected.items():
        assert hashlib.sha256((driver/name).read_bytes()).hexdigest()==digest,name
    shutil.copytree(driver,dest,dirs_exist_ok=True,ignore=shutil.ignore_patterns('.git'))
    p=dest/'src/split/Kconfig'
    p.write_text(replace(p.read_text(),'select MPSL_DYNAMIC_INTERRUPTS if !BLE','select MPSL_DYNAMIC_INTERRUPTS if !ZMK_BLE'))
    p=dest/'src/split/esb/Kconfig'
    p.write_text(replace(p.read_text(),'    depends on BLE','    depends on ZMK_BLE'))
    folder=dest/'src/split/esb'
    p=folder/'app_esb.h'
    p.write_text(replace(p.read_text(),'    uint32_t data_length;','    uint32_t data_length;\n    bool accepted; /* true only after complete RX queue insertion */'))
    (folder/'CMakeLists.txt').write_text('''zephyr_library()
zephyr_include_directories(${APPLICATION_SOURCE_DIR}/include)
zephyr_library_include_directories(.)
zephyr_library_sources(common.c app_esb.c link.c)
zephyr_library_sources_ifdef(CONFIG_ZMK_SPLIT_ROLE_CENTRAL central.c)
zephyr_library_sources_ifndef(CONFIG_ZMK_SPLIT_ROLE_CENTRAL peripheral.c)
''')
    for source,name in [('common.c','common.c'),('radio.c','app_esb.c'),('link.c','link.c'),('link.h','link.h')]:
        shutil.copyfile(ROOT/'src/esb_upstream'/source,folder/name)
    p=folder/'common.h';p.write_text(p.read_text()+'\n#include "link.h"\n')
    for name in ['central.c','peripheral.c']:
        p=folder/name;t=p.read_text()
        # The new driver controls direct PTX as well as timeslot PRX.
        t=replace(t,'#if IS_ENABLED(CONFIG_ZMK_SPLIT_ESB_USE_TIMESLOT)'+(' ' if name=='peripheral.c' else '')+'\n    return zmk_split_esb_set_enable(enabled);\n#else\n    return 0;\n#endif','    return zmk_split_esb_set_enable(enabled);')
        t=replace(t,'.available = true,','.available = lism_radio_ready(),')
        status='((lism_connected(0) && lism_connected(1)) ? ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_ALL_CONNECTED : ((lism_connected(0) || lism_connected(1)) ? ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_SOME_CONNECTED : ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_DISCONNECTED))' if name=='central.c' else '(lism_connected(CONFIG_ZMK_SPLIT_ESB_PERIPHERAL_ID) ? ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_ALL_CONNECTED : ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_DISCONNECTED)'
        t=replace(t,'.connections = ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_ALL_CONNECTED,','.connections = '+status+',')
        t=replace(t,'            case -EAGAIN:\n                break;','            case -EAGAIN:\n                return;')
        t=t.replace('while (ring_buf_size_get(rx_buf) > ESB_MSG_EXTRA_SIZE)','while (ring_buf_size_get(rx_buf) >= sizeof(struct esb_msg_prefix) + sizeof(struct esb_msg_postfix))')
        if name=='central.c':
            t=replace(t,'    sources[0] = 0;\n\n    return 1;','    int count=0;\n    for(unsigned i=0;i<2;i++) if(lism_connected(i)) sources[count++]=i;\n    return count;')
            start=t.index('                if (&esb_central == active_transport) {')
            end=t.index('                break;\n            case -EAGAIN:',start)
            t=t[:start]+'''                if (lism_valid_event(pipe, &env))
                    lism_handle_event(env.payload.source, &env.payload.event);
'''+t[end:]
            fn='split_central_esb_send_command';sig='uint8_t source,\n                                          struct zmk_split_transport_central_command cmd'
            wrap='''
static int split_central_esb_send_command(uint8_t source, struct zmk_split_transport_central_command cmd) {
    if(source>=2) return -EINVAL;
    lism_tx_lock(); state.tx_pipe=source;
    int rc=split_central_esb_send_command_impl(source,cmd);
    lism_tx_unlock();return rc;
}
'''
            # Validate decoded commands on the other endpoint, before invoking any behavior.
        else:
            t=t.replace('    switch (evt->type) {\n    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT:', '    switch ((int)evt->type) {\n    case LISM_SNAPSHOT_TYPE: return sizeof(struct lism_snapshot);\n    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT:',1)
            t=replace(t,'    switch (evt->type) {\n    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT:', '    switch ((int)evt->type) {\n    case LISM_SNAPSHOT_TYPE: return 1;\n    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT:')
            marker='''            case 0:
                if (env.payload.cmd.type =='''
            t=replace(t,marker,'''            case 0:
                if (pipe != peripheral_id || env.payload.source != peripheral_id ||
                    env.prefix.payload_size != sizeof(env.payload.source) + sizeof(env.payload.cmd.type) +
                    lism_command_size(&env.payload.cmd)) break;
                if (env.payload.cmd.type ==''')
            cmdhelper='''
static size_t lism_command_size(const struct zmk_split_transport_central_command *cmd) {
    switch(cmd->type) {
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_POLL_EVENTS:return 0;
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_PHYSICAL_LAYOUT:return sizeof(cmd->data.set_physical_layout);
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_HID_INDICATORS:return sizeof(cmd->data.set_hid_indicators);
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR:
        return memchr(cmd->data.invoke_behavior.behavior_dev,0,sizeof(cmd->data.invoke_behavior.behavior_dev)) ? sizeof(cmd->data.invoke_behavior) : SIZE_MAX;
    default:return SIZE_MAX;
    }
}
'''
            t=replace(t,'static void process_rx_work_cb(struct k_work *work) {',cmdhelper+'\nstatic void process_rx_work_cb(struct k_work *work) {')
            fn='split_peripheral_esb_report_event';sig='const struct zmk_split_transport_peripheral_event *event'
            wrap='''
static int split_peripheral_esb_report_event(const struct zmk_split_transport_peripheral_event *event) {
    lism_tx_lock();lism_track_event(event);
    int rc=split_peripheral_esb_report_event_impl(event);
    lism_tx_unlock();return rc;
}
'''
        t=replace(t,fn+'('+sig+')',fn+'_impl('+sig+')')
        t=replace(t,'static const struct zmk_split_transport_'+('central' if name=='central.c' else 'peripheral')+'_api',wrap+'\nstatic const struct zmk_split_transport_'+('central' if name=='central.c' else 'peripheral')+'_api')
        t+='\nvoid lism_notify_status(void) { k_work_submit(&notify_status_work); }\n'
        p.write_text(t)
    return dest
