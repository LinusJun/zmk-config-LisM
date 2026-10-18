"""Permit per-position remapping, reject silent removal of custom firmware nodes.

This deliberately allows ONLY the eight layers' binding values to differ.
Whitespace/comments are immaterial. Layer ordering, custom behaviors, macros,
listeners, sensor bindings and compile-time defaults remain owned by firmware.
"""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'tests/fixtures/keymap_editor_baseline.keymap'
LAYER=re.compile(r'\b(default_layer|layer_\d+)\s*\{[^{}]*?\bbindings\s*=\s*<(.*?)>;',re.S)

def clean(text):
    return re.sub(r'//[^\n]*','',re.sub(r'/\*.*?\*/','',text,flags=re.S))

def tokens(text):
    return re.findall(r'"(?:\\.|[^"\\])*"|[A-Za-z_]\w*|0[xX][\da-fA-F]+|\d+|[^\s]',text)

def parse(text):
    text=clean(text)
    found=list(LAYER.finditer(text))
    expected=['default_layer']+[f'layer_{i}' for i in range(1,8)]
    if [m[1] for m in found]!=expected:
        raise ValueError('Keep all eight existing layers in order; layer numbers also control trackball modes.')
    for m in found:
        # A remap is a list of phandles/parameters, never a preprocessor or DTS
        # statement. In particular a hidden #define could change later,
        # supposedly protected sensor bindings after preprocessing.
        if re.search(r'[{};#"\'<>\\\[\]]',m[2]):
            raise ValueError(f'{m[1]} bindings contain a directive or structure injection')
        if not re.fullmatch(r'[&A-Za-z0-9_\s(),+*/|~^%!.:\-]*',m[2]):
            raise ValueError(f'{m[1]} contains unsupported binding syntax')
        depth=0
        for char in m[2]:
            if char=='(':depth+=1
            elif char==')':depth-=1
            if depth<0:raise ValueError('Unbalanced binding macro')
        if depth:raise ValueError('Unbalanced binding macro')
        bindings=re.findall(r'&[A-Za-z_]\w*[^&]*',m[2])
        if len(bindings)!=58:
            raise ValueError(f'{m[1]} has {len(bindings)} positions; expected58 including JP19, CirclePad and4waystick.')
    for m in reversed(found):
        text=text[:m.start(2)]+' EDITABLE_BINDINGS '+text[m.end(2):]
    return tokens(text)

def validate(text):
    if parse(text)!=parse(BASE.read_text()):
        raise ValueError('Editor changed protected keymap configuration. Restore custom behaviors/listeners/macros/defaults; only layer bindings are editable on this branch.')

if __name__=='__main__':
    validate((ROOT/'config/lism.keymap').read_text())
    print('PASS:8 layers x58 editable positions; custom keymap configuration preserved')
