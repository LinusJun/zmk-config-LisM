"""Portable full-feature build entrypoint. No flashing, settings reset or secrets.

Each matrix job owns its west workspace; generated upstream policy copies are
bound to the pinned sources and never modify the upstream checkouts.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
COMMON=['-DCONFIG_ZMK_SPLIT=y','-DCONFIG_ZMK_SPLIT_ROLE_CENTRAL=n']
TARGETS={
 'dongle':('lism_dongle prospector_adapter xiao_plus_147_touch','lism_dongle_xiao147',[
    '-DCONFIG_ZMK_SPLIT=y','-DCONFIG_ZMK_SPLIT_ROLE_CENTRAL=y',
    '-DCONFIG_LISM_BLE_HID_BRIDGE=y','-DCONFIG_LISM_BLE_INPUT_BRIDGE=y',
    '-DCONFIG_LISM_BLE_HOST_PROFILES=6','-DCONFIG_LISM_TWO_KEYBOARD_SETS=y',
    '-DCONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS=6','-DCONFIG_BT_MAX_PAIRED=14',
    '-DCONFIG_INPUT_THREAD_STACK_SIZE=2048','-DCONFIG_SYSTEM_WORKQUEUE_STACK_SIZE=4096',
    '-DCONFIG_LISM_INPUT_LEGACY_COMPAT=y','-DCONFIG_BT_SMP_SC_PAIR_ONLY=n',
    '-DCONFIG_LISM_INPUT_AUTO_PAIR=y','-DCONFIG_LISM_AUTO_SPLIT_SETS=y',
    '-DCONFIG_LISM_XIAO147_AUTO_TOUCH=y','-DCONFIG_I2C_NRFX_TRANSFER_TIMEOUT=100',
    '-DCONFIG_LISM_XIAO147_DISPLAY_SELFTEST=n','-DCONFIG_LISM_XIAO147_RAW_CAPTURE=n',
    '-DCONFIG_LISM_XIAO147_GESTURE_SHADOW=n','-DCONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST=n',
    '-DCONFIG_LOG_PROCESS_THREAD_STARTUP_DELAY_MS=0']),
 'left':('lism_left rgbled_adapter','lism_left_4waystick',COMMON+['-DCONFIG_LISM_SPLIT_ROLE_TAG=y']),
 'right':('lism_right rgbled_adapter','lism_right_trackball',COMMON+['-DCONFIG_LISM_SPLIT_ROLE_TAG=y']),
 'jp19':('lism_joystick','lism_jp19',COMMON),
 'circlepad':('lism_circlepad','lism_circlepad',COMMON),
 'reset':('settings_reset','settings_reset-xiao_ble-zmk',[]),
}

def run(command,cwd):
    print('+ '+' '.join(map(str,command)),flush=True)
    subprocess.run(list(map(str,command)),cwd=cwd,check=True)

def config_values(text):
    return dict(s.split('=',1) for s in text.splitlines() if s.startswith('CONFIG_') and '=' in s)

def require(values,expected):
    for k,v in expected.items():
        if values.get(k)!=v: raise ValueError(f'Configuration mismatch:{k} expected{v}, found{values.get(k)}')

def check_build(target,build):
    values=config_values((build/'zephyr/.config').read_text())
    if target!='reset' and values.get('CONFIG_ZMK_SETTINGS_RESET_ON_START')=='y':
        raise ValueError('Refusing settings-reset enabled in a normal firmware image')
    if target=='dongle':
        from package_input_receiver import check_config
        check_config((build/'zephyr/.config').read_text(),True)
        require(values,{'CONFIG_LISM_INPUT_AUTO_PAIR':'y','CONFIG_LISM_AUTO_SPLIT_SETS':'y',
          'CONFIG_LISM_CHARGE_TELEMETRY':'y','CONFIG_LISM_BLE_HID_BRIDGE':'y'})
        generated=build/'modules/zmk-config-LisM'
        for file,needles in {
            'lism_input_zephyr_smp.c':['lism_input_legacy_permitted(conn)','lism_input_identity_allowed(conn, &req->addr)'],
            'lism_hid_zmk_ble.c':['lism_profile_identity_lock','&loaded_peripheral'],
            'lism_input_zephyr_keys.c':['BT_SETTINGS_DEFINE(keys, "keys", lism_keys_set, keys_commit)'],
            'lism_zmk_battery.c':['lism_charge_note_battery_sample(sample_result)','*sample_result = 0;'],
        }.items():
            text=(generated/file).read_text()
            for needle in needles:
                if needle not in text:raise ValueError(f'Missing generated policy:{file}:{needle}')
        elf=(build/'zephyr/zmk.elf').read_bytes()
        for symbol in (b'lism_split_auto_observe',b'lism_keys_set',b'xiao147_palette_bright',b'lism_input_legacy_allowed'):
            if symbol not in elf:raise ValueError(f'Missing compiled feature:{symbol!r}')
    elif target in ('left','right'):
        require(values,{'CONFIG_LISM_SPLIT_ROLE_TAG':'y','CONFIG_LISM_CHARGE_TELEMETRY':'y',
                        'CONFIG_ZMK_SPLIT':'y'})
        if values.get('CONFIG_ZMK_SPLIT_ROLE_CENTRAL')=='y':raise ValueError('Peripheral unexpectedly central')
        generated=(build/'modules/zmk-config-LisM/lism_zmk_battery.c').read_text()
        if 'lism_charge_note_battery_sample(sample_result)' not in generated:raise ValueError('Missing battery recovery integration')
    elif target in ('jp19','circlepad'):
        require(values,{'CONFIG_LISM_RESISTIVE_JOYSTICK':'y','CONFIG_ZMK_SPLIT':'y'})
    else:
        if values.get('CONFIG_ZMK_SETTINGS_RESET_ON_START')!='y':raise ValueError('Wrong reset target')
    return values

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',required=True,choices=TARGETS)
    parser.add_argument('--workspace',type=Path,default=ROOT/'ci-workspace')
    parser.add_argument('--existing-modules',help='Local offline verification only: semicolon-separated extra module paths')
    parser.add_argument('--app',type=Path,help='Local offline verification only: pinned ZMK app path')
    args=parser.parse_args()
    from keymap_guard import validate
    validate((ROOT/'config/lism.keymap').read_text())
    ws=args.workspace.resolve();ws.mkdir(parents=True,exist_ok=True)
    if args.app:
        app=args.app.resolve()
        modules=str(ROOT)+';'+(args.existing_modules or '')
    else:
        manifest=ws/'config'
        shutil.copytree(ROOT/'config',manifest,dirs_exist_ok=True)
        if not (ws/'.west').exists():run(['west','init','-l',manifest],ws)
        run(['west','update','--fetch-opt=--filter=tree:0'],ws)
        run(['west','zephyr-export'],ws)
        app=ws/'zmk/app';modules=str(ROOT)
    shield,name,options=TARGETS[args.target]
    options=list(options)
    if args.target=='dongle':
        from prepare_input_security import generate
        generated=ws/'Kconfig';generate(app/'Kconfig',generated)
        options.append('-DKCONFIG_ROOT='+str(generated))
    build=ws/'build'
    command=['west','build','-p','always','-s',app,'-d',build,'-b','xiao_ble//zmk']
    if args.target=='dongle':command+=['-S','zmk-usb-logging']
    command+=['--','-DZMK_CONFIG='+str(ROOT/'config'),'-DZMK_EXTRA_MODULES='+modules,
              '-DSHIELD='+shield,*options]
    run(command,ws if not args.app else app.parent.parent)
    values=check_build(args.target,build)
    data=(build/'zephyr/zmk.uf2').read_bytes()
    from package_input_receiver import validate_uf2
    _,blocks=validate_uf2(data)
    out=ROOT/'ci-artifacts';out.mkdir(exist_ok=True)
    relative=('reset_危险备用_默认不要刷/' if args.target=='reset' else '')+name+'.uf2'
    dest=out/relative;dest.parent.mkdir(exist_ok=True);dest.write_bytes(data)
    record={'target':args.target,'file':relative,'bytes':len(data),'blocks':blocks,
       'sha256':hashlib.sha256(data).hexdigest(),'source_commit':os.environ.get('GITHUB_SHA','local-uncommitted'),
       'keymap_sha256':hashlib.sha256((ROOT/'config/lism.keymap').read_bytes()).hexdigest(),
       'settings_reset':args.target=='reset','configuration_checked':True,
       'config_sha256':hashlib.sha256((build/'zephyr/.config').read_bytes()).hexdigest(),
       'enabled_features':[k for k,v in sorted(values.items()) if k.startswith('CONFIG_LISM_') and v=='y']}
    (out/(args.target+'-build-record.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(record,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
