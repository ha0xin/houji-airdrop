import subprocess,shlex,json,time
from pathlib import Path
R=Path(__file__).resolve().parent
j=json.loads((R.parents[1]/'artifacts/houji-baseline-20261110/reports/module-comparison.json').read_text())
def cmd(s,timeout=70):
 p=subprocess.run(['adb','-s',__import__("os").environ["ANDROID_SERIAL"],'shell','su -c '+shlex.quote(s)],capture_output=True,text=True,timeout=timeout)
 with (R/'baseline-lifecycle.log').open('a') as f:f.write(time.strftime('%Y-%m-%d %H:%M:%S')+' '+s+'\n'+p.stdout+p.stderr)
 if p.returncode:raise RuntimeError(p.stdout+p.stderr)
 return p.stdout
radio='/data/local/tmp/houji-buildtest-20261110/radio-baseline.sh'
for i in range(1,4):
 try:
  if i>1:cmd('sh '+radio+' enter')
  assert 'XOS_READY' in cmd('cat /data/local/tmp/houji-buildtest-20261110/state/status')
  assert 'scope link' in cmd('ip -6 addr show xos0')
  for name,v in j.items():
   h=cmd('sha256sum /sys/module/'+name+'/notes/.note.gnu.build-id').split()[0]
   assert h==v['rebuilt']['alloc']['.note.gnu.build-id']['sha256'],name
  time.sleep(2)
 finally:cmd('sh '+radio+' restore')
 for _ in range(15):
  status=cmd('cmd wifi status')
  if 'Wifi is connected to' in status:break
  time.sleep(1)
 else:raise RuntimeError('Wi-Fi did not reconnect')
 print('BASELINE_LIFECYCLE_PASS',i,flush=True)
