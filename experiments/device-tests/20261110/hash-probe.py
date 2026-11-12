import socket,struct,time,json,sys,hashlib
rows=[];size=int(sys.argv[3]) if len(sys.argv)>3 else 2*1024*1024
payload=(bytes(range(256))*(size//256+1))[:size];expected=hashlib.sha256(payload).digest()
for mode in [0,1]:
 s=socket.socket(socket.AF_INET6);s.settimeout(45);s.setsockopt(socket.SOL_SOCKET,0x1104,1);s.setsockopt(socket.IPPROTO_IPV6,125,socket.if_nametoindex('awdl0'))
 s.connect((sys.argv[1],18870,0,socket.if_nametoindex('awdl0')));t=time.monotonic();s.sendall(struct.pack('>ii',mode,size))
 if mode==0:
  s.sendall(payload);ack=b''
  while len(ack)<32:
   x=s.recv(32-len(ack))
   if not x:raise EOFError()
   ack+=x
  assert ack==expected
 else:
  n=0;md=hashlib.sha256()
  while n<size:
   b=s.recv(min(65536,size-n))
   if not b:raise EOFError()
   n+=len(b);md.update(b)
  assert md.digest()==expected;s.sendall(md.digest())
 elapsed=time.monotonic()-t;row={'direction':'Mac->Xiaomi' if mode==0 else 'Xiaomi->Mac','bytes':size,'seconds':elapsed,'MBps':size/elapsed/1e6,'sha256':expected.hex()};rows.append(row);print(json.dumps(row),flush=True);s.close()
open(sys.argv[2],'w').write(json.dumps(rows,indent=2)+'\n')
