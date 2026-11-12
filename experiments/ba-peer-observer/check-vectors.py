#!/usr/bin/env python3
"""Run the unchanged raw/mapping observer functions against synthetic inputs."""
import os
from pathlib import Path
import subprocess
import tempfile

state = (Path(__file__).resolve().parent / "dp_pt_ba_peer_state.h").read_text()
logic = state[state.index("static DECLARE_BITMAP"):state.index("static int pt_ba_peer_stats_get")]
logic = logic.replace("READ_ONCE(pt_ba_observe)", "true")
header = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
enum { ETH_ALEN = 6, ETH_FCS_LEN = 4, wlan_op_mode_passthru = 5, DP_MOD_ID_HTT = 0 };
enum pt_ba_peer_event { PT_BA_HTT_MISSING, PT_BA_HTT_LOOKUP,
    PT_BA_HTT_HOST_SUCCESS, PT_BA_HTT_HOST_FAILURE, PT_BA_REO_NO_ROUTE,
    PT_BA_REO_QDESC, PT_BA_REO_NO_QDESC, PT_BA_REO_SUBMIT_OK,
    PT_BA_REO_SUBMIT_ERROR, PT_BA_PEER_EVENTS };
#define DECLARE_BITMAP(name, n) bool name[n]
#define test_bit(index, bits) ((bits)[index])
#define __set_bit(index, bits) ((bits)[index] = true)
#define spin_lock_irqsave(lock, flags) ((void)((flags) = 0))
#define spin_unlock_irqrestore(lock, flags) ((void)(flags))
#define READ_ONCE(x) (x)
struct address { u8 raw[6]; };
struct dp_vdev { int opmode; u8 vdev_id; struct address mac_addr; };
struct dp_peer { struct dp_vdev *vdev; struct address mac_addr; u16 peer_id; };
struct dp_soc { struct dp_vdev *vdev; };
struct mock_nbuf { u8 *data; size_t len; };
typedef struct mock_nbuf *qdf_nbuf_t;
#define qdf_nbuf_len(nbuf) ((nbuf)->len)
#define qdf_nbuf_data(nbuf) ((nbuf)->data)
static const u8 pt_ba_peer_address[ETH_ALEN] = {2, 1, 2, 3, 4, 5};
static bool pt_ba_peer_configured;
static struct dp_vdev *dp_vdev_get_ref_by_id(struct dp_soc *soc, u8 id, int mod)
{
    return soc->vdev && id == soc->vdev->vdev_id ? soc->vdev : NULL;
}
static void dp_vdev_unref_delete(struct dp_soc *soc, struct dp_vdev *vdev, int mod) {}
static u16 get_unaligned_le16(const u8 *bytes)
{
    return bytes[0] | (u16)bytes[1] << 8;
}
"""
vectors = r"""
int main(void)
{
    struct dp_vdev vdev = {wlan_op_mode_passthru, 3, {{2, 0, 0, 0, 0, 3}}};
    struct dp_soc soc = {&vdev};
    struct dp_peer remote = {&vdev, {{2, 1, 2, 3, 4, 5}}, 81};
    struct dp_peer self = {&vdev, {{2, 0, 0, 0, 0, 3}}, 1};
    u8 frame[37] = {[0]=0xd0, [24]=3, [27]=3<<2, [28]=16,
                    [31]=0x30, [32]=0x12};
    struct mock_nbuf buffer = {frame, sizeof(frame)};
    struct pt_ba_peer_counts *counts = &pt_ba_peer_counts;

    pt_ba_peer_vdev_start(&vdev);
    assert(counts->vdev_starts == 0);
    pt_ba_peer_configured = true;
    pt_ba_peer_vdev_start(&vdev);
    memcpy(frame + 4, vdev.mac_addr.raw, ETH_ALEN);
    memcpy(frame + 10, remote.mac_addr.raw, ETH_ALEN);
    pt_ba_peer_raw(&vdev, &buffer, true, false);
    assert(counts->request_tid[3] == 1 && counts->last_window == 64 &&
           counts->last_ssn == 0x123);
    pt_ba_peer_raw(&vdev, &buffer, true, true);
    frame[22] = 1;
    pt_ba_peer_raw(&vdev, &buffer, true, false);
    frame[22] = 0;
    assert(counts->request_tid[3] == 1);
    frame[10] ^= 2;
    pt_ba_peer_raw(&vdev, &buffer, true, false);
    frame[10] ^= 2;
    frame[4] ^= 2;
    pt_ba_peer_raw(&vdev, &buffer, true, false);
    frame[4] ^= 2;
    assert(counts->unmatched_request == 2);
    buffer.len = 32;
    pt_ba_peer_raw(&vdev, &buffer, false, false);
    buffer.len = 33;
    pt_ba_peer_raw(&vdev, &buffer, true, false);
    pt_ba_peer_raw(&vdev, &buffer, false, false);
    assert(counts->incomplete_request == 2 && counts->unknown_fcs == 1);
    buffer.len = 37;
    pt_ba_peer_raw(&vdev, &buffer, false, false);
    assert(counts->request_tid[3] == 2);

    pt_ba_peer_map_event(&soc, 3, 1, self.mac_addr.raw);
    pt_ba_peer_map_result(&self, 1);
    assert(counts->self_map == 1);
    pt_ba_peer_map_event(&soc, 3, 81, remote.mac_addr.raw);
    pt_ba_peer_map_result(&remote, 81);
    pt_ba_peer_record(&remote, PT_BA_HTT_LOOKUP, 3, 64);
    assert(counts->remote_map_event == 1 && counts->remote_map == 1 &&
           counts->htt_tid[3] == 1 && counts->mapped);
    pt_ba_peer_unmap(&remote, 81);
    pt_ba_peer_map_event(&soc, 3, 81, remote.mac_addr.raw);
    pt_ba_peer_map_result(&remote, 81);
    pt_ba_peer_record(&remote, PT_BA_HTT_LOOKUP, 3, 64);
    assert(counts->remote_unmap == 1 && counts->reused_map == 1 &&
           counts->htt_tid[3] == 1 && !counts->mapped);
    remote.peer_id = 82;
    pt_ba_peer_map_event(&soc, 3, 82, remote.mac_addr.raw);
    pt_ba_peer_map_event(&soc, 3, 82, remote.mac_addr.raw);
    pt_ba_peer_map_result(&remote, 82);
    assert(counts->reused_map == 2 && !counts->mapped);
    remote.peer_id = 83;
    pt_ba_peer_map_event(&soc, 3, 83, remote.mac_addr.raw);
    pt_ba_peer_unmap(&remote, 83);
    pt_ba_peer_map_result(&remote, 83);
    assert(counts->reused_map == 3 && !counts->mapped);
    pt_ba_peer_vdev_stop(&vdev);
    pt_ba_peer_vdev_start(&vdev);
    remote.peer_id = 84;
    pt_ba_peer_map_event(&soc, 3, 84, remote.mac_addr.raw);
    pt_ba_peer_map_result(&remote, 84);
    pt_ba_peer_raw(&vdev, &buffer, true, false);
    assert(counts->request_tid[3] == 2 && !counts->mapped);
    puts("PASS raw address, FCS and fragment boundaries, TID/window/SSN, map and ID retirement");
}
"""

with tempfile.TemporaryDirectory() as tmp:
    source = Path(tmp) / "vectors.c"
    binary = Path(tmp) / "vectors"
    source.write_text(header + logic + vectors)
    cc = os.environ.get("CC", "clang")
    subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", source, "-o", binary], check=True)
    subprocess.run([binary], check=True)
