"""Paired peer diagnostics with read-only per-socket Darwin telemetry."""
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
TF = HERE.parent
m = runpy.run_path(str(TF / 'run-three-findings.py'))
bench = runpy.run_path(str(TF.parent / 'owly-evaluation/houji-runner/bench.py'))
timeline = bench['load_tcp_timeline'](HERE / 'mac-tcp-info.dylib')
original = m['R']['run_flow_samples']

for name, associated in [('self', 0), ('remote', 1)]:
    folder = TF / 'runs' / ('peer-tcp-20261111-' + name)
    observations = []

    def observe(sample, moment):
        text = m['root']('cat /sys/module/qca_cld3_kiwi_v2/parameters/pt_rate_stats')
        observations.append(dict(sample=sample, moment=moment, rate_stats=text))
        (folder / 'direction-rate-snapshots.json').write_text(
            json.dumps(observations, indent=2) + '\n')

    def flow(*args, **kwargs):
        return original(*args, **kwargs, tcp_timeline=timeline, observe=observe)

    m['R']['run_flow_samples'] = flow
    print('CASE_START', name, flush=True)
    try:
        m['start'](TF / 'qca_cld3_kiwi_v2-three-findings.ko', folder,
                   'vht80-2ss-mcs9-sgi', dict(remote_assoc=associated,
                   remote_reo=0, fw_addba=0, tx_ampdu=0,
                   dynamic_rate=0, sta_coexist=0))
        m['sample'](folder, '2ss', 16, 'amsdu', 'off')
    finally:
        if (folder / 'session.json').exists():
            m['stop'](folder)
        print(m['ensure_restored']().splitlines()[0], flush=True)
    print('CASE_DONE', name, flush=True)
