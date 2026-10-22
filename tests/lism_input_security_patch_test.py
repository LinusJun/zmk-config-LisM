"""SMP metadata observers must not change policy, payload, or upstream source."""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
zephyr = Path(os.environ.get("LISM_ZEPHYR_BASE", "/private/tmp/stress-zmk-upstream/zephyr"))
source = zephyr / "subsys/bluetooth/host/smp.c"
original = source.read_text()
header = root / "src/lism_input_security.h"
module = root / "cmake/lism_input_security.cmake"

with tempfile.TemporaryDirectory(prefix="lism-smp-metadata-") as tmp:
    out = Path(tmp)
    script = out / "patch.cmake"
    generated = out / "smp.c"

    def run(path, legacy=False):
        script.write_text(f'set(CONFIG_LISM_INPUT_LEGACY_COMPAT {"ON" if legacy else "OFF"})\n'
                          f'include("{module}")\n'
                          f'lism_input_patch_smp("{path}" "{generated}" "{header}")\n')
        return subprocess.run(["cmake", "-P", str(script)], capture_output=True, text=True)

    result = run(source)
    assert result.returncode == 0, result.stderr
    patched = generated.read_text()
    # Strong invariant: remove only standalone observer calls and our include,
    # obtaining the exact upstream bytes, including every policy branch/return.
    stripped = patched.replace(f'#include "{header}"\n', "", 1)
    stripped = re.sub(r'^\t+lism_input_smp_(?:received|blocked|failed)\([^\n]*\);\n',
                      "", stripped, flags=re.M)
    assert stripped == original
    assert source.read_text() == original
    assert patched.count("lism_input_smp_blocked(") == 9
    assert patched.count("lism_input_smp_received(") == 1
    assert patched.count("lism_input_smp_failed(") == 1
    recv = patched.split("static int bt_smp_recv(", 1)[1].split("static void pub_key_ready_notify", 1)[0]
    observation = recv.index("lism_input_smp_received(")
    for guard in ("buf->len < sizeof(*hdr)", "SMP_FLAG_TIMEOUT",
                  "hdr->code >= ARRAY_SIZE(handlers)", "!handlers[hdr->code].func",
                  "!atomic_test_and_clear_bit(smp->allowed_cmds, hdr->code)",
                  "buf->len != handlers[hdr->code].expect_len"):
        assert recv.index(guard) < observation
    assert observation < recv.index("err = handlers[hdr->code].func")
    assert recv.index("if (err)") < recv.index("lism_input_smp_failed(") < recv.index("smp_error(smp, err)")
    # Hooks are void observers, never used as policy predicates or payload copies.
    additions = [line for line in patched.splitlines() if "lism_input_smp_" in line]
    assert all(line.strip().startswith("lism_input_smp_") for line in additions)
    assert not any(word in "\n".join(additions) for word in ("memcpy", "LOG_", "print", "key->", "random"))
    # Drift/duplicate of each unique semantic anchor fails the configure step.
    anchors = [
        "req->max_key_size < BT_SMP_MIN_ENC_KEY_SIZE",
        "rsp->max_key_size < BT_SMP_MIN_ENC_KEY_SIZE",
        "\tif (!update_keys_check(smp, conn->le.keys)) {\n\t\treturn BT_SMP_ERR_AUTH_REQUIREMENTS;",
        "\terr = remote_sec_level_reachable(smp);\n\tif (err) {\n\t\treturn err;",
        "#if defined(CONFIG_BT_SMP_SC_PAIR_ONLY)\n\t\treturn BT_SMP_ERR_AUTH_REQUIREMENTS;",
        "\tif (IS_ENABLED(CONFIG_BT_SMP_SC_PAIR_ONLY) &&\n\t    !(auth & BT_SMP_AUTH_SC)) {",
        "\terr = handlers[hdr->code].func(smp, buf);",
    ]
    bad = out / "drift.c"
    handler_offset = original.index("static uint8_t smp_pairing_req(struct bt_smp *smp, struct net_buf *buf)\n{\n\tstruct bt_conn *conn = smp->chan.chan.conn;")
    for anchor in anchors:
        prefix, handlers = original[:handler_offset], original[handler_offset:]
        assert anchor in handlers
        bad.write_text(prefix + handlers.replace(anchor, "DRIFT" + anchor[1:], 1))
        assert run(bad).returncode != 0, anchor
    # A repeated full receive dispatch anchor is rejected instead of double-hooked.
    dispatch = "\terr = handlers[hdr->code].func(smp, buf);\n\tif (err) {\n\t\tsmp_error(smp, err);"
    bad.write_text(original + "\n" + dispatch)
    assert run(bad).returncode != 0

    result = run(source, True)
    assert result.returncode == 0, result.stderr
    compatible = generated.read_text()
    assert source.read_text() == original
    # SC authcap, security-level, key size, bond overwrite policy remain intact.
    for invariant in ("auth &= BT_SMP_AUTH_MASK_SC;", "CONFIG_BT_SMP_ENFORCE_MITM",
                      "req->max_key_size < BT_SMP_MIN_ENC_KEY_SIZE",
                      "rsp->max_key_size < BT_SMP_MIN_ENC_KEY_SIZE",
                      "!update_keys_check(smp, conn->le.keys)",
                      "conn->role == BT_CONN_ROLE_CENTRAL"):
        assert invariant in compatible
    assert compatible.count("!lism_input_legacy_permitted(conn)") == 9
    assert "if (lism_input_legacy_permitted(conn) &&\n\t    conn->le.keys" in compatible
    # All validated non-handshake SMP payload opcodes pass the runtime gate.
    recv = compatible.split("static int bt_smp_recv(", 1)[1].split("static void pub_key_ready_notify", 1)[0]
    assert recv.index("buf->len != handlers[hdr->code].expect_len") < recv.index("!lism_input_legacy_permitted")
    assert recv.index("!lism_input_legacy_permitted") < recv.index("err = handlers[hdr->code].func")
    for name in ("bt_smp_auth_passkey_entry", "bt_smp_auth_pairing_confirm", "bt_smp_le_oob_set_tk"):
        section = compatible.split("int " + name + "(", 1)[1].split("\n}\n", 1)[0]
        assert section.index("!lism_input_legacy_permitted") < section.index("atomic_test_and_clear_bit")
    identity = compatible.split("static uint8_t smp_ident_addr_info(", 1)[1].split("\n}\n", 1)[0]
    preflight = identity.index("!lism_input_identity_allowed")
    assert identity.index("!bt_addr_le_is_identity") < preflight
    for mutation in ("bt_keys_find_addr", "bt_keys_clear", "bt_keys_get_type",
                     "bt_conn_foreach", "bt_addr_le_copy", "bt_conn_identity_resolved"):
        assert preflight < identity.index(mutation), mutation
    bad.write_text(original + "\n/* drift */\n")
    assert run(bad, True).returncode != 0

    # Compile the actual patched key-reuse/TK functions, not a parallel model.
    def function(name):
        match = re.search(r"(?:static )?(?:bool|int|uint8_t) " + name + r"\([^\n]*\)\n\{", compatible)
        assert match, name
        start = match.start()
        depth = 0
        for position in range(compatible.index("{", start), len(compatible)):
            depth += (compatible[position] == "{") - (compatible[position] == "}")
            if depth == 0:
                return compatible[start:position + 1]
        raise AssertionError(name)

    harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <assert.h>
