# EZIF RX MSDU-buffer adapter

Review patch: `rx-msdu-buffer-adapter.patch`, applied at the WLAN source root. Three files, 204 added lines. `base/` is a read-only snapshot of the current build-host three-findings source; `work/` is the isolated edit. `source-hashes.json` records both. No shared source edit, full module build, deployment, or phone operation was performed.

## Controls and ownership

- `pt_rx_msdu_buf` defaults false, mode 0400, selectable at module load only.
- Only `HAL_RX_REO_MSDU_BUF_ADDR_TYPE` with a referenced peer whose referenced vdev is PASSTHRU is admitted. No guessed metadata bit 30 and no unknown-peer fallback.
- `pt_rx_msdu_stats` is read-only (0444): seen, peer_miss, bad_desc, reaped, delivered, dropped, last_drop. After traffic quiesces, reaped should equal delivered + dropped; concurrent reads are not an atomic multi-counter snapshot.
- last_drop: 1 unsupported skb shape; 2 TLV size/capacity; 3 done/RAW/first/last flags; 4 packet length; 5 existing raw wrapper rejected before OSIF; 6 multi-MSDU or fragmented MPDU. Zero means no recorded drop.
- Before DMA ownership changes, rejected descriptors use the unchanged legacy exception path and thresholds. After unmap, fallback is forbidden: exactly one descriptor reclaim, one nbuf consumption, and one outer refill count.
- Uses existing IPA mapping lock, `dp_rx_nbuf_unmap_pool`, and `dp_rx_add_to_free_desc_list`. A real vdev reference spans delivery. Existing raw wrapper supplies passthru admission/lifecycle protection. An OSIF invocation consumes the skb even if OSIF returns an error; it is never freed again by this adapter.
- Only complete linear single-buffer RAW frames with valid done/first/last flags and bounded lengths are delivered. Capacity is bounded by both actual skb allocation and original DMA mapping size. Unsupported or malformed frames are explicitly dropped and counted.

## Verification performed

`git apply --check --whitespace=error` passes.

Both standalone C harnesses extract the actual new function body from `work/`, mock lower HAL/DMA/reference APIs, and compile with Clang `-Wall -Wextra -Werror` plus ASan/UBSan:

- `test-wrapper.py`: 17 valid, exact-capacity, shape, TLV, done/RAW/first/last, length overflow, over-mapping-capacity and delivery rejection cases. Checks exactly one skb consumption.
- `test-handler.py`: 16 gate, peer/reference, cookie/nbuf/in_use/pool/paddr/pdev/unmapped and success/drop cases. Checks no ownership mutation on fallback, one unmap/reclaim/consumption on admission, and balanced references. The free-list mock deliberately destroys pool_id to verify refill ID is saved before recycling.

All 33 cases pass. The first wrapper harness run exposed a fixture macro shadowing a production local variable; the fixture name was corrected without changing production logic. These isolated tests do not replace target kernel compilation or device DMA/lifecycle validation.

## Integration boundaries

Parent agent must review, compile the target module, audit ABI and add the two parameters to its expected module-parameter list. This patch does not implement wire506 caching or default REO routing; that is a separate parent-owned change.

First gate-enabled EZIF run should be a short RX/action receipt check with counters, not bulk traffic. Unknown-peer packets still use the legacy exception path. If peer_miss grows, verify the metadata flag or a justified restricted attribution rule before expanding eligibility. Do not suppress the legacy recovery threshold. This adapter alone does not establish TX A-MPDU support.
