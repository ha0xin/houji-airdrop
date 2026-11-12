/* SPDX-License-Identifier: ISC */
#ifndef DP_PT_BA_PEER_STATE_H
#define DP_PT_BA_PEER_STATE_H

#include <asm/unaligned.h>
#include <linux/bitmap.h>
#include <linux/if_ether.h>
#include "dp_pt_ba_peer.h"

static u8 pt_ba_peer_address[ETH_ALEN];
static bool pt_ba_peer_configured;

static int pt_ba_hex(char c)
{
	if (c >= '0' && c <= '9')
		return c - '0';
	if (c >= 'a' && c <= 'f')
		return c - 'a' + 10;
	if (c >= 'A' && c <= 'F')
		return c - 'A' + 10;
	return -EINVAL;
}

static int pt_ba_peer_set(const char *value, const struct kernel_param *kp)
{
	u8 mac[ETH_ALEN], nonzero = 0;
	int i, hi, lo;

	if (!value || strlen(value) != 3 * ETH_ALEN - 1)
		return -EINVAL;
	for (i = 0; i < ETH_ALEN; i++) {
		hi = pt_ba_hex(value[3 * i]);
		lo = pt_ba_hex(value[3 * i + 1]);
		if (hi < 0 || lo < 0 ||
		    (i + 1 < ETH_ALEN && value[3 * i + 2] != ':'))
			return -EINVAL;
		mac[i] = hi * 16 + lo;
		nonzero |= mac[i];
	}
	if (!nonzero || (mac[0] & 1))
		return -EINVAL;

	memcpy(pt_ba_peer_address, mac, ETH_ALEN);
	pt_ba_peer_configured = true;
	return 0;
}

static int pt_ba_peer_get(char *buf, const struct kernel_param *kp)
{
	return scnprintf(buf, PAGE_SIZE, "%u\n", pt_ba_peer_configured);
}

static const struct kernel_param_ops pt_ba_peer_ops = {
	.set = pt_ba_peer_set,
	.get = pt_ba_peer_get,
};
module_param_cb(pt_ba_peer, &pt_ba_peer_ops, NULL, 0444);
MODULE_PARM_DESC(pt_ba_peer, "Opt-in benchmark peer MAC; readback reports configuration only");

static DEFINE_SPINLOCK(pt_ba_peer_lock);
/* Firmware peer IDs are u16. A reused ID cannot identify a late HTT event. */
static DECLARE_BITMAP(pt_ba_seen_peer_ids, 1 << 16);
static DECLARE_BITMAP(pt_ba_reused_peer_ids, 1 << 16);

struct pt_ba_peer_counts {
	u64 vdev_starts, self_map, self_unmap, remote_map_event, remote_map;
	u64 remote_unmap, reused_map, unknown_identity;
	u64 unmatched_request, incomplete_request, unknown_fcs, request_tid[16];
	u64 event[PT_BA_PEER_EVENTS], htt_tid[16], reo_aggregate_submit;
	u16 last_window, last_ssn, last_htt_window;
	u8 vdev_id;
	bool open, mapped;
	u16 peer_id;
};

static struct pt_ba_peer_counts pt_ba_peer_counts;

static bool pt_ba_peer_enabled(void)
{
	return READ_ONCE(pt_ba_observe) && READ_ONCE(pt_ba_peer_configured);
}

static bool pt_ba_mac_matches(const u8 *mac)
{
	return !memcmp(mac, pt_ba_peer_address, ETH_ALEN);
}

/* These diagnostics accept only one PASSTHRU vdev lifetime per module load. */
static bool pt_ba_peer_scope(const struct dp_vdev *vdev)
{
	return pt_ba_peer_counts.vdev_starts == 1 &&
	       pt_ba_peer_counts.open &&
	       vdev->opmode == wlan_op_mode_passthru &&
	       pt_ba_peer_counts.vdev_id == vdev->vdev_id;
}

void pt_ba_peer_vdev_start(const struct dp_vdev *vdev)
{
	unsigned long flags;

	if (!pt_ba_peer_enabled())
		return;

	spin_lock_irqsave(&pt_ba_peer_lock, flags);
	pt_ba_peer_counts.vdev_starts++;
	pt_ba_peer_counts.vdev_id = vdev->vdev_id;
	pt_ba_peer_counts.open = true;
	pt_ba_peer_counts.mapped = false;
	spin_unlock_irqrestore(&pt_ba_peer_lock, flags);
}

void pt_ba_peer_vdev_stop(const struct dp_vdev *vdev)
{
	unsigned long flags;

	if (!pt_ba_peer_enabled())
		return;

	spin_lock_irqsave(&pt_ba_peer_lock, flags);
	if (pt_ba_peer_counts.vdev_id == vdev->vdev_id) {
		pt_ba_peer_counts.open = false;
		pt_ba_peer_counts.mapped = false;
	}
	spin_unlock_irqrestore(&pt_ba_peer_lock, flags);
}