#define BT_CONN_ROLE_CENTRAL 0
#define BT_CONN_FORCE_PAIR 0
#define BT_KEYS_LTK 1
#define BT_KEYS_LTK_P256 2
#define BT_KEYS_PERIPH_LTK 4
#define BT_KEYS_AUTHENTICATED 1
#define BT_SECURITY_L3 3
#define BT_SECURITY_L4 4
#define BT_SMP_MAX_ENC_KEY_SIZE 16
#define SMP_FLAG_SC 0
#define SMP_FLAG_PAIRING 1
#define SMP_FLAG_ENC_PENDING 2
#define SMP_FLAG_SEC_REQ 3
#define BT_HCI_ERR_PIN_OR_KEY_MISSING 6
#define BT_SECURITY_ERR_PIN_OR_KEY_MISSING 2
#define BT_SMP_DIST_ID_KEY 1
#define BT_SMP_ERR_INVALID_PARAMS 10
#define BT_SMP_ERR_AUTH_REQUIREMENTS 3
#define LISM_INPUT_SMP_STORED_BOND 1
#define LOG_DBG(...) do {} while(0)
#define LOG_ERR(...) do {} while(0)
typedef int bt_addr_le_t;
struct material { uint8_t val[16], rand[8], ediv[2]; };
struct bt_keys { unsigned keys, flags, enc_size; struct material ltk, periph_ltk; };
struct bt_conn { unsigned role, flags[1], id, required_sec_level; struct { struct bt_keys *keys; int dst; } le; };
struct bt_smp { unsigned flags[1]; uint8_t tk[16]; unsigned remote_dist; struct { struct { struct bt_conn *conn; } chan; } chan; };
struct bt_smp_ident_addr_info { bt_addr_le_t addr; };
struct net_buf { void *data; };
static struct bt_smp sample;
static bool allow;
static bool identity_allowed;
static unsigned identity_queries, blocked;
static bool bt_addr_le_is_identity(const bt_addr_le_t *a) { return *a != 0; }
static bool lism_input_identity_allowed(struct bt_conn *c,const bt_addr_le_t *a) { (void)c;(void)a;identity_queries++;return identity_allowed; }
static void lism_input_smp_blocked(struct bt_conn *c,unsigned why) { (void)c;(void)why;blocked++; }
static bool atomic_test_bit(unsigned *bits,unsigned n) { return (*bits & (1u<<n)) != 0; }
static void atomic_set_bit(unsigned *bits,unsigned n) { *bits |= 1u<<n; }
static bool lism_input_legacy_allowed(struct bt_conn *c) { (void)c; return allow; }
static struct bt_keys *bt_keys_find(unsigned t,unsigned id,int *a) { (void)t;(void)id;(void)a;return 0; }
static struct bt_smp *smp_chan_get(struct bt_conn *c) { (void)c;return &sample; }
static unsigned get_encryption_key_size(struct bt_smp *s) { (void)s;return 16; }
static void bt_conn_security_changed(struct bt_conn *c,int h,int e) { (void)c;(void)h;(void)e; }
static void smp_reset(struct bt_smp *s) { memset(s,0,sizeof(*s)); }
'''
    harness += "\n".join(function(name) for name in
                          ("lism_input_legacy_permitted", "smp_keys_check", "bt_smp_request_ltk"))
    # Actual identity-handler prefix stops precisely before the first lookup or
    # mutation. Returning 99 marks arrival at that dangerous continuation.
    prefix = function("smp_ident_addr_info").split("\tif (!bt_addr_le_eq", 1)[0]
    harness += "\n" + prefix + "\t(void)err; return 99;\n}\n"
    harness += r'''
