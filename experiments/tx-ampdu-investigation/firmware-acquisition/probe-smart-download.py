import sys,hashlib,xml.etree.ElementTree as E
from pathlib import Path
sys.path.insert(0,'samloader-smart')
from samloader import fusclient,request
from Cryptodome.Cipher import AES
c=fusclient.FUSClient();fw='S928BXXU6EZIF/S928BOXM6EZIF/S928BXXU6EZIF/S928BXXU6EZIF'
r=E.fromstring(c.makereq('NF_SmartDownloadBinaryInform.do',request.binaryinform(fw,'SM-S928B','FWD',c.nonce)))
kv={e.tag:e.findtext('Data') for e in r.iter() if e.find('Data') is not None}
name=kv['BINARY_NAME'];size=int(kv['BINARY_BYTE_SIZE']);key=hashlib.md5(request.getlogiccheck(fw,kv['LOGIC_VALUE_FACTORY']).encode()).digest()
res=c.makereq('NF_SmartDownloadBinaryInitForMass.do',request.binaryinit(name,fw,kv['DEVICE_MODEL_TYPE'],'FWD',c.nonce));Path('fus-init.xml').write_text(res);print('init',res[:250],flush=True)
headers={'Authorization':f'FUS nonce="{c.nonce}", signature="{c.auth}", nc="", type="", realm="", newauth="1"','User-Agent':'SMART 2.0','Range':f'bytes={size-131072}-{size-1}'}
u='https://cloud-neofussvr.samsungmobile.com/NF_SmartDownloadBinaryForMass.do?file='+kv['MODEL_PATH']+name
r=c.sess.get(u,headers=headers,timeout=30,stream=True);print('GET',r.status_code,dict((k,v) for k,v in r.headers.items() if k.lower() in ['content-range','content-length','content-type']),flush=True)
if r.status_code==206:
 b=r.content;d=AES.new(key,AES.MODE_ECB).decrypt(b);Path('zip-tail.bin').write_bytes(d);print('tail zip',d.rfind(b'PK\x05\x06'),'len',len(d))
else:print(next(r.iter_content(1024),b'')[:200])
