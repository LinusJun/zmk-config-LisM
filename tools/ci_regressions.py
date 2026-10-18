"""Run every current host test; do not overwrite deployment or device data."""
from pathlib import Path
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'ci-results'
OUT.mkdir(exist_ok=True)
subprocess.run([sys.executable,str(ROOT/'tools/keymap_guard.py')],check=True)
results=[]
for test in sorted((ROOT/'tests').glob('*_test.py')):
    result=subprocess.run([sys.executable,str(test)],cwd=ROOT,capture_output=True,text=True)
    (OUT/(test.stem+'.log')).write_text(result.stdout+result.stderr)
    results.append({'test':test.name,'returncode':result.returncode})
    print(('PASS: ' if result.returncode==0 else 'FAIL: ')+test.name,flush=True)
    if result.returncode: print((result.stdout+result.stderr)[-5000:],flush=True)
report={'program_count':len(results),'all_passed':all(r['returncode']==0 for r in results),'results':results}
(OUT/'regression-results.json').write_text(json.dumps(report,indent=2)+'\n')
sys.exit(0 if report['all_passed'] else 1)
