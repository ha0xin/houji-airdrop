#!/usr/bin/env python3
"""Validate Android's Owly sender against a temporary scoped AWDL TLS receiver."""
from pathlib import Path
import gzip, hashlib, ipaddress, json, plistlib, runpy, shlex, socket, ssl, struct, subprocess, tempfile, threading, time, zlib

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
WORK=REPO / "experiments"
g=runpy.run_path(str(WORK/'three-findings-validation/run-three-findings.py'))
root=g['root'];app='/data/user/0/org.houji.owly/files'
phone=root('ip -6 -o addr show dev owly0 scope link').split('inet6 ')[1].split('/')[0]
mac,_=g['R']['current_awdl']();octets=bytearray.fromhex(mac.replace(':',''));octets[0]^=2
macip=str(ipaddress.IPv6Address(b'\xfe\x80'+bytes(6)+octets[:3]+b'\xff\xfe'+octets[3:]))
scope=socket.if_nametoindex('awdl0')
payload=b'Owly Android sender: mDNS, TLS, archive and upload verified.\n'
name='owly-send-smoke.txt'
def dns_name(value):return b''.join(bytes([len(x)])+x.encode() for x in value.rstrip('.').split('.'))+b'\0'
instance='owly-smoke._airdrop._tcp.local';host='owly-smoke.local'
def record(name,kind,data):return dns_name(name)+struct.pack('!HHIH',kind,1,10,len(data))+data
packet=struct.pack('!HHHHHH',0,0x8400,0,4,0,0)+b''.join([
    record('_airdrop._tcp.local',12,dns_name(instance)),
    record(instance,33,struct.pack('!HHH',0,0,8772)+dns_name(host)),
    record(instance,16,b'\x07flags=0'),
    record(host,28,ipaddress.IPv6Address(macip).packed)])
receipt={};errors=[]
with tempfile.TemporaryDirectory() as tmp:
    cert,key=Path(tmp)/'cert.pem',Path(tmp)/'key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=owly.local'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
    listener=socket.socket(socket.AF_INET6,socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    listener.setsockopt(socket.SOL_SOCKET,0x1104,1);listener.setsockopt(socket.IPPROTO_IPV6,125,scope)
    listener.bind((macip,8772,0,scope));listener.listen(1);listener.settimeout(40)
    def serve():
        try:
            raw,_=listener.accept();raw.settimeout(25)
            with context.wrap_socket(raw,server_side=True) as conn:
                stream=conn.makefile('rb')
                for expected in ['/Ask','/Upload']:
                    first=stream.readline().decode();assert first.split()[1]==expected,first
                    headers={}
                    while True:
                        line=stream.readline()
                        if line==b'\r\n':break
                        k,v=line.decode().split(':',1);headers[k.lower()]=v.strip()
                    body=stream.read(int(headers['content-length']))
                    if expected=='/Ask':
                        ask=plistlib.loads(body);assert ask['Files'][0]['FileName']==name
                        response=plistlib.dumps({'ReceiverComputerName':'Owly smoke','ReceiverModelName':'Mac'},fmt=plistlib.FMT_BINARY)
                    else:
                        if headers['content-type']=='application/x-dvzip':
                            archive=b'';offset=0
                            while offset<len(body):
                                length=struct.unpack('!I',body[offset:offset+4])[0];offset+=4
                                stored=length&0x80000000;length&=0x7fffffff
                                piece=body[offset:offset+length];assert len(piece)==length;offset+=length
                                archive+=piece if stored else zlib.decompress(piece)
                        else: archive=gzip.decompress(body)
                        receipt['compression']=headers['content-type']
                        position=0;found=None
                        while position<len(archive):
                            header=archive[position:position+76];assert header[:6]==b'070707'
                            ns=int(header[59:65],8);size=int(header[65:76],8)
                            filename=archive[position+76:position+76+ns-1].decode()
                            data=archive[position+76+ns:position+76+ns+size]
                            position+=76+ns+size
                            if filename.endswith(name):found=data
                            if filename=='TRAILER!!!':break
                        assert found==payload,(found,payload)
                        receipt.update(verified=True,bytes=len(payload),sha256=hashlib.sha256(found).hexdigest(),upload_http=200)
                        response=b''
                    conn.sendall(f'HTTP/1.1 200 OK\r\nContent-Length: {len(response)}\r\nConnection: keep-alive\r\n\r\n'.encode()+response)
        except Exception as e:errors.append(repr(e))
    thread=threading.Thread(target=serve);thread.start()
    udp=socket.socket(socket.AF_INET6,socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET,0x1104,1);udp.setsockopt(socket.IPPROTO_IPV6,125,scope)
    for _ in range(30):
        udp.sendto(packet,(phone,5353,0,scope));time.sleep(.2)
        state=json.loads(root(f'cat {app}/status.json'))
        if state.get('receiver',{} ) and state['receiver']['name']=='owly-smoke':break
    else:raise RuntimeError('Owly browser did not resolve the test receiver')
    root(f'printf %s {shlex.quote(payload.decode())} > {app}/outbox/{name}')
    command=json.dumps({'op':'send','path':f'{app}/outbox/{name}','session':state['session']})
    root(f'printf %s {shlex.quote(command)} > {app}/commands/send-smoke.tmp; mv {app}/commands/send-smoke.tmp {app}/commands/send-smoke.json')
    thread.join(45);listener.close();udp.close()
    assert not thread.is_alive() and not errors,(thread.is_alive(),errors)
    for _ in range(20):
        state=json.loads(root(f'cat {app}/status.json'))
        if state.get('message')=='发送完成':break
        time.sleep(.2)
    assert state.get('message')=='发送完成',state
    receipt['app_message']=state['message']
(HERE/'evidence/send-smoke.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
