/* SPDX-License-Identifier: ISC */
#ifndef _QDF_PT_REMOTE_PEER_H_
#define _QDF_PT_REMOTE_PEER_H_

#include <linux/types.h>

enum pt_remote_peer_event {
	PT_REMOTE_HTT_MAP = 1,
	PT_REMOTE_SLOT_INSTALL = 2,
	PT_REMOTE_HTT_UNMAP = 3,
	PT_REMOTE_SLOT_REMOVE = 4,
};

enum pt_remote_peer_object {
	PT_REMOTE_DP_OBJECT = 1,
	PT_REMOTE_OBJMGR_OBJECT = 2,
};

void pt_remote_peer_dp_event(const void *psoc, u8 vdev_id, const u8 *mac,
			     u16 peer_id, enum pt_remote_peer_event event);
void pt_remote_peer_set_ast(const void *psoc, u8 vdev_id, const u8 *mac,
			    u16 peer_id, u16 ast_idx, u16 ast_hash);
bool pt_remote_peer_lookup_ast(const void *psoc, u8 vdev_id, const u8 *ra_mac,
			       u16 *ast_idx, u16 *ast_hash);
u16 pt_remote_peer_active_id(void);
u32 pt_remote_peer_reo_mode(void);
u64 pt_remote_peer_release_key(const void *psoc, u8 vdev_id, const u8 *mac);
void pt_remote_peer_released(u64 generation, enum pt_remote_peer_object obj);

#endif
