/* SPDX-License-Identifier: ISC */
#include "wlan_hdd_passthru_control.h"
#include <linux/etherdevice.h>
#include "wlan_hdd_main.h"
#include "wlan_hdd_object_manager.h"
#include "wlan_hdd_assoc.h"
#include "wlan_hdd_passthru_iface.h"
#include "wlan_hdd_passthru_experiment.h"
#include "wlan_hdd_cfg80211.h"
#include "wlan_hdd_power.h"
#include "wlan_policy_mgr_api.h"
#include "wlan_reg_services_api.h"
#include "wlan_reg_ucfg_api.h"
#include "wlan_scan_ucfg_api.h"
#include "wlan_vdev_mgr_api.h"
#include "wlan_vdev_mlme.h"
#include "wlan_vdev_mgr_tgt_if_tx_defs.h"
#include "cdp_txrx_peer_ops.h"
#include "sme_api.h"
#include "wma.h"
#include "wma_api.h"
#include <linux/kernel.h>
#include <linux/moduleparam.h>
#include <qdf_pt_remote_peer.h>

/* Load-time opt-in, restricted to the tested non-DFS 149-161 block.
 * Retain HT20 as the default and retain single-MPDU TX throughout.
 */
static bool pt_vht80;
module_param(pt_vht80, bool, 0444);
MODULE_PARM_DESC(pt_vht80, "Experimental VHT80 NSS1 on primary 149");

static bool pt_vht80_2ss;
module_param(pt_vht80_2ss, bool, 0400);
MODULE_PARM_DESC(pt_vht80_2ss, "Experimental VHT80 NSS1-2 MCS0-9 LDPC on primary 149");

static bool pt_remote_assoc;
module_param(pt_remote_assoc, bool, 0400);
MODULE_PARM_DESC(pt_remote_assoc, "Associate explicit remote peer on PASSTHRU vdev");

static bool pt_remote_multi;
module_param(pt_remote_multi, bool, 0400);
MODULE_PARM_DESC(pt_remote_multi, "Allow dynamic multi-peer association on PASSTHRU vdev");

static unsigned int pt_remote_reo;
module_param(pt_remote_reo, uint, 0400);
MODULE_PARM_DESC(pt_remote_reo, "Run cdp_peer_setup RX REO setup for associated remote peer");

static bool pt_tx_ampdu;
module_param(pt_tx_ampdu, bool, 0400);
MODULE_PARM_DESC(pt_tx_ampdu, "Allow outbound TX A-MPDU aggregation up to 64 MPDUs");

static bool pt_dynamic_rate = true;
module_param(pt_dynamic_rate, bool, 0444);
MODULE_PARM_DESC(pt_dynamic_rate, "Skip fixed_rate override so firmware rate control uses peer_assoc caps");

static u8 pt_remote_mac[QDF_MAC_ADDR_SIZE];

static int hdd_pt_remote_mac_set(const char *val, const struct kernel_param *kp)
{
	u8 mac[ETH_ALEN];

	(void)kp;
	if (!val || !mac_pton(val, mac) || !is_valid_ether_addr(mac))
		return -EINVAL;
	memcpy(pt_remote_mac, mac, ETH_ALEN);
	return 0;
}

static int hdd_pt_remote_mac_get(char *buf, const struct kernel_param *kp)
{
	(void)kp;
	return scnprintf(buf, PAGE_SIZE, "%pM\n", pt_remote_mac);
}

static const struct kernel_param_ops hdd_pt_remote_mac_ops = {
	.set = hdd_pt_remote_mac_set,
	.get = hdd_pt_remote_mac_get,
};
module_param_cb(pt_remote_mac, &hdd_pt_remote_mac_ops, NULL, 0400);
MODULE_PARM_DESC(pt_remote_mac, "Unicast MAC address of remote peer for PASSTHRU peer_assoc");

