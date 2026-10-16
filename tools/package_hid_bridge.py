"""Export a verified test dongle plus unchanged e6fcd41 peripherals; no flashing."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import zipfile

p = argparse.ArgumentParser()
p.add_argument('--dongle', type=Path, required=True)
p.add_argument('--stable', type=Path, required=True)
p.add_argument('--out', type=Path, required=True)
p.add_argument('--commit', required=True)
p.add_argument('--build-source', type=Path, required=True)
a = p.parse_args()
assert len(a.commit) == 40 and all(c in '0123456789abcdef' for c in a.commit)
a.out.mkdir(parents=True, exist_ok=False)
root = Path(__file__).resolve().parents[1]
stable = json.loads((a.stable / 'validation.json').read_text())
assert stable['commit'] == 'e6fcd4135b74ffe4d3a964915ecf33541e8e4323'
old_hashes = {r['name']: r['sha256'] for r in stable['files']}
files = [('lism_dongle_xiao147_hid_bridge_TEST.uf2', a.dongle)]
for name in ('lism_left_4waystick.uf2', 'lism_right_trackball.uf2',
             'lism_jp19.uf2', 'lism_circlepad.uf2', 'settings_reset-xiao_ble-zmk.uf2'):
    source = a.stable / name
    assert hashlib.sha256(source.read_bytes()).hexdigest() == old_hashes[name]
    files.append((name, source))
records = []
for name, source in files:
    data = source.read_bytes()
    assert len(data) and len(data) % 512 == 0
    count = len(data) // 512
    payload = bytearray()
    for i in range(count):
        block = data[i * 512:(i + 1) * 512]
        magic, magic2, flags, address, size, index, total, family = struct.unpack('<8I', block[:32])
        assert (magic, magic2, flags, size, index, total, family) == (
            0x0a324655, 0x9e5d5157, 0x2000, 256, i, count, 0xada52840)
        assert address == 0x27000 + i * 256 and address + size <= 0xec000
        assert struct.unpack('<I', block[-4:])[0] == 0x0ab16f30
        payload.extend(block[32:32 + size])
    if name.endswith('hid_bridge_TEST.uf2'):
        assert b'BLE Mouse (test)' in payload
        assert b'BLE relative mouse bridge (test)' in payload
        assert b'AXS_AUTO_READY' in payload
        for marker in (b'AXS_RAW_READY', b'AXS_SHADOW_READY', b'AXS_GESTURE_READY'):
            assert marker not in payload
    shutil.copyfile(source, a.out / name)
    records.append({'name': name, 'bytes': len(data), 'blocks': count,
                    'sha256': hashlib.sha256(data).hexdigest(),
                    'source_commit': a.commit if name.endswith('hid_bridge_TEST.uf2') else stable['commit']})
source_record = json.loads(a.build_source.read_text())
assert source_record['head_sha'] == a.commit
shutil.copyfile(a.build_source, a.out / 'build-source.json')
shutil.copyfile(root / 'diagnostics/cheerdots2/HID_BRIDGE_TEST.md', a.out / '配对及测试说明.md')
validation = {
    'commit': a.commit, 'files': records, 'uf2_count': 6,
    'experimental': True, 'generic_ble_relative_mouse': True,
    'ble_hid_peripheral_slots': 1, 'split_slots': 4, 'paired_capacity': 10,
    'forwarded_buttons': [1, 2, 3, 4, 5], 'forwarded_axes': ['X', 'Y', 'Wheel', 'AC Pan'],
    'native_multitouch_supported': False, 'vendor_protocol_supported': False,
    'cheerdots2_hardware_tested': False, 'other_mouse_hardware_tested': False,
    'device_flashed': False, 'peripherals_unchanged_from_e6fcd41': True,
    'normal_update_needs_reset': False,
    'known_limits': ['BLE only, no Classic', 'No PIN UI', 'Unsupported probe may retain an SMP bond',
                     'Release buttons before host switching; old host delivery not guaranteed'],
}
(a.out / 'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2))
(a.out / '先读我.txt').write_text(
    '通用 BLE HID 鼠标桥接实验版，尚未实机配对验证。\n'
    '这次只刷 lism_dongle_xiao147_hid_bridge_TEST.uf2。\n'
    '不要刷 reset，不必重刷左右手和摇杆；其余五个 UF2 是原 e6fcd41 文件。\n'
    'USR2 循环到第四页 BLE Mouse (test) → Start scan (60s) → 鼠标进配对模式 → 点设备。\n'
    '先用电脑 USB 输出测试移动、左右键、上下和左右滚动。换电脑前松开所有鼠标键。\n'
    '只支持标准 BLE 相对鼠标报告，不承诺 Cheerdots2 原生多指或厂商驱动功能。\n'
    '详细限制和配对流程见“配对及测试说明.md”。\n')
archive = a.out / f'lism-hid-bridge-TEST-{a.commit[:7]}.zip'
with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as z:
    for name, _ in files:
        z.write(a.out / name, name)
    for name in ('先读我.txt', '配对及测试说明.md', 'validation.json', 'build-source.json'):
        z.write(a.out / name, name)
with zipfile.ZipFile(archive) as z:
    assert sum(n.endswith('.uf2') for n in z.namelist()) == 6
    for record in records:
        assert hashlib.sha256(z.read(record['name'])).hexdigest() == record['sha256']
print(json.dumps({'package': str(archive), 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                  'uf2_count': 6, 'files': records}, ensure_ascii=False))
