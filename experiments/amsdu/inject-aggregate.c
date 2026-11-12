/* SPDX-License-Identifier: MIT */
/* Linked into the independent test runner; original libxos is untouched. */
#include "amsdu-core.h"
extern void *dlsym(void *,const char *);
extern char *getenv(const char *);
extern int atoi(const char *);
extern int printf(const char *,...);
extern int fflush(void *);
struct xa_time { long sec,ns; };
extern int clock_gettime(int,struct xa_time *);
extern int nanosleep(const struct xa_time *,struct xa_time *);
extern int pthread_create(unsigned long *,const void *,void *(*)(void *),void *);
typedef int (*xa_inject_fn)(void *,const void *,unsigned long);
static xa_inject_fn xa_real;
static unsigned char xa_busy;
static xa_u8 xa_frames[5][XA_FRAME_MAX],xa_out[XA_OUTPUT_MAX];
static xa_size xa_lengths[5];
static unsigned xa_count,xa_limit=2,xa_worker,xa_wait_us=200;
static void *xa_handle;
static unsigned long xa_deadline,xa_groups,xa_singles,xa_members,xa_errors,xa_rejected;
static unsigned long xa_full,xa_timeout,xa_control,xa_mismatch;
static unsigned long xa_inputs,xa_direct,xa_reads,xa_read_bytes;
static unsigned long xa_started,xa_hold_max,xa_hold_bins[6];
static xa_u8 xa_wire[XA_OUTPUT_MAX];
static struct {
 void *handle;
 xa_u8 addresses[18],tid;
 unsigned next;
} xa_seq[16];
/* One sequence number per emitted MPDU, not one per input MSDU.
 * Include small QoS packets so ACK-only TCP traffic cannot create gaps.
 * All calls are under xa_busy; failed injection does not consume a number.
 */
