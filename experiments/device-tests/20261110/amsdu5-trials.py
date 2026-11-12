import subprocess,shlex,time,json,sys,os
from pathlib import Path
R=Path(__file__).resolve().parent
D='/data/local/tmp/houji-buildtest-20261110'
def root(s):
 p=subprocess.run(['adb','-s',__import__("os").environ["ANDROID_SERIAL"],'shell','su -c '+shlex.quote(s)],capture_output=True,text=True,timeout=90)
 with (R/'rate-lifecycle.log').open('a') as f:f.write(time.strftime('%Y-%m-%d %H:%M:%S')+' '+s+'\n'+p.stdout+p.stderr)
 if p.returncode:raise RuntimeError(p.stdout+p.stderr)
 return p.stdout
root('''set -e
test ! -e /data/local/tmp/xos/native/saved.env
test ! -e /sys/module/houji_wonder_bridge
p=$(cat /data/local/tmp/xos/native/supervisor.pid)
case "$(tr '\\0' ' ' < /proc/$p/cmdline)" in *bridge-supervisor149.sh*) kill -TERM "$p";; *) echo unexpected_supervisor; exit 1;; esac
''')
try:
 for index,profile in enumerate(['lg','sg']):
  rate=7
  assert rate == 7
  for _ in range(20):
   if 'Wifi is connected to' in root('cmd wifi status | head -n 4'):break
   time.sleep(1)
  else:raise RuntimeError('Wi-Fi recovery not confirmed')
  time.sleep(6)
  name=f'amsdu5-{profile}-16m';radio=D+f'/radio-amsdu5-{profile}.sh';server=None
  try:
   root('sh -x '+radio+' enter')
   assert root('set -e; cat /sys/class/net/wonder0/mtu; ip link set dev wonder0 mtu 8192; cat /sys/class/net/wonder0/mtu').strip().endswith('8192')
   assert 'freq=5745' in root('cat /sys/module/qca_cld3_kiwi_v2/parameters/pt_activate')
   root('iw dev; cat /sys/module/qca_cld3_kiwi_v2/parameters/pt_vht80')
   tail=root('tail -8 '+D+'/state/xos.log');assert 'VHT80 NSS1 MCS7' in tail,tail
   (R/(name+'-before.txt')).write_text(root('cat /sys/module/qca_cld3_kiwi_v2/parameters/pt_rate_stats'))
   with (R/(name+'-server.log')).open('w') as f:
    server=subprocess.Popen(['python3',str(R/'start-probe.py')],stdout=f,stderr=subprocess.STDOUT)
    for _ in range(80):
     if 'READY ' in (R/(name+'-server.log')).read_text():break
     if server.poll() is not None:raise RuntimeError('Probe exited')
     time.sleep(.2)
    else:raise RuntimeError('Probe readiness timeout')
    subprocess.run(['python3',str(R/'hash-probe.py'),(R/'probe-ip.txt').read_text(),str(R/(name+'.json')),str(16*1024*1024)],check=True,timeout=110)
    assert server.wait(timeout=10)==0
   (R/(name+'-after.txt')).write_text(root('cat /sys/module/qca_cld3_kiwi_v2/parameters/pt_rate_stats'))
   (R/(name+'-inject.txt')).write_text(root('grep AMSDU '+D+'/state/xos.log'))
   print('RATE_PASS',name,flush=True)
  finally:
   if server and server.poll() is None:server.terminate();server.wait(timeout=5)
   root('sh '+radio+' restore')
finally:
 # Restart the previously accepted native MCS3/149 service only after restoration.
 root('''set -e
test ! -e /sys/module/houji_wonder_bridge
test ! -e /data/local/tmp/houji-buildtest-20261110/state/saved.env
test ! -e /data/local/tmp/xos/native/saved.env
/data/adb/ksu/bin/busybox setsid sh /data/local/tmp/houji-buildtest-20261110/bridge-supervisor149.sh </dev/null >>/data/local/tmp/houji-buildtest-20261110/native149-bridge.log 2>&1 &
''')
print('RATE_TRIALS_DONE_NATIVE_MCS3_RESTORED',flush=True)