static bool hdd_pt_vht80(const struct wondertap_init_params *p)
{
    return p && pt_vht80 && p->channel.freq == 5745 &&
        p->channel.bandwidth == WONDERTAP_RATE_BW_80 &&
        p->tx_rate.bw == WONDERTAP_RATE_BW_80 &&
        p->tx_rate.preamble == WONDERTAP_RATE_PREAMBLE_VHT;
}

static bool hdd_pt_vht80_2ss(const struct wondertap_init_params *p)
{
    return pt_vht80_2ss && hdd_pt_vht80(p);
}

bool hdd_pt_control_remote_multi_enabled(void)
{
	return READ_ONCE(pt_remote_multi);
}

u32 hdd_pt_control_remote_reo_mode(void)
{
	return READ_ONCE(pt_remote_reo);
}

bool hdd_pt_control_params_is_2ss(const struct wondertap_init_params *params)
{
	return hdd_pt_vht80_2ss(params);
}

bool hdd_pt_control_params_vht80(const struct wondertap_init_params *params)
{
	return hdd_pt_vht80(params);
}


static int hdd_pt_control_channel(struct hdd_context *hdd, u32 freq)
{
	if (!WLAN_REG_IS_24GHZ_CH_FREQ(freq) &&
	    !WLAN_REG_IS_5GHZ_CH_FREQ(freq))
		return -EOPNOTSUPP;
	/* Channel 14 permits legacy DSSS, not this HT-only profile. */
	if (freq == 2484)
		return -EOPNOTSUPP;
	if (wlan_reg_get_channel_state_for_pwrmode(hdd->pdev, freq,
			REG_CURRENT_PWR_MODE) != CHANNEL_STATE_ENABLE ||
	    wlan_reg_is_dfs_for_freq(hdd->pdev, freq) ||
	    wlan_reg_is_passive_for_freq(hdd->pdev, freq))
		return -EPERM;
	return 0;
}

int hdd_pt_control_validate(struct hdd_context *hdd,
			   const struct wondertap_init_params *params)
{
	u8 country[3] = {0};
	QDF_STATUS status;

	if (!hdd || !hdd->pdev || !hdd->psoc || !params)
		return -EINVAL;
	if (!is_valid_ether_addr(params->mac_addr) ||
	    !is_valid_ether_addr(params->bssid) ||
	    params->country_code[2] || !params->country_code[0] ||
	    !params->country_code[1] || params->reserved1 ||
	    params->reserved2 || params->reserved3 ||
	    params->tx_rate.reserved[0] || params->tx_rate.reserved[1])
		return -EINVAL;
	if ((!hdd_pt_vht80(params) &&
	    (params->channel.bandwidth != WONDERTAP_RATE_BW_20 ||
	    params->tx_rate.bw != WONDERTAP_RATE_BW_20 ||
	    params->tx_rate.preamble != WONDERTAP_RATE_PREAMBLE_HT)) ||
	    (hdd_pt_vht80_2ss(params) ?
	     (params->tx_rate.nss < 1 || params->tx_rate.nss > 2 ||
	      params->tx_rate.mcs > 9) :
	     (params->tx_rate.nss != 1 || params->tx_rate.mcs > 7)) ||
	    (params->tx_rate.gi != WONDERTAP_RATE_GI_DEFAULT &&
	     params->tx_rate.gi != WONDERTAP_RATE_GI_SHORT) ||
	    params->amsdu_enable || params->ampdu_enable ||
	    params->mgmt_retry_limit || params->data_retry_limit)
		return -EOPNOTSUPP;
	status = ucfg_reg_get_current_country(hdd->psoc, country);
	if (QDF_IS_STATUS_ERROR(status))
		return qdf_status_to_os_return(status);
	if (memcmp(country, params->country_code, 2))
		return -EOPNOTSUPP;
	if (ucfg_scan_get_pdev_status(hdd->pdev) != SCAN_NOT_IN_PROGRESS)
		return -EBUSY;
	if (hdd_pt_vht80(params)) {
        u32 freq;
        int ret;
        for (freq = 5745; freq <= 5805; freq += 20) {
            ret = hdd_pt_control_channel(hdd, freq);
            if (ret)
                return ret;
        }
    }
    return hdd_pt_control_channel(hdd, params->channel.freq);
}

