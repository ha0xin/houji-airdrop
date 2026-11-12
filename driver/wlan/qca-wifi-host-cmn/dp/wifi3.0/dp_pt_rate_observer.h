/* SPDX-License-Identifier: ISC */
#ifndef DP_PT_RATE_OBSERVER_H
#define DP_PT_RATE_OBSERVER_H
#ifdef DRIVER_PASSTHRU_DP
#include <linux/moduleparam.h>
#include <linux/spinlock.h>
#ifdef DRIVER_PASSTHRU_OWNER
#include <qdf_pt_remote_peer.h>
#endif
static bool pt_single_ring;
module_param(pt_single_ring, bool, 0444);
static bool pt_rate_observe;
module_param(pt_rate_observe, bool, 0444);
struct pt_rate_counts {
 u64 completed, valid, invalid, tries, amsdu, ldpc;
 u64 ppdus, agg_ppdus, agg_mpdus, ast_overrides, remote_peer_compl;
 u64 bw[8], mcs[16], pkt[16], gi[4], status[17], tx_ring[8], ppdu_bin[6];
 bool have_ppdu;
 u32 last_ppdu_id;
 u32 cur_ppdu_cnt;
 u32 max_ppdu_mpdus;
 u16 last_peer_id;
};
static struct pt_rate_counts pt_rates;
static DEFINE_SPINLOCK(pt_rate_lock);
static void dp_pt_ring_begin(unsigned int ring)
{
 unsigned long flags;
 if (!pt_rate_observe || ring >= 8)
  return;
 spin_lock_irqsave(&pt_rate_lock, flags);
 pt_rates.tx_ring[ring]++;
 spin_unlock_irqrestore(&pt_rate_lock, flags);
}
static inline void dp_pt_ast_override_inc(void)
{
 unsigned long flags;
 if (!pt_rate_observe)
  return;
 spin_lock_irqsave(&pt_rate_lock, flags);
 pt_rates.ast_overrides++;
 spin_unlock_irqrestore(&pt_rate_lock, flags);
}
static inline unsigned int dp_pt_ppdu_bucket(u32 cnt)
{
 if (cnt <= 1)
  return 0;
 if (cnt <= 3)
  return 1;
 if (cnt <= 7)
  return 2;
 if (cnt <= 15)
  return 3;
 if (cnt <= 31)
  return 4;
 return 5;
}
/* Only called with a parsed TQM completion, before descriptor recycling.
 * Does not change status, peer ownership, queueing or callbacks.
 */
static void dp_pt_rate_end(const struct hal_tx_completion_status *ts)
{
 unsigned long flags;
#ifdef DRIVER_PASSTHRU_OWNER
 u16 active_peer;
#endif
 if (!pt_rate_observe || !ts)
  return;
 spin_lock_irqsave(&pt_rate_lock, flags);
 pt_rates.completed++;
 pt_rates.tries += ts->transmit_cnt;
 pt_rates.status[ts->status < 16 ? ts->status : 16]++;
 pt_rates.last_peer_id = ts->peer_id;
#ifdef DRIVER_PASSTHRU_OWNER
 active_peer = pt_remote_peer_active_id();
 if (active_peer != U16_MAX && ts->peer_id == active_peer)
  pt_rates.remote_peer_compl++;
#endif
 if (ts->valid) {
  pt_rates.valid++;
  pt_rates.bw[ts->bw]++;
  pt_rates.mcs[ts->mcs]++;
  pt_rates.pkt[ts->pkt_type]++;
  pt_rates.gi[ts->sgi]++;
  pt_rates.amsdu += ts->msdu_part_of_amsdu;
  pt_rates.ldpc += ts->ldpc;
  if (!pt_rates.have_ppdu || ts->ppdu_id != pt_rates.last_ppdu_id) {
   if (pt_rates.have_ppdu)
    pt_rates.ppdu_bin[dp_pt_ppdu_bucket(pt_rates.cur_ppdu_cnt)]++;
   pt_rates.have_ppdu = true;
   pt_rates.last_ppdu_id = ts->ppdu_id;
   pt_rates.cur_ppdu_cnt = 1;
   pt_rates.ppdus++;
   if (pt_rates.max_ppdu_mpdus < 1)
    pt_rates.max_ppdu_mpdus = 1;
  } else {
   pt_rates.cur_ppdu_cnt++;
   if (pt_rates.cur_ppdu_cnt == 2) {
    pt_rates.agg_ppdus++;
    pt_rates.agg_mpdus += 2;
   } else {
    pt_rates.agg_mpdus++;
   }
   if (pt_rates.cur_ppdu_cnt > pt_rates.max_ppdu_mpdus)
    pt_rates.max_ppdu_mpdus = pt_rates.cur_ppdu_cnt;
  }
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
 RP("schema=1 enabled=%u completed=%llu valid=%llu invalid=%llu tries=%llu amsdu=%llu ldpc=%llu ppdus=%llu agg_ppdus=%llu agg_mpdus=%llu ast_overrides=%llu remote_peer_compl=%llu\n",
    pt_rate_observe, s.completed, s.valid, s.invalid, s.tries, s.amsdu, s.ldpc,
    s.ppdus, s.agg_ppdus, s.agg_mpdus, s.ast_overrides, s.remote_peer_compl);
#define RPA(name, count) do { RP(#name "="); for (i = 0; i < count; i++) RP("%s%llu", i ? "," : "", s.name[i]); RP("\n"); } while (0)
 RPA(bw, 8); RPA(mcs, 16); RPA(pkt, 16); RPA(gi, 4); RPA(status, 17); RPA(tx_ring, 8); RPA(ppdu_bin, 6);
#undef RPA
#undef RP
 return n;
}
static const struct kernel_param_ops pt_rate_ops = { .get = dp_pt_rate_get };
module_param_cb(pt_rate_stats, &pt_rate_ops, NULL, 0444);
static int dp_pt_ampdu_peak_get(char *buf, const struct kernel_param *kp)
{
 unsigned long flags;
 u32 peak;
 u16 last_peer;
 (void)kp;
 spin_lock_irqsave(&pt_rate_lock, flags);
 peak = pt_rates.max_ppdu_mpdus;
 last_peer = pt_rates.last_peer_id;
 spin_unlock_irqrestore(&pt_rate_lock, flags);
 return scnprintf(buf, PAGE_SIZE, "max_ppdu_mpdus=%u last_peer_id=%u\n",
                  peak, last_peer);
}
static const struct kernel_param_ops pt_ampdu_peak_ops = { .get = dp_pt_ampdu_peak_get };
module_param_cb(pt_ampdu_peak, &pt_ampdu_peak_ops, NULL, 0400);
#endif
#endif
