#include <stdint.h>
#include <stdio.h>
#include <assert.h>
#include <string.h>
typedef uint64_t u64; typedef uint32_t u32; typedef uint8_t u8;
#include "pt_tx_observer_core.h"
static struct pto_state p;
static void invariant(void) {
 unsigned g,i; u64 n=0;
 for(g=0;g<PTO_GROUPS;g++) for(i=0;i<PTO_WAYS;i++) n+=p.entries[g][i].key!=0;
 assert(n==p.s.active);
 assert(p.s.active==p.s.cls[0].active+p.s.cls[1].active+p.s.cls[2].active);
 assert(p.s.admitted==p.s.active+p.s.completed+p.s.released);
}
int main(void) {
 unsigned i; unsigned long keys[5]; unsigned found=0; u64 now=100;
 pto_begin(&p,64,10,100,0);
 /* completion can precede sender return; release must not count twice */
 pto_end(&p,64,1000010,0,1);pto_end(&p,64,1000011,-1,0);
 assert(p.s.completed==1 && p.s.released==0 && p.s.active==0);
 assert(p.s.cls[0].delay[1]==1 && p.s.cls[0].status[0]==1);
 /* descriptor reuse and unsuccessful enqueue/teardown */
 pto_begin(&p,64,2000000,150,1);pto_end(&p,64,2000010,-1,0);
 assert(p.s.released==1);invariant();
 memset(&p,0,sizeof(p));
 for(unsigned long k=64;found<5;k+=64) if(pto_group(k)==1) keys[found++]=k;
 for(i=0;i<5;i++) pto_begin(&p,keys[i],100,1,2);
 assert(p.s.active==4 && p.s.overflow==1);
 pto_begin(&p,keys[0],101,1,2);assert(p.s.duplicate==1);
 assert(pto_oldest(&p,200)==100);
 for(i=0;i<5;i++) pto_end(&p,keys[i],300,2,16);
 assert(p.s.completed==4 && p.s.unmatched==1);invariant();
 assert(p.s.cls[2].tries==64);
 /* Bounded ledger under out-of-order completions, releases and reuse. */
 memset(&p,0,sizeof(p));
 uint32_t seed=1;
 for(i=0;i<100000;i++) {
  seed=seed*1664525U+1013904223U;unsigned long key=((seed>>8)%1024+1)*64;
  now+=1000;
  if(seed&1) pto_begin(&p,key,now,1500,(seed>>20)%3);
  else pto_end(&p,key,now,(seed&4) ? -1 : (int)((seed>>16)%256),16);
  if(i%100==0) invariant();
 }
 for(i=1;i<=1024;i++) pto_end(&p,i*64,now,-1,0);
 invariant();assert(!p.s.active);
 for(i=0;i<7;i++) {
  const u64 edges[]={1000000,4000000,16000000,64000000,256000000,1024000000,4096000000ULL};
  assert(pto_bucket(edges[i]-1)==i);assert(pto_bucket(edges[i])==i+1);
 }
 puts("LEDGER_TEST_PASS: early completion, release, reuse, collision, duplicate, histogram, randomized conservation");
}
