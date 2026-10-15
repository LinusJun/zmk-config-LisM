"""Replay complete recorded sessions through the automatic one/two arbiter.
Development recordings, not independent validation of physical finger counts.
"""
from pathlib import Path
import base64
import hashlib
import json
import subprocess
import tempfile
import zlib
root=Path(__file__).resolve().parents[1]
source=r'''
#include <stdio.h>
#include "xiao147_auto_touch.h"
static unsigned left,right,wp,wn,hp,hn,moves;
static bool output(void *ctx,struct xiao147_auto_output o){
 (void)ctx;left+=o.left;right+=o.right;wp+=o.wheel>0;wn+=o.wheel<0;
 hp+=o.pan>0;hn+=o.pan<0;moves+=o.dx!=0||o.dy!=0;
 if(o.left||o.right||o.wheel||o.pan)printf("OUTPUT %d %d %d %d\n",o.left,o.right,o.wheel,o.pan);
 return true;
}
int main(void){
 struct xiao147_auto_touch s;const struct xiao147_auto_ops ops={.output=output};
 xiao147_auto_init(&s,&ops,NULL);s.allowed=true;
 xiao147_raw_baseline_begin(&s.baseline,true);
 int32_t values[12];unsigned now;
 while(scanf("%u",&now)==1){
  for(int i=0;i<12;i++)if(scanf("%d",&values[i])!=1)return 2;
  s.now=now;s.capture.ops.sample(&s,XIAO147_RAW_DIFF,12,values,now);
 }
 printf("TOTAL %u %u %u %u %u %u %u\n",left,right,wp,wn,hp,hn,moves);
}
'''
with tempfile.TemporaryDirectory(prefix='xiao147-auto-replay-') as d:
    c=Path(d)/'replay.c';c.write_text(source);exe=c.with_suffix('')
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                    '-I'+str(root/'src'),str(c),*[str(root/'src'/f) for f in
                    ('xiao147_auto_touch.c','xiao147_raw_capture.c','xiao147_raw_gestures.c')],
                    '-o',str(exe)],check=True)
    fixture=json.loads((root/'tests/fixtures/xiao147_raw_cc970ac.json').read_text())
    for scene in fixture['scenes']:
        payload=zlib.decompress(base64.b64decode(scene['zlib_base64']))
        assert hashlib.sha256(payload).hexdigest()==scene['frames_sha256']
        frames=json.loads(payload)
        result=subprocess.run([str(exe)],input='\n'.join(' '.join(map(str,r)) for r in frames),
                              text=True,capture_output=True,check=True)
        print(scene['id'],result.stdout,flush=True)
        totals=list(map(int,result.stdout.split('TOTAL ')[1].strip().split()))
        assert totals=={'click_only':[0,17,0,0,0,0,0],
                       'independent_scroll':[0,0,8,9,7,7,0],
                       'mixed':[0,2,4,5,6,5,0]}[scene['id']]
