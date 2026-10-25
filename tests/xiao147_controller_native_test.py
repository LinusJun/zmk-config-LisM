from pathlib import Path
import subprocess,re,json,hashlib
P=Path(__file__).resolve().parents[2]
src=P/'source/src/xiao147_ui.c';(P/'preview-host/controller_ui.inc').write_text(re.sub(r'^\s*#\s*include.*$','',src.read_text(),flags=re.M))
r=subprocess.run(['cmake','-S',str(P/'preview-host'),'-B',str(P/'preview-host/build')],capture_output=True,text=True);assert not r.returncode,r.stdout+r.stderr
r=subprocess.run(['cmake','--build',str(P/'preview-host/build'),'--target','controller','-j4'],capture_output=True,text=True);assert not r.returncode,r.stdout+r.stderr
r=subprocess.run([str(P/'preview-host/build/controller'),'9','unused'],capture_output=True,text=True,timeout=30);assert not r.returncode,r.stdout+r.stderr
report={'passed':True,'hardware_tested':False,'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'stdout':r.stdout,'scope':'Actual XIAO UI controller and device-page LVGL; modeled input ownership completion, buttons, clock/PWM/battery. Real ownership barrier tested separately.'}
(P/'verification/xiao147-controller-native.json').write_text(json.dumps(report,indent=2)+'\n');print(r.stdout)

# A 32-bit model uses the actual B 32768-byte LVGL pool. Its scheduling and
# transport callbacks are still fixture inputs; this is not runtime RF proof.
r=subprocess.run(['cmake','-S',str(P/'preview-host'),'-B',str(P/'preview-host/build32'),'-DCMAKE_C_FLAGS=-m32 -DLV_MEM_SIZE=32768','-DCMAKE_EXE_LINKER_FLAGS=-m32'],capture_output=True,text=True);assert not r.returncode,r.stdout+r.stderr
r=subprocess.run(['cmake','--build',str(P/'preview-host/build32'),'--target','controller','-j4'],capture_output=True,text=True);assert not r.returncode,r.stdout+r.stderr
r=subprocess.run([str(P/'preview-host/build32/controller'),'9','unused'],capture_output=True,text=True,timeout=30);assert not r.returncode,r.stdout+r.stderr
print(r.stdout)
report={'passed':True,'hardware_tested':False,'pointer_bits':32,'lvgl_pool_bytes':32768,'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'stdout':r.stdout,'scope':'Actual XIAO controller/icons and device-page LVGL, modeled home constructor inputs/button/PWM/telemetry/transport; seven layers, 40 page cycles, search/connect/auth dialogs. Native 32-bit memory model, not firmware runtime high-water measurement.'}
(P/'verification/xiao147-controller32-v50418.json').write_text(json.dumps(report,indent=2)+'\n')
