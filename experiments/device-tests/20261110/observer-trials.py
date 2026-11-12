import subprocess,shlex,time,json
from pathlib import Path
R=Path(__file__).resolve().parent; W=R.parents[1]; obs=W/'tx-observer'
def root(s):
 p=subprocess.run(['adb','-s',__import__("os").environ["ANDROID_SERIAL"],'shell','su -c '+shlex.quote(s)],capture_output=True,text=True,timeout=90)
 with (R/'observer-lifecycle.log').open('a') as f:f.write(time.strftime('%Y-%m-%d %H:%M:%S')+' '+s+'\n'+p.stdout+p.stderr)
 if p.returncode:raise RuntimeError(p.stdout+p.stderr)
 return p.stdout
for name in ['observer-off','observer']:
 for _ in range(20):
  if 'Wifi is connected to' in root('cmd wifi status'):break
  time.sleep(1)
 else:raise RuntimeError('Stock Wi-Fi not connected')
 time.sleep(8)
 radio='/data/local/tmp/houji-buildtest-20261110/radio-'+name+'.sh'
 server=None;capture=None
 try:
  root('sh -x '+radio+' enter')
  stats=root('cat /sys/module/qca_cld3_kiwi_v2/parameters/pt_tx_stats')
  assert ('enabled=0' if name=='observer-off' else 'enabled=1') in stats
  (R/(name+'-before.txt')).write_text(stats)
  audit=json.loads((W/'artifacts/houji-tx-observer-20261110/reports/module-comparison.json').read_text())
  assert root('sha256sum /sys/module/qca_cld3_kiwi_v2/notes/.note.gnu.build-id').split()[0]==audit['observer']['alloc']['.note.gnu.build-id']['sha256']
  if name=='observer':capture=subprocess.Popen(['python3',str(obs/'collect-observer.py'),'--serial',__import__("os").environ["ANDROID_SERIAL"],'--seconds','25','--output',str(R/'observer-capture.jsonl')])
  with (R/(name+'-server.log')).open('w') as f:
   server=subprocess.Popen(['python3',str(R/'start-probe.py')],stdout=f,stderr=subprocess.STDOUT)
   for _ in range(80):
    if 'READY ' in (R/(name+'-server.log')).read_text():break
    if server.poll() is not None:raise RuntimeError('Probe exited early')
    time.sleep(.2)
   else:raise RuntimeError('Probe not ready')
   subprocess.run(['python3',str(R/'hash-probe.py'),(R/'probe-ip.txt').read_text(),str(R/(name+'-link.json'))],check=True,timeout=110)
   assert server.wait(timeout=10)==0
  if capture:assert capture.wait(timeout=35)==0
  (R/(name+'-after.txt')).write_text(root('cat /sys/module/qca_cld3_kiwi_v2/parameters/pt_tx_stats'))
  print('TRIAL_PASS',name,flush=True)
 finally:
  if capture and capture.poll() is None:capture.terminate();capture.wait(timeout=5)
  if server and server.poll() is None:server.terminate();server.wait(timeout=5)
  root('sh '+radio+' restore')
for _ in range(20):
 if 'Wifi is connected to' in root('cmd wifi status'):break
 time.sleep(1)
else:raise RuntimeError('Final Wi-Fi recovery failed')
print('OBSERVER_TRIALS_AND_WIFI_RESTORE_PASS',flush=True)