static int hdd_pt_control_state(struct hdd_pt_iface *iface,
				const struct wondertap_init_params *params,
				bool changing)
{
	struct hdd_pt_status state;
	int ret;

	if (!iface || !iface->adapter || !iface->adapter->deflink ||
	    !iface->registry || !iface->vdev_created ||
	    !iface->owner_started || iface->owner_reaped)
		return -EINVAL;
	ret = hdd_pt_control_validate(iface->hdd, params);
	if (ret)
		return ret;
	if (iface->adapter->device_mode != QDF_PASSTHRU_MODE ||
	    WLAN_HDD_GET_CTX(iface->adapter) != iface->hdd ||
	    !ether_addr_equal(iface->adapter->mac_addr.bytes,
			      params->mac_addr) ||
	    (!changing && iface->frequency != params->channel.freq))
		return -EINVAL;
	ret = hdd_pt_status(iface->registry, iface->generation, &state);
	if (ret)
		return ret;
	if (state.error)
		return state.error;
	return state.phase == (changing ? HDD_PT_ACTIVE : HDD_PT_BOUND) ?
		0 : -EAGAIN;
}

static int hdd_pt_control_submit(struct hdd_pt_iface *iface,
		const struct wondertap_init_params *params, u32 cookie, bool changing)
{
	struct channel_change_req request = {0};
	enum phy_ch_width width = hdd_pt_vht80(params) ?
        CH_WIDTH_80MHZ : CH_WIDTH_20MHZ;
    struct ch_params channel = { .ch_width = width };
	struct wlan_objmgr_vdev *vdev;
	struct vdev_mlme_obj *mlme;
	struct hdd_adapter *adapter;
	QDF_STATUS status;
	int ret;

	ret = hdd_pt_control_state(iface, params, changing);
	if (ret)
		return ret;
	adapter = iface->adapter;
	ret = hdd_validate_channel_and_bandwidth(adapter, params->channel.freq,
						 width);
	if (ret)
		return ret;
	wlan_reg_set_channel_params_for_pwrmode(iface->hdd->pdev,
			params->channel.freq, 0, &channel, REG_CURRENT_PWR_MODE);
	if (channel.ch_width != width)
		return -EOPNOTSUPP;
	if (hdd_pt_sta_coexist_mode()) {
		struct hdd_adapter *sta;
		struct wlan_hdd_link_info *link;

		/* Until channel-change policy has its own validation, this
		 * experimental coexistence session stays on its initial channel.
		 */
		if (changing)
			return -EOPNOTSUPP;
		if (!policy_mgr_allow_concurrency(iface->hdd->psoc,
				PM_PASSTHRU_MODE, params->channel.freq,
				width == CH_WIDTH_80MHZ ? HW_MODE_80_MHZ : HW_MODE_20_MHZ,
				0, adapter->deflink->vdev_id)) {
			hdd_err("PT coexist rejected: freq %u requires supported STA/DBS policy",
				params->channel.freq);
			return -EPERM;
		}
		iface->coexist_prepared = true;
		wlan_hdd_set_roaming_state(adapter->deflink,
					 RSO_PASSTHRU_SET_CHANNEL, false);
		ret = wlan_hdd_change_hw_mode_for_given_chnl(adapter,
				params->channel.freq,
				POLICY_MGR_UPDATE_REASON_SET_OPER_CHAN);
		if (ret)
			return ret;
		sta = hdd_get_adapter(iface->hdd, QDF_STA_MODE);
		if (sta) {
			hdd_adapter_for_each_active_link_info(sta, link) {
				ret = wlan_hdd_set_powersave(link, false, 0);
				if (ret)
					return ret;
			}
		}
		hdd_info("PT coexist hardware preparation complete freq %u",
			 params->channel.freq);
	}

	vdev = hdd_objmgr_get_vdev_by_user(adapter->deflink, WLAN_OSIF_ID);
	if (!vdev)
		return -ENODEV;
	if (!changing) {
		status = wlan_vdev_mgr_set_param_bssid(vdev, params->bssid);
		if (QDF_IS_STATUS_ERROR(status))
			goto put;
	}
	mlme = wlan_vdev_mlme_get_cmpt_obj(vdev);
	if (!mlme) {
		status = QDF_STATUS_E_INVAL;
		goto put;
	}
	wlan_vdev_obj_lock(vdev);
	status = wlan_pt_txn_prepare(&mlme->passthru_txn, cookie,
				     params->channel.freq);
	wlan_vdev_obj_unlock(vdev);
	if (QDF_IS_STATUS_ERROR(status))
		goto put;
	request.passthru_cookie = cookie;

	request.vdev_id = adapter->deflink->vdev_id;
	request.target_chan_freq = params->channel.freq;
	request.ch_width = channel.ch_width;
	request.sec_ch_offset = channel.sec_ch_offset;
	request.center_freq_seg0 = channel.center_freq_seg0;
	request.center_freq_seg1 = channel.center_freq_seg1;
	sme_fill_channel_change_request(iface->hdd->mac_handle, &request,
				       hdd_pt_vht80(params) ? eCSR_DOT11_MODE_11ac : eCSR_DOT11_MODE_11n);
	/* Do not let global PHY policy override the explicit profile. */
    request.dot11mode = hdd_pt_vht80(params) ?
        MLME_DOT11_MODE_11AC : MLME_DOT11_MODE_11N;
	status = sme_send_channel_change_req(iface->hdd->mac_handle, &request);
	if (QDF_IS_STATUS_ERROR(status)) {
		wlan_vdev_obj_lock(vdev);
		wlan_pt_txn_seal(&mlme->passthru_txn);
		wlan_vdev_obj_unlock(vdev);
	}
put:
	hdd_objmgr_put_vdev_by_user(vdev, WLAN_OSIF_ID);
	return qdf_status_to_os_return(status);
}

