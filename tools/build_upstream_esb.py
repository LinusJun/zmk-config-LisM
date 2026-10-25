"""Build one original LisM pair: pure ESB halves, USB/BLE five-profile receiver."""
from pathlib import Path
import argparse,subprocess,os,hashlib,json,shutil
from prepare_upstream_esb import prepare,replace
from prepare_esb_kconfig import generate
from prepare_mirror_display import prepare as prepare_mirror
from prepare_lcd_diagnostics import prepare as prepare_lcd_diagnostics
from check_esb_artifacts import validate_uf2,check_radio_vectors
ROOT=Path(__file__).resolve().parents[1]
BASE='b116964cbc5669bc9acca233c5d0974d1be9cd32'
KEYMAP='326d61e855b10da6cbd410c32be931d431695396a8f0d47e8b16116fdaae59d5'
PINS={'zmk':'9ebbeff0a8b69a42f14aec022cdf16c7a107b9e0','zephyr':'10ba6d0cb38bc3d258775d27982f707599320085','nrf':'9b3d2623fdcd9c0fd0284f860beea924568c9826','nrfxlib':'dfadf17305d8f000eda9aa74a5b9ff1c5647a23e','prospector-zmk-module':'ed98221f3b52b7066dbb10ba3af8a29150b93a5a'}
def production_fingerprint():
    files=[]
    for folder in ['src','cmake','config','boards','dts','zephyr','tools']:
        files += [p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    files += [ROOT/'CMakeLists.txt',ROOT/'Kconfig']
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    return hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest(),hashes
def build(target,ws,modules=None,driver=None):
    fingerprint,source_hashes=production_fingerprint()
    ws=Path(ws).absolute();ws.mkdir(exist_ok=True,parents=True);display_central=target in ['dongle_a','dongle_b'];xiao=target=='dongle_b';central=display_central or target=='right';dest=ws/('build-'+target)
    assert hashlib.sha256((ROOT/'config/lism.keymap').read_bytes()).hexdigest()==KEYMAP
    for name,pin in PINS.items():
        assert subprocess.check_output(['git','-C',str(ws/name),'rev-parse','HEAD'],text=True).strip()==pin,name
    prepared=prepare(driver or ws/'zmk-feature-split-esb',ws/('module-'+target))
    compat=ws/('include-'+target)/'zmk';compat.mkdir(parents=True,exist_ok=True)
    h=(ws/'zmk/app/include/zmk/split/central.h').read_text()
    h=replace(h,'#define ZMK_SPLIT_CENTRAL_PERIPHERAL_COUNT MAX(BLE_PERIPHERAL_COUNT, WIRED_PERIPHERAL_COUNT)','#define ZMK_SPLIT_CENTRAL_PERIPHERAL_COUNT 2 /* single ESB pair */')
    (compat/'split').mkdir(exist_ok=True);(compat/'split/central.h').write_text(h)
    # All three targets use one explicit extended data union. The pinned ABI's
    # 13-byte sensor member cannot hold a stamped routing control record.
    h=(ws/'zmk/app/include/zmk/split/transport/types.h').read_text()
    h=replace(h,'    union {','    union {\n        uint8_t lism_route_data[24];') if h.count('    union {')==1 else h.replace('    union {','    union {\n        uint8_t lism_route_data[24];',1)
    (compat/'split/transport').mkdir(exist_ok=True)
    (compat/'split/transport/types.h').write_text(h)
    h=(ws/'zmk/app/include/zmk/ble.h').read_text()
    # Keep all seven old bond slots, but exactly five original host profiles.
    h=replace(h,'#define ZMK_BLE_PROFILE_COUNT CONFIG_BT_MAX_PAIRED','#define ZMK_BLE_PROFILE_COUNT 5 /* existing host profiles; two legacy half bonds retained */')
    (compat/'ble.h').write_text(h)
    if central:
        shutil.copyfile(ROOT/'src/esb_upstream/lism_nrfx_reserved_resources.h',
                        compat.parent/'lism_nrfx_reserved_resources.h')
        shutil.copyfile(ROOT/'src/esb_upstream/lism_hog_routing.h', compat.parent/'lism_hog_routing.h')
    display=ws/('display-'+target)
    shutil.copytree(ws/'prospector-zmk-module',display,dirs_exist_ok=True,ignore=shutil.ignore_patterns('.git'))
    if xiao:
        shutil.copyfile(ROOT/'src/display_jd9853a.c',display/'drivers/display/display_st7789v.c')
        (display/'boards/shields/prospector_adapter/src/display_rotate_init.c').write_text('/* Fixed native landscape; no rotation buffer. */\n')
    if target=='dongle_a':
        prepare_lcd_diagnostics(display/'drivers/display/display_st7789v.c')
    p=display/'CMakeLists.txt';p.write_text(replace(p.read_text(),'        zephyr_library_sources(src/split/bluetooth/central_status_changed_observer.c)','        # ESB link emits the existing peripheral-status event directly.'))
    operator=display/'boards/shields/prospector_adapter/src/layouts/operator'
    for suffix in ['c','h']:
        shutil.copyfile(ROOT/('src/esb_upstream/host_output.'+suffix),operator/('output.'+suffix))
    p=display/'boards/shields/prospector_adapter/src/layouts/operator/battery_circles.c'
    text=replace(p.read_text(),'#define PERIPHERAL_COUNT ZMK_SPLIT_BLE_PERIPHERAL_COUNT','#define PERIPHERAL_COUNT CONFIG_ZMK_SPLIT_ESB_PERIPHERAL_COUNT')
    text=replace(text,'int zmk_widget_battery_circles_init(',
                 (ROOT/'src/esb_upstream/battery_ui_sync.inc').read_text()+'\nint zmk_widget_battery_circles_init(')
    text=replace(text,'    sys_slist_append(&widgets, &widget->node);',
                 '    sys_slist_append(&widgets, &widget->node);\n    lism_esb_battery_sync_start();')
    p.write_text(text)
    p=operator/'wpm_meter.c'
    p.write_text(replace(p.read_text(),'    lv_obj_set_size(widget->obj, 260, 90);','    lv_obj_set_size(widget->obj, 260, 90);\n    lv_obj_remove_flag(widget->obj, LV_OBJ_FLAG_SCROLLABLE);\n    lv_obj_set_scrollbar_mode(widget->obj, LV_SCROLLBAR_MODE_OFF);'))
    prepare_mirror(operator)
    if display_central:
        # One bounded plot recovery only when WPM/its old peak actually reaches
        # zero. No periodic/full-screen refresh and no synchronous LVGL call.
        text=p.read_text()
        text=replace(text,'        if (dirty) wpm_meter_render(0);',
            '        if (dirty) {\n'
            '            wpm_meter_render(0);\n'
            '            struct zmk_widget_wpm_meter *widget;\n'
            '            SYS_SLIST_FOR_EACH_CONTAINER(&widgets, widget, node) {\n'
            '                lv_obj_invalidate(widget->obj);\n'
            '            }\n'
            '        }')
        p.write_text(text)
    p=operator/'status_screen.c'
    text=p.read_text()
    text=replace(text,'#include <lvgl.h>','#include <lvgl.h>\n#include <lism_screen.h>\n#include <kb_route/icons.h>\n#include <kb_route/mirror.h>\n#include <kb_route/modifier_band.h>\n#include <kb_route/prospector169_layout.h>')
    text=replace(text,'    return screen;','    kb_route_icons_create(screen);\n    kb_mirror_ui_start();\n    lism_screen_create(screen);\n    return screen;')
    text=replace(text,'    return screen;','    kb_modifier_band_layout(zmk_widget_modifier_indicator_obj(&modifier_indicator_widget));\n    return screen;')
    text=replace(text,'    return screen;',
        '    kb_prospector169_wpm_layout(wpm_meter_widget.obj,wpm_meter_widget.bars,WPM_BAR_COUNT,wpm_meter_widget.peak_indicator);\n'
        '    kb_prospector169_layer_row_layout(layer_display_widget.obj);\n'
        '    return screen;')
    if target=='dongle_a':
        text='#include <kb_route/lcd_ui_diagnostics.h>\n'+text
        text=replace(text,'    return screen;',
                     '    lism_lcd_ui_diagnostics_start(screen,wpm_meter_widget.wpm_label,output_widget.ble_btn);\n    return screen;')
    if xiao:
        text='#include "xiao147_ui.h"\n'+text
        text=replace(text,'    return screen;','    xiao147_ui_start(screen);\n    return screen;')
    p.write_text(text)
    mods=modules.split(';') if modules else subprocess.check_output(['west','list','-f','{abspath}'],cwd=ws,text=True).splitlines()
    mods=[m for m in mods if Path(m).is_dir() and Path(m).name not in ['prospector-zmk-module','zmk-feature-split-esb',ROOT.name]]
    mods=list(dict.fromkeys([str(ROOT),str(prepared),str(display),str(ws/'nrf'),str(ws/'nrfxlib')]+mods))
    shield={'left':'lism_left rgbled_adapter','right':'lism_right rgbled_adapter','dongle_a':'lism_dongle prospector_adapter','dongle_b':'lism_dongle prospector_adapter xiao_plus_147_touch','reset':'settings_reset'}[target]
    options=[];overlay=str(ROOT/'config/esb-upstream.overlay')
    if target=='dongle_a':overlay+=';'+str(ROOT/'config/prospector169-lcd-8mhz.overlay')
    if target!='reset':
        options=['-DEXTRA_CONF_FILE='+str(ROOT/'config/esb-upstream.conf'),'-DCONFIG_ZMK_SPLIT=y','-DCONFIG_ZMK_SPLIT_ROLE_CENTRAL='+('y' if central else 'n'),'-DLISM_COMPAT_INCLUDE='+str(compat.parent),'-DLISM_ESB_FOLDER='+str(prepared/'src/split/esb')]
        if display_central:options+=['-DCONFIG_SYS_HEAP_RUNTIME_STATS=y','-DCONFIG_LISM_DONGLE_STATION='+('1' if xiao else '0')]
        if xiao:options+=['-DCONFIG_BT_SCAN_WITH_IDENTITY=y','-DCONFIG_PROSPECTOR_FIXED_BRIGHTNESS=100','-DCONFIG_LISM_XIAO147_TOUCH=y','-DCONFIG_I2C=y','-DCONFIG_I2C_NRFX_TRANSFER_TIMEOUT=100','-DCONFIG_INPUT_GPIO_KEYS=y','-DCONFIG_LV_COLOR_16_SWAP=n']
        options+=['-DSNIPPET=zmk-usb-logging','-DCONFIG_LOG_PROCESS_THREAD_STARTUP_DELAY_MS=0',
                  '-DCONFIG_ZMK_LOG_LEVEL_INF=y','-DCONFIG_LOG_BUFFER_SIZE=8192']
        if central:
            kroot=ws/'Kconfig.esb-host';generate(ws/'zmk/app/Kconfig',kroot,legacy_build_policy=True,sdc_resources=True)
            options+=['-DKCONFIG_ROOT='+str(kroot),'-DCONFIG_ZMK_BLE=y',
                      '-DCONFIG_BT_LL_SOFTDEVICE=y','-DCONFIG_BT_LL_SW_SPLIT=n',
                      '-DCONFIG_BT_SMP_SC_PAIR_ONLY=n','-DCONFIG_BT_MAX_PAIRED='+('9' if display_central else '5'),'-DCONFIG_BT_MAX_CONN='+('7' if display_central else '5'),
                      '-DCONFIG_BT_CTLR_SDC_PERIPHERAL_COUNT=5','-DCONFIG_NRF_SECURITY=n',
                      '-DCONFIG_BT_KEYS_OVERWRITE_OLDEST=n','-DCONFIG_MPSL_TIMESLOT_SESSION_COUNT=1',
                      '-DCONFIG_MPSL_FEM_ONLY=n','-DCONFIG_MPSL_DYNAMIC_INTERRUPTS=n',
                      '-DCONFIG_ZMK_SPLIT_ESB_USE_TIMESLOT=y',
                      '-DCONFIG_LV_Z_VDB_SIZE=20','-DCONFIG_LV_Z_DOUBLE_VDB=y','-DCONFIG_LV_Z_MEM_POOL_SIZE='+('32768' if xiao else '24000'),'-DCONFIG_LV_FONT_MONTSERRAT_10=y','-DCONFIG_LV_FONT_MONTSERRAT_18=y','-DCONFIG_LV_FONT_MONTSERRAT_12=y','-DCONFIG_LV_FONT_MONTSERRAT_14=y','-DCONFIG_LV_USE_CUSTOM_SPRINTF=y']
            if not display_central:
                options=[o for o in options if not o.startswith('-DCONFIG_LV_')]
                options+=['-DCONFIG_LISM_BLE_INPUT_BRIDGE=n','-DCONFIG_ZMK_USB=n','-DCONFIG_ZMK_WPM=y','-DCONFIG_ZMK_HID_INDICATORS=y','-DCONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS=2','-DCONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_QUEUE_SIZE=16','-DCONFIG_ZMK_SPLIT_ESB_PERIPHERAL_ID=1','-DCONFIG_ZMK_KEYBOARD_NAME="LisM Keyboard"']
                hub_overlay=ws/'kb-right.overlay'
                hub_overlay.write_text('#include "'+str(ROOT/'boards/shields/lism/lism_dongle.overlay')+'"\n/ { chosen { zmk,kscan = &kscan0; }; };\n')
                overlay+=';'+str(hub_overlay)
            overlay+=';'+str(ROOT/'config/esb-sdc.overlay')
        else:
            options+=['-DCONFIG_ZMK_BLE=n','-DCONFIG_ZMK_SPLIT_ESB_USE_TIMESLOT=n',
                      '-DCONFIG_MPSL_FEM_ONLY=y',
                      '-DCONFIG_ZMK_SPLIT_ESB_PERIPHERAL_ID='+('1' if target=='right' else '0')]
        options+=['-DDTC_OVERLAY_FILE='+overlay]
    cmd=['cmake','-S',str((ws/'zmk/app').resolve()),'-B',str(dest),'-G','Ninja','-DBOARD=xiao_ble//zmk','-DLISM_UPSTREAM_ESB_BUILD=ON','-DLISM_DISPLAY_MODULE='+str(display),'-DZephyr_DIR='+str(ws/'zephyr/share/zephyr-package/cmake'),'-DZMK_CONFIG='+str(ROOT/'config'),'-DZMK_EXTRA_MODULES='+str(ROOT),'-DZEPHYR_MODULES='+';'.join(mods),'-DSHIELD='+shield,*options]
    for stage,command in [('configure',cmd),('build',['cmake','--build',str(dest),'-j4'])]:
        log=ws/(target+'-'+stage+'.log');print(target,stage,flush=True)
        with log.open('w') as f:rc=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,env=dict(os.environ,ZEPHYR_BASE=str(ws/'zephyr'))).returncode
        if rc:print(log.read_text()[-15000:]);raise RuntimeError(target+' '+stage+' failed')
    conf=(dest/'zephyr/.config').read_text();v=dict(l.split('=',1) for l in conf.splitlines() if l.startswith('CONFIG_') and '=' in l)
    assert v.get('CONFIG_ZMK_SETTINGS_RESET_ON_START')==('y' if target=='reset' else None)
    if target!='reset':
        assert v.get('CONFIG_ZMK_SPLIT_ESB')=='y' and v.get('CONFIG_ZMK_SPLIT_BLE')!='y'
        assert v.get('CONFIG_ZMK_BLE')==('y' if central else None)
        assert v.get('CONFIG_MPSL_DYNAMIC_INTERRUPTS') is None
        assert v.get('CONFIG_ZMK_SPLIT_ESB_USE_TIMESLOT')==('y' if central else None)
        assert v.get('CONFIG_ESB_DYNAMIC_INTERRUPTS')=='y'
        from elftools.elf.elffile import ELFFile
        with (dest/'zephyr/zmk.elf').open('rb') as f:
            elf=ELFFile(f);syms=elf.get_section_by_name('.symtab')
            assert syms.get_symbol_by_name('radio_thread')
            assert syms.get_symbol_by_name('_k_thread_data_lism_radio_thread')
            if central:
                if display_central:assert v['CONFIG_PROSPECTOR_STATUS_SCREEN_OPERATOR']=='y'
                assert v['CONFIG_BT_MAX_PAIRED']==('9' if display_central else '5') and v['CONFIG_BT_MAX_CONN']==('7' if display_central else '5')
                assert v['CONFIG_BT_LL_SOFTDEVICE']=='y' and v.get('CONFIG_BT_SMP_SC_PAIR_ONLY') is None
                assert v.get('CONFIG_MPSL_FEM_ONLY') is None
                assert v.get('CONFIG_MPSL_FEM') is None
                assert syms.get_symbol_by_name('peripheral_battery_levels')[0]['st_size']==2
                assert syms.get_symbol_by_name('profiles')[0]['st_size']==5*22
                for n in ['mpsl_timeslot_request','bt_enable','lism_esb_resources_init','lism_esb_radio_irq']:
                    assert syms.get_symbol_by_name(n),n
            mem={'flash_bytes':sum(seg['p_filesz'] for seg in elf.iter_segments() if seg['p_type']=='PT_LOAD' and seg['p_paddr']<0x100000),
                 'ram_bytes':sum(seg['p_memsz'] for seg in elf.iter_segments() if seg['p_type']=='PT_LOAD' and 0x20000000<=seg['p_vaddr']<0x20040000)}
        if central:check_radio_vectors(dest/'zephyr/zmk.elf')
    else:mem={}
    assert production_fingerprint()[0]==fingerprint,'Production sources changed during this build; rebuild required'
    data=(dest/'zephyr/zmk.uf2').read_bytes();validate_uf2(data)
    out=ROOT/'artifacts';out.mkdir(exist_ok=True)
    name={'left':'LisM_ESBv50414_PRIVATE76_TEST_Left_4waystick.uf2','right':'LisM_ESBv50417_LEFT_RSSI_TEST_Right_Trackball_BLE5_Hub.uf2','dongle_a':'LisM_ESBv50417_LEFT_RSSI_TEST_Dongle_A_Prospector169.uf2','dongle_b':'LisM_ESBv50418_FULL_TEST_Dongle_B_XIAO147_Touch.uf2','reset':'settings_reset_XIAO_nRF52840.uf2'}[target]
    (out/name).write_bytes(data);(out/(target+'.config')).write_text(conf);(out/(target+'.dts')).write_bytes((dest/'zephyr/zephyr.dts').read_bytes())
    rec={'target':target,'file':name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'verified_base':BASE,'keymap_sha256':KEYMAP,'upstream_esb':'314c7cbaf4a74e1add1d6ffc8249de3e29965b8c','host_profiles':5 if central else 0,'simultaneous_host_connections':5 if central else 0,'host_ble':central,'split_transport':'ESB only' if target!='reset' else None,'hardware_tested':False,'pins':PINS,'production_fingerprint':fingerprint,'source_hashes':source_hashes,**mem}
    (out/(target+'-build.json')).write_text(json.dumps(rec,indent=2)+'\n');print('SUCCESS',json.dumps({k:rec[k] for k in ['target','file','sha256','bytes','production_fingerprint'] }|mem),flush=True)
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--workspace',type=Path,required=True);a.add_argument('--target',choices=['left','right','dongle_a','dongle_b','reset'],required=True);a.add_argument('--modules');a.add_argument('--driver',type=Path);args=a.parse_args()
    build(args.target,args.workspace,args.modules,args.driver)
