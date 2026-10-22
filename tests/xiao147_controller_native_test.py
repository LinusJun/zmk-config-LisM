from pathlib import Path
import subprocess,re,json,hashlib
P=Path(__file__).resolve().parents[2]
src=P/'source/src/xiao147_ui.c';(P/'preview-host/controller_ui.inc').write_text(re.sub(r'^\s*#\s*include.*$','',src.read_text(),flags=re.M))
r=subprocess.run(['cmake','-S',str(P/'preview-host'),'-B',str(P/'preview-host/build')],capture_output=True,text=True);assert not r.returncode,r.stdout+r.stderr
r=subprocess.run(['cmake','--build',str(P/'preview-host/build'),'--target','controller','-j4'],capture_output=True,text=True);assert not r.returncode,r.stdout+r.stderr
r=subprocess.run([str(P/'preview-host/build/controller'),'9','unused'],capture_output=True,text=True,timeout=30);assert not r.returncode,r.stdout+r.stderr
report={'passed':True,'hardware_tested':False,'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'stdout':r.stdout,'scope':'Actual XIAO UI controller and device-page LVGL; modeled input ownership completion, buttons, clock/PWM/battery. Real ownership barrier tested separately.'}
(P/'verification/xiao147-controller-native.json').write_text(json.dumps(report,indent=2)+'\n');print(r.stdout)
