#!/usr/bin/env python3
"""Read-only, explicit-device capture. Does not start Wi-Fi or load modules."""
import argparse,json,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--serial',required=True);p.add_argument('--seconds',type=int,default=60);p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
if not 1<=a.seconds<=600:p.error('seconds must be between 1 and 600')
cmd=['adb','-s',a.serial,'shell',"su -c 'cat /sys/module/qca_cld3_kiwi_v2/parameters/pt_tx_stats'"]
with a.output.open('x') as f:
 for i in range(a.seconds+1):
  start=time.monotonic();r=subprocess.run(cmd,capture_output=True,text=True,timeout=8)
  if r.returncode or not r.stdout.startswith('schema=1 enabled=1 '):
   raise SystemExit('Observer unavailable/disabled; capture stopped without changing device state: '+r.stderr.strip())
  f.write(json.dumps({'host_monotonic':start,'stats':r.stdout})+'\n');f.flush()
  if i<a.seconds:time.sleep(max(0,1-(time.monotonic()-start)))
print(a.output)
