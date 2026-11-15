#!/usr/bin/env python3
"""Small real TLS/Discover/Ask/Upload check over AWDL, not a throughput test."""
from pathlib import Path
import gzip, hashlib, http.client, json, plistlib, shlex, socket, ssl, threading, time, uuid

from device import root

HERE=Path(__file__).resolve().parent
app='/data/user/0/org.houji.owly/files'
ip=root("ip -6 -o addr show dev owly0 scope link").split('inet6 ')[1].split('/')[0]
scope=socket.if_nametoindex('awdl0')
raw=socket.socket(socket.AF_INET6,socket.SOCK_STREAM)
raw.settimeout(20);raw.setsockopt(socket.SOL_SOCKET,0x1104,1);raw.setsockopt(socket.IPPROTO_IPV6,125,scope)
raw.connect((ip,8771,0,scope))
conn=ssl._create_unverified_context().wrap_socket(raw,server_hostname='owly.local')
def post(path,body,headers=None):
    head={'Host':'owly.local','User-Agent':'AirDrop/1.0','Content-Length':str(len(body)), 'Content-Type':'application/octet-stream'}
    head.update(headers or {})
    conn.sendall((f'POST {path} HTTP/1.1\r\n'+''.join(f'{k}: {v}\r\n' for k,v in head.items())+'\r\n').encode()+body)
    response=http.client.HTTPResponse(conn);response.begin();data=response.read()
    assert response.status==200,(path,response.status,data)
    return data
discover=plistlib.loads(post('/Discover',plistlib.dumps({},fmt=plistlib.FMT_BINARY)))
assert discover['ReceiverComputerName']=='Xiaomi 14 Owly'
payload=b'Owly standalone app: TLS, confirmation and file extraction verified.\n'
name='owly-functional-'+uuid.uuid4().hex[:8]+'.txt'; tid=str(uuid.uuid4()).upper()
ask={'TransferID':{'id':tid},'TransferType':{'files':{}},'SenderComputerName':'Owly functional check',
     'SenderModelName':'Mac','SenderID':'123456abcdef','BundleID':'com.apple.finder','Files':[
         {'FileName':name,'FileType':'public.plain-text','FileSize':len(payload),'FileBomPath':'./'+name,'FileIsDirectory':False}], 'Items':[]}
errors=[]
def accept():
    try:
        for _ in range(25):
            state=json.loads(root(f'cat {app}/status.json'))
            request=state.get('request')
            if request and request.get('sender')=='Owly functional check':
                data=json.dumps({'op':'accept','id':request['id'],'session':state.get('session')})
                root(f'printf %s {shlex.quote(data)} > {app}/commands/smoke.tmp; mv {app}/commands/smoke.tmp {app}/commands/smoke.json')
                return
            time.sleep(.2)
        raise RuntimeError('No acceptance prompt')
    except Exception as e: errors.append(str(e))
thread=threading.Thread(target=accept);thread.start()
post('/Ask',plistlib.dumps(ask,fmt=plistlib.FMT_BINARY));thread.join();assert not errors,errors
def entry(name,mode,data=b''):
    fields=(0,1,mode,501,20,1,0,0,len(name.encode())+1,len(data))
    widths=(6,6,6,6,6,6,6,11,6,11)
    return b'070707'+''.join(format(n,f'0{w}o') for n,w in zip(fields,widths)).encode()+name.encode()+b'\0'+data
archive=gzip.compress(entry('.',0o40755)+entry('./'+name,0o100644,payload)+entry('TRAILER!!!',0))
post('/Upload',archive,{'Content-Type':'application/x-cpio','TransferID':tid,'TotalBytes':'0'})
conn.close()
expected=hashlib.sha256(payload).hexdigest()
actual=root(f'sha256sum {app}/received/{name}').split()[0]
assert actual==expected
state={}
for _ in range(15):
    state=json.loads(root(f'cat {app}/status.json'))
    tr=state.get('transfer') or {}
    if tr.get('ok') is True and tr.get('direction')=='recv' and str(state.get('message','')).startswith('接收完成'):break
    time.sleep(.2)
tr=state.get('transfer') or {}
assert tr.get('ok') is True and tr.get('direction')=='recv' and tr.get('bytes')==len(payload) and 'speed_mbps' in tr and 'duration_secs' in tr,state
receipt={'discover_name':discover['ReceiverComputerName'],'ask':'confirmed through app IPC','upload_http':200,
         'file':name,'bytes':len(payload),'sha256':actual,'app_message':state.get('message'),'transfer':tr,'verified':True}
(HERE/'evidence/receive-smoke.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