int hdd_pt_control_submit_start(struct hdd_pt_iface *iface,
                               const struct wondertap_init_params *params,
                               u32 cookie)
{
	return hdd_pt_control_submit(iface, params, cookie, false);
}

int hdd_pt_control_submit_channel(struct hdd_pt_iface *iface,
                                 const struct wondertap_init_params *params,
                                 u32 cookie)
{
	return hdd_pt_control_submit(iface, params, cookie, true);
}

/* No configuration writes: callers may retry PENDING until their deadline. */
int hdd_pt_control_check_start(struct hdd_pt_iface *iface, u32 cookie)
{
	struct wlan_objmgr_vdev *vdev;
	struct vdev_mlme_obj *mlme;
	QDF_STATUS status = QDF_STATUS_E_FAILURE;

	if (!iface || !iface->adapter || !iface->adapter->deflink ||
	    !iface->vdev_created || iface->owner_reaped)
		return -EINVAL;
	vdev = hdd_objmgr_get_vdev_by_user(iface->adapter->deflink, WLAN_OSIF_ID);
	if (!vdev)
		return -ENODEV;
	mlme = wlan_vdev_mlme_get_cmpt_obj(vdev);
	if (mlme) {
		wlan_vdev_obj_lock(vdev);
		status = wlan_pt_txn_completion(&mlme->passthru_txn, cookie);
		wlan_vdev_obj_unlock(vdev);
	}
	hdd_objmgr_put_vdev_by_user(vdev, WLAN_OSIF_ID);
	return status == QDF_STATUS_E_PENDING ? -EINPROGRESS :
		qdf_status_to_os_return(status);
}

