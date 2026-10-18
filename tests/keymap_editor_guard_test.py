"""Remapping is permitted; missing positions or hidden configuration are not."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from keymap_guard import validate,BASE

text=BASE.read_text()
validate(text)
validate(text.replace('&kp Q           &kp W','&kp A           &kp W',1))
validate(text.replace('&kp UP  &kp RIGHT  &kp DOWN  &kp LEFT','&kp HOME  &kp END  &kp PAGE_UP  &kp PAGE_DOWN',1))
validate(text.replace('&stick_scroll MOVE_Y(-LISM_4WAY_SCROLL_SPEED)','&kp UP',1))
validate('// Formatting/comment roundtrip\n'+text.replace('bindings = <','bindings\n =\n <'))
for bad in (text.replace('tapping-term-ms = <200>','tapping-term-ms = <300>'),
            text.replace('device = <&stick_scroll>;',''),
            text.replace('&kp UP  &kp RIGHT  &kp DOWN  &kp LEFT','&kp UP  &kp RIGHT  &kp DOWN',1),
            text.replace('layer_2 {','layer_8 {'),
            text.replace('/* Base: Up Right Down Left. */','\n#undef SCRL_DOWN\n#define SCRL_DOWN SCRL_UP\n'),
            text.replace('&kp Q           &kp W','&kp Q; injected = 1; &kp W',1),
            text.replace('x-input-code = <INPUT_REL_HWHEEL>;','')):
    try: validate(bad)
    except ValueError:pass
    else:raise AssertionError('Destructive editor change accepted')
print('PASS:normal key /4way/scroll replacement accepted; hidden config loss rejected')
