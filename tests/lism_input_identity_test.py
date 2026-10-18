"""Real generated protected-role query and synchronized profile writer."""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
app = Path(os.environ.get("LISM_ZMK_APP", "/private/tmp/lism-trackpad-build/zmk/app"))
source = app / "src/ble.c"
original = source.read_bytes()

def function(text, name):
    match = re.search(r"^(?:static )?[^\n;{}]+\b" + name + r"\([^;]*?\)\s*\{", text, re.M)
    assert match, name
    first = text.index("{", match.start())
    depth, end = 1, first + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[match.start():end] + "\n"

with tempfile.TemporaryDirectory(prefix="lism-identity-") as directory:
    out = Path(directory)
    generated = out / "ble.c"
    script = out / "patch.cmake"
    script.write_text(f'''include("{root}/cmake/lism_hid_central.cmake")
include("{root}/cmake/lism_split_sets.cmake")
include("{root}/cmake/lism_input_security.cmake")
include("{root}/cmake/lism_input_identity.cmake")
lism_hid_patch_ble("{source}" "{generated}")
lism_split_sets_patch_ble("{generated}" "{generated}" "{root}/src/lism_split_sets.h")
file(READ "{generated}" before_identity)
file(WRITE "{out}/before.c" "${{before_identity}}")
lism_input_patch_identity("{generated}" "{generated}")
''')
    subprocess.run(["cmake", "-P", str(script)], check=True)
    code = generated.read_text()
    assert source.read_bytes() == original
    assert "read_cb(cb_arg, &profiles[idx]" not in code
    assert "read_cb(cb_arg, &peripheral_addrs[i]" not in code
    assert "k_spin_lock(&lism_profile_identity_lock);\n        profiles[idx] = loaded_profile;" in code
    assert "k_spin_lock(&lism_split_address_lock);\n            bt_addr_le_copy(&peripheral_addrs[i], &loaded_peripheral);" in code
    query = function(code, "lism_input_protected_identity")
    assert "k_mutex" not in query and "settings_" not in query
    harness = out / "test.c"
    prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#define ZMK_BLE_PROFILE_COUNT 6
#define ZMK_SPLIT_BLE_PERIPHERAL_COUNT 6
#define IS_ENABLED(x) x
#define CONFIG_SETTINGS 1
#define BT_ADDR_LE_STR_LEN 32
#define LOG_DBG(...) ((void)0)
typedef struct {uint8_t bytes[7];} bt_addr_le_t;
static bt_addr_le_t any,peripheral_addrs[6];
#define BT_ADDR_LE_ANY (&any)
struct zmk_ble_profile {bt_addr_le_t peer;char name[16];};
static struct zmk_ble_profile profiles[6];
struct k_spinlock {bool held;};
static struct k_spinlock lism_profile_identity_lock,lism_split_address_lock;
typedef int k_spinlock_key_t;
static int k_spin_lock(struct k_spinlock *lock){assert(!lock->held);lock->held=true;return 0;}
static void k_spin_unlock(struct k_spinlock *lock,int key){(void)key;assert(lock->held);lock->held=false;}
static int bt_addr_le_cmp(const bt_addr_le_t*a,const bt_addr_le_t*b){return memcmp(a,b,sizeof(*a));}
static void bt_addr_le_to_str(const bt_addr_le_t*a,char*out,size_t n){(void)a;snprintf(out,n,"address");}
static void settings_save_one(const char*n,const void*v,size_t size){
 (void)n;(void)v;(void)size;assert(!lism_profile_identity_lock.held);
}
static int raise_profile_changed_event_work;
static void k_work_submit(int*w){(void)w;assert(!lism_profile_identity_lock.held);}
'''
    tests = r'''
int main(void){
 assert(lism_input_protected_identity(NULL)&&lism_input_protected_identity(BT_ADDR_LE_ANY));
 for(unsigned i=0;i<6;i++){
  bt_addr_le_t a={{i+1}},b={{i+11}};
  peripheral_addrs[i]=a;set_profile_address(i,&b);
  assert(lism_input_protected_identity(&a)&&lism_input_protected_identity(&b));
 }
 bt_addr_le_t unknown={{99}};assert(!lism_input_protected_identity(&unknown));
 set_profile_address(0,BT_ADDR_LE_ANY);
 bt_addr_le_t removed={{11}},preserved={{12}};
 assert(!lism_input_protected_identity(&removed)&&lism_input_protected_identity(&preserved));
 assert(!lism_profile_identity_lock.held&&!lism_split_address_lock.held);
 return 0;
}
'''
    harness.write_text(prelude + function(code, "lism_split_sets_find_addr") +
                       function(code, "set_profile_address") + query + tests)
    binary = out / "test"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-g",
                    "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    str(harness), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
    # Changed anchors are never silently treated as synchronized.
    bad = out / "bad.c"
    bad.write_text((out / "before.c").read_text().replace(
        "    memcpy(&profiles[index].peer, addr, sizeof(bt_addr_le_t));", "    drift;"))
    script.write_text(f'include("{root}/cmake/lism_input_security.cmake")\n'
                      f'include("{root}/cmake/lism_input_identity.cmake")\n'
                      f'lism_input_patch_identity("{bad}" "{out}/bad-out.c")\n')
    assert subprocess.run(["cmake", "-P", str(script)], capture_output=True).returncode != 0
print("PASS: synchronized protected split/host identity query, all role slots, settings loads and drift rejection")
