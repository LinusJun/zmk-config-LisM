"""Bind each queued report to its original connection, using pinned local copies.

Keep the HID descriptor, report bodies, GATT attributes and host bonds unchanged.
An enqueue owns one connection reference until delivery, failure or discard.
"""
from pathlib import Path
import hashlib, re, sys
from prepare_upstream_esb import replace

HOG_SHA = '2e8c12bb4197173d238a6be1e33abf2db5dedbd4a6cf29a83d5e2f6c79d4919b'
ENDPOINT_SHA = '6524dd3f09b747f6106dd208af2a59245f0463a7c7aa5b417036a61b3533b0e8'

def function(text, name):
    m = re.search(r'(?m)^(?:static )?[^\n;]+\b'+name+r'\([^;]*?\)\s*\{', text)
    assert m, name
    i = text.index('{', m.start())+1
    depth = 1
    while depth:
        depth += (text[i] == '{')-(text[i] == '}')
        i += 1
    return text[m.start():i]

def prepare_hog(source, destination):
    raw = Path(source).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == HOG_SHA
    text = '#include <lism_hog_routing.h>\n'+raw.decode()
    for kind in ['keyboard', 'consumer', 'mouse']:
        body = 'struct zmk_hid_'+kind+'_report_body'
        item = 'struct lism_hog_'+kind+'_item'
        queue = 'zmk_hog_'+kind+'_msgq'
        anchor = f'K_MSGQ_DEFINE({queue}, sizeof({body}),'
        text = replace(text, anchor,
                       f'{item} {{ struct bt_conn *conn; {body} report; }};\n\n'
                       f'K_MSGQ_DEFINE({queue}, sizeof({item}),')
        name = 'send_'+kind+'_report_callback'
        old = function(text, name)
        new = old.replace(body+' report;', item+' item;')
        new = new.replace(f'k_msgq_get(&{queue}, &report,', f'k_msgq_get(&{queue}, &item,')
        new = replace(new, '''        struct bt_conn *conn = zmk_ble_active_profile_conn();
        if (conn == NULL) {
            return;
        }''', '''        struct bt_conn *conn = item.conn;
        /* The queued reference remains valid even after a disconnect. */''')
        new = replace(new, '.data = &report,', '.data = &item.report,')
        new = replace(new, '.len = sizeof(report),', '.len = sizeof(item.report),')
        text = replace(text, old, new)
        name = 'zmk_hog_send_'+kind+'_report'
        old = function(text, name)
        new = f'''int {name}_to_profile({body} *report, uint8_t profile) {{
    if (profile >= ZMK_BLE_PROFILE_COUNT) return -ERANGE;
    bt_addr_le_t *addr = zmk_ble_profile_address(profile);
    if (!bt_addr_le_cmp(addr, BT_ADDR_LE_ANY)) return -ENOTCONN;
    struct bt_conn *conn = bt_conn_lookup_addr_le(BT_ID_DEFAULT, addr);
    if (conn == NULL) return -ENOTCONN;
    {item} item = {{.conn = conn, .report = *report}};
    int err = k_msgq_put(&{queue}, &item, K_MSEC(100));
    if (err == -EAGAIN) {{
        /* Retain the upstream bounded queue's drop-oldest policy. Every
         * discarded item also releases its owned connection reference. */
        {item} discarded;
        if (!k_msgq_get(&{queue}, &discarded, K_NO_WAIT))
            bt_conn_unref(discarded.conn);
        err = k_msgq_put(&{queue}, &item, K_NO_WAIT);
    }}
    if (err) {{
        bt_conn_unref(conn);
        LOG_WRN("Failed to queue {kind} report (%d)", err);
        return err;
    }}
    k_work_submit_to_queue(&hog_work_q, &hog_{kind}_work);
    return 0;
}}

int {name}({body} *report) {{
    return {name}_to_profile(report, zmk_ble_active_profile_index());
}}'''
        text = replace(text, old, new)
    Path(destination).write_text(text)

def prepare_endpoints(source, destination):
    raw = Path(source).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ENDPOINT_SHA
    text = '#include <lism_hog_routing.h>\n'+raw.decode()
    text = replace(text, '    zmk_hid_mouse_clear();\n#endif // IS_ENABLED(CONFIG_ZMK_POINTING)',
                   '    zmk_hid_mouse_clear();\n    zmk_endpoint_send_mouse_report(); // Release on the current, old transport.\n#endif // IS_ENABLED(CONFIG_ZMK_POINTING)')
    for kind in ['keyboard', 'consumer', 'mouse']:
        text = replace(text, f'zmk_hog_send_{kind}_report(&{kind}_report->body)',
                       f'zmk_hog_send_{kind}_report_to_profile(&{kind}_report->body, current_instance.ble.profile_index)')
    Path(destination).write_text(text)

if __name__ == '__main__':
    prepare_hog(sys.argv[1], sys.argv[2])
    prepare_endpoints(sys.argv[3], sys.argv[4])
