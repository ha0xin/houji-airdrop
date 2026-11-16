/* SPDX-License-Identifier: ISC */
#ifndef DP_PT_RATE_OBSERVER_H
#define DP_PT_RATE_OBSERVER_H
#ifdef DRIVER_PASSTHRU_DP
#include <linux/cache.h>
#include <linux/irqflags.h>
#include <linux/moduleparam.h>
#include <linux/smp.h>
#include <linux/threads.h>
#ifdef DRIVER_PASSTHRU_OWNER
#include <qdf_pt_remote_peer.h>
#endif
static bool pt_single_ring;
module_param(pt_single_ring, bool, 0444);
static bool pt_rate_observe;
module_param(pt_rate_observe, bool, 0644);
struct pt_rate_counts {
 u64 completed, valid, invalid, tries, amsdu, ldpc;
 u64 ppdus, agg_ppdus, agg_mpdus, ast_overrides, remote_peer_compl;
 u64 bw[8], mcs[16], pkt[16], gi[4], status[17], tx_ring[8], ppdu_bin[6];
 bool have_ppdu;
 u32 last_ppdu_id;
 u16 last_ppdu_peer_id;
 u32 cur_ppdu_cnt;
 u32 max_ppdu_mpdus;
 u16 last_peer_id;
};
struct pt_rate_cpu_slot {
 struct pt_rate_counts c;
} ____cacheline_aligned_in_smp;
static struct pt_rate_cpu_slot pt_rates_cpu[NR_CPUS];
static u16 pt_last_peer_id = U16_MAX;

static void dp_pt_ring_begin(unsigned int ring)
{
 unsigned long flags;
 unsigned int cpu;

 if (!READ_ONCE(pt_rate_observe) || ring >= 8)
  return;
 local_irq_save(flags);
 cpu = smp_processor_id();
 if (likely(cpu < ARRAY_SIZE(pt_rates_cpu))) {
  struct pt_rate_counts *r = &pt_rates_cpu[cpu].c;
  WRITE_ONCE(r->tx_ring[ring], r->tx_ring[ring] + 1);
 }
 local_irq_restore(flags);
}

static inline void dp_pt_ast_override_inc(void)
{
 unsigned long flags;
 unsigned int cpu;

 if (!READ_ONCE(pt_rate_observe))
  return;
 local_irq_save(flags);
 cpu = smp_processor_id();
 if (likely(cpu < ARRAY_SIZE(pt_rates_cpu))) {
  struct pt_rate_counts *r = &pt_rates_cpu[cpu].c;
  WRITE_ONCE(r->ast_overrides, r->ast_overrides + 1);
 }
 local_irq_restore(flags);
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
 unsigned int cpu;
 bool remote_active = false;

 if (!READ_ONCE(pt_rate_observe) || !ts)
  return;
#ifdef DRIVER_PASSTHRU_OWNER
 remote_active = pt_remote_peer_is_active_id(ts->peer_id);
#endif
 local_irq_save(flags);
 cpu = smp_processor_id();
 if (likely(cpu < ARRAY_SIZE(pt_rates_cpu))) {
  struct pt_rate_counts *r = &pt_rates_cpu[cpu].c;
  WRITE_ONCE(r->completed, r->completed + 1);
  WRITE_ONCE(r->tries, r->tries + ts->transmit_cnt);
  {
   unsigned int st = ts->status < 16 ? ts->status : 16;
   WRITE_ONCE(r->status[st], r->status[st] + 1);
  }
  WRITE_ONCE(pt_last_peer_id, ts->peer_id);
  if (remote_active)
   WRITE_ONCE(r->remote_peer_compl, r->remote_peer_compl + 1);
  if (ts->valid) {
   WRITE_ONCE(r->valid, r->valid + 1);
   if (likely(ts->bw < 8))
    WRITE_ONCE(r->bw[ts->bw], r->bw[ts->bw] + 1);
   if (likely(ts->mcs < 16))
    WRITE_ONCE(r->mcs[ts->mcs], r->mcs[ts->mcs] + 1);
   if (likely(ts->pkt_type < 16))
    WRITE_ONCE(r->pkt[ts->pkt_type], r->pkt[ts->pkt_type] + 1);
   if (likely(ts->sgi < 4))
    WRITE_ONCE(r->gi[ts->sgi], r->gi[ts->sgi] + 1);
   if (ts->msdu_part_of_amsdu)
    WRITE_ONCE(r->amsdu, r->amsdu + 1);
   if (ts->ldpc)
    WRITE_ONCE(r->ldpc, r->ldpc + 1);
   if (!r->have_ppdu || ts->ppdu_id != r->last_ppdu_id ||
       ts->peer_id != r->last_ppdu_peer_id) {
    if (r->have_ppdu) {
     unsigned int b = dp_pt_ppdu_bucket(r->cur_ppdu_cnt);
     WRITE_ONCE(r->ppdu_bin[b], r->ppdu_bin[b] + 1);
    }
    r->have_ppdu = true;
    r->last_ppdu_id = ts->ppdu_id;
    r->last_ppdu_peer_id = ts->peer_id;
    r->cur_ppdu_cnt = 1;
    WRITE_ONCE(r->ppdus, r->ppdus + 1);
    if (r->max_ppdu_mpdus < 1)
     WRITE_ONCE(r->max_ppdu_mpdus, 1);
   } else {
    r->cur_ppdu_cnt++;
    if (r->cur_ppdu_cnt == 2) {
     WRITE_ONCE(r->agg_ppdus, r->agg_ppdus + 1);
     WRITE_ONCE(r->agg_mpdus, r->agg_mpdus + 2);
    } else {
     WRITE_ONCE(r->agg_mpdus, r->agg_mpdus + 1);
    }
    if (r->cur_ppdu_cnt > r->max_ppdu_mpdus)
     WRITE_ONCE(r->max_ppdu_mpdus, r->cur_ppdu_cnt);
   }
  } else {
   WRITE_ONCE(r->invalid, r->invalid + 1);
  }
 }
 local_irq_restore(flags);
}