/* Serialized runtime control only; no waits or firmware commands. */
void hdd_pt_control_abort(struct hdd_pt_iface *iface)
{
	struct wlan_objmgr_vdev *vdev;
	struct vdev_mlme_obj *mlme;

	if (!iface || !iface->adapter || !iface->adapter->deflink ||
	    !iface->vdev_created || iface->owner_reaped)
		return;
	vdev = hdd_objmgr_get_vdev_by_user(iface->adapter->deflink, WLAN_OSIF_ID);
	if (!vdev)
		return;
	mlme = wlan_vdev_mlme_get_cmpt_obj(vdev);
	if (mlme) {
		wlan_vdev_obj_lock(vdev);
		wlan_pt_txn_seal(&mlme->passthru_txn);
		wlan_vdev_obj_unlock(vdev);
	}
	hdd_objmgr_put_vdev_by_user(vdev, WLAN_OSIF_ID);
}

static int hdd_pt_control_vdev_param(tp_wma_handle wma, u8 vdev_id,
				    u32 id, u32 value)
{
	struct vdev_set_params param = {
		.vdev_id = vdev_id,
		.param_id = id,
		.param_value = value,
	};

	/* Unlike wma_cli_set_command, return the actual WMI send result. */
	return qdf_status_to_os_return(
		wmi_unified_vdev_set_param_send(wma->wmi_handle, &param));
}

/* Same TLV as wma_set_sw_retry_by_qos. Its public wrapper skips zero,
 * so explicitly send zero for both aggregation classes on every AC.
 * This controls firmware SOFTWARE retries; it is not proof that hardware
 * retries are disabled. Do not advertise a hardware-retry capability.
 */
static int hdd_pt_control_no_sw_retry(tp_wma_handle wma, u8 vdev_id)
{
	wmi_vdev_set_custom_sw_retry_th_cmd_fixed_param *cmd;
	wmi_buf_t buf;
	u32 type, ac;
	int ret;

	for (type = 0; type < WMI_VDEV_CUSTOM_SW_RETRY_TYPE_MAX; type++) {
		for (ac = 0; ac < WMI_AC_MAX; ac++) {
			buf = wmi_buf_alloc(wma->wmi_handle, sizeof(*cmd));
			if (!buf)
				return -ENOMEM;
			cmd = (void *)wmi_buf_data(buf);
			qdf_mem_zero(cmd, sizeof(*cmd));
			WMITLV_SET_HDR(&cmd->tlv_header,
				WMITLV_TAG_STRUC_wmi_vdev_set_custom_sw_retry_th_cmd_fixed_param,
				WMITLV_GET_STRUCT_TLVLEN(
					wmi_vdev_set_custom_sw_retry_th_cmd_fixed_param));
			cmd->vdev_id = vdev_id;
			cmd->ac_type = ac;
			cmd->sw_retry_type = type;
			cmd->sw_retry_th = 0;
			ret = wmi_unified_cmd_send(wma->wmi_handle, buf,
						  sizeof(*cmd),
					WMI_VDEV_SET_CUSTOM_SW_RETRY_TH_CMDID);
			if (ret) {
				wmi_buf_free(buf);
				return qdf_status_to_os_return(ret);
			}
		}
	}
	return 0;
}

/* Default remains one MPDU/MSDU unless pt_tx_ampdu explicitly opens the
 * outbound A-MPDU window to 64 MPDUs. Leave RX BA limits untouched.
 */
static int hdd_pt_control_tx_aggr(tp_wma_handle wma, u8 vdev_id,
				  bool allow_ampdu)
{
	wmi_vdev_set_custom_aggr_size_cmd_fixed_param *cmd;
	wmi_buf_t buf;
	u32 type;
	int ret;

	for (type = 0; type < WMI_VDEV_CUSTOM_AGGR_TYPE_MAX; type++) {
		buf = wmi_buf_alloc(wma->wmi_handle, sizeof(*cmd));
		if (!buf)
			return -ENOMEM;
		cmd = (void *)wmi_buf_data(buf);
		qdf_mem_zero(cmd, sizeof(*cmd));
		WMITLV_SET_HDR(&cmd->tlv_header,
			WMITLV_TAG_STRUC_wmi_vdev_set_custom_aggr_size_cmd_fixed_param,
			WMITLV_GET_STRUCT_TLVLEN(
				wmi_vdev_set_custom_aggr_size_cmd_fixed_param));
		cmd->vdev_id = vdev_id;
		cmd->tx_aggr_size =
			(allow_ampdu && type == WMI_VDEV_CUSTOM_AGGR_TYPE_AMPDU) ?
			64 : 1;
		WMI_VDEV_CUSTOM_AGGR_TYPE_SET(cmd->enable_bitmap, type);
		WMI_VDEV_CUSTOM_RX_AGGR_SZ_DIS_SET(cmd->enable_bitmap, 1);
		ret = wmi_unified_cmd_send(wma->wmi_handle, buf, sizeof(*cmd),
					  WMI_VDEV_SET_CUSTOM_AGGR_SIZE_CMDID);
		if (ret) {
			wmi_buf_free(buf);
			return qdf_status_to_os_return(ret);
		}
	}
	return 0;
}

