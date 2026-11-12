/* Included only by the PASSTHRU raw RX implementation. */
#include <linux/etherdevice.h>

static u8 pt_rx_phy_mac[ETH_ALEN] __aligned(2);

static int pt_rx_phy_peer_set(const char *value, const struct kernel_param *kp)
{
	u8 mac[ETH_ALEN] __aligned(2);

	if (!THIS_MODULE || READ_ONCE(THIS_MODULE->state) != MODULE_STATE_COMING)
		return -EPERM;
	if (is_valid_ether_addr(pt_rx_phy_mac) || strlen(value) != 17 ||
	    !mac_pton(value, mac) || !is_valid_ether_addr(mac))
		return -EINVAL;

	ether_addr_copy(pt_rx_phy_mac, mac);
	return 0;
}

static int pt_rx_phy_peer_get(char *buf, const struct kernel_param *kp)
{
	if (!is_valid_ether_addr(pt_rx_phy_mac))
		return scnprintf(buf, PAGE_SIZE, "unset\n");
	return scnprintf(buf, PAGE_SIZE, "%pM\n", pt_rx_phy_mac);
}

static const struct kernel_param_ops pt_rx_phy_peer_ops = {
	.set = pt_rx_phy_peer_set,
	.get = pt_rx_phy_peer_get,
};
module_param_cb(pt_rx_phy_peer, &pt_rx_phy_peer_ops, NULL, 0400);

enum pt_rx_phy_count {
	PT_PHY_MATCHED,
	PT_PHY_FCS,
	PT_PHY_LAYOUT,
	PT_PHY_NOT_DONE,
	PT_PHY_PPDU_MISMATCH,
	PT_PHY_RANGE,
	PT_PHY_OUTSIDE_SCOPE,
	PT_PHY_VALID,
	PT_PHY_RETRY,
	PT_PHY_AMSDU,
	PT_PHY_RX_AMPDU,
	PT_PHY_COUNTS,
};

static atomic64_t pt_phy_counts[PT_PHY_COUNTS];
static atomic64_t pt_phy_packet_type[16];
static atomic64_t pt_phy_type_bw[4][3];
static atomic64_t pt_phy_mcs[2][16];
static atomic64_t pt_phy_nss[2][8];
static atomic64_t pt_phy_gi[2][4];
static atomic64_t pt_phy_reception[8];
static atomic64_t pt_phy_qos_ack[4];
static atomic64_t pt_phy_length[4];

static int pt_rx_phy_hist(char *buf, int used, const char *name,
			 atomic64_t *values, unsigned int count)
{
	unsigned int i;

	used += scnprintf(buf + used, PAGE_SIZE - used, " %s=", name);
	for (i = 0; i < count; i++)
		used += scnprintf(buf + used, PAGE_SIZE - used, "%s%lld",
				  i ? "," : "", atomic64_read(&values[i]));
	return used;
}

static int pt_rx_phy_stats_get(char *buf, const struct kernel_param *kp)
{
	int used;

	used = scnprintf(buf, PAGE_SIZE,
			"schema=1 enabled=%u matched=%lld fcs=%lld layout=%lld not_done=%lld"
			" ppdu_mismatch=%lld range=%lld outside_scope=%lld valid=%lld"
			" retry=%lld amsdu=%lld rx_ampdu=%lld ampdu=unknown",
			is_valid_ether_addr(pt_rx_phy_mac),
			atomic64_read(&pt_phy_counts[PT_PHY_MATCHED]),
			atomic64_read(&pt_phy_counts[PT_PHY_FCS]),
			atomic64_read(&pt_phy_counts[PT_PHY_LAYOUT]),
			atomic64_read(&pt_phy_counts[PT_PHY_NOT_DONE]),
			atomic64_read(&pt_phy_counts[PT_PHY_PPDU_MISMATCH]),
			atomic64_read(&pt_phy_counts[PT_PHY_RANGE]),
			atomic64_read(&pt_phy_counts[PT_PHY_OUTSIDE_SCOPE]),
			atomic64_read(&pt_phy_counts[PT_PHY_VALID]),
			atomic64_read(&pt_phy_counts[PT_PHY_RETRY]),
			atomic64_read(&pt_phy_counts[PT_PHY_AMSDU]),
			atomic64_read(&pt_phy_counts[PT_PHY_RX_AMPDU]));
	used = pt_rx_phy_hist(buf, used, "packet_type", pt_phy_packet_type, 16);
	used = pt_rx_phy_hist(buf, used, "ofdm_bw", pt_phy_type_bw[0], 3);
	used = pt_rx_phy_hist(buf, used, "cck_bw", pt_phy_type_bw[1], 3);
	used = pt_rx_phy_hist(buf, used, "ht_bw", pt_phy_type_bw[2], 3);
	used = pt_rx_phy_hist(buf, used, "vht_bw", pt_phy_type_bw[3], 3);
	used = pt_rx_phy_hist(buf, used, "ht_mcs", pt_phy_mcs[0], 16);
	used = pt_rx_phy_hist(buf, used, "vht_mcs", pt_phy_mcs[1], 10);
	used = pt_rx_phy_hist(buf, used, "ht_nss", pt_phy_nss[0], 8);
	used = pt_rx_phy_hist(buf, used, "vht_nss", pt_phy_nss[1], 8);
	used = pt_rx_phy_hist(buf, used, "ht_gi", pt_phy_gi[0], 4);
	used = pt_rx_phy_hist(buf, used, "vht_gi", pt_phy_gi[1], 4);
	used = pt_rx_phy_hist(buf, used, "reception", pt_phy_reception, 8);
	used = pt_rx_phy_hist(buf, used, "qos_ack_policy", pt_phy_qos_ack, 4);
	used = pt_rx_phy_hist(buf, used, "raw_len", pt_phy_length, 4);
	return used + scnprintf(buf + used, PAGE_SIZE - used, "\n");
}

