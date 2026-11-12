/* SPDX-License-Identifier: ISC */
#ifndef HOUJI_REMOTE_PEER_STATE_H
#define HOUJI_REMOTE_PEER_STATE_H

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/* Offline transaction model. Firmware events have no generation on the wire.
 * A future dispatcher must prove the event belongs to the pinned vdev epoch.
 */
struct remote_peer_key {
	uint64_t generation;
	uint8_t vdev;
	uint8_t mac[6];
};

enum remote_peer_phase {
	REMOTE_PEER_IDLE,
	REMOTE_PEER_ARMED,
	REMOTE_PEER_CREATING,
	REMOTE_PEER_DELETING,
	REMOTE_PEER_HOST_ROLLBACK,
	REMOTE_PEER_RETIRING,
	REMOTE_PEER_DRAINED,
	REMOTE_PEER_BLOCKED,
};

enum remote_peer_evidence {
	REMOTE_HOST_OBJ = 1U << 0,
	REMOTE_HOST_DP = 1U << 1,
	REMOTE_CREATE_SENT = 1U << 2,
	REMOTE_CREATE_CONF = 1U << 3,
	REMOTE_HTT_MAP = 1U << 4,
	REMOTE_DP_SLOT = 1U << 5,
	REMOTE_DELETE_SENT = 1U << 6,
	REMOTE_DELETE_CONF = 1U << 7,
	REMOTE_HTT_UNMAP = 1U << 8,
	REMOTE_DP_SLOT_REMOVED = 1U << 9,
	REMOTE_DP_FREED = 1U << 10,
	REMOTE_OBJ_FREED = 1U << 11,
	REMOTE_CREATE_REJECTED = 1U << 12,
	REMOTE_CREATE_SUBMIT_FAILED = 1U << 13,
};

#define REMOTE_CREATE_PROOF (REMOTE_HOST_OBJ | REMOTE_HOST_DP | REMOTE_CREATE_SENT | \
			     REMOTE_CREATE_CONF | REMOTE_HTT_MAP | REMOTE_DP_SLOT)
#define REMOTE_DELETE_PROOF (REMOTE_DELETE_SENT | REMOTE_DELETE_CONF | REMOTE_HTT_UNMAP | \
			     REMOTE_DP_SLOT_REMOVED | REMOTE_DP_FREED | REMOTE_OBJ_FREED)

struct remote_peer_txn {
	struct remote_peer_key key;
	enum remote_peer_phase phase;
	uint32_t seen;
	uint16_t peer_id;
	bool peer_id_known;
	bool cleanup_only;
};

struct remote_peer_admission {
	bool create_conf;
	bool sync_delete;
	bool stock_hashes_verified;
	bool reboot_fallback_authorized;
	bool post_boot_restore_validated;
};

static inline bool remote_peer_same(const struct remote_peer_key *a,
				    const struct remote_peer_key *b)
{
	return a->generation == b->generation && a->vdev == b->vdev &&
	       memcmp(a->mac, b->mac, sizeof(a->mac)) == 0;
}

static inline bool remote_peer_arm(struct remote_peer_txn *txn,
				   const struct remote_peer_key *key,
				   const struct remote_peer_admission *admission)
{
	const uint8_t zero[6] = {0};

	if (txn->phase != REMOTE_PEER_IDLE || !key->generation ||
	    (key->mac[0] & 1) || memcmp(key->mac, zero, sizeof(zero)) == 0 ||
	    !admission->create_conf || !admission->sync_delete ||
	    !admission->stock_hashes_verified ||
	    !admission->reboot_fallback_authorized ||
	    !admission->post_boot_restore_validated)
		return false;
	txn->key = *key;
	txn->phase = REMOTE_PEER_ARMED;
	return true;
}

static inline bool remote_peer_begin_create(struct remote_peer_txn *txn)
{
	if (txn->phase != REMOTE_PEER_ARMED ||
	    txn->seen != (REMOTE_HOST_OBJ | REMOTE_HOST_DP))
		return false;
	txn->phase = REMOTE_PEER_CREATING;
	return true;
}

static inline bool remote_peer_begin_delete(struct remote_peer_txn *txn)
{
	if (txn->phase != REMOTE_PEER_CREATING ||
	    (txn->seen & REMOTE_CREATE_REJECTED) ||
	    (txn->seen & REMOTE_CREATE_PROOF) != REMOTE_CREATE_PROOF)
		return false;
	txn->phase = REMOTE_PEER_DELETING;
	return true;
}

/* A matching map and occupied slot can prove ownership even if the
 * create confirmation failed or never arrived. This remains a failed test.
 */
static inline bool remote_peer_compensate(struct remote_peer_txn *txn)
{
	const uint32_t owned = REMOTE_HOST_OBJ | REMOTE_HOST_DP |
			       REMOTE_HTT_MAP | REMOTE_DP_SLOT;

	if (txn->phase != REMOTE_PEER_CREATING || (txn->seen & owned) != owned ||
	    !(txn->seen & (REMOTE_CREATE_SENT | REMOTE_CREATE_SUBMIT_FAILED)))
		return false;
	txn->cleanup_only = true;
	txn->phase = REMOTE_PEER_DELETING;
	return true;
}

/* This path is allowed only before the WMI command boundary. A failed
 * send return without proof of non-submission goes to BLOCKED instead.
 */
