"""Replay three complete captures through actual C, never device/HID access.

These recordings are development evidence, NOT independent accuracy validation.
The operator reported missed taps but did not count total physical taps.
TRACE=1 prints every non-quiet frame for review of changed decisions.
"""
from pathlib import Path
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import zlib

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root/'tests/fixtures/xiao147_raw_cc970ac.json').read_text())
harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include "xiao147_raw_gestures.h"
#include "xiao147_orientation.h"
int main(void) {
    struct xiao147_raw_baseline b;
    struct xiao147_shadow_gesture g;
    xiao147_raw_baseline_begin(&b,true);
    xiao147_shadow_gesture_init(&g);
    int32_t r[13];
    unsigned tap=0,wp=0,wn=0,hp=0,hn=0;
    while(scanf("%d", &r[0])==1) {
        for(unsigned i=1;i<13;i++) if(scanf("%d",&r[i])!=1) return 2;
        bool armed=xiao147_raw_baseline_observe(&b,r+1,12,true,r[0]);
        struct xiao147_raw_profile p;
        bool valid=xiao147_raw_profile_evaluate(r+1,12,b.values,&p);
        struct xiao147_xy xy=xiao147_touch_to_logical(XIAO147_USB_LEFT,p.native_short_x,p.native_long_y);
        struct xiao147_shadow_result o=xiao147_shadow_gesture_observe_profile(&g,&p,valid&&armed,xy.x,xy.y,0,r[0]);
        tap+=o.candidate_right_tap;wp+=o.candidate_wheel>0;wn+=o.candidate_wheel<0;
        hp+=o.candidate_hwheel>0;hn+=o.candidate_hwheel<0;
        if(o.candidate_right_tap||o.candidate_wheel||o.candidate_hwheel)
            printf("OUTPUT %d %d %d %d\n",r[0],o.candidate_right_tap,o.candidate_wheel,o.candidate_hwheel);
        if(getenv("TRACE")&&(p.hypothesis!=0||g.active))
            printf("TRACE %d shape=%d qualified=%d blocked=%d disq=%d fade=%d moving=%d cancel=%d\n",r[0],p.hypothesis,g.qualified_two,g.blocked,g.tap_disqualified,g.fading_tail,g.moving,o.cancelled);
    }
    printf("TOTAL %u %u %u %u %u\n",tap,wp,wn,hp,hn);
}
'''
with tempfile.TemporaryDirectory() as directory:
    folder=Path(directory)
    source=folder/'replay.c'
    source.write_text(harness)
    binary=folder/'replay'
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',
                    '-fsanitize=address,undefined','-fno-omit-frame-pointer',
                    '-I',str(root/'src'),str(source),str(root/'src/xiao147_raw_gestures.c'),
                    '-o',str(binary)],check=True)
    # Ablation disables only the new fixed-support rescue, retaining every
    # existing guard. It must reproduce the captured cc970ac decisions.
    driver=(root/'src/xiao147_raw_gestures.c').read_text()
    marker='    if (!gesture->association_valid || gesture->moving || gesture->tap_disqualified ||'
    assert driver.count(marker)==1
    old=folder/'without_fading.c'
    old.write_text(driver.replace(marker,'    if (true) { return false; }\n'+marker))
    old_binary=folder/'old'
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',
                    '-fsanitize=address,undefined','-fno-omit-frame-pointer',
                    '-I',str(root/'src'),str(source),str(old),'-o',str(old_binary)],check=True)
    expected={'click_only':([14,0,0,0,0],[17,0,0,0,0]),
              'independent_scroll':([0,8,9,7,7],[0,8,9,7,7]),
              'mixed':([2,4,5,6,5],[2,4,5,6,5])}
    for scene in fixture['scenes']:
        payload=zlib.decompress(base64.b64decode(scene['zlib_base64']))
        assert hashlib.sha256(payload).hexdigest()==scene['frames_sha256']
        frames=json.loads(payload)
        assert len(frames)==scene['frames'] and all(len(row)==13 for row in frames)
        assert all(0<b[0]-a[0]<=31 for a,b in zip(frames,frames[1:]))
        rows='\n'.join(' '.join(map(str,row)) for row in frames)
        result=subprocess.run([str(binary)],input=rows,text=True,capture_output=True,check=True)
        previous=subprocess.run([str(old_binary)],input=rows,text=True,capture_output=True,check=True)
        def outputs(text):
            return [tuple(map(int,line.split()[1:])) for line in text.splitlines() if line.startswith('OUTPUT ')]
        def totals(text):
            return list(map(int,next(line for line in text.splitlines() if line.startswith('TOTAL ')).split()[1:]))
        assert totals(previous.stdout)==expected[scene['id']][0]
        assert totals(result.stdout)==expected[scene['id']][1]
        before,after=outputs(previous.stdout),outputs(result.stdout)
        assert [r for r in after if r[2] or r[3]]==[r for r in before if r[2] or r[3]]
        assert set(before)<=set(after) # No previously accepted click was lost.
        added=set(after)-set(before)
        assert added==({(21216,1,0,0),(22150,1,0,0),(27874,1,0,0)}
                       if scene['id']=='click_only' else set())
        print(scene['id'],result.stdout,flush=True)
