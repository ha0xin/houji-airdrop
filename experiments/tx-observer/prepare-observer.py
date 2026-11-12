#!/usr/bin/env python3
from pathlib import Path
import shutil,subprocess
R=(Path(__import__("os").environ["HOUJI_LEGACY_ROOT"])); src=R/'src/baseline/wlan'; dst=R/'src/observer/wlan'; here=Path(__file__).resolve().parent
if dst.exists():
 assert (dst/'qca-wifi-host-cmn/dp/wifi3.0/dp_tx.c').read_bytes()==(src/'qca-wifi-host-cmn/dp/wifi3.0/dp_tx.c').read_bytes(), 'Refuse to overwrite changed observer tree'
# Isolated copy preserves baseline. Changed build paths can invalidate cached objects.
if not dst.exists():shutil.copytree(src,dst,symlinks=True)
p=dst/'qca-wifi-host-cmn/dp/wifi3.0/dp_tx.c';s=p.read_text()
def replace(old,new):
 global s
 assert s.count(old)==1,(old[:80],s.count(old));s=s.replace(old,new)
replace('/* Flag to skip CCE classify', '#include "dp_pt_tx_observer.h"\n\n/* Flag to skip CCE classify')
replace('uint8_t comp_status = 0;\n', 'uint8_t comp_status = 0;\n\n\tdp_pto_end(tx_desc, NULL);\n')
replace('dp_tx_update_mcast_param(peer_id, &htt_tcl_metadata, vdev, msdu_info);', 'dp_tx_update_mcast_param(peer_id, &htt_tcl_metadata, vdev, msdu_info);\n\tdp_pto_begin(tx_desc, nbuf);')
replace('dp_tx_send_passthru_internal(struct cdp_soc_t *soc_hdl', 'dp_tx_send_passthru_impl(struct cdp_soc_t *soc_hdl')
replace('qdf_nbuf_t dp_tx_send_passthru(struct cdp_soc_t *soc_hdl', '''static qdf_nbuf_t
dp_tx_send_passthru_internal(struct cdp_soc_t *soc_hdl, uint8_t vdev_id,
                            qdf_nbuf_t nbuf, struct dp_vdev *expected)
{
 qdf_nbuf_t result;
 dp_pto_call(false, false);
 result = dp_tx_send_passthru_impl(soc_hdl, vdev_id, nbuf, expected);
 dp_pto_call(true, result != NULL);
 return result;
}

qdf_nbuf_t dp_tx_send_passthru(struct cdp_soc_t *soc_hdl''')
replace('/* No Ethernet statistics, peer lookup or fabricated ACK callback. */','''/* No Ethernet statistics, peer lookup or fabricated ACK callback. */
		dp_pto_end(tx_desc, ts);''')
p.write_text(s)
for name in ['pt_tx_observer_core.h','dp_pt_tx_observer.h']:shutil.copyfile(here/name,p.parent/name)
patch=subprocess.run(['diff','-u',str(src/'qca-wifi-host-cmn/dp/wifi3.0/dp_tx.c'),str(p)],capture_output=True,text=True)
assert patch.returncode==1
(here/'dp_tx.patch').write_text(patch.stdout)
(dst/'.observer-ready').write_text('schema=1\n')
print('OBSERVER_SOURCE_READY')
