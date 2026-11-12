"""Single-peer experiment: retain pending unicast frame across unavailable AWDL slots.
Skips tail rotation in xos_send_unicast; original timer/wait calculation remains.
May cause head-of-line blocking across multiple peers: NOT a production fix.
"""
from pathlib import Path
import hashlib,struct,json,sys
base=Path(__file__).resolve().parent
src=base.parent.parent/'madrid-analysis/libxos.so'
b=bytearray(src.read_bytes())
assert hashlib.sha256(b).hexdigest()=='0a1b6401bcbde7d50f1e503b2f581940c6f40d4c3a701a1aa1a03ee04bbfffab'
off=0x14a48;target=0x14aec
assert struct.unpack_from('<I',b,off)[0]==0xb4000520
struct.pack_into('<I',b,off,0x14000000|((target-off)//4))
guard=int(sys.argv[1]) if len(sys.argv)>1 else 3
assert guard in (3,8,12)
if guard!=3:
 assert struct.unpack_from('<I',b,0x149bc)[0]==0x52800063
 struct.pack_into('<I',b,0x149bc,0x52800003|(guard<<5))
 outdir=base/('guard'+str(guard));outdir.mkdir(exist_ok=True)
else:outdir=base
out=outdir/'libxos.so';out.write_bytes(b)
print(json.dumps(dict(path=str(out),sha256=hashlib.sha256(b).hexdigest(),offset=hex(off),target=hex(target))))