void pt_ba_peer_map_event(struct dp_soc *soc, u8 vdev_id, u16 peer_id,
			 const u8 *mac)
{
	struct dp_vdev *vdev = NULL;
	unsigned long flags;
	bool reused;

	if (!pt_ba_peer_enabled())
		return;
	if (pt_ba_mac_matches(mac))
		vdev = dp_vdev_get_ref_by_id(soc, vdev_id, DP_MOD_ID_HTT);

	spin_lock_irqsave(&pt_ba_peer_lock, flags);
	reused = test_bit(peer_id, pt_ba_seen_peer_ids);
	__set_bit(peer_id, pt_ba_seen_peer_ids);
	if (reused)
		__set_bit(peer_id, pt_ba_reused_peer_ids);
	if (reused && pt_ba_peer_counts.mapped &&
	    pt_ba_peer_counts.peer_id == peer_id) {
		pt_ba_peer_counts.mapped = false;
		pt_ba_peer_counts.unknown_identity++;
	}
	if (vdev && pt_ba_peer_scope(vdev))
		pt_ba_peer_counts.remote_map_event++;
	spin_unlock_irqrestore(&pt_ba_peer_lock, flags);

	if (vdev)
		dp_vdev_unref_delete(soc, vdev, DP_MOD_ID_HTT);
}

void pt_ba_peer_map_result(const struct dp_peer *peer, u16 peer_id)
{
	unsigned long flags;
	bool reused;

	if (!pt_ba_peer_enabled() ||
	    peer->vdev->opmode != wlan_op_mode_passthru)
		return;

	spin_lock_irqsave(&pt_ba_peer_lock, flags);
	if (!memcmp(peer->mac_addr.raw, peer->vdev->mac_addr.raw, ETH_ALEN)) {
		pt_ba_peer_counts.self_map++;
	} else if (pt_ba_mac_matches(peer->mac_addr.raw)) {
		pt_ba_peer_counts.remote_map++;
		/* A duplicate notification may have arrived during host lookup. */
		reused = test_bit(peer_id, pt_ba_reused_peer_ids);
		if (reused)
			pt_ba_peer_counts.reused_map++;
		/* Do not attribute HTT/REO if ID or vdev identity is ambiguous. */
		if (!pt_ba_peer_scope(peer->vdev) || reused ||
		    READ_ONCE(peer->peer_id) != peer_id ||
		    pt_ba_peer_counts.mapped) {
			pt_ba_peer_counts.unknown_identity++;
			pt_ba_peer_counts.mapped = false;
		} else {
			pt_ba_peer_counts.peer_id = peer_id;
			pt_ba_peer_counts.mapped = true;
		}
	}
	spin_unlock_irqrestore(&pt_ba_peer_lock, flags);
}

void pt_ba_peer_unmap(const struct dp_peer *peer, u16 peer_id)
{
	unsigned long flags;

	if (!pt_ba_peer_enabled())
		return;

	spin_lock_irqsave(&pt_ba_peer_lock, flags);
	/* A retired ID cannot be attributed even if its map callback was late. */
	__set_bit(peer_id, pt_ba_reused_peer_ids);
	if (pt_ba_peer_counts.mapped && pt_ba_peer_counts.peer_id == peer_id)
		pt_ba_peer_counts.mapped = false;
	if (peer->vdev->opmode == wlan_op_mode_passthru) {
		if (!memcmp(peer->mac_addr.raw, peer->vdev->mac_addr.raw, ETH_ALEN))
			pt_ba_peer_counts.self_unmap++;
		else if (pt_ba_mac_matches(peer->mac_addr.raw))
			pt_ba_peer_counts.remote_unmap++;
	}
	spin_unlock_irqrestore(&pt_ba_peer_lock, flags);
}

void pt_ba_peer_record(const struct dp_peer *peer, enum pt_ba_peer_event event,
		       int tid, u32 window)
{
	unsigned long flags;

	if (!pt_ba_peer_enabled())
		return;
	if (peer && (peer->vdev->opmode != wlan_op_mode_passthru ||
		     !pt_ba_mac_matches(peer->mac_addr.raw)))
		return;

	spin_lock_irqsave(&pt_ba_peer_lock, flags);
	if (peer && (!pt_ba_peer_scope(peer->vdev) || !pt_ba_peer_counts.mapped ||
		     pt_ba_peer_counts.peer_id != READ_ONCE(peer->peer_id))) {
		pt_ba_peer_counts.unknown_identity++;
		goto unlock;
	}
	pt_ba_peer_counts.event[event]++;
	if (event == PT_BA_HTT_LOOKUP && tid >= 0 && tid < 16) {
		pt_ba_peer_counts.htt_tid[tid]++;
		pt_ba_peer_counts.last_htt_window = window;
	}
	if (event == PT_BA_REO_SUBMIT_OK && window > 1)
		pt_ba_peer_counts.reo_aggregate_submit++;
unlock:
	spin_unlock_irqrestore(&pt_ba_peer_lock, flags);
}

