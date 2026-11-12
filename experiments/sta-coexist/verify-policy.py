#!/usr/bin/env python3
"""Verify transplanted table entries against both independent source trees."""
from pathlib import Path
import json
import re
import runpy

HERE = Path(__file__).resolve().parent
port = runpy.run_path(str(HERE / 'port-policy.py'))

def leaves(text):
    result = {}
    for n in port['nodes'](text):
        if n['key'][0].startswith(('fourth_', 'fifth_')):
            continue
        if '{' in text[n['start'] + 1:n['end']]:
            continue
        assert n['key'] not in result, n['key']
        result[n['key']] = re.sub(r'\s+', '', text[n['start']:n['end'] + 1])
    return result

receipts = []
for candidate in sorted((port['OUT'] / 'src').glob('*tables*.h')):
    before = leaves((port['BASE'] / 'src' / candidate.name).read_text())
    after = leaves(candidate.read_text())
    samsung = leaves(port['mobile_source']((port['SAMSUNG'] / 'src' / candidate.name).read_text()))
    assert all(after.get(k) == v for k, v in before.items()), candidate.name
    added = {k: v for k, v in after.items() if k not in before}
    assert added and all(any('PASSTHRU' in p for p in k) for k in added)
    assert all(samsung.get(k) == v for k, v in added.items()), candidate.name
    receipts.append(dict(file=candidate.name, unchanged_entries=len(before),
                         samsung_passthru_entries=len(added)))
print(json.dumps(dict(verified=True, tables=receipts), indent=2))
