"""Build one original LisM pair: pure ESB halves, USB/BLE five-profile receiver."""
from pathlib import Path
import argparse,subprocess,os,hashlib,json,shutil
from prepare_upstream_esb import prepare,replace
from prepare_esb_kconfig import generate
from check_esb_artifacts import validate_uf2,check_radio_vectors
ROOT=Path(__file__).resolve().parents[1]
BASE='b116964cbc5669bc9acca233c5d0974d1be9cd32'
KEYMAP='dca2e00c452443223648b7f71b51b492ceb888e56f6157e95f74e476d9e4eb96'
PINS={'zmk':'9ebbeff0a8b69a42f14aec022cdf16c7a107b9e0','zephyr':'10ba6d0cb38bc3d258775d27982f707599320085','nrf':'9b3d2623fdcd9c0fd0284f860beea924568c9826','nrfxlib':'dfadf17305d8f000eda9aa74a5b9ff1c5647a23e','prospector-zmk-module':'ed98221f3b52b7066dbb10ba3af8a29150b93a5a'}
def production_fingerprint():
    files=[]
    for folder in ['src','cmake','config','boards','zephyr','tools']:
        files += [p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    files += [ROOT/'CMakeLists.txt',ROOT/'Kconfig']
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    return hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest(),hashes
def build(target,ws,modules=None,driver=None):
    fingerprint,source_hashes=production_fingerprint()
    ws=Path(ws).absolute();ws.mkdir(exist_ok=True,parents=True);central=target=='dongle';dest=ws/('build-'+target)
    assert hashlib.sha256((ROOT/'config/lism.keymap').read_bytes()).hexdigest()==KEYMAP
    for name,pin in PINS.items():
        assert subprocess.check_output(['git','-C',str(ws/name),'rev-parse','HEAD'],text=True).strip()==pin,name
    prepared=prepare(driver or ws/'zmk-feature-split-esb',ws/('module-'+target))
    compat=ws/('include-'+target)/'zmk';compat.mkdir(parents=True,exist_ok=True)
    h=(ws/'zmk/app/include/zmk/split/central.h').read_text()
    h=replace(h,'#define ZMK_SPLIT_CENTRAL_PERIPHERAL_COUNT MAX(BLE_PERIPHERAL_COUNT, WIRED_PERIPHERAL_COUNT)','#define ZMK_SPLIT_CENTRAL_PERIPHERAL_COUNT 2 /* single ESB pair */')
    (compat/'split').mkdir(exist_ok=True);(compat/'split/central.h').write_text(h)
    h=(ws/'zmk/app/include/zmk/ble.h').read_text()
    # Keep all seven old bond slots, but exactly five original host profiles.
    h=replace(h,'#define ZMK_BLE_PROFILE_COUNT CONFIG_BT_MAX_PAIRED','#define ZMK_BLE_PROFILE_COUNT 5 /* existing host profiles; two legacy half bonds retained */')
    (compat/'ble.h').write_text(h)
    display=ws/('display-'+target)
    shutil.copytree(ws/'prospector-zmk-module',display,dirs_exist_ok=True,ignore=shutil.ignore_patterns('.git'))
    p=display/'CMakeLists.txt';p.write_text(replace(p.read_text(),'        zephyr_library_sources(src/split/bluetooth/central_status_changed_observer.c)','        # ESB link emits the existing peripheral-status event directly.'))
    p=display/'boards/shields/prospector_adapter/src/layouts/operator/battery_circles.c'
    p.write_text(replace(p.read_text(),'#define PERIPHERAL_COUNT ZMK_SPLIT_BLE_PERIPHERAL_COUNT','#define PERIPHERAL_COUNT CONFIG_ZMK_SPLIT_ESB_PERIPHERAL_COUNT'))
    mods=modules.split(';') if modules else subprocess.check_output(['west','list','-f','{abspath}'],cwd=ws,text=True).splitlines()
    mods=[m for m in mods if Path(m).is_dir() and Path(m).name not in ['prospector-zmk-module','zmk-feature-split-esb',ROOT.name]]
    mods=list(dict.fromkeys([str(ROOT),str(prepared),str(display),str(ws/'nrf'),str(ws/'nrfxlib')]+mods))
    shield={'left':'lism_left rgbled_adapter','right':'lism_right rgbled_adapter','dongle':'lism_dongle prospector_adapter','reset':'settings_reset'}[target]
    options=[];overlay=str(ROOT/'config/esb-upstream.overlay')
    if target!='reset':
        options=['-DEXTRA_CONF_FILE='+str(ROOT/'config/esb-upstream.conf'),'-DCONFIG_ZMK_SPLIT=y','-DCONFIG_ZMK_SPLIT_ROLE_CENTRAL='+('y' if central else 'n'),'-DLISM_COMPAT_INCLUDE='+str(compat.parent)]
        if central:
            kroot=ws/'Kconfig.esb-host';generate(ws/'zmk/app/Kconfig',kroot,legacy_build_policy=False)
            options+=['-DKCONFIG_ROOT='+str(kroot),'-DCONFIG_ZMK_BLE=y','-DCONFIG_BT_LL_SOFTDEVICE=y','-DCONFIG_BT_LL_SW_SPLIT=n','-DCONFIG_BT_MAX_PAIRED=7','-DCONFIG_BT_MAX_CONN=1','-DCONFIG_BT_CTLR_SDC_PERIPHERAL_COUNT=1','-DCONFIG_NRF_SECURITY=n','-DCONFIG_BT_KEYS_OVERWRITE_OLDEST=n','-DCONFIG_MPSL_TIMESLOT_SESSION_COUNT=1','-DCONFIG_ZMK_SPLIT_ESB_USE_TIMESLOT=y','-DCONFIG_LV_Z_VDB_SIZE=25','-DCONFIG_LV_USE_CUSTOM_SPRINTF=y','-DSNIPPET=zmk-usb-logging','-DCONFIG_LOG_PROCESS_THREAD_STARTUP_DELAY_MS=0']
            overlay+=';'+str(ROOT/'config/esb-sdc.overlay')
        else:
            options+=['-DCONFIG_ZMK_BLE=n','-DCONFIG_ZMK_SPLIT_ESB_PERIPHERAL_ID='+('0' if target=='left' else '1'),'-DCONFIG_ZMK_SPLIT_ESB_USE_TIMESLOT=n','-DCONFIG_MPSL_TIMESLOT_SESSION_COUNT=1']
        options+=['-DDTC_OVERLAY_FILE='+overlay]
    cmd=['cmake','-S',str((ws/'zmk/app').resolve()),'-B',str(dest),'-G','Ninja','-DBOARD=xiao_ble//zmk','-DLISM_UPSTREAM_ESB_BUILD=ON','-DZephyr_DIR='+str(ws/'zephyr/share/zephyr-package/cmake'),'-DZMK_CONFIG='+str(ROOT/'config'),'-DZMK_EXTRA_MODULES='+str(ROOT),'-DZEPHYR_MODULES='+';'.join(mods),'-DSHIELD='+shield,*options]
    for stage,command in [('configure',cmd),('build',['cmake','--build',str(dest),'-j4'])]:
        log=ws/(target+'-'+stage+'.log');print(target,stage,flush=True)
        with log.open('w') as f:rc=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,env=dict(os.environ,ZEPHYR_BASE=str(ws/'zephyr'))).returncode
        if rc:print(log.read_text()[-15000:]);raise RuntimeError(target+' '+stage+' failed')
    conf=(dest/'zephyr/.config').read_text();v=dict(l.split('=',1) for l in conf.splitlines() if l.startswith('CONFIG_') and '=' in l)
    assert v.get('CONFIG_ZMK_SETTINGS_RESET_ON_START')==('y' if target=='reset' else None)
    if target!='reset':
        assert v.get('CONFIG_ZMK_SPLIT_ESB')=='y' and v.get('CONFIG_ZMK_SPLIT_BLE')!='y'
        assert v.get('CONFIG_ZMK_BLE')==('y' if central else None)
        if central:
            assert v['CONFIG_BT_MAX_PAIRED']=='7' and v['CONFIG_BT_MAX_CONN']=='1'
            assert v['CONFIG_BT_LL_SOFTDEVICE']=='y' and v['CONFIG_BT_SMP_SC_PAIR_ONLY']=='y'
            assert v['CONFIG_PROSPECTOR_STATUS_SCREEN_OPERATOR']=='y'
            mem=check_radio_vectors(dest/'zephyr/zmk.elf')
            from elftools.elf.elffile import ELFFile
            with (dest/'zephyr/zmk.elf').open('rb') as f:
                symbols=ELFFile(f).get_section_by_name('.symtab')
                assert symbols.get_symbol_by_name('profiles')[0]['st_size']==5*22
                assert symbols.get_symbol_by_name('peripheral_battery_levels')[0]['st_size']==2
            assert v['CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING']=='y'
            assert v.get('CONFIG_MPSL_DYNAMIC_INTERRUPTS')!='y'
        else:mem={}
    else:mem={}
    assert production_fingerprint()[0]==fingerprint,'Production sources changed during this build; rebuild required'
    data=(dest/'zephyr/zmk.uf2').read_bytes();validate_uf2(data)
    out=ROOT/'artifacts';out.mkdir(exist_ok=True)
    name={'left':'LisM_ESBv2_Left_4waystick.uf2','right':'LisM_ESBv2_Right_Trackball.uf2','dongle':'LisM_ESBv2_Prospector169_BLE5_USB.uf2','reset':'settings_reset_XIAO_nRF52840.uf2'}[target]
    (out/name).write_bytes(data);(out/(target+'.config')).write_text(conf);(out/(target+'.dts')).write_bytes((dest/'zephyr/zephyr.dts').read_bytes())
    rec={'target':target,'file':name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'verified_base':BASE,'keymap_sha256':KEYMAP,'upstream_esb':'314c7cbaf4a74e1add1d6ffc8249de3e29965b8c','host_profiles':5 if central else None,'host_ble':central,'split_transport':'ESB only' if target!='reset' else None,'hardware_tested':False,'pins':PINS,'production_fingerprint':fingerprint,'source_hashes':source_hashes,**mem}
    (out/(target+'-build.json')).write_text(json.dumps(rec,indent=2)+'\n');print('SUCCESS',json.dumps(rec),flush=True)
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--workspace',type=Path,required=True);a.add_argument('--target',choices=['left','right','dongle','reset'],required=True);a.add_argument('--modules');a.add_argument('--driver',type=Path);args=a.parse_args()
    build(args.target,args.workspace,args.modules,args.driver)