/* Caller has the existing linear, FCS-checked raw buffer and vdev reference. */
static void pt_ba_peer_raw(const struct dp_vdev *vdev, qdf_nbuf_t nbuf,
			   bool fcs_present, bool fcs_error)
{
	const u8 *frame = qdf_nbuf_data(nbuf);
	unsigned long flags;
	u32 len = qdf_nbuf_len(nbuf);
	u16 params;

	if (!pt_ba_peer_enabled() || fcs_error || len < 26 ||
	    frame[0] != 0xd0 || (frame[1] & 0xc7) || (frame[22] & 0x0f) ||
	    frame[24] != 3 || frame[25] != 0)
		return;

	spin_lock_irqsave(&pt_ba_peer_lock, flags);
	if (!pt_ba_peer_scope(vdev)) {
		pt_ba_peer_counts.unknown_identity++;
	} else if (!pt_ba_mac_matches(frame + 10) ||
		   memcmp(frame + 4, vdev->mac_addr.raw, ETH_ALEN)) {
		pt_ba_peer_counts.unmatched_request++;
	} else if (len < 33 || (fcs_present && len < 33 + ETH_FCS_LEN)) {
		pt_ba_peer_counts.incomplete_request++;
	} else if (!fcs_present && len < 33 + ETH_FCS_LEN) {
		/* Without FCS metadata the last four bytes may be a trailer. */
		pt_ba_peer_counts.unknown_fcs++;
	} else {
		params = get_unaligned_le16(frame + 27);
		pt_ba_peer_counts.request_tid[(params >> 2) & 0xf]++;
		pt_ba_peer_counts.last_window = params >> 6;
		pt_ba_peer_counts.last_ssn = get_unaligned_le16(frame + 31) >> 4;
	}
	spin_unlock_irqrestore(&pt_ba_peer_lock, flags);
}

static int pt_ba_peer_stats_get(char *buf, const struct kernel_param *kp)
{
	struct pt_ba_peer_counts s;
	unsigned long flags;
	int n, i;

	spin_lock_irqsave(&pt_ba_peer_lock, flags);
	s = pt_ba_peer_counts;
	spin_unlock_irqrestore(&pt_ba_peer_lock, flags);

	n = scnprintf(buf, PAGE_SIZE,
		"schema=1 enabled=%u vdev_starts=%llu open=%u mapped=%u\n"
		"self_map=%llu self_unmap=%llu remote_map_event=%llu remote_map=%llu "
		"remote_unmap=%llu reused_map=%llu unknown_identity=%llu\n"
		"unmatched_request=%llu incomplete_request=%llu unknown_fcs=%llu "
		"last_window=%u last_ssn=%u last_htt_window=%u\n"
		"htt_missing_global=%llu htt_lookup=%llu htt_host_success=%llu "
		"htt_host_failure=%llu reo_no_route=%llu reo_qdesc=%llu "
		"reo_no_qdesc=%llu reo_submit_ok=%llu reo_submit_error=%llu "
		"reo_aggregate_submit=%llu\n",
		pt_ba_peer_enabled(), s.vdev_starts, s.open, s.mapped,
		s.self_map, s.self_unmap, s.remote_map_event, s.remote_map,
		s.remote_unmap, s.reused_map, s.unknown_identity,
		s.unmatched_request, s.incomplete_request, s.unknown_fcs, s.last_window,
		s.last_ssn, s.last_htt_window,
		s.event[PT_BA_HTT_MISSING], s.event[PT_BA_HTT_LOOKUP],
		s.event[PT_BA_HTT_HOST_SUCCESS], s.event[PT_BA_HTT_HOST_FAILURE],
		s.event[PT_BA_REO_NO_ROUTE], s.event[PT_BA_REO_QDESC],
		s.event[PT_BA_REO_NO_QDESC], s.event[PT_BA_REO_SUBMIT_OK],
		s.event[PT_BA_REO_SUBMIT_ERROR], s.reo_aggregate_submit);
	n += scnprintf(buf + n, PAGE_SIZE - n, "request_tid=");
	for (i = 0; i < ARRAY_SIZE(s.request_tid); i++)
		n += scnprintf(buf + n, PAGE_SIZE - n, "%s%llu",
			       i ? "," : "", s.request_tid[i]);
	n += scnprintf(buf + n, PAGE_SIZE - n, "\nhtt_tid=");
	for (i = 0; i < ARRAY_SIZE(s.htt_tid); i++)
		n += scnprintf(buf + n, PAGE_SIZE - n, "%s%llu",
			       i ? "," : "", s.htt_tid[i]);
	return n + scnprintf(buf + n, PAGE_SIZE - n, "\n");
}

static const struct kernel_param_ops pt_ba_peer_stats_ops = {
	.get = pt_ba_peer_stats_get,
};
module_param_cb(pt_ba_peer_stats, &pt_ba_peer_stats_ops, NULL, 0444);
MODULE_PARM_DESC(pt_ba_peer_stats, "Read-only single-peer PASSTHRU RX lifecycle counters");
#endif