int main(void) {
 struct bt_keys keys={.keys=BT_KEYS_LTK,.enc_size=16};
 struct bt_conn c={.role=0,.required_sec_level=2,.le={.keys=&keys}};
 uint8_t output[16]; memset(output,0xa5,16);
 assert(!smp_keys_check(&c));
 allow=true; assert(smp_keys_check(&c));
 c.role=1; assert(!smp_keys_check(&c)); c.role=0;
 keys.keys=BT_KEYS_LTK_P256; allow=false; assert(smp_keys_check(&c));
 sample.flags[0]=(1u<<SMP_FLAG_PAIRING)|(1u<<SMP_FLAG_ENC_PENDING);
 memset(sample.tk,0x42,16);
 assert(!bt_smp_request_ltk(&c,0,0,output)); assert(output[0]==0xa5);
 allow=true; assert(bt_smp_request_ltk(&c,0,0,output)); assert(output[0]==0x42);
 allow=false; sample.flags[0]|=1u<<SMP_FLAG_SC;
 assert(bt_smp_request_ltk(&c,0,0,output));
 sample.flags[0]=0; keys.keys=BT_KEYS_PERIPH_LTK; memset(output,0xa5,16);
 assert(!bt_smp_request_ltk(&c,0,0,output)); assert(output[0]==0xa5);
 allow=true; assert(bt_smp_request_ltk(&c,0,0,output));
 c.role=1; assert(!bt_smp_request_ltk(&c,0,0,output));
 c.role=0; allow=false; keys.keys=BT_KEYS_LTK_P256;
 assert(bt_smp_request_ltk(&c,0,0,output));
 struct bt_smp_ident_addr_info identity={.addr=0};
 struct net_buf packet={.data=&identity}; sample.chan.chan.conn=&c;
 assert(smp_ident_addr_info(&sample,&packet)==BT_SMP_ERR_INVALID_PARAMS);
 assert(identity_queries==0 && blocked==0);
 identity.addr=42;
 assert(smp_ident_addr_info(&sample,&packet)==BT_SMP_ERR_AUTH_REQUIREMENTS);
 assert(identity_queries==1 && blocked==1);
 identity_allowed=true;
 assert(smp_ident_addr_info(&sample,&packet)==99);
 assert(identity_queries==2 && blocked==1);
 return 0;
}
'''
    cfile, binary = out / "policy.c", out / "policy"
    cfile.write_text(harness)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    str(cfile), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

print("PASS: validated SMP metadata hooks, exact policy preservation, no payload copies, drift/duplicate rejection")
