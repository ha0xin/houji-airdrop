#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
extern int pcap_inject(void *,const void *,unsigned long);
extern void pcap_close(void *);
static unsigned members,groups,closed;
static int mock_inject(void *p,const void *data,unsigned long n) {
 const unsigned char *b=data;unsigned long pos=34;
 assert(p==(void *)1 && !closed && n>=34);
 if(b[32]&0x80) {
  groups++;
  if(getenv("MOCK_FAIL_GROUP"))return -1;
  while(pos<n) {
   assert(pos+14<=n);
   unsigned size=(b[pos+12]<<8)|b[pos+13];pos+=14;
   assert(size==1467 && pos+size<=n);
   for(unsigned i=0;i<size;i++)assert(b[pos+i]==(unsigned char)i);
   members++;pos+=size;
   if(pos<n)pos+=((4-((pos-34)&3))&3);
  }
  assert(pos==n);
 } else { assert(n==1501);members++; }
 return (int)n;
}
static void mock_close(void *p) { assert(p==(void *)1 && !closed);closed=1; }
void *dlsym(void *handle,const char *name) {
 (void)handle;
 if(!strcmp(name,"pcap_inject"))return (void *)mock_inject;
 if(!strcmp(name,"pcap_close"))return (void *)mock_close;
 return 0;
}
int main(void) {
 unsigned char f[1501]={0};f[2]=8;f[8]=0x88;f[12]=2;f[18]=4;f[24]=6;
 for(unsigned i=34;i<sizeof(f);i++)f[i]=(unsigned char)(i-34);
 for(unsigned i=0;i<2001;i++) {
  f[30]=(unsigned char)(i<<4);f[31]=(unsigned char)(i>>4);
  assert(pcap_inject((void *)1,f,sizeof(f))==(int)sizeof(f));
 }
 pcap_close((void *)1);
 assert(closed && members==2001 && groups>0);
 puts("AMSDU worker/close flush preserves every input MSDU");
 return 0;
}
