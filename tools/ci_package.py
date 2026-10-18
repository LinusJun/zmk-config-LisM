"""Reject missing/mixed builds and produce one complete downloadable artifact."""
from pathlib import Path
import hashlib
import json
import os
import shutil

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'ci-artifacts'
records=[]
for target in ('dongle','left','right','jp19','circlepad','reset'):
    record=json.loads((OUT/(target+'-build-record.json')).read_text())
    path=OUT/record['file']
    assert not Path(record['file']).is_absolute() and '..' not in Path(record['file']).parts
    assert path.is_file() and not path.is_symlink()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256']
    assert record['configuration_checked'] is True and record['target']==target
    assert record['source_commit']==os.environ['GITHUB_SHA']
    assert record['keymap_sha256']==hashlib.sha256((ROOT/'config/lism.keymap').read_bytes()).hexdigest()
    records.append(record)
manifest={'source_commit':os.environ['GITHUB_SHA'],'branch':'keymap-editor','targets':records,
  'warning':'Normal updates must NOT flash reset. Never clears existing bonds as part of the build.',
  'hardware_retested':False}
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
shutil.copy2(ROOT/'KEYMAP_EDITOR.md',OUT/'先读我_改键与刷写.md')
print('PASS: all six targets match source commit/keymap and verified UF2 hashes')
