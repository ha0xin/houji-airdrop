#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "amsdu-core.h"
int main(void) {
 unsigned char f[5][2048]={{0}},out[8192],old[2048];
 const unsigned char *v[5]={f[0],f[1],f[2],f[3],f[4]};
 unsigned long n[5]={1501,1502,1503,1504,1505},packed,pos;
 unsigned i,j;
 for(i=0;i<5;i++) {
  f[i][2]=8;f[i][8]=0x88;f[i][12]=2;f[i][18]=4;f[i][24]=6;
  f[i][30]=(unsigned char)(i<<4);
  for(j=34;j<n[i];j++)f[i][j]=(unsigned char)(j+i);
 }
 memcpy(old,f[0],sizeof(old));
 packed=xa_pack(out,sizeof(out),v,n,5,7935);assert(packed);
 assert(out[32]==0x80 && !memcmp(old,f[0],sizeof(old)));
 pos=34;
 for(i=0;i<5;i++) {
  if(i)pos+=((4-((pos-34)&3))&3);
  assert(!memcmp(out+pos,f[i]+12,12));pos+=12;
  unsigned size=(out[pos]<<8)|out[pos+1];pos+=2;
  assert(size==n[i]-34 && !memcmp(out+pos,f[i]+34,size));pos+=size;
 }
 assert(pos==packed);
 assert(xa_pack(out,sizeof(out),v,n,2,3839));
 assert(!xa_pack(out,sizeof(out),v,n,4,3839));
 assert(!xa_pack(out,100,v,n,2,3839));
 f[1][12]=8;assert(!xa_pack(out,sizeof(out),v,n,2,3839));f[1][12]=2;
 f[1][32]=0x80;assert(!xa_pack(out,sizeof(out),v,n,2,3839));f[1][32]=0;
 f[1][9]=0x40;assert(!xa_pack(out,sizeof(out),v,n,2,3839));f[1][9]=0;
 f[1][30]|=1;assert(!xa_pack(out,sizeof(out),v,n,2,3839));f[1][30]&=0xf0;
 for(i=0;i<34;i++)assert(!xa_header(f[0],i));
 /* Invalid/truncated radiotap flags and FCS-bearing input must not merge. */
 f[1][4]=2;f[1][2]=8;assert(!xa_header(f[1],n[1]));
 puts("AMSDU bounds, destination isolation, padding and payload tests passed");
 return 0;
}
