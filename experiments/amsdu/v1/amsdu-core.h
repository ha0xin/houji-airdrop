/* SPDX-License-Identifier: MIT */
#ifndef XOS_AMSDU_CORE_H
#define XOS_AMSDU_CORE_H
typedef unsigned char xa_u8;
typedef unsigned long xa_size;
#define XA_FRAME_MAX 2048
#define XA_OUTPUT_MAX 8192
static unsigned xa_le16(const xa_u8 *p) { return p[0] | (unsigned)p[1]<<8; }
static unsigned xa_le32(const xa_u8 *p) { return xa_le16(p) | xa_le16(p+2)<<16; }
static void xa_copy(xa_u8 *d,const xa_u8 *s,xa_size n) { while(n--) *d++=*s++; }
static int xa_equal(const xa_u8 *a,const xa_u8 *b,xa_size n) { while(n--) if(*a++!=*b++)return 0; return 1; }
/* Only ordinary, unencrypted, unfragmented, normal-ACK QoS data.
 * Frames already aggregated, frames with an FCS and exotic radiotap layouts
 * are passed through unchanged by the caller. Never inspect IPv6 payloads.
 */
static unsigned xa_header(const xa_u8 *p,xa_size n)
{
 unsigned rt,present,off=8;
 if(n<8 || n>XA_FRAME_MAX || p[0] || p[1])return 0;
 rt=xa_le16(p+2);present=xa_le32(p+4);
 if(rt<8 || rt>128 || rt+26+1000>n || (present&0x80000000U))return 0;
 if(present&1)off=16;
 if(present&2) { if(off>=rt || (p[off]&0x10))return 0; }
 if(p[rt]!=0x88 || p[rt+1] || (p[rt+4]&1) ||
    (p[rt+22]&15) || (p[rt+24]&0xf0) || p[rt+25])return 0;
 return rt+26;
}
static int xa_compatible(const xa_u8 *a,xa_size an,const xa_u8 *b,xa_size bn)
{
 unsigned h=xa_header(a,an),bh=xa_header(b,bn),rt;
 if(!h || h!=bh)return 0;
 rt=h-26;
 return xa_equal(a,b,rt) && xa_equal(a+rt,b+rt,2) &&
        xa_equal(a+rt+4,b+rt+4,18) && xa_equal(a+rt+24,b+rt+24,2);
}
/* max_body is 3839 for the conservative two-frame trial, 7935 only in a
 * separately selected extended-size trial. Padding precedes the next MSDU.
 */
static xa_size xa_pack(xa_u8 *out,xa_size cap,const xa_u8 *const frames[],
                      const xa_size lens[],unsigned count,unsigned max_body)
{
 unsigned h,i;xa_size pos,body_start;
 if(count<2 || count>4 || !(h=xa_header(frames[0],lens[0])))return 0;
 if(cap<h)return 0;
 for(i=1;i<count;i++)if(!xa_compatible(frames[0],lens[0],frames[i],lens[i]))return 0;
 xa_copy(out,frames[0],h);out[h-2]|=0x80;pos=h;body_start=h;
 for(i=0;i<count;i++) {
  xa_size size=lens[i]-h,pad=i ? ((4-((pos-body_start)&3))&3) : 0;
  if(size>65535 || pos+pad+14+size>cap || pos+pad+14+size-body_start>max_body)return 0;
  while(pad--)out[pos++]=0;
  xa_copy(out+pos,frames[i]+h-22,12);pos+=12;
  out[pos++]=(xa_u8)(size>>8);out[pos++]=(xa_u8)size;
  xa_copy(out+pos,frames[i]+h,size);pos+=size;
 }
 return pos;
}
#endif
