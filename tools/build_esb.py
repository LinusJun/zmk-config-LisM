"""Build the opt-in, full-feature always-ESB split experiment; never flash/reset.

Use an initialized west workspace containing the integration/west.yml pins.
Default Zephyr SDK builds do not require the local GNU Arm compatibility flag.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import struct

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    'zmk': '9ebbeff0a8b69a42f14aec022cdf16c7a107b9e0',
    'zephyr': '10ba6d0cb38bc3d258775d27982f707599320085',
    'nrf': '9b3d2623fdcd9c0fd0284f860beea924568c9826',
    'nrfxlib': 'dfadf17305d8f000eda9aa74a5b9ff1c5647a23e',
}


def check_radio_vectors(path):
    from elftools.elf.elffile import ELFFile
    with path.open('rb') as stream:
        elf = ELFFile(stream)
        symbols = elf.get_section_by_name('.symtab')
        table = symbols.get_symbol_by_name('_irq_vector_table')[0]
        section = elf.get_section(table['st_shndx'])
        offset = table['st_value'] - section['sh_addr']
        raw = section.data()
        for irq, handler in [(1,'mpsl_radio_isr_wrapper'), (8,'mpsl_timer0_isr_wrapper'),
                             (11,'mpsl_rtc0_isr_wrapper')]:
            address = struct.unpack_from('<I', raw, offset + 4*irq)[0]
            expected = symbols.get_symbol_by_name(handler)[0]['st_value'] | 1
            if address != expected:
                raise ValueError(f'IRQ {irq} is not owned by MPSL: {address:x} != {expected:x}')
        return {'flash_bytes':symbols.get_symbol_by_name('_flash_used')[0]['st_value'],
                'ram_bytes':symbols.get_symbol_by_name('_image_ram_size')[0]['st_value']}


def build(target, ws, modules=None, local_minimal_libc=False):
    from ci_firmware import TARGETS, check_build, config_values, require
    from prepare_input_security import generate
    from package_input_receiver import validate_uf2
    from keymap_guard import validate
    validate((ROOT/'config/lism.keymap').read_text())
    app = (ws / 'zmk/app').resolve()
    for name, revision in PINS.items():
        found = subprocess.check_output(['git', '-C', ws/name, 'rev-parse', 'HEAD'], text=True).strip()
        if found != revision:
            raise ValueError(f'{name}: expected {revision}, got {found}')
    shield, _, flags = TARGETS[target]
    dest = ws / ('esb-build-' + target)
    flags = list(flags)
    if target == 'dongle':
        root = ws / 'Kconfig.esb'
        generate(app / 'Kconfig', root, legacy_build_policy=True)
        flags += ['-DKCONFIG_ROOT=' + str(root), '-DCONFIG_LV_USE_CUSTOM_SPRINTF=y',
                  '-DSNIPPET=zmk-usb-logging']
    command = ['cmake', '-S', str(app), '-B', str(dest), '-DBOARD=xiao_ble//zmk',
        '-DZephyr_DIR=' + str(ws/'zephyr/share/zephyr-package/cmake'),
        '-DZMK_CONFIG=' + str(ROOT/'config'), '-DZMK_EXTRA_MODULES=' + str(ROOT),
        '-DSHIELD=' + shield, '-DEXTRA_CONF_FILE=' + str(ROOT/'experimental/trimode/integration/esb.conf'),
        '-DDTC_OVERLAY_FILE=' + str(ROOT/'experimental/trimode/integration/sdc.overlay'), *flags]
    if not (dest/'CMakeCache.txt').exists():
        command += ['-G', 'Ninja']
    if modules:
        command += ['-DZEPHYR_MODULES=' + modules]
    if local_minimal_libc:
        # The execution host's Debian newlib lacks retargetable locking.
        # This flag is a declared local verification environment, not firmware policy.
        command += ['-DCONFIG_MINIMAL_LIBC=y', '-DEXTRA_CFLAGS=-DESTALE=116']
    env = dict(os.environ, ZEPHYR_BASE=str(ws/'zephyr'))
    dest.mkdir(parents=True, exist_ok=True)
    for suffix, cmd in [('configure', command), ('build', ['cmake', '--build', str(dest), '-j4'])]:
        log = ws / f'esb-{target}-{suffix}.log'
        print(f'{target}: {suffix}, log {log}', flush=True)
        with log.open('w') as output:
            result = subprocess.run(cmd, env=env, stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            print(log.read_text()[-10000:], file=sys.stderr)
            raise subprocess.CalledProcessError(result.returncode, cmd)
    values = check_build(target, dest)
    require(values, {'CONFIG_LISM_SPLIT_ESB':'y', 'CONFIG_BT_LL_SOFTDEVICE':'y',
        'CONFIG_ESB_DYNAMIC_INTERRUPTS':'y', 'CONFIG_ESB_MAX_PAYLOAD_LENGTH':'68',
        'CONFIG_MPSL_TIMESLOT_SESSION_COUNT':'1', 'CONFIG_ESB_RADIO_IRQ_PRIORITY':'0',
        'CONFIG_BT_SMP':'y', 'CONFIG_TINYCRYPT_AES_CCM':'y'})
    sdk = (dest/'modules/zmk-config-LisM/lism_nordic_esb.c').read_text()
    if 'irq_disable(ESB_RADIO_IRQ_NUMBER);' in sdk or 'ARM_IRQ_DIRECT_DYNAMIC_CONNECT(ESB_RADIO_IRQ_NUMBER' in sdk:
        raise ValueError('ESB may not replace/disable the MPSL RADIO interrupt')
    elf = (dest/'zephyr/zmk.elf').read_bytes()
    memory = check_radio_vectors(dest/'zephyr/zmk.elf')
    for symbol in [b'lism_esb_transport_plan', b'lism_esb_seal', b'lism_esb_radio_thread',
                   b'lism_esb_central' if target == 'dongle' else b'lism_esb_peripheral']:
        if symbol not in elf:
            raise ValueError(f'Missing compiled transport: {symbol!r}')
    image = (dest/'zephyr/zmk.uf2').read_bytes()
    validate_uf2(image)
    out = ROOT / 'experimental/trimode/artifacts'
    out.mkdir(exist_ok=True)
    name = f'lism_esb_{target}_EXPERIMENTAL.uf2'
    (out/name).write_bytes(image)
    record = {'target': target, 'file':name, 'sha256':hashlib.sha256(image).hexdigest(),
        'source_base':'140d66bc5db8d2508af70f40aa3ecbe2656f0d25',
        'source_head':subprocess.check_output(['git','-C',ROOT,'rev-parse','HEAD'],text=True).strip(),
        'source_dirty':bool(subprocess.check_output(['git','-C',ROOT,'status','--porcelain','--untracked-files=no'],text=True).strip()),
        'pins':PINS, 'hardware_tested':False, 'split_data':'authenticated ESB in every host output mode',
        'split_control':'encrypted BLE remains connected; session is not persisted',
        'third_host_esb_endpoint':False, 'local_minimal_libc':local_minimal_libc,
        'config_sha256':hashlib.sha256((dest/'zephyr/.config').read_bytes()).hexdigest(),
        'full_feature_checks_passed':True, 'mpsl_radio_timer_rtc_vectors_checked':True, **memory}
    (out/(target+'-build-record.json')).write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=('dongle','left','right'), required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--modules', help='Explicit semicolon-separated local modules (offline build)')
    parser.add_argument('--local-minimal-libc', action='store_true')
    args = parser.parse_args()
    build(args.target, Path(os.path.abspath(args.workspace)), args.modules, args.local_minimal_libc)
