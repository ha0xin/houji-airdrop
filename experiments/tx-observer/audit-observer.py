#!/usr/bin/env python3
import json,runpy
from pathlib import Path
R=(Path(__import__("os").environ["HOUJI_LEGACY_ROOT"]))
inspect=runpy.run_path(str(R/'host/audit-modules.py'))['inspect']
a=inspect(R/'src/baseline/wlan/qcacld-3.0/qca_cld3_kiwi_v2.ko')
b=inspect(R/'src/observer/wlan/qcacld-3.0/qca_cld3_kiwi_v2.ko')
known={}
for p in [R/'upstream/kernel/configs/kernel.symvers',R/'out/wlan-build-extra.symvers']:
 for line in p.read_text().splitlines():
  crc,symbol,*_=line.split();known[symbol]=f'0x{int(crc,16):08x}'
added=sorted(b['versions'].keys()-a['versions'].keys());removed=sorted(a['versions'].keys()-b['versions'].keys())
checks=dict(machine=b['machine']==a['machine'],vermagic=b['info'].get('vermagic')==a['info'].get('vermagic'),depends=b['info'].get('depends')==a['info'].get('depends'),export_crcs=b['exports']==a['exports'],existing_import_crcs=all(b['versions'].get(s)==crc for s,crc in a['versions'].items()),all_import_crcs_resolve=all(known.get(s)==crc for s,crc in b['versions'].items()),module_size=b['module_size']==a['module_size'],module_relocations=b['module_relocations']==a['module_relocations'],code_changed=b['executable']!=a['executable'],observer_bool_param='pt_tx_observe:bool' in b['info'].get('parmtype',[]))
result=dict(checks=checks,offline_checks_pass=all(checks.values()),added_imports=added,removed_imports=removed,baseline=a,observer=b,device_validation='not performed')
out=R/'artifacts/observer';out.mkdir(exist_ok=True)
(out/'module-comparison.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ['baseline','observer']},indent=2))
assert result['offline_checks_pass']
