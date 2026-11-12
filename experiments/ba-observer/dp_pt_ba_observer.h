/* SPDX-License-Identifier: ISC */
#ifndef DP_PT_BA_OBSERVER_H
#define DP_PT_BA_OBSERVER_H

#include <linux/moduleparam.h>
#include <linux/spinlock.h>

static bool pt_ba_observe;
module_param(pt_ba_observe, bool, 0444);
MODULE_PARM_DESC(pt_ba_observe, "Read-only PASSTHRU RX aggregation counters (default off)");

enum pt_ba_reject {
	PT_BA_RX_PRECHECK,
	PT_BA_RX_LAYOUT,
	PT_BA_RX_NONRAW,
	PT_BA_RX_RADIOTAP,
	PT_BA_RX_REJECTS,
};

struct pt_ba_rx_counts {
	u64 reject[PT_BA_RX_REJECTS];
	u64 raw, bad_fcs, layout_unknown, nonampdu, ampdu;
	u64 ampdu_ppdu_adjacent, ampdu_tid[16], ampdu_tid_unknown;
	u64 ba_action_header[3];
	u16 last_ppdu;
	u8 last_vdev;
	bool last_ampdu;
};

static DEFINE_SPINLOCK(pt_ba_lock);
static struct pt_ba_rx_counts pt_ba_rx;

static void pt_ba_note_reject(enum pt_ba_reject reason)
{
	unsigned long flags;

	if (!READ_ONCE(pt_ba_observe))
		return;

	spin_lock_irqsave(&pt_ba_lock, flags);
	pt_ba_rx.reject[reason]++;
	spin_unlock_irqrestore(&pt_ba_lock, flags);
}

/*
 * Input is the validated, linear raw buffer before radiotap prepending.
 * Reuse the existing Kiwi layout check before asking HAL for new fields.
 * In particular, raw_mpdu does not imply ampdu_flag.
 */
static void pt_ba_note_raw(struct dp_soc *soc, struct dp_vdev *vdev,
			  qdf_nbuf_t nbuf, u8 *tlv, u32 tlv_len, bool bad_fcs)
{
	struct hal_soc *hal;
	const u8 *frame;
	unsigned long flags;
	u64 ticks;
	s8 rssi;
	u16 ppdu = 0;
	int tid = -1, ba_action = -1;
	bool known, ampdu = false;

	if (!READ_ONCE(pt_ba_observe))
		return;

	hal = (struct hal_soc *)soc->hal_soc;
	known = hal && hal->ops &&
		hal->ops->hal_rx_mpdu_info_ampdu_flag_get &&
		hal->ops->hal_rx_tlv_phy_ppdu_id_get &&
		hal_rx_passthru_kiwi_metadata(soc->hal_soc, tlv, tlv_len,
					      &ticks, &rssi);
	if (known) {
		ampdu = hal_rx_mpdu_info_ampdu_flag_get(soc->hal_soc, tlv);
		ppdu = hal_rx_attn_phy_ppdu_id_get(soc->hal_soc, tlv);
	}

	/* Only classify complete, FCS-good three-address QoS Data headers. */
	frame = qdf_nbuf_data(nbuf);
	if (ampdu && !bad_fcs && qdf_nbuf_len(nbuf) >= 26 &&
	    (frame[0] & 0xfc) == 0x88 && !(frame[1] & 0x03))
		tid = frame[24] & 0x0f;
	/* Count clear three-address Block Ack Action headers, not sessions. */
	if (!bad_fcs && qdf_nbuf_len(nbuf) >= 26 && frame[0] == 0xd0 &&
	    !(frame[1] & 0xc7) && frame[24] == 3 && frame[25] <= 2)
		ba_action = frame[25];

	spin_lock_irqsave(&pt_ba_lock, flags);
	pt_ba_rx.raw++;
	pt_ba_rx.bad_fcs += bad_fcs;
	if (ba_action >= 0)
		pt_ba_rx.ba_action_header[ba_action]++;
	if (!known) {
		pt_ba_rx.layout_unknown++;
	} else if (!ampdu) {
		pt_ba_rx.nonampdu++;
	} else {
		pt_ba_rx.ampdu++;
		if (tid < 0)
			pt_ba_rx.ampdu_tid_unknown++;
		else
			pt_ba_rx.ampdu_tid[tid]++;
		/* Adjacent observations, never a unique PPDU or BA count. */
		if (pt_ba_rx.last_ampdu && pt_ba_rx.last_ppdu == ppdu &&
		    pt_ba_rx.last_vdev == vdev->vdev_id)
			pt_ba_rx.ampdu_ppdu_adjacent++;
		pt_ba_rx.last_ppdu = ppdu;
		pt_ba_rx.last_vdev = vdev->vdev_id;
	}
	pt_ba_rx.last_ampdu = known && ampdu;
	spin_unlock_irqrestore(&pt_ba_lock, flags);
}

static int pt_ba_stats_get(char *buf, const struct kernel_param *kp)
{
	struct pt_ba_rx_counts counts;
	unsigned long flags;
	int n;
	int tid;

	(void)kp;
	spin_lock_irqsave(&pt_ba_lock, flags);
	counts = pt_ba_rx;
	spin_unlock_irqrestore(&pt_ba_lock, flags);

	n = scnprintf(buf, PAGE_SIZE,
		"schema=1 enabled=%u raw=%llu bad_fcs=%llu unknown=%llu "
		"nonampdu=%llu ampdu=%llu ampdu_ppdu_adjacent=%llu "
		"ampdu_tid_unknown=%llu\n"
		"addba_request_header=%llu addba_response_header=%llu "
		"delba_header=%llu\n"
		"reject_precheck=%llu reject_layout=%llu reject_nonraw=%llu "
		"reject_radiotap=%llu\nampdu_tid=",
		READ_ONCE(pt_ba_observe), counts.raw, counts.bad_fcs,
		counts.layout_unknown, counts.nonampdu, counts.ampdu,
		counts.ampdu_ppdu_adjacent, counts.ampdu_tid_unknown,
		counts.ba_action_header[0], counts.ba_action_header[1],
		counts.ba_action_header[2], counts.reject[PT_BA_RX_PRECHECK], counts.reject[PT_BA_RX_LAYOUT],
		counts.reject[PT_BA_RX_NONRAW], counts.reject[PT_BA_RX_RADIOTAP]);
	for (tid = 0; tid < ARRAY_SIZE(counts.ampdu_tid); tid++)
		n += scnprintf(buf + n, PAGE_SIZE - n, "%s%llu",
			       tid ? "," : "", counts.ampdu_tid[tid]);
	return n + scnprintf(buf + n, PAGE_SIZE - n, "\n");
}

static const struct kernel_param_ops pt_ba_stats_ops = {
	.get = pt_ba_stats_get,
};
module_param_cb(pt_ba_stats, &pt_ba_stats_ops, NULL, 0444);
MODULE_PARM_DESC(pt_ba_stats, "PASSTHRU RX observation snapshot");

#endif
