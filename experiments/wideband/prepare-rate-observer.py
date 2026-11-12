#!/usr/bin/env python3
from pathlib import Path
import shutil
import subprocess
R = (Path(__import__("os").environ["HOUJI_LEGACY_ROOT"]))
D = R / 'src/wideband/wlan/qca-wifi-host-cmn/dp/wifi3.0'
p = D / 'dp_tx.c'
s = p.read_text()
assert 'dp_pt_rate_observer.h' not in s
anchor = '/* No Ethernet statistics, peer lookup or fabricated ACK callback. */'
assert s.count(anchor) == 1
s = s.replace(anchor, anchor + '\n\t\tdp_pt_rate_end(ts);')
anchor = '/* Flag to skip CCE classify'
assert s.count(anchor) == 1
s = s.replace(anchor, '#include "dp_pt_rate_observer.h"\n\n' + anchor)
p.write_text(s)
shutil.copyfile(R/'wideband/dp_pt_rate_observer.h', D/'dp_pt_rate_observer.h')
base=R/'src/baseline/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_tx.c'
diff=subprocess.run(['diff','-u',str(base),str(p)],capture_output=True,text=True)
assert diff.returncode==1
(R/'wideband/rate-observer.patch').write_text(diff.stdout)