static inline bool remote_peer_rollback_host(struct remote_peer_txn *txn,
					    bool create_never_submitted)
{
	if (!create_never_submitted ||
	    (txn->phase != REMOTE_PEER_ARMED && txn->phase != REMOTE_PEER_CREATING) ||
	    (txn->seen & ~(REMOTE_HOST_OBJ | REMOTE_HOST_DP | REMOTE_CREATE_SUBMIT_FAILED)))
		return false;
	txn->phase = REMOTE_PEER_HOST_ROLLBACK;
	return true;
}

/* Caller supplies facts, never inferred readiness. Even a matching WMI
 * confirmation cannot stand in for HTT, the DP slot, or reference release.
 */
static inline bool remote_peer_observe(struct remote_peer_txn *txn,
				       const struct remote_peer_key *key,
				       enum remote_peer_evidence evidence,
				       uint16_t peer_id)
{
	const uint32_t create_events = REMOTE_CREATE_SENT | REMOTE_CREATE_CONF |
				       REMOTE_CREATE_REJECTED | REMOTE_CREATE_SUBMIT_FAILED |
				       REMOTE_HTT_MAP | REMOTE_DP_SLOT;
	const uint32_t peer_id_events = REMOTE_HTT_MAP | REMOTE_DP_SLOT |
					REMOTE_HTT_UNMAP | REMOTE_DP_SLOT_REMOVED;
	const uint32_t event = evidence;
	bool valid_phase =
		(txn->phase == REMOTE_PEER_ARMED &&
		 (evidence == REMOTE_HOST_OBJ || evidence == REMOTE_HOST_DP)) ||
		(txn->phase == REMOTE_PEER_CREATING && (evidence & create_events)) ||
		(txn->phase == REMOTE_PEER_DELETING && (evidence & REMOTE_DELETE_PROOF)) ||
		(txn->phase == REMOTE_PEER_HOST_ROLLBACK &&
		 ((evidence == REMOTE_OBJ_FREED && (txn->seen & REMOTE_HOST_OBJ)) ||
		  (evidence == REMOTE_DP_FREED && (txn->seen & REMOTE_HOST_DP))));

	if (!event || (event & (event - 1)) || !remote_peer_same(&txn->key, key))
		return false;
	if (!valid_phase) {
		/* A peer disappearing while CREATE is pending invalidates the
		 * positive proof even when its delete was not requested here.
		 */
		if (txn->phase == REMOTE_PEER_CREATING &&
		    (event & (REMOTE_HTT_UNMAP | REMOTE_DP_SLOT_REMOVED |
			      REMOTE_DP_FREED | REMOTE_OBJ_FREED)))
			txn->phase = REMOTE_PEER_BLOCKED;
		if (txn->phase == REMOTE_PEER_DELETING &&
		    (event & (REMOTE_HTT_MAP | REMOTE_DP_SLOT)) &&
		    txn->peer_id != peer_id)
			txn->phase = REMOTE_PEER_BLOCKED;
		return false;
	}
	if ((event == REMOTE_CREATE_CONF && (txn->seen & REMOTE_CREATE_REJECTED)) ||
	    (event == REMOTE_CREATE_REJECTED && (txn->seen & REMOTE_CREATE_CONF)) ||
	    (event == REMOTE_CREATE_SENT && (txn->seen & REMOTE_CREATE_SUBMIT_FAILED)) ||
	    (event == REMOTE_CREATE_SUBMIT_FAILED && (txn->seen & REMOTE_CREATE_SENT))) {
		txn->phase = REMOTE_PEER_BLOCKED;
		return false;
	}
	if (evidence & peer_id_events) {
		if (peer_id == UINT16_MAX ||
		    (txn->peer_id_known && txn->peer_id != peer_id)) {
			txn->phase = REMOTE_PEER_BLOCKED;
			return false;
		}
		txn->peer_id = peer_id;
		txn->peer_id_known = true;
	}
	txn->seen |= evidence;
	return true;
}

static inline bool remote_peer_begin_retire(struct remote_peer_txn *txn)
{
	if (txn->phase == REMOTE_PEER_DELETING) {
		if ((txn->seen & REMOTE_DELETE_PROOF) != REMOTE_DELETE_PROOF)
			return false;
	} else if (txn->phase == REMOTE_PEER_HOST_ROLLBACK) {
		if (((txn->seen & REMOTE_HOST_OBJ) && !(txn->seen & REMOTE_OBJ_FREED)) ||
		    ((txn->seen & REMOTE_HOST_DP) && !(txn->seen & REMOTE_DP_FREED)))
			return false;
	} else {
		return false;
	}
	txn->phase = REMOTE_PEER_RETIRING;
	return true;
}

static inline bool remote_peer_retired(struct remote_peer_txn *txn,
				       const struct remote_peer_key *key,
				       bool owner_drained)
{
	if (txn->phase != REMOTE_PEER_RETIRING || !owner_drained ||
	    !remote_peer_same(&txn->key, key))
		return false;
	txn->phase = REMOTE_PEER_DRAINED;
	return true;
}

/* A timeout or firmware failure is not an unmap or a destructor receipt. */
static inline void remote_peer_uncertain(struct remote_peer_txn *txn)
{
	if (txn->phase != REMOTE_PEER_IDLE && txn->phase != REMOTE_PEER_DRAINED)
		txn->phase = REMOTE_PEER_BLOCKED;
}
#endif
