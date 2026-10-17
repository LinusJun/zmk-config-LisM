"""Patch and execute the actual pinned Prospector observer, never a copied implementation.

Pass the module checkout as argv[1] or LISM_PROSPECTOR_MODULE. CI must check
out the manifest's pinned ed98221f3b52b7066dbb10ba3af8a29150b93a5a revision.
No hardware, settings, bonds, downloads or source mutations are performed.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
module = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "LISM_PROSPECTOR_MODULE", "/private/tmp/lism-trackpad-build/prospector-zmk-module"))
original_path = module / "src/split/bluetooth/central_status_changed_observer.c"
original = original_path.read_text()
anchors = (
    "#include <zmk/ble.h>",
    "static void split_central_connected(struct bt_conn *conn, uint8_t conn_err) {",
    "static void split_central_disconnected(struct bt_conn *conn, uint8_t reason) {",
)
with tempfile.TemporaryDirectory(prefix="lism-hid-observer-") as directory:
    temp = Path(directory)
    source, output, driver = temp / "observer.c", temp / "patched.c", temp / "patch.cmake"
    driver.write_text(f'include("{root / "cmake/lism_hid_observer.cmake"}")\n'
                      f'lism_hid_patch_observer("{source}" "{output}")\n')

    def patch(text, success=True):
        source.write_text(text)
        result = subprocess.run(["cmake", "-P", str(driver)], capture_output=True, text=True)
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        if not success:
            assert "Pinned HID observer hook moved or duplicated" in result.stderr

    patch(original)
    patched = output.read_text()
    for anchor in anchors:
        patch(original.replace(anchor, "/* moved anchor */", 1), False)
        patch(original + "\n" + anchor, False)
    assert original_path.read_text() == original
    reverted = patched.replace(
        "\n\nextern bool lism_split_owns_connection(struct bt_conn *conn);", "", 1).replace(
        "\n    if (!lism_split_owns_connection(conn)) { return; }", "", 1).replace(
        "\n    if (psptr_peripheral_slot_index_for_conn(conn) < 0) { return; }", "", 1)
    assert reverted == original  # All original split paths remain byte-identical.

    prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <string.h>
#define ZMK_SPLIT_BLE_PERIPHERAL_COUNT 4
#define CONFIG_ZMK_BLE_INIT_PRIORITY 50
#define IS_ENABLED(x) x
#define BT_ADDR_LE_STR_LEN 32
#define BT_CONN_ROLE_CENTRAL 0
#define LOG_MODULE_DECLARE(...)
static void test_log(const char *fmt, ...) {(void)fmt;}
#define LOG_DBG(...) test_log(__VA_ARGS__)
#define LOG_ERR(...) test_log(__VA_ARGS__)
#define SYS_INIT(fn, ...) static int (*keep_init)(void) __attribute__((unused)) = fn
struct bt_conn {bool owned; int role, address;};
struct phy {int rx_phy;};
struct bt_conn_info {int role; struct {int interval, latency; struct phy *phy;} le;};
struct bt_conn_cb {void (*connected)(struct bt_conn *, uint8_t); void (*disconnected)(struct bt_conn *, uint8_t);};
struct zmk_split_central_status_changed {int slot; bool connected;};
static int put_calls, events, info_calls, addr_calls, next_slot;
static struct zmk_split_central_status_changed last;
bool lism_split_owns_connection(struct bt_conn *conn) {return conn->owned;}
static const int *bt_conn_get_dst(struct bt_conn *conn) {addr_calls++; return &conn->address;}
static void bt_addr_le_to_str(const int *addr, char *buffer, size_t len) {
    (void)addr; if(len)buffer[0]='\0';
}
static int bt_conn_get_info(struct bt_conn *conn, struct bt_conn_info *info) {
    static struct phy phy = {1}; info_calls++; *info=(struct bt_conn_info){.role=conn->role,.le={.phy=&phy}}; return 0;
}
static int bt_conn_get_security(struct bt_conn *conn) {(void)conn;return 2;}
int zmk_ble_put_peripheral_addr(const int *addr) {(void)addr;put_calls++;return next_slot;}
static void raise_zmk_split_central_status_changed(struct zmk_split_central_status_changed event) {
    assert(event.slot >= 0 && event.slot < 4); last=event;events++;
}
static void bt_conn_cb_register(struct bt_conn_cb *cb) {(void)cb;}
'''
    checks = r'''
int main(void) {
    struct bt_conn hid = {false,0,10}, split={true,0,11}, host={false,1,12};
    assert(zmk_split_bt_central_init()==0);
    for(int error=0; error<2; error++) {
        split_central_connected(&hid,(uint8_t)error);
        split_central_disconnected(&hid,19);
        split_central_connected(&host,(uint8_t)error);
        split_central_disconnected(&host,19);
    }
    assert(put_calls==0 && events==0 && info_calls==0 && addr_calls==0);
    for(int i=0;i<4;i++) assert(peripherals[i].conn==NULL && peripherals[i].state==PERIPHERAL_SLOT_STATE_OPEN);
    /* Failure must retain the original tracked-slot release path. */
    peripherals[1].conn=&split; peripherals[1].state=PERIPHERAL_SLOT_STATE_CONNECTED;
    split_central_connected(&split,1);
    assert(peripherals[1].conn==NULL && peripherals[1].state==PERIPHERAL_SLOT_STATE_OPEN);
    assert(events==0 && put_calls==0);
    split_central_disconnected(&split,19); assert(events==0);
    /* The original role gate is still effective even for an owned identity. */
    split.role=1; split_central_connected(&split,0); assert(events==0 && put_calls==0); split.role=0;
    next_slot=2;
    split_central_connected(&split,0);
#if CONFIG_ZMK_SPLIT_BLE_PREF_WEAK_BOND
    assert(put_calls==0 && last.slot==0);
#else
    assert(put_calls==1 && last.slot==2);
#endif
    assert(events==1 && last.connected);
    int registered=last.slot;
    assert(peripherals[registered].conn==&split);
    /* Central's slot can be released first: observer still owns its own entry. */
    split.owned=false;
    split_central_disconnected(&split,19);
    assert(events==2 && !last.connected && last.slot==registered);
    assert(peripherals[registered].conn==NULL && peripherals[registered].state==PERIPHERAL_SLOT_STATE_OPEN);
    split_central_disconnected(&split,19);assert(events==2);
    assert(release_psptr_peripheral_slot(-1)==-EINVAL);
    assert(release_psptr_peripheral_slot(4)==-EINVAL);
    return 0;
}
'''
    # Only includes are replaced by API stubs; all actual observer bodies compile.
    compilable = re.sub(r"^#include[^\n]*\n", "", patched, flags=re.MULTILINE)
    for weak in (0, 1):
        c, executable = temp / f"observer-{weak}.c", temp / f"observer-{weak}"
        c.write_text(prelude + compilable + checks)
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", f"-DCONFIG_ZMK_SPLIT_BLE_PREF_WEAK_BOND={weak}",
                        str(c), "-o", str(executable)], check=True)
        subprocess.run([str(executable)], check=True)
print("PASS: actual pinned Prospector observer; strict anchors; HID isolation; weak/nonweak split callbacks; ASAN/UBSAN")
