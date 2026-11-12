/* SPDX-License-Identifier: ISC */
#include <assert.h>
#include <stdio.h>
#include "remote-peer-state.h"

static const struct remote_peer_key key = {8, 2, {2, 0, 0, 0, 0, 8}};
static const struct remote_peer_key old = {7, 2, {2, 0, 0, 0, 0, 8}};
static const struct remote_peer_key other = {8, 2, {2, 0, 0, 0, 0, 7}};
static const struct remote_peer_admission ready = {
	.create_conf = true,
	.sync_delete = true,
	.stock_hashes_verified = true,
	.reboot_fallback_authorized = true,
	.post_boot_restore_validated = true,
};

static void start(struct remote_peer_txn *t)
{
	struct remote_peer_admission missing = ready;

	missing.post_boot_restore_validated = false;
	assert(!remote_peer_arm(t, &key, &missing));
	missing = ready;
	missing.sync_delete = false;
	assert(!remote_peer_arm(t, &key, &missing));
	assert(remote_peer_arm(t, &key, &ready));
	assert(!remote_peer_begin_create(t));
	assert(!remote_peer_observe(t, &key, REMOTE_HOST_OBJ | REMOTE_CREATE_CONF, 0));
	assert(remote_peer_observe(t, &key, REMOTE_HOST_OBJ, 0));
	assert(remote_peer_observe(t, &key, REMOTE_HOST_DP, 0));
	assert(remote_peer_begin_create(t));
	assert(!remote_peer_begin_create(t));
}

static void create(struct remote_peer_txn *t)
{
	start(t);
	assert(!remote_peer_observe(t, &old, REMOTE_CREATE_CONF, 0));
	assert(!remote_peer_observe(t, &other, REMOTE_HTT_MAP, 3));
	assert(remote_peer_observe(t, &key, REMOTE_CREATE_CONF, 0));
	assert(remote_peer_observe(t, &key, REMOTE_HTT_MAP, 9));
	assert(!remote_peer_begin_delete(t));
	assert(remote_peer_observe(t, &key, REMOTE_DP_SLOT, 9));
	assert(!remote_peer_begin_delete(t));
	assert(remote_peer_observe(t, &key, REMOTE_CREATE_SENT, 0));
	assert(remote_peer_begin_delete(t));
	assert(!remote_peer_begin_delete(t));
}

int main(void)
{
	struct remote_peer_txn t = {0};

	create(&t);
	assert(remote_peer_observe(&t, &key, REMOTE_DELETE_CONF, 0));
	assert(remote_peer_observe(&t, &key, REMOTE_DELETE_SENT, 0));
	assert(!remote_peer_begin_retire(&t));
	assert(remote_peer_observe(&t, &key, REMOTE_HTT_UNMAP, 9));
	assert(remote_peer_observe(&t, &key, REMOTE_DP_SLOT_REMOVED, 9));
	assert(!remote_peer_begin_retire(&t));
	assert(remote_peer_observe(&t, &key, REMOTE_OBJ_FREED, 0));
	assert(remote_peer_observe(&t, &key, REMOTE_DP_FREED, 0));
	assert(remote_peer_begin_retire(&t));
	assert(!remote_peer_retired(&t, &key, false));
	assert(remote_peer_retired(&t, &key, true));
	assert(!remote_peer_arm(&t, &key, &ready));

	t = (struct remote_peer_txn){0};
	create(&t);
	assert(!remote_peer_observe(&t, &key, REMOTE_HTT_UNMAP, 10));
	assert(t.phase == REMOTE_PEER_BLOCKED);
	assert(!remote_peer_observe(&t, &key, REMOTE_HTT_UNMAP, 9));

	t = (struct remote_peer_txn){0};
	start(&t);
	assert(remote_peer_observe(&t, &key, REMOTE_CREATE_SENT, 0));
	assert(!remote_peer_rollback_host(&t, true));
	assert(!remote_peer_compensate(&t));
	remote_peer_uncertain(&t);
	assert(t.phase == REMOTE_PEER_BLOCKED);
	assert(!remote_peer_begin_delete(&t));
	assert(!remote_peer_begin_retire(&t));

	t = (struct remote_peer_txn){0};
	start(&t);
	assert(!remote_peer_rollback_host(&t, false));
	assert(remote_peer_rollback_host(&t, true));
	assert(!remote_peer_begin_retire(&t));
	assert(remote_peer_observe(&t, &key, REMOTE_DP_FREED, 0));
	assert(remote_peer_observe(&t, &key, REMOTE_OBJ_FREED, 0));
	assert(remote_peer_begin_retire(&t));
	assert(remote_peer_retired(&t, &key, true));

	t = (struct remote_peer_txn){0};
	start(&t);
	assert(remote_peer_observe(&t, &key, REMOTE_CREATE_REJECTED, 0));
	assert(remote_peer_observe(&t, &key, REMOTE_HTT_MAP, 12));
	assert(!remote_peer_compensate(&t));
	assert(remote_peer_observe(&t, &key, REMOTE_DP_SLOT, 12));
	assert(!remote_peer_begin_delete(&t));
	assert(!remote_peer_compensate(&t));
	assert(remote_peer_observe(&t, &key, REMOTE_CREATE_SUBMIT_FAILED, 0));
	assert(remote_peer_compensate(&t) && t.cleanup_only);
	assert(!remote_peer_compensate(&t));
	puts("PASS gated create/delete, split completion evidence, stale identities and failed cleanup");
	return 0;
}
