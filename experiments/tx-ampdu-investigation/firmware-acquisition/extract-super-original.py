import bisect, pathlib, struct, sys
src = open('super.img','rb')
magic,major,minor,hs,cs,block,total,n,_ = struct.unpack('<I4H4I',src.read(28))
assert magic == 0xed26ff3a and major == 1
spans = []
pos = 0
src.seek(hs)
for _ in range(n):
    t,_,blocks,size = struct.unpack('<2H2I',src.read(cs)[:12])
    offset = src.tell()
    length = blocks*block
    fill = src.read(4) if t == 0xcac2 else b'\0'*4
    if length:
        spans.append((pos, pos+length, t, offset, fill))
    src.seek(offset+size-cs)
    pos += length
assert pos == total*block
starts = [s[0] for s in spans]
def read(p,n):
    out = bytearray()
    while n:
        start,end,t,off,fill = spans[bisect.bisect_right(starts,p)-1]
        count = min(n,end-p)
        if t==0xcac1:
            src.seek(off+p-start)
            data = src.read(count)
            assert len(data)==count
        else:
            assert t in (0xcac2,0xcac3)
            shift=(p-start)%4
            data=(fill*((count+shift+3)//4))[shift:shift+count]
        out.extend(data)
        p+=count
        n-=count
    return out
geo=read(4096,4096)
assert struct.unpack_from('<I',geo)[0]==0x616c4467
maxsize,slots,_=struct.unpack_from('<III',geo,40)
meta=read(12288,maxsize)
magic,_,_,header=struct.unpack_from('<IHHI',meta)
assert magic==0x414c5030
p_off,p_n,p_size,e_off,e_n,e_size=struct.unpack_from('<6I',meta,80)
tables=meta[header:]
for i in range(p_n):
    name,attr,first,num,grp=struct.unpack_from('<36s4I',tables,p_off+i*p_size)
    name=name.rstrip(b'\0').decode()
    if not num: continue
    extents=[struct.unpack_from('<QIQI',tables,e_off+(first+j)*e_size) for j in range(num)]
    print(name,sum(x[0] for x in extents)*512,flush=True)
    if name.removesuffix('_a') not in sys.argv[1:]: continue
    with open(name+'.img','wb') as out:
        for sectors,typ,sector,source in extents:
            assert source==0 and typ in (0,1)
            if typ==1:
                out.seek(sectors*512,1)
                continue
            pos=sector*512
            left=sectors*512
            while left:
                amount=min(left,4*1024*1024)
                out.write(read(pos,amount))
                left-=amount
                pos+=amount
        out.truncate()
