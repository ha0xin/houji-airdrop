/* SPDX-License-Identifier: ISC */
#ifndef DP_PT_RATE_OBSERVER_H
#define DP_PT_RATE_OBSERVER_H
#ifdef DRIVER_PASSTHRU_DP
#include <linux/moduleparam.h>
#include <linux/spinlock.h>
static bool pt_rate_observe;
module_param(pt_rate_observe, bool, 0444);
struct pt_rate_counts {
 u64 completed, valid, invalid, tries, amsdu;
 u64 bw[8], mcs[16], pkt[16], gi[4], status[17];
};
static struct pt_rate_counts pt_rates;
static DEFINE_SPINLOCK(pt_rate_lock);
/* Only called with a parsed TQM completion, before descriptor recycling.
 * Does not change status, peer ownership, queueing or callbacks.
 */
static void dp_pt_rate_end(const struct hal_tx_completion_status *ts)
{
 unsigned long flags;
 if (!pt_rate_observe || !ts)
  return;
 spin_lock_irqsave(&pt_rate_lock, flags);
 pt_rates.completed++;
 pt_rates.tries += ts->transmit_cnt;
 pt_rates.status[ts->status < 16 ? ts->status : 16]++;
 if (ts->valid) {
  pt_rates.valid++;
  pt_rates.bw[ts->bw]++;
  pt_rates.mcs[ts->mcs]++;
  pt_rates.pkt[ts->pkt_type]++;
  pt_rates.gi[ts->sgi]++;
  pt_rates.amsdu += ts->msdu_part_of_amsdu;
 } else {
  pt_rates.invalid++;
 }
 spin_unlock_irqrestore(&pt_rate_lock, flags);
}
static int dp_pt_rate_get(char *buf, const struct kernel_param *kp)
{
 struct pt_rate_counts s;
 unsigned long flags;
 int n = 0, i;
 (void)kp;
 spin_lock_irqsave(&pt_rate_lock, flags);
 s = pt_rates;
 spin_unlock_irqrestore(&pt_rate_lock, flags);
#define RP(...) n += scnprintf(buf + n, PAGE_SIZE - n, __VA_ARGS__)
 RP("schema=1 enabled=%u completed=%llu valid=%llu invalid=%llu tries=%llu amsdu=%llu\n",
    pt_rate_observe, s.completed, s.valid, s.invalid, s.tries, s.amsdu);
#define RPA(name, count) do { RP(#name "="); for (i=0;i<count;i++) RP("%s%llu",i?",":"",s.name[i]); RP("\n"); } while (0)
 RPA(bw, 8); RPA(mcs, 16); RPA(pkt, 16); RPA(gi, 4); RPA(status, 17);
#undef RPA
#undef RP
 return n;
}
static const struct kernel_param_ops pt_rate_ops = { .get = dp_pt_rate_get };
module_param_cb(pt_rate_stats, &pt_rate_ops, NULL, 0444);
#endif
#endif
