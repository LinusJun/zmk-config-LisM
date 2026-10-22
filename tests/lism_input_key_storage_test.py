"""Execute the actual fixed loader/store, including ARM32 persisted layout.

No device, no real key data: deterministic fixtures plus ASAN/UBSAN. Host
alignment is fixed to ARM32 for the pinned header; firmware asserts it again.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
zephyr = Path(os.environ.get("LISM_ZEPHYR_BASE", "/private/tmp/stress-zmk-upstream/zephyr"))
keys = zephyr / "subsys/bluetooth/host/keys.c"
original = keys.read_bytes()
layout = (zephyr / "subsys/bluetooth/host/keys.h").read_text()
layout = re.sub(r"^#include.*$", "", layout, flags=re.M)
layout = layout.replace("__aligned(sizeof(void *))", "__attribute__((aligned(4)))")
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include <sys/types.h>
#include <stdio.h>
#define BIT(n) (1U<<(n))
#define BUILD_ASSERT(c,...) _Static_assert(c, "pinned ABI")
#define IS_ENABLED(n) n
#define CONFIG_BT_SIGNING 0
#define CONFIG_BT_KEYS_OVERWRITE_OLDEST 0
#define CONFIG_BT_SMP_SC_PAIR_ONLY 0
#define CONFIG_BT_ID_MAX 1
#define CONFIG_BT_SETTINGS 1
#define BT_ID_DEFAULT 0
#define LOG_WRN(...) ((void)0)
typedef struct {uint8_t val[6];} bt_addr_t;
typedef struct {uint8_t type;bt_addr_t a;} bt_addr_le_t;
struct bt_bond_info;
static bt_addr_le_t zero;
#define BT_ADDR_LE_ANY (&zero)
static bool bt_addr_le_eq(const bt_addr_le_t*a,const bt_addr_le_t*b){return !memcmp(a,b,sizeof(*a));}
typedef ssize_t (*settings_read_cb)(void*,void*,size_t);
'''
# #if defined() in the real layout must see disabled options as undefined;
# IS_ENABLED() in the adapter sees the numeric zero definitions later.
layout_prelude = prelude.replace("#define CONFIG_BT_SIGNING 0\n", "").replace(
    "#define CONFIG_BT_KEYS_OVERWRITE_OLDEST 0\n", "").replace(
    "#define CONFIG_BT_SMP_SC_PAIR_ONLY 0\n", "")
config = "\n#define CONFIG_BT_SIGNING 0\n#define CONFIG_BT_KEYS_OVERWRITE_OLDEST 0\n#define CONFIG_BT_SMP_SC_PAIR_ONLY 0\n"
mocks = r'''
static struct bt_keys pool[3];static unsigned allocations,writes,deletes;
static uint8_t saved[100];static size_t saved_len;static int save_error;
static bool authorized=true;static size_t actual_len;
static ssize_t forced_read=-999;
bool lism_input_legacy_key_allowed(uint8_t id,const bt_addr_le_t *a){return authorized&&!id&&a->a.val[0]==7;}
int bt_settings_store_keys(uint8_t id,const bt_addr_le_t*a,const void*p,size_t n){
 (void)id;(void)a;writes++;if(save_error)return save_error;
 memcpy(saved,p,n);saved_len=n;return 0;
}
static int bt_settings_decode_key(const char*n,bt_addr_le_t*a){
 if(!n||n[0]!='x')return -EINVAL;memset(a,0,sizeof(*a));a->a.val[0]=7;return 0;
}
static int settings_name_next(const char*n,const char**next){
 const char*s=strchr(n,'/');*next=s?s+1:NULL;return 1;
}
static void bt_id_del(struct bt_keys*k){k->state=0;}
struct bt_keys *bt_keys_find_addr(uint8_t id,const bt_addr_le_t*a){
 for(unsigned i=0;i<3;i++)if(pool[i].id==id&&bt_addr_le_eq(&pool[i].addr,a))return &pool[i];return NULL;
}
struct bt_keys *bt_keys_get_addr(uint8_t id,const bt_addr_le_t*a){
 struct bt_keys*k=bt_keys_find_addr(id,a);if(k)return k;
 for(unsigned i=0;i<3;i++)if(bt_addr_le_eq(&pool[i].addr,BT_ADDR_LE_ANY)){
 allocations++;pool[i].id=id;pool[i].addr=*a;return &pool[i];}return NULL;
}
static ssize_t read_value(void*data,void*out,size_t n){
 if(forced_read!=-999)return forced_read;
 size_t take=n<actual_len?n:actual_len;if(take)memcpy(out,data,take);return take;
}
'''
consumer=(zephyr/'subsys/bluetooth/host/conn.c').read_text()
consumer=consumer[consumer.index('bool bt_conn_ltk_present('):consumer.index('void bt_conn_identity_resolved(')]
consumer_prelude=r'''
#define BT_HCI_ROLE_CENTRAL 0
struct bt_conn {unsigned role,id;struct {const struct bt_keys *keys;bt_addr_le_t dst;}le;};
'''
checks = r'''
int main(void){
 struct bt_keys sc={0};sc.addr.a.val[0]=7;sc.enc_size=16;sc.flags=BT_KEYS_SC;
 sc.keys=BT_KEYS_LTK_P256|BT_KEYS_IRK;memset(sc.ltk.val,0x5a,16);
 uint8_t old[52];memcpy(old,sc.storage_start,52);actual_len=52;
 assert(!lism_keys_set("x",52,read_value,old));assert(allocations==1&&!writes&&!deletes);
 assert(pool[0].keys==sc.keys&&!memcmp(pool[0].storage_start,old,52));
 assert(!lism_keys_store(&pool[0])&&saved_len==52&&!memcmp(saved,old,52));
 /* Old strict loader reads exactly these bytes without truncation or deletion. */
 struct bt_keys strict={0};memcpy(strict.storage_start,saved,52);
 assert(strict.flags==BT_KEYS_SC&&strict.keys==sc.keys);
 /* Bad-version80 SC accepted read-only, but extra28 must all be zero. */
 actual_len=80;unsigned before=writes;
 assert(!lism_keys_set("x",80,read_value,sc.storage_start)&&writes==before);
 sc.periph_ltk.val[0]=1;assert(lism_keys_set("x",80,read_value,sc.storage_start)==-EINVAL);
 sc.periph_ltk.val[0]=0;
 for(unsigned n=1;n<100;n++)if(n!=52&&n!=80){
  actual_len=n;unsigned a=allocations;assert(lism_keys_set("x",n,read_value,saved)==-EINVAL);
  assert(allocations==a&&writes==before&&!deletes);
 }
 /* NVS clamping must not make an 81-byte record look like a valid80/52. */
 actual_len=81;assert(lism_keys_set("x",81,read_value,saved)==-EINVAL);
 actual_len=52;forced_read=51;assert(lism_keys_set("x",52,read_value,old)==-EINVAL);
 forced_read=0;assert(lism_keys_set("x",52,read_value,old)==-EINVAL);
 forced_read=-EIO;assert(lism_keys_set("x",52,read_value,old)==-EINVAL);forced_read=-999;
 assert(writes==before&&!deletes);
 struct bt_keys legacy={0};legacy.addr.a.val[0]=7;legacy.enc_size=16;
 legacy.keys=BT_KEYS_LTK|BT_KEYS_PERIPH_LTK|BT_KEYS_IRK;
 memset(legacy.ltk.val,0x33,16);memset(legacy.periph_ltk.val,0x66,16);
 actual_len=80;assert(!lism_keys_set("x",80,read_value,legacy.storage_start));
 assert(pool[0].keys==(BT_KEYS_LTK|BT_KEYS_IRK)&&pool[0].periph_ltk.val[0]==0);
 assert(!lism_keys_store(&legacy)&&saved_len==52);
 assert(legacy.keys&BT_KEYS_PERIPH_LTK); /* runtime never modified */
 struct bt_keys wire={0};memcpy(wire.storage_start,saved,52);
 assert(wire.keys==(BT_KEYS_LTK|BT_KEYS_IRK)&&!memcmp(wire.ltk.val,legacy.ltk.val,16));
 assert(memcmp(wire.ltk.val,legacy.periph_ltk.val,16));
 struct bt_conn central={.role=BT_HCI_ROLE_CENTRAL,.le.keys=&wire};
 /* Actual pinned GATT/L2CAP helper: without the unused local peripheral LTK,
  * an UNENCRYPTED request is classified as authentication, not encryption.
  * Host session tests separately require L2 before HID service discovery. */
 assert(!bt_conn_ltk_present(&central));
 wire.keys|=BT_KEYS_PERIPH_LTK;assert(bt_conn_ltk_present(&central));
 wire.keys&=~BT_KEYS_PERIPH_LTK;
 central.role=1;assert(bt_conn_ltk_present(&central));
 actual_len=52;assert(!lism_keys_set("x",52,read_value,saved));
 /* Store only the real central LTK, exact live authorized identity. */
 authorized=false;assert(lism_keys_store(&legacy)==-EACCES);authorized=true;
 legacy.addr.a.val[0]=8;assert(lism_keys_store(&legacy)==-EACCES);legacy.addr.a.val[0]=7;
 legacy.keys=BT_KEYS_PERIPH_LTK;assert(lism_keys_store(&legacy)==-EINVAL);
 legacy.keys=BT_KEYS_LTK;legacy.enc_size=7;assert(lism_keys_store(&legacy)==-EINVAL);
 legacy.enc_size=16;save_error=-EIO;assert(lism_keys_store(&legacy)==-EIO);save_error=0;
 sc.keys=BT_KEYS_LTK;actual_len=52;assert(lism_keys_set("x",52,read_value,sc.storage_start)==-EINVAL);
 sc.keys=BT_KEYS_LTK_P256;sc.flags=0;assert(lism_keys_set("x",52,read_value,sc.storage_start)==-EINVAL);
 assert(lism_keys_set("x/1",52,read_value,old)==-EINVAL);
 assert(lism_keys_set("x/no",52,read_value,old)==-EINVAL);
 assert(lism_keys_set("x/",52,read_value,old)==-EINVAL);
 actual_len=0;assert(!lism_keys_set("x",0,read_value,NULL));assert(!pool[0].keys&&!deletes);
 puts("PASS: canonical52 SC/central legacy; exact52/80 reads; no load writes/deletes; ABI and rollback bytes");
}
'''
with tempfile.TemporaryDirectory(prefix="lism-key-abi-") as tmp:
    out = Path(tmp)
    script = out / "patch.cmake"
    script.write_text(f'set(ZEPHYR_BASE "{zephyr}")\n'
                      f'include("{root}/cmake/lism_input_security.cmake")\n'
                      f'include("{root}/cmake/lism_input_keys.cmake")\n'
                      f'lism_input_patch_keys("{keys}" "{out}/keys.c" "{root}/src/lism_input_key_storage.h")\n')
    subprocess.run(["cmake", "-P", str(script)], check=True)
    patched = (out / "keys.c").read_text()
    assert "bt_keys_clear(keys);" not in patched.split("#if defined(CONFIG_BT_SETTINGS)\nint bt_keys_store", 1)[1].split("static void id_add", 1)[0]
    assert "BT_SETTINGS_DEFINE(keys, \"keys\", lism_keys_set, keys_commit)" in patched
    assert keys.read_bytes() == original
    harness = out / "keys-test.c"
    harness.write_text(layout_prelude + layout + config + mocks +consumer_prelude+consumer+
                       f'\n#include "{root}/src/lism_input_key_storage.h"\n' + checks)
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-misleading-indentation",
                    "-fsanitize=address,undefined", str(harness), "-o", str(out / "test")], check=True)
    subprocess.run([str(out / "test")], check=True)
