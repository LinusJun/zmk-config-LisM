"""Actual C replay of the 1309c8f live recording; signal bursts aren't ground truth.

This is a regression fixture, not independent accuracy validation after tuning.
No device access or HID output. Optional TRACE=1 prints per-frame state.
"""
from pathlib import Path
import json
import hashlib
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / 'tests/fixtures/xiao147_raw_live_1309c8f.json').read_text())
frames = fixture['frames']
assert len(frames) == 2224 and all(len(r) == 13 for r in frames)
assert all(0 < b[0] - a[0] <= 31 for a, b in zip(frames, frames[1:]))
assert fixture['source_sha256'] == '8b23edd5420269a65eff2fbd6cc14a4adcf68dd2e3f74178bb23bb6464a94b7e'
assert hashlib.sha256(json.dumps(frames, separators=(',', ':')).encode()).hexdigest() == '736ed7649b61ae9b7e34d5f3f01a85513fd9f7b93606c8d6fffb9ee2f895dcaf'
rows = ',\n'.join('{' + ','.join(map(str, r)) + '}' for r in frames)
harness = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "xiao147_raw_gestures.h"
#include "xiao147_orientation.h"
static const int32_t rows[][13] = {__ROWS__};
int main(void) {
    struct xiao147_raw_baseline b;
    struct xiao147_shadow_gesture g;
    xiao147_raw_baseline_begin(&b, true);
    xiao147_shadow_gesture_init(&g);
    unsigned taps=0, pan_pos=0, pan_neg=0, wheel_pos=0, wheel_neg=0;
    for(unsigned i=0;i<sizeof(rows)/sizeof(rows[0]);i++) {
        const int32_t *r=rows[i];
        bool armed=xiao147_raw_baseline_observe(&b,r+1,12,true,r[0]);
        struct xiao147_raw_profile p;
        bool valid=xiao147_raw_profile_evaluate(r+1,12,b.values,&p);
        struct xiao147_xy xy=xiao147_touch_to_logical(XIAO147_USB_LEFT,p.native_short_x,p.native_long_y);
        struct xiao147_shadow_result out=xiao147_shadow_gesture_observe_profile(
            &g,&p,valid&&armed,xy.x,xy.y,0,r[0]);
        taps+=out.candidate_right_tap;
        pan_pos+=out.candidate_hwheel>0; pan_neg+=out.candidate_hwheel<0;
        wheel_pos+=out.candidate_wheel>0; wheel_neg+=out.candidate_wheel<0;
        if(out.candidate_right_tap||out.candidate_hwheel||out.candidate_wheel)
            printf("OUTPUT t=%d tap=%d wheel=%d pan=%d\n",r[0],out.candidate_right_tap,out.candidate_wheel,out.candidate_hwheel);
        if(getenv("TRACE") && r[0]>=27000 && r[0]<=42000 && (p.hypothesis!=0||g.active))
            printf("TRACE t=%d shape=%d xy=%d,%d lobe=%u,%u;%u,%u valley=%u blocked=%d qualify=%d moved=%d tap_disq=%d tail=%d cancel=%d\n",
                r[0],p.hypothesis,xy.x,xy.y,p.lobes[0].native_short_x,p.lobes[0].native_long_y,
                p.lobes[1].native_short_x,p.lobes[1].native_long_y,p.valley_permille,
                g.blocked,g.qualified_two,g.moving,g.tap_disqualified,g.tail_one,out.cancelled);
        /* Quiet segments before/after the human action bursts emit nothing. */
        if(r[0]<27000 || r[0]>42000)
            assert(!out.candidate_right_tap&&!out.candidate_hwheel&&!out.candidate_wheel);
    }
    printf("LIVE REPLAY taps=%u pan_positive_frames=%u pan_negative_frames=%u wheel_positive_frames=%u wheel_negative_frames=%u\n",taps,pan_pos,pan_neg,wheel_pos,wheel_neg);
    /* Pinned regression output, NOT physical success rate. Changing these
     * requires explicit evidence review, not automatically rebaselining. */
    assert(taps==1 && pan_pos==2 && pan_neg==3 && wheel_pos==3 && wheel_neg==6);
    return 0;
}
'''.replace('__ROWS__', rows)
with tempfile.TemporaryDirectory() as d:
    source = Path(d) / 'replay.c'
    source.write_text(harness)
    binary = Path(d) / 'replay'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I', str(root/'src'), str(source),
                    str(root/'src/xiao147_raw_gestures.c'), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, env=os.environ)
