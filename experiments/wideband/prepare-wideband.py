#!/usr/bin/env python3
"""Isolated opt-in VHT80 experiment. No aggregation or lifecycle changes."""
from pathlib import Path
import shutil
import subprocess

R = (Path(__import__("os").environ["HOUJI_LEGACY_ROOT"]))
src = R / 'src/baseline/wlan'
dst = R / 'src/wideband/wlan'
if dst.exists():
    raise SystemExit('Refuse to overwrite existing experiment')
shutil.copytree(src, dst, symlinks=True)
rel = Path('qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c')
s = (src / rel).read_text()
def replace(old, new):
    global s
    assert s.count(old) == 1, (old, s.count(old))
    s = s.replace(old, new)

replace('#include "wma.h"', '''#include "wma.h"
#include <linux/moduleparam.h>

/* Load-time opt-in, restricted to the tested non-DFS 149-161 block.
 * Retain HT20 as the default and retain single-MPDU TX throughout.
 */
static bool pt_vht80;
module_param(pt_vht80, bool, 0444);
MODULE_PARM_DESC(pt_vht80, "Experimental VHT80 NSS1 on primary 149");

static bool hdd_pt_vht80(const struct wondertap_init_params *p)
{
    return p && pt_vht80 && p->channel.freq == 5745 &&
        p->channel.bandwidth == WONDERTAP_RATE_BW_80 &&
        p->tx_rate.bw == WONDERTAP_RATE_BW_80 &&
        p->tx_rate.preamble == WONDERTAP_RATE_PREAMBLE_VHT;
}
''')
replace('if (params->channel.bandwidth != WONDERTAP_RATE_BW_20 ||',
        'if ((!hdd_pt_vht80(params) &&\n\t    (params->channel.bandwidth != WONDERTAP_RATE_BW_20 ||')
replace('params->tx_rate.preamble != WONDERTAP_RATE_PREAMBLE_HT ||',
        'params->tx_rate.preamble != WONDERTAP_RATE_PREAMBLE_HT)) ||')
replace('return hdd_pt_control_channel(hdd, params->channel.freq);', '''if (hdd_pt_vht80(params)) {
        u32 freq;
        int ret;
        for (freq = 5745; freq <= 5805; freq += 20) {
            ret = hdd_pt_control_channel(hdd, freq);
            if (ret)
                return ret;
        }
    }
    return hdd_pt_control_channel(hdd, params->channel.freq);''')
replace('struct ch_params channel = { .ch_width = CH_WIDTH_20MHZ };',
        '''enum phy_ch_width width = hdd_pt_vht80(params) ?
        CH_WIDTH_80MHZ : CH_WIDTH_20MHZ;
    struct ch_params channel = { .ch_width = width };''')
replace(' CH_WIDTH_20MHZ);', ' width);')
replace('if (channel.ch_width != CH_WIDTH_20MHZ)', 'if (channel.ch_width != width)')
replace('eCSR_DOT11_MODE_11n);', 'hdd_pt_vht80(params) ? eCSR_DOT11_MODE_11ac : eCSR_DOT11_MODE_11n);')
replace('/* SME fills from global PHY policy; this PT profile is fixed HT. */\n\trequest.dot11mode = MLME_DOT11_MODE_11N;',
        '''/* Do not let global PHY policy override the explicit profile. */
    request.dot11mode = hdd_pt_vht80(params) ?
        MLME_DOT11_MODE_11AC : MLME_DOT11_MODE_11N;''')
replace('hdd_assemble_rate_code(WMI_RATE_PREAMBLE_HT, 0,',
        'hdd_assemble_rate_code(hdd_pt_vht80(params) ?\n        WMI_RATE_PREAMBLE_VHT : WMI_RATE_PREAMBLE_HT, 0,')
replace('wmi_vdev_param_chwidth, 0);',
        'wmi_vdev_param_chwidth, hdd_pt_vht80(params) ? 2 : 0);')
replace('hdd_convert_ch_width_to_cdp_peer_bw(CH_WIDTH_20MHZ);',
        'hdd_convert_ch_width_to_cdp_peer_bw(hdd_pt_vht80(params) ?\n        CH_WIDTH_80MHZ : CH_WIDTH_20MHZ);')
(dst / rel).write_text(s)
patch = subprocess.run(['diff', '-u', str(src / rel), str(dst / rel)], capture_output=True, text=True)
assert patch.returncode == 1
(R / 'wideband/control.patch').write_text(patch.stdout)
print('WIDEBAND_SOURCE_READY')