static int xa_send(void *p,const void *data,unsigned long n) {
 const xa_u8 *b=data;unsigned h=xa_data_header(b,n),i,slot=16;int rc;
 if(!h)return xa_real(p,data,n);
 for(i=0;i<16;i++) {
  if(!xa_seq[i].handle && slot==16)slot=i;
  if(xa_seq[i].handle==p && xa_seq[i].tid==(b[h-2]&15) &&
     xa_equal(xa_seq[i].addresses,b+h-22,18))break;
 }
 if(i==16) {
  if(slot==16)return xa_real(p,data,n);
  i=slot;xa_seq[i].handle=p;xa_seq[i].tid=b[h-2]&15;
  xa_copy(xa_seq[i].addresses,b+h-22,18);xa_seq[i].next=xa_le16(b+h-4)>>4;
 }
 xa_copy(xa_wire,b,n);
 xa_wire[h-4]=(xa_u8)(xa_seq[i].next<<4);
 xa_wire[h-3]=(xa_u8)(xa_seq[i].next>>4);
 rc=xa_real(p,xa_wire,n);
 if(rc==(int)n)xa_seq[i].next=(xa_seq[i].next+1)&4095;
 return rc;
}
static unsigned long xa_now(void) {
 struct xa_time t;
 if(clock_gettime(1,&t))return 0;
 return (unsigned long)t.sec*1000000000UL+t.ns;
}
static void xa_lock(void) {
 const struct xa_time t={0,50000};
 while(__atomic_test_and_set(&xa_busy,__ATOMIC_ACQUIRE))nanosleep(&t,0);
}
static void xa_unlock(void) { __atomic_clear(&xa_busy,__ATOMIC_RELEASE); }
static void xa_report(void) {
 printf("XOS_FLOW inputs=%lu direct=%lu reads=%lu read_bytes=%lu\n",xa_inputs,xa_direct,__atomic_load_n(&xa_reads,__ATOMIC_RELAXED),__atomic_load_n(&xa_read_bytes,__ATOMIC_RELAXED));
 printf("AMSDU max=%u wait_us=%u groups=%lu members=%lu singles=%lu inject_errors=%lu pack_rejects=%lu full=%lu timeout=%lu control=%lu mismatch=%lu\n",
        xa_limit,xa_wait_us,xa_groups,xa_members,xa_singles,xa_errors,xa_rejected,
        xa_full,xa_timeout,xa_control,xa_mismatch);
 printf("AMSDU_HOLD max_ns=%lu bins=%lu,%lu,%lu,%lu,%lu,%lu\n",xa_hold_max,
        xa_hold_bins[0],xa_hold_bins[1],xa_hold_bins[2],xa_hold_bins[3],xa_hold_bins[4],xa_hold_bins[5]);fflush(0);
}
/* All handle access, including pcap_close, is serialized with this lock. */
static void xa_flush(void) {
 const xa_u8 *frames[5]={xa_frames[0],xa_frames[1],xa_frames[2],xa_frames[3],xa_frames[4]};
 xa_size size=0;unsigned i;int sent=0;
 if(!xa_count)return;
 {
  unsigned long ns=xa_now()-xa_started,us=ns/1000;
  unsigned bin=us<=250?0:us<=1000?1:us<=4000?2:us<=16000?3:us<=64000?4:5;
  xa_hold_bins[bin]++;if(ns>xa_hold_max)xa_hold_max=ns;
 }
 if(xa_count>1)size=xa_pack(xa_out,sizeof(xa_out),frames,xa_lengths,xa_count,xa_limit==2?3839:7935);
 if(size) {
  if(xa_send(xa_handle,xa_out,size)==(int)size) {
   sent=1;xa_groups++;xa_members+=xa_count;
   if(!(xa_groups%128))xa_report();
  } else {
   typedef const char *(*err_fn)(void *);
   err_fn err=(err_fn)dlsym((void *)-1L,"pcap_geterr");
   xa_errors++;
   if(xa_errors<=3) { printf("AMSDU inject rejected size=%lu reason=%s; sending original frames\n",size,err?err(xa_handle):"unavailable");fflush(0); }
  }
 }
 if(!sent) {
  if(xa_count>1 && !size)xa_rejected++;
  for(i=0;i<xa_count;i++) {
   if(xa_send(xa_handle,xa_frames[i],xa_lengths[i])!=(int)xa_lengths[i])xa_errors++;
   xa_singles++;
  }
 }
 xa_count=0;xa_handle=0;
}
static void *xa_loop(void *unused) {
 const struct xa_time t={0,100000};(void)unused;
 for(;;) {
  nanosleep(&t,0);xa_lock();
  if(xa_count && xa_now()>=xa_deadline){xa_timeout++;xa_flush();}
  xa_unlock();
 }
 return 0;
}
int pcap_inject(void *p,const void *buf,unsigned long len) {
 int rc;unsigned long thread;const xa_u8 *b=buf;
 xa_lock();
 if(!xa_real)xa_real=(xa_inject_fn)dlsym((void *)-1L,"pcap_inject");
 if(!xa_real){xa_unlock();return -1;}
 xa_inputs++;
 if(!xa_header(b,len)) {
  xa_direct++;
  if(xa_count)xa_control++;
  xa_flush();rc=xa_send(p,buf,len);xa_unlock();return rc;
 }
 if(!xa_worker) {
  const char *mode=getenv("XOS_AMSDU_MAX");
  const char *wait=getenv("XOS_AMSDU_US");
  if(mode && (atoi(mode)==4 || atoi(mode)==5))xa_limit=(unsigned)atoi(mode);
  if(wait && atoi(wait)>=100 && atoi(wait)<=1500)xa_wait_us=(unsigned)atoi(wait);
  if(pthread_create(&thread,0,xa_loop,0)) {
   rc=xa_send(p,buf,len);xa_unlock();return rc;
  }
  xa_worker=1;xa_report();
 }
 if(xa_count && (xa_handle!=p || !xa_compatible(xa_frames[0],xa_lengths[0],b,len))) {
  xa_mismatch++;xa_flush();
 }
 if(xa_count && xa_now()>=xa_deadline){xa_timeout++;xa_flush();}
 if(!xa_count){xa_handle=p;xa_started=xa_now();xa_deadline=xa_started+xa_wait_us*1000UL;}
 xa_copy(xa_frames[xa_count],b,len);xa_lengths[xa_count++]=len;
 if(xa_count==xa_limit){xa_full++;xa_flush();}
 /* Accepted into a bounded queue. Real injection failures are counted;
  * TCP/hash verification, not this return value, establishes delivery. */
 xa_unlock();return (int)len;
}
void pcap_close(void *p) {
 typedef void (*close_fn)(void *);
 close_fn real=(close_fn)dlsym((void *)-1L,"pcap_close");
 xa_lock();if(xa_handle==p)xa_flush();if(xa_worker)xa_report();
 if(real)real(p);
 for(unsigned i=0;i<16;i++)if(xa_seq[i].handle==p)xa_seq[i].handle=0;
 xa_unlock();
}

/* Observe host reads without changing their data, return value or errno. */
long __read_chk(int fd,void *buf,unsigned long count,unsigned long capacity) {
 typedef long (*read_fn)(int,void *,unsigned long,unsigned long);
 static read_fn real;
 if(!real)real=(read_fn)dlsym((void *)-1L,"__read_chk");
 if(!real)return -1;
 long n=real(fd,buf,count,capacity);
 if(n>0) {
  __atomic_fetch_add(&xa_reads,1,__ATOMIC_RELAXED);
  __atomic_fetch_add(&xa_read_bytes,(unsigned long)n,__ATOMIC_RELAXED);
 }
 return n;
}
