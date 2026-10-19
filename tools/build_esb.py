"""Build original Prospector + left four-way + right trackball; never flash/reset."""
from pathlib import Path
import argparse,hashlib,json,os,re,subprocess,sys
from check_esb_artifacts import check_radio_vectors,validate_uf2
from prepare_esb_kconfig import generate
ROOT=Path(__file__).resolve().parents[1]
PINS={'zmk':'9ebbeff0a8b69a42f14aec022cdf16c7a107b9e0','zephyr':'10ba6d0cb38bc3d258775d27982f707599320085','nrf':'9b3d2623fdcd9c0fd0284f860beea924568c9826','nrfxlib':'dfadf17305d8f000eda9aa74a5b9ff1c5647a23e','zmk-driver-paw3222':'df652881be2520bde4f64ab6ca35e4c5708f4f9b','zmk-rgbled-widget':'e6b467792a1dabef8cfa5c9fd26bfefc9c16662b','zmk-feature-charge-indicator':'f0d311ed7804cda0e5837ef902f0419aeace4a88','prospector-zmk-module':'ed98221f3b52b7066dbb10ba3af8a29150b93a5a'}

def build(target,ws,modules=None):
    app=(ws/'zmk/app').resolve();dest=ws/('esb-'+target);central=target=='dongle'
    for name,pin in PINS.items():
        found=subprocess.check_output(['git','-C',str(ws/name),'rev-parse','HEAD'],text=True).strip()
        if found!=pin:raise ValueError(f'{name}: unexpected source {found}')
    if target=="reset":return build_reset(ws,modules)
    options=['-DCONFIG_ZMK_SPLIT=y','-DCONFIG_ZMK_SPLIT_ROLE_CENTRAL='+('y' if central else 'n'),'-DCONFIG_BT_KEYS_OVERWRITE_OLDEST=n','-DCONFIG_SYSTEM_WORKQUEUE_STACK_SIZE=4096','-DCONFIG_INPUT_THREAD_STACK_SIZE=2048']
    shield={'left':'lism_left rgbled_adapter','right':'lism_right rgbled_adapter','dongle':'lism_dongle prospector_adapter'}[target]
    if central:
        kroot=ws/'Kconfig.esb-original';generate(app/'Kconfig',kroot,legacy_build_policy=False)
        options+=['-DKCONFIG_ROOT='+str(kroot),'-DCONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS=2','-DCONFIG_BT_MAX_PAIRED=9','-DCONFIG_BT_MAX_CONN=3','-DCONFIG_LV_USE_CUSTOM_SPRINTF=y','-DCONFIG_LV_Z_VDB_SIZE=25','-DSNIPPET=zmk-usb-logging','-DCONFIG_LOG_PROCESS_THREAD_STARTUP_DELAY_MS=0']
    cmd=['cmake','-S',str(app),'-B',str(dest),'-G','Ninja','-DBOARD=xiao_ble//zmk','-DZephyr_DIR='+str(ws/'zephyr/share/zephyr-package/cmake'),'-DZMK_CONFIG='+str(ROOT/'config'),'-DZMK_EXTRA_MODULES='+str(ROOT),'-DSHIELD='+shield,'-DEXTRA_CONF_FILE='+str(ROOT/'config/esb.conf'),'-DDTC_OVERLAY_FILE='+str(ROOT/'config/esb-sdc.overlay'),*options]
    if modules:cmd+=['-DZEPHYR_MODULES='+modules]
    env=dict(os.environ,ZEPHYR_BASE=str(ws/'zephyr'))
    for stage,command in [('configure',cmd),('build',['cmake','--build',str(dest),'-j4'])]:
        log=ws/f'{target}-{stage}.log';print(target,stage,log,flush=True)
        with log.open('w') as out:rc=subprocess.run(command,env=env,stdout=out,stderr=subprocess.STDOUT).returncode
        if rc:print(log.read_text()[-14000:]);raise RuntimeError(f'{target} {stage} failed')
    conf=(dest/'zephyr/.config').read_text();vals=dict(l.split('=',1) for l in conf.splitlines() if l.startswith('CONFIG_') and '=' in l)
    for k,v in {'LISM_SPLIT_ESB':'y','BT_LL_SOFTDEVICE':'y','ESB_MAX_PAYLOAD_LENGTH':'68','ESB_RADIO_IRQ_PRIORITY':'0','MPSL_TIMESLOT_SESSION_COUNT':'1','BT_SMP_SC_PAIR_ONLY':'y','BT_KEYS_OVERWRITE_OLDEST':None}.items():
        if v is None:assert vals.get('CONFIG_'+k)!='y',k
        else:assert vals.get('CONFIG_'+k)==v,(k,vals.get('CONFIG_'+k))
    for k in ['LISM_STANDALONE','LISM_COMPANION_SERVER','LISM_TWO_KEYBOARD_SETS','LISM_AUTO_SPLIT_SETS','LISM_XIAO147_TOUCH','LISM_PROSPECTOR169_TOUCH','LISM_BLE_HID_BRIDGE','LISM_CHARGE_TELEMETRY','ZMK_SETTINGS_RESET_ON_START']:assert vals.get('CONFIG_'+k)!='y',k
    if central:assert vals.get('CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS')=='2'
    else:
        assert vals.get('CONFIG_ZMK_SPLIT_ROLE_CENTRAL')!='y'
        assert vals.get('CONFIG_RGBLED_WIDGET')=='y' and vals.get('CONFIG_CHARGE_INDICATOR')=='y'
        assert vals.get('CONFIG_PAW3222')==('y' if target=='right' else None)
    dt=(dest/'zephyr/zephyr.dts').read_text();bindings=re.findall(r'bindings = <([^>]+)>;',dt[dt.index('keymap {'):],re.S)
    # DTS keymap binding cells differ by behavior; source-level regression checks all 46 positions.
    mem=check_radio_vectors(dest/'zephyr/zmk.elf');elf=(dest/'zephyr/zmk.elf').read_bytes()
    for sym in [b'lism_esb_transport_plan',b'lism_esb_seal',b'lism_esb_radio_thread',b'lism_esb_central' if central else b'lism_esb_peripheral']:assert sym in elf,sym
    if central:
        generated=(dest/'modules/zmk-config-LisM/legacy_central.c').read_text()
        assert 'if (lism_set_observe_ad(addr, rssi, type, ad)) { split_central_eir_found(addr); return; }' in generated
    image=(dest/'zephyr/zmk.uf2').read_bytes();validate_uf2(image)
    out=ROOT/'artifacts';out.mkdir(exist_ok=True)
    name={'left':'LisM_Original_Left_4waystick_ESB.uf2','right':'LisM_Original_Right_Trackball_ESB.uf2','dongle':'LisM_Original_Prospector169_ESB.uf2'}[target]
    (out/name).write_bytes(image)
    rec={'target':target,'file':name,'sha256':hashlib.sha256(image).hexdigest(),'bytes':len(image),'keymap_sha256':hashlib.sha256((ROOT/'config/lism.keymap').read_bytes()).hexdigest(),'base_commit':'86bf1e95803c07381bde194825dce18c7ecf8dbb','source_head':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),'source_dirty':bool(subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip()),'hardware_tested':False,'transport':'ESB data + encrypted BLE control','slots':2,'physical_key_positions':46,'pins':PINS,**mem}
    (out/(target+'-build.json')).write_text(json.dumps(rec,indent=2)+'\n')
    (out/(target+'.config')).write_text(conf);(out/(target+'.dts')).write_text(dt)
    print('SUCCESS',json.dumps(rec),flush=True)


