import hashlib,io,json,struct,tarfile,time,xml.etree.ElementTree as ET,zlib
from pathlib import Path
from samloader import fusclient,request
from Cryptodome.Cipher import AES
FW='S928BXXU6EZIF/S928BOXM6EZIF/S928BXXU6EZIF/S928BXXU6EZIF'
c=fusclient.FUSClient();xml=c.makereq('NF_SmartDownloadBinaryInform.do',request.binaryinform(FW,'SM-S928B','FWD',c.nonce));Path('official-metadata.xml').write_text(xml)
kv={e.tag:e.findtext('Data') for e in ET.fromstring(xml).iter() if e.find('Data') is not None};size=int(kv['BINARY_BYTE_SIZE']);key=hashlib.md5(request.getlogiccheck(FW,kv['LOGIC_VALUE_FACTORY']).encode()).digest();name=kv['BINARY_NAME']
Path('official-init.xml').write_text(c.makereq('NF_SmartDownloadBinaryInitForMass.do',request.binaryinit(name,FW,kv['DEVICE_MODEL_TYPE'],'FWD',c.nonce)))
url='https://cloud-neofussvr.samsungmobile.com/NF_SmartDownloadBinaryForMass.do?file='+kv['MODEL_PATH']+name
headers={'Authorization':f'FUS nonce="{c.nonce}", signature="{c.auth}", nc="", type="", realm="", newauth="1"','User-Agent':'SMART 2.0'}
def get_range(start,end):
 h=dict(headers,Range=f'bytes={start}-{end}');r=c.sess.get(url,headers=h,timeout=(30,120),stream=True);r.raise_for_status();assert r.status_code==206,(r.status_code,r.headers);return r
r=get_range(size-131072,size-1);tail=AES.new(key,AES.MODE_ECB).decrypt(r.content);Path('zip-tail.bin').write_bytes(tail)
entries=[];p=0
while True:
 p=tail.find(b'PK\x01\x02',p)
 if p<0:break
 a=struct.unpack_from('<4s6H3I5H2I',tail,p);n,x,cm=a[10:13];fn=tail[p+46:p+46+n].decode();extra=tail[p+46+n:p+46+n+x];usize,csize,offset=struct.unpack_from('<QQQ',extra,4);entries.append(dict(name=fn,size=usize,compressed=csize,offset=offset,crc32=a[7],method=a[4]));p+=46+n+x+cm
Path('zip-entries.json').write_text(json.dumps(entries,indent=2));entry=next(e for e in entries if e['name'].startswith('AP_'));off=entry['offset'];aligned=off//16*16
head=AES.new(key,AES.MODE_ECB).decrypt(get_range(aligned,aligned+4095).content)[off-aligned:];assert head[:4]==b'PK\x03\x04';n,x=struct.unpack_from('<HH',head,26);start=off+30+n+x;end=start+entry['compressed'];aligned=start//16*16;aligned_end=(end+15)//16*16
response=get_range(aligned,aligned_end-1);cipher=AES.new(key,AES.MODE_ECB);d=zlib.decompressobj(-15);crc=0;total=0;compressed=0;last=time.monotonic();sha=hashlib.sha256()
def blocks():
 global crc,total,compressed,last
 skip=start-aligned;remaining=entry['compressed'];pending=b''
 for chunk in response.iter_content(1024*1024):
  pending+=chunk;n=len(pending)//16*16
  if not n:continue
  dec=cipher.decrypt(pending[:n]);pending=pending[n:]
  if skip:dec=dec[skip:];skip=0
  dec=dec[:remaining];remaining-=len(dec);compressed+=len(dec)
  out=d.decompress(dec)
  if out:crc=zlib.crc32(out,crc);total+=len(out);sha.update(out);yield out
  if time.monotonic()-last>20:print('PROGRESS compressed',compressed,'of',entry['compressed'],'tar',total,flush=True);last=time.monotonic()
 out=d.flush()
 if out:crc=zlib.crc32(out,crc);total+=len(out);sha.update(out);yield out
 assert remaining==0 and d.eof,(remaining,d.eof)
class Reader(io.RawIOBase):
 def __init__(self):self.it=iter(blocks());self.buf=b''
 def readable(self):return True
 def readinto(self,b):
  if not self.buf:
   try:self.buf=next(self.it)
   except StopIteration:return 0
  n=min(len(b),len(self.buf));b[:n]=self.buf[:n];self.buf=self.buf[n:];return n
reader=io.BufferedReader(Reader(),buffer_size=1024*1024)
with tarfile.open(fileobj=reader,mode='r|') as tar:
 for member in tar:
  print('TAR',member.name,member.size,flush=True)
  if Path(member.name).name in ['super.img.lz4','vendor.img.lz4','vendor.img'] and member.isfile():
   out=Path(Path(member.name).name);h=hashlib.sha256()
   with tar.extractfile(member) as src,out.open('wb') as dst:
    while True:
     b=src.read(1024*1024)
     if not b:break
     h.update(b);dst.write(b)
   Path(out.name+'.sha256').write_text(h.hexdigest()+'  '+out.name+'\n');print('SAVED',out,h.hexdigest(),flush=True)
while reader.read(1024*1024):pass
assert total==entry['size'] and crc==entry['crc32'],(total,crc,entry)
Path('AP-verified.json').write_text(json.dumps(dict(source=url,entry=entry,actual_crc32=crc,actual_size=total,sha256=sha.hexdigest()),indent=2));print('AP_VERIFIED',sha.hexdigest(),flush=True)