static void dp_pt_rate_aggregate(struct pt_rate_counts *s)
{
 unsigned int cpu, i;

 memset(s, 0, sizeof(*s));
 for (cpu = 0; cpu < ARRAY_SIZE(pt_rates_cpu); cpu++) {
  const struct pt_rate_counts *r = &pt_rates_cpu[cpu].c;
  u32 peak;

  s->completed += READ_ONCE(r->completed);
  s->valid += READ_ONCE(r->valid);
  s->invalid += READ_ONCE(r->invalid);
  s->tries += READ_ONCE(r->tries);
  s->amsdu += READ_ONCE(r->amsdu);
  s->ldpc += READ_ONCE(r->ldpc);
  s->ppdus += READ_ONCE(r->ppdus);
  s->agg_ppdus += READ_ONCE(r->agg_ppdus);
  s->agg_mpdus += READ_ONCE(r->agg_mpdus);
  s->ast_overrides += READ_ONCE(r->ast_overrides);
  s->remote_peer_compl += READ_ONCE(r->remote_peer_compl);
  for (i = 0; i < 8; i++)
   s->bw[i] += READ_ONCE(r->bw[i]);
  for (i = 0; i < 16; i++)
   s->mcs[i] += READ_ONCE(r->mcs[i]);
  for (i = 0; i < 16; i++)
   s->pkt[i] += READ_ONCE(r->pkt[i]);
  for (i = 0; i < 4; i++)
   s->gi[i] += READ_ONCE(r->gi[i]);
  for (i = 0; i < 17; i++)
   s->status[i] += READ_ONCE(r->status[i]);
  for (i = 0; i < 8; i++)
   s->tx_ring[i] += READ_ONCE(r->tx_ring[i]);
  for (i = 0; i < 6; i++)
   s->ppdu_bin[i] += READ_ONCE(r->ppdu_bin[i]);
  peak = READ_ONCE(r->max_ppdu_mpdus);
  if (peak > s->max_ppdu_mpdus)
   s->max_ppdu_mpdus = peak;
 }
 s->last_peer_id = READ_ONCE(pt_last_peer_id);
}

static int dp_pt_rate_get(char *buf, const struct kernel_param *kp)
{
 struct pt_rate_counts s;
 int n = 0, i;
 (void)kp;
 dp_pt_rate_aggregate(&s);
#define RP(...) n += scnprintf(buf + n, PAGE_SIZE - n, __VA_ARGS__)
 RP("schema=1 enabled=%u completed=%llu valid=%llu invalid=%llu tries=%llu amsdu=%llu ldpc=%llu ppdus=%llu agg_ppdus=%llu agg_mpdus=%llu ast_overrides=%llu remote_peer_compl=%llu\n",
    READ_ONCE(pt_rate_observe), s.completed, s.valid, s.invalid, s.tries,
    s.amsdu, s.ldpc, s.ppdus, s.agg_ppdus, s.agg_mpdus, s.ast_overrides,
    s.remote_peer_compl);
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
 struct pt_rate_counts s;
 (void)kp;
 dp_pt_rate_aggregate(&s);
 return scnprintf(buf, PAGE_SIZE, "max_ppdu_mpdus=%u last_peer_id=%u\n",
                  s.max_ppdu_mpdus, s.last_peer_id);
}
static const struct kernel_param_ops pt_ampdu_peak_ops = { .get = dp_pt_ampdu_peak_get };
module_param_cb(pt_ampdu_peak, &pt_ampdu_peak_ops, NULL, 0400);
#endif
#endif