def build_reset(ws,modules=None):
    """Independent erase-settings image for XIAO nRF52840; never a normal image."""
    app=(ws/'zmk/app').resolve();dest=ws/'settings-reset'
    cmd=['cmake','-S',str(app),'-B',str(dest),'-G','Ninja','-DBOARD=xiao_ble//zmk','-DZephyr_DIR='+str(ws/'zephyr/share/zephyr-package/cmake'),'-DZMK_CONFIG='+str(ROOT/'config'),'-DZMK_EXTRA_MODULES='+str(ROOT),'-DSHIELD=settings_reset']
    if modules:cmd+=['-DZEPHYR_MODULES='+modules]
    env=dict(os.environ,ZEPHYR_BASE=str(ws/'zephyr'))
    for stage,command in [('configure',cmd),('build',['cmake','--build',str(dest),'-j4'])]:
        log=ws/f'reset-{stage}.log';print('reset',stage,flush=True)
        with log.open('w') as out:rc=subprocess.run(command,env=env,stdout=out,stderr=subprocess.STDOUT).returncode
        if rc:print(log.read_text()[-12000:]);raise RuntimeError('reset build failed')
    conf=(dest/'zephyr/.config').read_text();vals=dict(l.split('=',1) for l in conf.splitlines() if l.startswith('CONFIG_') and '=' in l)
    assert vals.get('CONFIG_ZMK_SETTINGS_RESET_ON_START')=='y'
    assert vals.get('CONFIG_LISM_SPLIT_ESB')!='y'
    data=(dest/'zephyr/zmk.uf2').read_bytes();validate_uf2(data)
    from elftools.elf.elffile import ELFFile
    with (dest/'zephyr/zmk.elf').open('rb') as f:
        elf=ELFFile(f);symbols=elf.get_section_by_name('.symtab');mem={k:symbols.get_symbol_by_name(n)[0]['st_value'] for k,n in [('flash_bytes','_flash_used'),('ram_bytes','_image_ram_size')]}
    out=ROOT/'artifacts';out.mkdir(exist_ok=True);name='settings_reset_XIAO_nRF52840.uf2';(out/name).write_bytes(data)
    rec={'target':'reset','file':name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'clears_pairings_and_saved_settings':True,'hardware_tested':False,'board':'xiao_ble//zmk','source_head':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),'source_dirty':bool(subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip()),**mem}
    (out/'reset-build.json').write_text(json.dumps(rec,indent=2)+'\n');(out/'reset.config').write_text(conf);(out/'reset.dts').write_text((dest/'zephyr/zephyr.dts').read_text())
    print('SUCCESS',json.dumps(rec),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--target',choices=['left','right','dongle','reset'],required=True);parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--modules');args=parser.parse_args()
    build(args.target,args.workspace.absolute(),args.modules)
