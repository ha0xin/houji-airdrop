import socket,struct,time,select,json,sys
size=1378;count=40000;duration_ms=5000
idx=socket.if_nametoindex('awdl0')
def sock(kind):
 s=socket.socket(socket.AF_INET6,kind);s.setsockopt(socket.SOL_SOCKET,0x1104,1);s.setsockopt(socket.IPPROTO_IPV6,125,idx);return s
u=sock(socket.SOCK_DGRAM);u.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4*1024*1024);u.bind(('::',18874,0,idx));u.setblocking(False)
c=sock(socket.SOCK_STREAM);c.settimeout(10);c.connect((sys.argv[1],18872,0,idx));c.sendall(struct.pack('>III',size,count,duration_ms));c.setblocking(False)
seen=bytearray(count);payload=(bytes(range(256))*6)[:size];duplicates=bad=reordered=max_gap=0;highest=-1
start=time.monotonic();first=last=None;done=None;control=b'';bins={};received=0
while time.monotonic()-start<20:
 now=time.monotonic()
 if done is not None and now-done>1:break
 for s in select.select([u]+([] if done is not None else [c]),[],[],.05)[0]:
  if s is c:
   b=c.recv(4096);control+=b
   if not b or b'\n' in control:done=time.monotonic()
   continue
  b,addr=u.recvfrom(8192);now=time.monotonic()
  if len(b)!=size or b[4:]!=payload[4:]:bad+=1;continue
  seq=struct.unpack_from('>I',b)[0]
  if seq>=count:bad+=1;continue
  if seen[seq]:duplicates+=1;continue
  if seq<highest:reordered+=1;max_gap=max(max_gap,highest-seq)
  highest=max(highest,seq);seen[seq]=1;received+=1
  if first is None:first=now
  last=now;k=int((now-start)*10);bins[k]=bins.get(k,0)+1
result=dict(sent=count,received=received,loss=count-received,loss_percent=(count-received)*100/count,
 reordered=reordered,max_reorder_distance=max_gap,duplicates=duplicates,bad_payload=bad,
 first_packet_seconds=None if first is None else first-start,last_packet_seconds=None if last is None else last-start,
 received_MBps=None if first is None or first==last else received*size/(last-first)/1e6,
 sender=control.decode(errors='replace').strip(),receive_buffer=u.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF),packets_per_100ms=bins)
open(sys.argv[2],'w').write(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='packets_per_100ms'}),flush=True)
c.close();u.close()
if bad or not control.startswith(b'DONE'):raise SystemExit('Invalid/incomplete probe')
