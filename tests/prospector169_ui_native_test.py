from pathlib import Path
import subprocess,json,hashlib
P=Path(__file__).resolve().parents[2];D=P/'preview-169';B=D/'build-v50'
for cmd in [['cmake','-S',str(D),'-B',str(B)],['cmake','--build',str(B),'--target','preview','-j4']]:
 r=subprocess.run(cmd,capture_output=True,text=True);assert not r.returncode,r.stdout+r.stderr
r=subprocess.run([str(B/'preview'),'9','unused'],capture_output=True,text=True,timeout=30);assert not r.returncode,r.stdout+r.stderr
print(r.stdout)
(P/'verification/prospector169-ui-native.json').write_text(json.dumps({'passed':True,'hardware_tested':False,'UI_source_sha256':hashlib.sha256((P/'source/src/lism_screen169.c').read_bytes()).hexdigest(),'stdout':r.stdout},indent=2))
