import subprocess,shlex,re,time
from pathlib import Path
r=Path(__file__).resolve().parent
for _ in range(50):
 raw=subprocess.check_output(['adb','-s',__import__("os").environ["ANDROID_SERIAL"],'shell','ip -6 -o addr show dev xos0 scope link'],text=True)
 if 'inet6 fe80:' in raw and 'tentative' not in raw and 'dadfailed' not in raw:break
 time.sleep(.2)
else:raise RuntimeError('IPv6 DAD not ready')
ip=re.search(r'inet6 (fe80:[0-9a-f:]+)/',raw).group(1);(r/'probe-ip.txt').write_text(ip)
cmd=f'''set -e
for prio in 10980 10981 10982; do while ip -6 rule del priority "$prio" 2>/dev/null; do :; done; done
ip -6 route replace fe80::/64 dev xos0 table 10074
ip -6 route replace ff00::/8 dev xos0 table 10074
ip -6 rule add priority 10980 to fe80::/64 oif xos0 uidrange 0-0 lookup 10074
ip -6 rule add priority 10981 to ff00::/8 oif xos0 uidrange 0-0 lookup 10074
ip -6 rule add priority 10982 from {ip}/128 lookup 10074
/data/local/tmp/houji-buildtest-20261110/hashprobe
'''
p=subprocess.run(['adb','-s',__import__("os").environ["ANDROID_SERIAL"],'shell','su -c '+shlex.quote(cmd)])
raise SystemExit(p.returncode)