int hdd_pt_control_finish_start(struct hdd_pt_iface *iface,
			       const struct wondertap_init_params *params)
{
	struct ol_txrx_desc_type peer = {0};
	struct wlan_objmgr_vdev *vdev;
	struct hdd_adapter *adapter;
	tp_wma_handle wma;
	void *soc;
	QDF_STATUS status;
	u8 vdev_id;
	int ret, rate;

	ret = hdd_pt_control_state(iface, params, false);
	if (ret)
		return ret;
	adapter = iface->adapter;
	vdev_id = adapter->deflink->vdev_id;
	wma = cds_get_context(QDF_MODULE_ID_WMA);
	soc = cds_get_context(QDF_MODULE_ID_SOC);
	if (!wma || !wma->wmi_handle || !soc)
		return -ENODEV;
	if (hdd_pt_sta_coexist_mode()) {
		hdd_info("PT coexist firmware mapping vdev %u mac %u freq %u tx_streams %u",
			 vdev_id, wma->interfaces[vdev_id].mac_id,
			 wma->interfaces[vdev_id].ch_freq,
			 wma->interfaces[vdev_id].tx_streams);
		if (wma->interfaces[vdev_id].tx_streams < params->tx_rate.nss)
			return -EOPNOTSUPP;
	}
	if (hdd_pt_vht80_2ss(params)) {
		ret = hdd_pt_control_vdev_param(wma, vdev_id,
						wmi_vdev_param_nss,
						params->tx_rate.nss);
		if (ret)
			return ret;
		ret = hdd_pt_control_vdev_param(wma, vdev_id,
						wmi_vdev_param_ldpc, 3);
		if (ret)
			return ret;
	}
	if (!pt_dynamic_rate) {
		rate = hdd_assemble_rate_code(hdd_pt_vht80(params) ?
			WMI_RATE_PREAMBLE_VHT : WMI_RATE_PREAMBLE_HT,
			hdd_pt_vht80_2ss(params) ? (params->tx_rate.nss - 1) : 0,
			params->tx_rate.mcs);
		if (rate < 0)
			return rate;
		ret = hdd_pt_control_vdev_param(wma, vdev_id,
						wmi_vdev_param_fixed_rate, rate);
		if (ret)
			return ret;
	}
	ret = hdd_pt_control_vdev_param(wma, vdev_id, wmi_vdev_param_sgi,
			params->tx_rate.gi == WONDERTAP_RATE_GI_SHORT);
	if (ret)
		return ret;
	ret = hdd_pt_control_vdev_param(wma, vdev_id,
					wmi_vdev_param_chwidth, hdd_pt_vht80(params) ? 2 : 0);
	if (ret)
		return ret;
	if (!pt_tx_ampdu) {
		ret = hdd_pt_control_no_sw_retry(wma, vdev_id);
		if (ret)
			return ret;
	}
	ret = hdd_pt_control_tx_aggr(wma, vdev_id, pt_tx_ampdu);
	if (ret)
		return ret;

	qdf_mem_copy(peer.peer_addr.bytes, adapter->mac_addr.bytes,
		     QDF_MAC_ADDR_SIZE);
	peer.is_qos_enabled = 1;
	peer.bw = hdd_convert_ch_width_to_cdp_peer_bw(hdd_pt_vht80(params) ?
        CH_WIDTH_80MHZ : CH_WIDTH_20MHZ);
	status = cdp_peer_register(soc, OL_TXRX_PDEV_ID, &peer);
	if (QDF_IS_STATUS_ERROR(status))
		return qdf_status_to_os_return(status);
	status = hdd_change_peer_state(adapter->deflink,
				      adapter->mac_addr.bytes,
				      OL_TXRX_PEER_STATE_AUTH);
	if (QDF_IS_STATUS_ERROR(status))
		return qdf_status_to_os_return(status);

	if (hdd_pt_sta_coexist_mode() && !iface->policy_registered) {
		policy_mgr_incr_active_session(iface->hdd->psoc,
					      QDF_PASSTHRU_MODE, vdev_id);
		status = policy_mgr_check_conn_with_mode_and_vdev_id(
			iface->hdd->psoc, PM_PASSTHRU_MODE, vdev_id);
		if (QDF_IS_STATUS_ERROR(status))
			return qdf_status_to_os_return(status);
		iface->policy_registered = true;
		hdd_info("PT coexist session registered vdev %u", vdev_id);
	}
	if (pt_remote_multi) {
		if (pt_remote_reo > 2)
			return -EINVAL;
		pt_remote_peer_set_reo_mode(pt_remote_reo);
	}
	if (pt_remote_assoc) {
		bool vht80 = hdd_pt_vht80(params);
		bool is_2ss = hdd_pt_vht80_2ss(params);
		bool short_gi = params->tx_rate.gi == WONDERTAP_RATE_GI_SHORT;
		struct wma_pt_peer_caps caps = {
			.ht = true,
			.vht = vht80,
			.nss = is_2ss ? 2 : 1,
			.bw = vht80 ? 80 : 20,
			.max_mcs = vht80 ? 9 : 7,
			.sgi = short_gi,
			.ldpc = is_2ss,
			.has_ht_bw40 = true,
			.ht_bw40 = vht80,
			.has_ht_sgi20 = true,
			.ht_sgi20 = short_gi,
			.has_ht_sgi40 = true,
			.ht_sgi40 = vht80 && short_gi,
			.has_ht_ldpc = true,
			.ht_ldpc = is_2ss,
			.has_vht_sgi80 = true,
			.vht_sgi80 = vht80 && short_gi,
			.has_vht_ldpc = true,
			.vht_ldpc = vht80 && is_2ss,
			.max_mpdu = 3895,
			.ht_mcs_mask = is_2ss ? 0xffffU : 0x00ffU,
		};
		vdev = hdd_objmgr_get_vdev_by_user(adapter->deflink,
						   WLAN_OSIF_ID);
		if (!vdev)
			return -ENODEV;
		ret = wma_pt_remote_peer_setup_caps(vdev, iface->generation,
						    pt_remote_mac, pt_remote_reo,
						    &caps);
		hdd_objmgr_put_vdev_by_user(vdev, WLAN_OSIF_ID);
		if (ret)
			return ret;
	}
	return 0;
}

void hdd_pt_coexist_release(struct hdd_pt_iface *iface)
{
	struct hdd_adapter *sta;
	struct wlan_hdd_link_info *link;

	if (iface->policy_registered) {
		policy_mgr_decr_session_set_pcl(iface->hdd->psoc,
			QDF_PASSTHRU_MODE, iface->adapter->deflink->vdev_id);
		iface->policy_registered = false;
	}
	if (!iface->coexist_prepared)
		return;
	sta = hdd_get_adapter(iface->hdd, QDF_STA_MODE);
	if (sta) {
		hdd_adapter_for_each_active_link_info(sta, link)
			wlan_hdd_set_powersave(link, sta->allow_power_save, 0);
	}
	wlan_hdd_set_roaming_state(iface->adapter->deflink,
				 RSO_PASSTHRU_SET_CHANNEL, true);
	iface->coexist_prepared = false;
	hdd_info("PT coexist session released");
}
