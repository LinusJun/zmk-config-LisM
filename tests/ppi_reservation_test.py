"""Audit actual nrfx build policy and execute real allocator + ESB PPI setup.

MMIO and atomic CAS are modeled. Channel selection and SDK init/stop bodies
are production code, not a fixed list of supposedly safe test channels.
"""
from pathlib import Path
import argparse, hashlib, json, re, shlex, subprocess, tempfile
from elftools.elf.elffile import ELFFile

P = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser()
p.add_argument('--workspace', type=Path, required=True)
W = p.parse_args().workspace
B = W/'build-dongle'

def function(t, name):
    m = re.search(r'(?m)^(?:static )?[^\n;]+\b'+name+r'\([^;]*?\)\s*\{', t)
    assert m, name
    i = t.index('{', m.start())+1
    depth = 1
    while depth:
        depth += (t[i] == '{')-(t[i] == '}')
        i += 1
    return t[m.start():i]

with (B/'zephyr/zmk.elf').open('rb') as f:
    elf = ELFFile(f)
    syms = elf.get_section_by_name('.symtab')
    def word(name):
        sym, = syms.get_symbol_by_name(name)
        address = sym['st_value']
        for seg in elf.iter_segments():
            if seg['p_type'] == 'PT_LOAD' and seg['p_vaddr'] <= address and address+4 <= seg['p_vaddr']+seg['p_filesz']:
                offset = address-seg['p_vaddr']
                return int.from_bytes(seg.data()[offset:offset+4], 'little')
        raise AssertionError(name)
    available, groups = word('m_channels_allocated'), word('m_groups_allocated')
assert available == 0x1ffff and groups == 0x3f, (hex(available), hex(groups))

row, = [r for r in json.loads((B/'compile_commands.json').read_text()) if r['file'].endswith('/nrfx_ppi.c')]
args = shlex.split(row['command'])
cmd = []
i = 0
while i < len(args):
    if args[i] == '-o':
        i += 2
        continue
    if args[i] == '-c':
        i += 1
        continue
    cmd.append(args[i])
    i += 1
macros = subprocess.check_output(cmd+['-E', '-dM'], cwd=row['directory'], text=True)
assert '#define CONFIG_NRFX_RESERVED_RESOURCES_HEADER "lism_nrfx_reserved_resources.h"' in macros
assert '#define NRFX_PPI_CHANNELS_USED_BY_BT_CTLR SDC_PPI_CHANNELS_USED_MASK' in macros
assert '#define NRFX_PPI_CHANNELS_USED_BY_MPSL MPSL_RESERVED_PPI_CHANNELS' in macros
deps = subprocess.check_output(cmd+['-M'], cwd=row['directory'], text=True)
official = '/sdk/nrfxlib/softdevice_controller/include/sdc_soc.h'
mpsl_official = '/sdk/nrfxlib/mpsl/include/mpsl_hwres.h'
# Compiler dependency paths preserve workspace symlinks; normalize before check.
dep_paths = [Path(n).resolve() for n in deps.replace('\\\n',' ').split() if n.endswith('.h')]
assert Path(official).resolve() in dep_paths
assert Path(mpsl_official).resolve() in dep_paths
controller_table=Path('/sdk/nrf/subsys/bluetooth/controller/bt_ctlr_used_resources.h')
assert re.search(r'#define BT_CTLR_USED_PPI_GROUPS\s+0\b',controller_table.read_text())

assert (W/'include-dongle/lism_nrfx_reserved_resources.h').resolve() in dep_paths
assert Path('/sdk/zephyr/modules/hal_nordic/nrfx/nrfx_reserved_resources.h').resolve() in dep_paths
(W/'ppi-reservation-macros.txt').write_text(macros)
(W/'ppi-reservation-dependencies.txt').write_text(deps)

