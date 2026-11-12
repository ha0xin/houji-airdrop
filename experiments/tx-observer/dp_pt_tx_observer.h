/* SPDX-License-Identifier: ISC */
#ifndef DP_PT_TX_OBSERVER_H
#define DP_PT_TX_OBSERVER_H
#ifdef DRIVER_PASSTHRU_DP
#include <linux/moduleparam.h>
#include <linux/ktime.h>
#include <linux/spinlock.h>
#include "pt_tx_observer_core.h"
/* Load-time only: prevents untracked frames straddling an enable/reset. */
static bool pt_tx_observe;
module_param_named(pt_tx_observe, pt_tx_observe, bool, 0444);
MODULE_PARM_DESC(pt_tx_observe, "Passive PASSTHRU TX ledger; enable at module load");
static DEFINE_SPINLOCK(pto_lock);
static struct pto_state pto;
static u64 pto_epoch_ns;
static void dp_pto_call(bool result, bool rejected)
{
 unsigned long flags;
 if(!pt_tx_observe) return;
 spin_lock_irqsave(&pto_lock,flags);
 if(!pto_epoch_ns) pto_epoch_ns=ktime_get_ns();
 if(!result) pto.s.calls++;
 else if(rejected) pto.s.rejected++;
 else pto.s.accepted++;
 spin_unlock_irqrestore(&pto_lock,flags);
}
static void dp_pto_begin(struct dp_tx_desc_s *d, qdf_nbuf_t nbuf)
{
 unsigned long flags; const u8 *frame; u8 cls; u32 bytes;
 if(!pt_tx_observe || !dp_tx_desc_is_passthru(d)) return;
 /* Called before tx_hw_enqueue owns the descriptor. Raw header validated. */
 frame=qdf_nbuf_data(nbuf); bytes=qdf_nbuf_headlen(nbuf);
 cls=bytes>=10 && (frame[0]&0x0c)==0x08 ? ((frame[4]&1) ? 1 : 0) : 2;
 spin_lock_irqsave(&pto_lock,flags);
 pto_begin(&pto,(unsigned long)d,ktime_get_ns(),bytes,cls);
 spin_unlock_irqrestore(&pto_lock,flags);
}
static void dp_pto_end(struct dp_tx_desc_s *d,
                       const struct hal_tx_completion_status *ts)
{
 unsigned long flags;
 if(!pt_tx_observe || !dp_tx_desc_is_passthru(d)) return;
 spin_lock_irqsave(&pto_lock,flags);
 pto_end(&pto,(unsigned long)d,ktime_get_ns(),ts ? ts->status : -1,
         ts ? ts->transmit_cnt : 0);
 spin_unlock_irqrestore(&pto_lock,flags);
}
static int dp_pto_get(char *buf, const struct kernel_param *kp)
{
 struct pto_stats s; unsigned long flags; u64 now,oldest,epoch;
 unsigned int c,i; int n=0;
 (void)kp;
 spin_lock_irqsave(&pto_lock,flags);
 now=ktime_get_ns(); if(!pto_epoch_ns) pto_epoch_ns=now; epoch=pto_epoch_ns; oldest=pto_oldest(&pto,now); s=pto.s;
 spin_unlock_irqrestore(&pto_lock,flags);
#define PTO_PRINT(...) n+=scnprintf(buf+n,PAGE_SIZE-n,__VA_ARGS__)
 PTO_PRINT("schema=1 enabled=%u epoch_ns=%llu monotonic_ns=%llu calls=%llu accepted=%llu rejected=%llu\n",
           pt_tx_observe,epoch,now,s.calls,s.accepted,s.rejected);
 PTO_PRINT("admitted=%llu active=%llu highwater=%llu completed=%llu release_without_status=%llu overflow=%llu duplicate=%llu unmatched=%llu oldest_ns=%llu\n",
           s.admitted,s.active,s.highwater,s.completed,s.released,
           s.overflow,s.duplicate,s.unmatched,oldest);
 for(c=0;c<PTO_CLASSES;c++) {
  struct pto_class *a=&s.cls[c];
  PTO_PRINT("class=%u completed=%llu bytes=%llu delay_ns=%llu max_ns=%llu tries=%llu active=%llu highwater=%llu delay_bins=",
            c,a->completed,a->bytes,a->delay_ns,a->max_ns,a->tries,a->active,a->highwater);
  for(i=0;i<PTO_BINS;i++) PTO_PRINT("%s%llu",i ? "," : "",a->delay[i]);
  PTO_PRINT(" status_bins=");
  for(i=0;i<17;i++) PTO_PRINT("%s%llu",i ? "," : "",a->status[i]);
  PTO_PRINT("\n");
 }
#undef PTO_PRINT
 return n;
}
static const struct kernel_param_ops dp_pto_ops = { .get=dp_pto_get };
module_param_cb(pt_tx_stats, &dp_pto_ops, NULL, 0444);
#else
static inline void dp_pto_call(bool result, bool rejected) { }
static inline void dp_pto_begin(struct dp_tx_desc_s *d, qdf_nbuf_t nbuf) { }
static inline void dp_pto_end(struct dp_tx_desc_s *d,
                              const struct hal_tx_completion_status *ts) { }
#endif
#endif