static const struct kernel_param_ops pt_rx_phy_stats_ops = {
	.get = pt_rx_phy_stats_get,
};
module_param_cb(pt_rx_phy_stats, &pt_rx_phy_stats_ops, NULL, 0400);

/*
 * Call before radiotap, with PASSTHRU admission and an existing DP vdev ref.
 * The current observer counts only complete raw frames reaching this point.
 */
static void pt_rx_phy_record(struct dp_soc *soc, struct dp_vdev *vdev,
			     qdf_nbuf_t nbuf, u8 *tlvs, u32 tlv_len,
			     bool fcs_error)
{
	static const u8 awdl_bssid[ETH_ALEN] = {0x00, 0x25, 0x00, 0xff, 0x94, 0x73};
	struct hal_soc *hal = (struct hal_soc *)soc->hal_soc;
	const u8 *raw = qdf_nbuf_data(nbuf);
	u32 len = qdf_nbuf_len(nbuf), type, bw, reception, mcs, nss, gi, row;
	enum pt_rx_phy_count result;

	if (!is_valid_ether_addr(pt_rx_phy_mac) || len <= 26 || raw[0] != 0x88 ||
	    (raw[1] & 0xc7) || (raw[22] & 0x0f) ||
	    memcmp(raw + 4, vdev->mac_addr.raw, ETH_ALEN) ||
	    memcmp(raw + 10, pt_rx_phy_mac, ETH_ALEN) ||
	    memcmp(raw + 16, awdl_bssid, ETH_ALEN))
		return;

	atomic64_inc(&pt_phy_counts[PT_PHY_MATCHED]);
	if (fcs_error) {
		result = PT_PHY_FCS;
		goto count;
	}

	atomic64_add(!!(raw[1] & 0x08), &pt_phy_counts[PT_PHY_RETRY]);
	atomic64_add(!!(raw[24] & 0x80), &pt_phy_counts[PT_PHY_AMSDU]);
	atomic64_inc(&pt_phy_qos_ack[(raw[24] >> 5) & 3]);
	atomic64_inc(&pt_phy_length[len < 512 ? 0 : len < 1536 ? 1 : len < 3072 ? 2 : 3]);

	if (!IS_ENABLED(QCA_WIFI_KIWI_V2) || IS_ENABLED(CONFIG_WORD_BASED_TLV) ||
	    tlv_len != 272 || hal->target_type != TARGET_TYPE_KIWI) {
		result = PT_PHY_LAYOUT;
		goto count;
	}
	if (!hal_rx_attn_msdu_done_get(soc->hal_soc, tlvs)) {
		result = PT_PHY_NOT_DONE;
		goto count;
	}
	if (hal_rx_attn_phy_ppdu_id_get(soc->hal_soc, tlvs) !=
	    hal_rx_get_ppdu_id(soc->hal_soc, tlvs)) {
		result = PT_PHY_PPDU_MISMATCH;
		goto count;
	}
	atomic64_add(!!hal_rx_mpdu_info_ampdu_flag_get(soc->hal_soc, tlvs),
		     &pt_phy_counts[PT_PHY_RX_AMPDU]);

	type = hal_rx_tlv_get_pkt_type(soc->hal_soc, tlvs);
	bw = hal_rx_tlv_bw_get(soc->hal_soc, tlvs);
	reception = hal_rx_msdu_start_reception_type_get(soc->hal_soc, tlvs);
	if (type >= ARRAY_SIZE(pt_phy_packet_type) ||
	    reception >= ARRAY_SIZE(pt_phy_reception)) {
		result = PT_PHY_RANGE;
		goto count;
	}
	atomic64_inc(&pt_phy_packet_type[type]);
	atomic64_inc(&pt_phy_reception[reception]);

	if (reception != 0 || bw > 2 ||
	    (type != HAL_DOT11A && type != HAL_DOT11B &&
	     type != HAL_DOT11N_MM && type != HAL_DOT11N_GF && type != HAL_DOT11AC)) {
		result = PT_PHY_OUTSIDE_SCOPE;
		goto count;
	}
	if (type == HAL_DOT11A || type == HAL_DOT11B) {
		if (bw != 0) {
			result = PT_PHY_RANGE;
			goto count;
		}
		atomic64_inc(&pt_phy_type_bw[type == HAL_DOT11B][bw]);
		result = PT_PHY_VALID;
		goto count;
	}

	row = type == HAL_DOT11AC;
	mcs = hal_rx_tlv_rate_mcs_get(soc->hal_soc, tlvs);
	nss = hal_rx_msdu_start_nss_get(soc->hal_soc, tlvs);
	gi = hal_rx_tlv_sgi_get(soc->hal_soc, tlvs);
	if ((!row && bw > 1) || mcs > (row ? 9 : 15) || nss == 0 ||
	    nss >= ARRAY_SIZE(pt_phy_nss[row]) || gi >= ARRAY_SIZE(pt_phy_gi[row])) {
		result = PT_PHY_RANGE;
		goto count;
	}
	atomic64_inc(&pt_phy_type_bw[2 + row][bw]);
	atomic64_inc(&pt_phy_mcs[row][mcs]);
	atomic64_inc(&pt_phy_nss[row][nss]);
	atomic64_inc(&pt_phy_gi[row][gi]);
	result = PT_PHY_VALID;
count:
	atomic64_inc(&pt_phy_counts[result]);
}