allocator_path = Path('/deps/modules/hal/nordic/nrfx/helpers/nrfx_flag32_allocator.c')
nrfx_path = Path(row['file'])
sdk_path = next(B.rglob('sdk_esb_mpsl.c.ppi.c'))
allocator, nrfx, sdk = [q.read_text() for q in [allocator_path, nrfx_path, sdk_path]]
pre = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <errno.h>
typedef uint32_t nrfx_atomic_t;
typedef int nrfx_err_t;
typedef uint32_t nrf_ppi_channel_t;
typedef uint32_t nrf_ppi_channel_group_t;
#define NRFX_SUCCESS 0
#define NRFX_ERROR_NO_MEM 1
#define NRFX_BIT(n) (1U << (n))
#define BIT(n) NRFX_BIT(n)
#define NRF_CLZ(n) __builtin_clz(n)
static bool cas(nrfx_atomic_t *p, uint32_t old, uint32_t value) {
    if (*p != old) return false; *p=value; return true;
}
#define NRFX_ATOMIC_CAS cas
#define LOG_ERR(...) ((void)0)
#define IS_ENABLED(n) (n)
#define CONFIG_ESB_NEVER_DISABLE_TX 0
#define NRF_PPI 0
static nrfx_atomic_t m_channels_allocated, m_groups_allocated;
static nrf_ppi_channel_t egu_ramp_up, disabled_egu, egu_timer_start;
static nrf_ppi_channel_t radio_address_timer_stop, timer_compare0_radio_disable;
static nrf_ppi_channel_t timer_compare1_radio_txen, radio_end_timer_start;
static nrf_ppi_channel_group_t ramp_up_ppi_group;
static uint32_t stopped_mask;
static void nrf_ppi_channels_disable(int ppi, uint32_t mask) { stopped_mask=mask; }
'''
main = r'''
int main(void) {
    // Old compiled mask: actual allocator selects 19,18,17,16,15,14.
    m_channels_allocated=0xfffff; m_groups_allocated=0x3f;
    assert(!esb_ppi_init());
    uint8_t channels[6]; lism_esb_ppi_channels_get(channels);
    for(unsigned i=0;i<6;i++) assert(channels[i]==19-i);
    esb_ppi_disable_all();
    assert((stopped_mask & 0xfffe0000U)==0xe0000U);
    // Use the new FINAL ELF initializer, not an invented test channel list.
    m_channels_allocated=AVAILABLE; m_groups_allocated=GROUPS;
    egu_ramp_up=disabled_egu=egu_timer_start=radio_address_timer_stop=0;
    timer_compare0_radio_disable=timer_compare1_radio_txen=radio_end_timer_start=0;
    assert(!esb_ppi_init()); lism_esb_ppi_channels_get(channels);
    for(unsigned i=0;i<6;i++) assert(channels[i]==16-i);
    assert(ramp_up_ppi_group==5);
    for(unsigned cycle=0;cycle<1000;cycle++) {
        esb_ppi_disable_all();
        assert(stopped_mask==0x1f800U);
        assert(!(stopped_mask & 0xfffe0000U));
        assert(!(stopped_mask & 1)); // Unallocated radio_end_timer_start.
    }
    for(int expected=10;expected>=0;expected--) {
        nrf_ppi_channel_t ch=0;assert(!nrfx_ppi_channel_alloc(&ch));
        assert(ch==(unsigned)expected);
    }
    nrf_ppi_channel_t ch=0;assert(nrfx_ppi_channel_alloc(&ch)==NRFX_ERROR_NO_MEM);
    puts("PASS real nrfx highest-bit allocator + SDK PPI init/disable: old19..14 conflicts; final ELF mask allocates16..11, reserved17..31 untouched");
}
'''.replace('AVAILABLE', str(available)).replace('GROUPS', str(groups))
code = pre+'\n'+function(allocator, 'nrfx_flag32_alloc')+'\n'
code += '\n'.join(function(nrfx, n) for n in ['nrfx_ppi_channel_alloc', 'nrfx_ppi_group_alloc'])+'\n'
code += '\n'.join(function(sdk, n) for n in ['esb_ppi_init', 'esb_ppi_disable_all', 'lism_esb_ppi_channels_get'])+'\n'+main
with tempfile.TemporaryDirectory() as d:
    c, exe = Path(d)/'test.c', Path(d)/'test'
    c.write_text(code)
    subprocess.run(['cc', '-std=c11', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-Werror=implicit-function-declaration', str(c), '-o', str(exe)], check=True)
    result = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
report = {'passed': True, 'ELF_nrfx_available_mask': hex(available), 'ELF_nrfx_group_mask': hex(groups),
          'SDC_reserved_mask': '0xfffe0000', 'expected_first_ESB_channels': list(range(16,10,-1)),
          'old_mask_negative_control': list(range(19,13,-1)), 'SDC_resource_header': official,
          'actual_nrfx_compile_command_audited': True, 'stdout': result.stdout,
          'source_hashes': {str(q): hashlib.sha256(q.read_bytes()).hexdigest() for q in [allocator_path,nrfx_path,sdk_path,Path(official),Path(mpsl_official),controller_table]},
          'scope': 'Actual allocator and ESB init/disable code; modeled atomic CAS/MMIO, no real RF or SDC timing proof'}
(W/'ppi-reservation-test.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report, indent=2))
