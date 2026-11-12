/* SPDX-License-Identifier: ISC */
#ifndef DP_PT_BA_PEER_H
#define DP_PT_BA_PEER_H

#ifdef DRIVER_PASSTHRU_DP
struct dp_soc;
struct dp_vdev;
struct dp_peer;

enum pt_ba_peer_event {
	PT_BA_HTT_MISSING,
	PT_BA_HTT_LOOKUP,
	PT_BA_HTT_HOST_SUCCESS,
	PT_BA_HTT_HOST_FAILURE,
	PT_BA_REO_NO_ROUTE,
	PT_BA_REO_QDESC,
	PT_BA_REO_NO_QDESC,
	PT_BA_REO_SUBMIT_OK,
	PT_BA_REO_SUBMIT_ERROR,
	PT_BA_PEER_EVENTS,
};

void pt_ba_peer_vdev_start(const struct dp_vdev *vdev);
void pt_ba_peer_vdev_stop(const struct dp_vdev *vdev);
void pt_ba_peer_map_event(struct dp_soc *soc, u8 vdev_id, u16 peer_id,
			 const u8 *mac);
void pt_ba_peer_map_result(const struct dp_peer *peer, u16 peer_id);
void pt_ba_peer_unmap(const struct dp_peer *peer, u16 peer_id);
void pt_ba_peer_record(const struct dp_peer *peer, enum pt_ba_peer_event event,
		       int tid, u32 window);
#endif
#endif
