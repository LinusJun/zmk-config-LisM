"""Exercise the CMake patch against actual pinned ZMK, then compile its guards.

Usage: python3 tests/lism_hid_central_patch_test.py [path/to/zmk/app]
The default is the adjacent xiao147-zmk-reference/app checkout. No downloads,
hardware access, source mutation, pairing or settings writes are performed.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
app = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "LISM_ZMK_APP", root.parent / "xiao147-zmk-reference/app"))
central = (app / "src/split/bluetooth/central.c").read_text()
ble = (app / "src/ble.c").read_text()
endpoints = (app / "src/endpoints.c").read_text()
cmake = root / "cmake/lism_hid_central.cmake"


def function(code, signature):
    start = code.index(signature)
    return code[start:code.index("\n}", start) + 3]


with tempfile.TemporaryDirectory(prefix="lism-hid-central-") as directory:
    temp = Path(directory)
    input_central, input_ble = temp / "central.c", temp / "ble.c"
    patched_central, patched_ble = temp / "patched-central.c", temp / "patched-ble.c"
    input_endpoints, patched_endpoints = temp / "endpoints.c", temp / "patched-endpoints.c"
    driver = temp / "patch.cmake"
    driver.write_text(f'include("{cmake}")\n'
                      f'lism_hid_patch_central("{input_central}" "{patched_central}" '
                      f'"{root / "src/lism_hid_host.h"}")\n'
                      f'lism_hid_patch_ble("{input_ble}" "{patched_ble}")\n'
                      f'lism_hid_patch_endpoints("{input_endpoints}" "{patched_endpoints}")\n')

    def patch(source_central=central, source_ble=ble, source_endpoints=endpoints, succeeds=True):
        input_central.write_text(source_central)
        input_ble.write_text(source_ble)
        input_endpoints.write_text(source_endpoints)
        result = subprocess.run(["cmake", "-P", str(driver)], capture_output=True, text=True)
        assert (result.returncode == 0) == succeeds, result.stdout + result.stderr
        if not succeeds:
            assert "Pinned HID scan hook moved or duplicated" in result.stderr

    patch()
    output, ble_output = patched_central.read_text(), patched_ble.read_text()
    assert central == (app / "src/split/bluetooth/central.c").read_text()
    assert ble == (app / "src/ble.c").read_text()
    assert endpoints == (app / "src/endpoints.c").read_text()
    assert patched_endpoints.read_text().replace(
        "    zmk_hid_mouse_clear();\n    zmk_endpoint_send_mouse_report();",
        "    zmk_hid_mouse_clear();", 1) == endpoints
    assert 'BT_GAP_ADV_TYPE_ADV_DIRECT_IND && lism_split_known_addr(addr)' in output
    assert 'if (lism_hid_host_claim_ad(addr)) { return; }' in output
    assert 'int lism_hid_scan_pause(void) { return is_scanning ? stop_scanning() : 0; }' in output
    assert 'void lism_hid_scan_resume(void) {' in output
    for signature in ("static void split_central_connected(", "static void split_central_disconnected("):
        body = function(output, signature)
        assert body[body.index("{") + 1:].lstrip().startswith(
            "if (peripheral_slot_for_conn(conn) == NULL) { return; }")
    # Only the read-only accessor is inserted into ble.c. Existing SMP,
    # security, host profile selection and bond persistence are byte-identical.
    accessor = function(ble_output, "bool lism_split_known_addr(")
    inserted = "/* Read-only identity filter: never reserve or save a split slot for HID. */\n" + accessor + "\n"
    assert ble_output.replace(inserted, "", 1) == ble

    anchors = ("#include <zmk/ble.h>", "    bool has_unconnected = false;", "static int start_scanning(void) {",
               "static bool split_central_eir_found(const bt_addr_le_t *addr) {",
               "                                       struct net_buf_simple *ad) {\n    char dev[BT_ADDR_LE_STR_LEN];",
               "    } else if (type == BT_GAP_ADV_TYPE_ADV_DIRECT_IND) {\n        split_central_eir_found(addr);",
               "    is_scanning = true;\n    int err = bt_le_scan_start(BT_LE_SCAN_PASSIVE, split_central_device_found);",
               '    LOG_DBG("Scanning successfully started");',
               "static void split_central_connected(struct bt_conn *conn, uint8_t conn_err) {",
               "static void split_central_disconnected(struct bt_conn *conn, uint8_t reason) {",
               "static bool is_enabled;", "    is_enabled = enabled;",
               '        LOG_DBG("Not scanning, we\'re disabled");',
               '        LOG_DBG("Scanning already running");',
               '        LOG_DBG("All devices are connected, scanning is unnecessary");')
    for anchor in anchors:
        patch(central.replace(anchor, "/* moved anchor */", 1), succeeds=False)
        patch(central + "\n" + anchor, succeeds=False)
    ble_anchor = "int zmk_ble_put_peripheral_addr(const bt_addr_le_t *addr) {"
    patch(source_ble=ble.replace(ble_anchor, "/* moved anchor */", 1), succeeds=False)
    patch(source_ble=ble + "\n" + ble_anchor, succeeds=False)
    stop_anchor = function(central, "static int stop_scanning(void) {").rstrip()
    patch(central.replace(stop_anchor, "/* moved stop */", 1), succeeds=False)
    patch(central + "\n" + stop_anchor, succeeds=False)
    endpoint_anchor = "    zmk_hid_mouse_clear();"
    patch(source_endpoints=endpoints.replace(endpoint_anchor, "/* moved clear */", 1), succeeds=False)
    patch(source_endpoints=endpoints + "\n" + endpoint_anchor, succeeds=False)
    settings_anchor = ('#if IS_ENABLED(CONFIG_SETTINGS)\n'
                       '    settings_register(&ble_central_settings_handler);\n'
                       '    return 0;\n#else\n    return finish_init();\n'
                       '#endif // IS_ENABLED(CONFIG_SETTINGS)')
    patch(central.replace(settings_anchor, "/* moved settings init */", 1), succeeds=False)
    patch(central + "\n" + settings_anchor, succeeds=False)
    patched_init = function(output, "static int zmk_split_bt_central_init(void) {")
    assert patched_init.index("settings_subsys_init()") < patched_init.index("settings_register(")
    assert "settings_load(" not in patched_init and "settings_save" not in patched_init
    assert "settings_delete(" not in patched_init
    assert "BLE central settings registered" in patched_init
    assert "SYS_INIT(zmk_split_bt_central_init, APPLICATION, CONFIG_ZMK_BLE_INIT_PRIORITY);" in output

    scan = function(output, "static int start_scanning(void) {")
    assert scan[scan.index("{") + 1:].lstrip().startswith(
        "atomic_inc(&lism_owner_diag[OWNER_START]);")
    assert "if (lism_hid_host_scan_paused())" in scan
    assert scan.index("if (err < 0)") < scan.index("is_scanning = true;")
    guard = function(output, "static void split_central_device_found(")
    assert guard.index("atomic_inc(&lism_owner_diag[OWNER_RX])") < guard.index(
        "lism_hid_host_claim_ad(addr)")
    assert "BLE owner diag" not in guard  # RX stays free of diagnostic logs.
    prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
typedef struct { unsigned char type, value; } bt_addr_le_t;
static const bt_addr_le_t empty = {0};
#define BT_ADDR_LE_ANY (&empty)
#define ZMK_SPLIT_BLE_PERIPHERAL_COUNT 4
#define CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS 4
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define BT_ADDR_LE_STR_LEN 30
#define BT_GAP_ADV_TYPE_ADV_IND 0
#define BT_GAP_ADV_TYPE_ADV_DIRECT_IND 1
#define BT_LE_SCAN_ACTIVE 42
static void test_log(const char *fmt,...) {(void)fmt;}
#define LOG_DBG(...) test_log(__VA_ARGS__)
#define LOG_ERR(...) test_log(__VA_ARGS__)
#define LOG_INF(...) test_log(__VA_ARGS__)
typedef long atomic_t;
static long atomic_inc(atomic_t *p) {return (*p)++;}
static long atomic_get(const atomic_t *p) {return *p;}
static long atomic_set(atomic_t *p,long v) {long old=*p;*p=v;return old;}
static bt_addr_le_t peripheral_addrs[4];
static struct {void *conn;} peripherals[4];
struct bt_conn {int identity;};
static void *peripheral_slot_for_conn(struct bt_conn *conn) {
    for(size_t i=0;i<ARRAY_SIZE(peripherals);i++) {
        if(peripherals[i].conn==conn)return &peripherals[i];
    }
    return NULL;
}
static bool is_enabled = true, is_scanning, need_hid, claim_hid, paused;
static int attempts, scan_result, split_connections, parsed, stops, stop_result;
struct net_buf_simple {unsigned len;};
static int bt_addr_le_cmp(const bt_addr_le_t *a, const bt_addr_le_t *b) {
    return a->type != b->type || a->value != b->value;
}
static bool lism_hid_host_scan_needed(void) {return need_hid;}
static bool lism_hid_host_scan_paused(void) {return paused;}
static int bt_le_scan_stop(void) {stops++;return stop_result;}
static bool lism_hid_host_claim_ad(const bt_addr_le_t *addr) {(void)addr;return claim_hid;}
static void bt_addr_le_to_str(const bt_addr_le_t *a,char *s,unsigned n) {(void)a;(void)s;(void)n;}
static void split_central_eir_parse(void) {}
static void bt_data_parse(struct net_buf_simple *ad,void (*cb)(void),void *addr) {
    (void)ad;(void)cb;(void)addr;parsed++;
}
static void split_central_eir_found(const bt_addr_le_t *a) {(void)a;split_connections++;}
static void split_central_device_found(const bt_addr_le_t *,int8_t,uint8_t,struct net_buf_simple *);
static int bt_le_scan_start(int mode,void (*cb)(const bt_addr_le_t *,int8_t,uint8_t,struct net_buf_simple *)) {
    assert(mode==42);(void)cb;attempts++;return scan_result;
}
'''
    pause = output[output.index("int lism_hid_scan_pause(void)"):output.index("\nvoid lism_hid_scan_resume")]
    stop = function(output, "static int stop_scanning(void) {")
    resume = function(output, "void lism_hid_scan_resume(void) {")
    diagnostic_support = output[output.index("/* LISM owner diagnostics begin:"):
                                output.index("/* LISM owner diagnostics end */")]
    # Compile the actual assignments from set_enabled without exercising its
    # unrelated peripheral disconnect/event machinery.
    enable_body = function(output, "static int split_central_bt_set_enabled(bool enabled) {")
    enable_prefix = enable_body[enable_body.index("{") + 1:enable_body.index("    if (enabled)")]
    assert enable_prefix.strip() == ("is_enabled = enabled;\n"
                                    "    atomic_set(&lism_owner_diag[OWNER_ENABLED], enabled);")
    enable_mirror = "static void set_enabled_prefix(bool enabled) {" + enable_prefix + "}\n"
    owns_connection = function(output, "bool lism_split_owns_connection(struct bt_conn *conn) {")
    checks = r'''
int main(void) {
    struct bt_conn owned={1},unowned={2};
    assert(!lism_split_owns_connection(NULL));
    assert(!lism_split_owns_connection(&unowned));
    peripherals[3].conn=&owned;
    assert(lism_split_owns_connection(&owned));
    assert(!lism_split_owns_connection(&unowned));
    peripherals[3].conn=NULL;
    assert(!lism_split_owns_connection(&owned));
    bt_addr_le_t known={1,10},unknown={1,11},same_bytes_wrong_type={0,10};
    peripheral_addrs[2]=known;
    assert(!lism_split_known_addr(NULL));assert(!lism_split_known_addr(BT_ADDR_LE_ANY));
    assert(lism_split_known_addr(&known));assert(!lism_split_known_addr(&unknown));
    assert(!lism_split_known_addr(&same_bytes_wrong_type));
    struct net_buf_simple ad={0};
    split_central_device_found(&unknown,-30,1,&ad);assert(split_connections==0);
    split_central_device_found(&known,-30,1,&ad);assert(split_connections==1);
    claim_hid=true;split_central_device_found(&known,-30,1,&ad);assert(split_connections==1);
    split_central_device_found(&unknown,-30,0,&ad);assert(parsed==0);
    claim_hid=false;split_central_device_found(&unknown,-30,0,&ad);assert(parsed==1);
    assert(atomic_get(&lism_owner_diag[OWNER_RX])==5);
    set_enabled_prefix(false);assert(!is_enabled&&atomic_get(&lism_owner_diag[OWNER_ENABLED])==0);
    set_enabled_prefix(true);assert(is_enabled&&atomic_get(&lism_owner_diag[OWNER_ENABLED])==1);
    for(int i=0;i<4;i++)peripherals[i].conn=(void*)1;
    assert(start_scanning()==0 && attempts==0);
    assert(atomic_get(&lism_owner_diag[OWNER_FULL])==1);
    need_hid=true;assert(start_scanning()==0 && attempts==1 && is_scanning);
    assert(atomic_get(&lism_owner_diag[OWNER_START_CALL])==1);
    assert(atomic_get(&lism_owner_diag[OWNER_SCAN_CACHE])==1);
    assert(start_scanning()==0 && attempts==1);
    assert(atomic_get(&lism_owner_diag[OWNER_CACHED])==1);
    is_scanning=false;scan_result=-5;assert(start_scanning()==-5 && !is_scanning);
    assert(atomic_get(&lism_owner_diag[OWNER_START_RC])==-5);
    assert(atomic_get(&lism_owner_diag[OWNER_START_CALL])==2);
    scan_result=0;assert(start_scanning()==0 && attempts==3 && is_scanning);
    assert(atomic_get(&lism_owner_diag[OWNER_START_RC])==0);
    is_scanning=false;is_enabled=false;assert(start_scanning()==0 && attempts==3);
    assert(atomic_get(&lism_owner_diag[OWNER_DISABLED])==1);
    is_enabled=true;need_hid=false;peripherals[0].conn=NULL;
    assert(start_scanning()==0 && attempts==4 && is_scanning);
    stop_result=-5;assert(lism_hid_scan_pause()==-5&&stops==1&&is_scanning);
    assert(atomic_get(&lism_owner_diag[OWNER_STOP_CALL])==1);
    assert(atomic_get(&lism_owner_diag[OWNER_STOP_RC])==-5);
    assert(atomic_get(&lism_owner_diag[OWNER_SCAN_CACHE])==1);
    stop_result=0;assert(lism_hid_scan_pause()==0&&stops==2&&!is_scanning);
    assert(atomic_get(&lism_owner_diag[OWNER_SCAN_CACHE])==0);
    assert(lism_hid_scan_pause()==0&&stops==2);
    paused=true;assert(start_scanning()==0&&!is_scanning&&attempts==4);
    assert(atomic_get(&lism_owner_diag[OWNER_PAUSED])==1);
    paused=false;assert(start_scanning()==0&&is_scanning&&attempts==5);
    stop_result=-EALREADY;assert(lism_hid_scan_pause()==0&&stops==3&&!is_scanning);
    assert(atomic_get(&lism_owner_diag[OWNER_STOP_RC])==-EALREADY);
    assert(atomic_get(&lism_owner_diag[OWNER_SCAN_CACHE])==0);
    /* The expired explicit HID window must stop active scanning if every split
     * peer is connected. A missing split still owns an independent scan need. */
    stop_result=0;need_hid=true;lism_hid_scan_resume();assert(is_scanning&&attempts==6);
    for(int i=0;i<4;i++)peripherals[i].conn=(void*)1;
    need_hid=false;lism_hid_scan_resume();assert(!is_scanning&&stops==4);
    peripherals[0].conn=NULL;lism_hid_scan_resume();assert(is_scanning&&attempts==7);
    need_hid=false;lism_hid_scan_resume();assert(is_scanning&&stops==4);
    paused=true;peripherals[0].conn=(void*)1;lism_hid_scan_resume();assert(is_scanning&&stops==4);
    paused=false;lism_hid_scan_resume();assert(!is_scanning&&stops==5);
    assert(atomic_get(&lism_owner_diag[OWNER_RESUME])==6);
    assert(atomic_get(&lism_owner_diag[OWNER_START_CALL])==attempts);
    assert(atomic_get(&lism_owner_diag[OWNER_STOP_CALL])==stops);
    lism_hid_scan_diagnostics();
    assert(atomic_get(&lism_owner_diag[OWNER_RX])==5); /* logging never resets */
    /* EALREADY remains an error on start; cache is not falsely set. */
    scan_result=-EALREADY;assert(start_scanning()==0); /* all split peers full */
    need_hid=true;assert(start_scanning()==-EALREADY&&!is_scanning);
    assert(atomic_get(&lism_owner_diag[OWNER_START_RC])==-EALREADY);
    assert(atomic_get(&lism_owner_diag[OWNER_SCAN_CACHE])==0);
    assert(atomic_get(&lism_owner_diag[OWNER_START])==
           atomic_get(&lism_owner_diag[OWNER_START_CALL])+
           atomic_get(&lism_owner_diag[OWNER_PAUSED])+
           atomic_get(&lism_owner_diag[OWNER_DISABLED])+
           atomic_get(&lism_owner_diag[OWNER_CACHED])+
           atomic_get(&lism_owner_diag[OWNER_FULL]));
    return 0;
}
'''
    c, executable = temp / "guard.c", temp / "guard"
    c.write_text(prelude + diagnostic_support + enable_mirror + owns_connection + accessor + guard + scan + stop + pause + resume + checks)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", str(c), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
print("PASS: actual pinned CMake hooks; moved/duplicate anchors fail closed; known-directed filter, HID ownership, all-split scan and retry guards")
