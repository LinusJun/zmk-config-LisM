from pathlib import Path
import hashlib,json,subprocess
P=Path(__file__).resolve().parents[2]
r=subprocess.run(['cmake','--build',str(P/'preview-host/build'),'--target','preview','-j4'],capture_output=True,text=True)
assert not r.returncode,r.stdout+r.stderr
r=subprocess.run([str(P/'preview-host/build/preview'),'9','unused'],capture_output=True,text=True,timeout=30)
assert not r.returncode,r.stdout+r.stderr
print(r.stdout)
report={'passed':True,'hardware_tested':False,'UI_source_sha256':hashlib.sha256((P/'source/src/lism_screen.c').read_bytes()).hexdigest(),'native_harness_sha256':hashlib.sha256((P/'preview-host/main.c').read_bytes()).hexdigest(),'stdout':r.stdout,'scope':'Actual LVGL object lifecycle, navigation and dialog code; mocked BLE status/commands. Does not establish real LCD/UI latency or wireless behavior.'}
(P/'verification/ui-native.json').write_text(json.dumps(report,indent=2))
