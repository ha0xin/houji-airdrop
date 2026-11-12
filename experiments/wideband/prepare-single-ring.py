#!/usr/bin/env python3
from pathlib import Path
import subprocess
R=(Path(__import__("os").environ["HOUJI_LEGACY_ROOT"]))
D=R/'src/wideband/wlan/qca-wifi-host-cmn/dp/wifi3.0'
p=D/'dp_pt_rate_observer.h';s=p.read_text()
assert 'pt_single_ring' not in s
s=s.replace('static bool pt_rate_observe;', '''static bool pt_single_ring;
module_param(pt_single_ring, bool, 0444);
static bool pt_rate_observe;''')
s=s.replace('status[17];','status[17], tx_ring[8];')
s=s.replace('/* Only called with a parsed TQM', '''static void dp_pt_ring_begin(unsigned int ring)
{
 unsigned long flags;
 if(!pt_rate_observe || ring>=8)return;
 spin_lock_irqsave(&pt_rate_lock,flags);
 pt_rates.tx_ring[ring]++;
 spin_unlock_irqrestore(&pt_rate_lock,flags);
}
/* Only called with a parsed TQM''')
s=s.replace('RPA(status, 17);','RPA(status, 17); RPA(tx_ring, 8);')
p.write_text(s)
p=D/'dp_tx.c';s=p.read_text()
old='dp_tx_get_queue(vdev, nbuf, &msdu_info.tx_queue);\n\t/* This profile uses only ordinary data rings and descriptor pools. */'
assert s.count(old)==1
s=s.replace(old,'''dp_tx_get_queue(vdev, nbuf, &msdu_info.tx_queue);
    if (pt_single_ring) {
        msdu_info.tx_queue.ring_id = 0;
        msdu_info.tx_queue.desc_pool_id = 0;
    }
    /* This profile uses only ordinary data rings and descriptor pools. */''')
anchor="/* The caller's mac80211 cb must not be interpreted as QDF TX flags."
assert s.count(anchor)==1
s=s.replace(anchor,'dp_pt_ring_begin(msdu_info.tx_queue.ring_id);\n\n\t'+anchor)
p.write_text(s)
base=R/'src/baseline/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_tx.c'
diff=subprocess.run(['diff','-u',str(base),str(p)],capture_output=True,text=True)
assert diff.returncode==1
(R/'wideband/single-ring.patch').write_text(diff.stdout)
