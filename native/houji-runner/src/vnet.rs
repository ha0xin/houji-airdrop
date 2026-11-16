use owly_awdl::{Ipv6Packet, MacAddr};

use crate::ndp;

pub const VNET_HDR_LEN: usize = 10;
pub const ZERO_VNET_HDR: [u8; VNET_HDR_LEN] = [0; VNET_HDR_LEN];

pub const VIRTIO_NET_HDR_F_NEEDS_CSUM: u8 = 1;
pub const VIRTIO_NET_HDR_GSO_NONE: u8 = 0;
pub const VIRTIO_NET_HDR_GSO_TCPV6: u8 = 4;
pub const VIRTIO_NET_HDR_GSO_ECN: u8 = 0x80;

pub const IFF_VNET_HDR: i16 = 0x4000;
pub const TUN_F_CSUM: u32 = 0x01;
pub const TUN_F_TSO6: u32 = 0x04;
pub const TUNSETOFFLOAD: usize = 0x4004_54d0;
pub const TUNSETVNETHDRSZ: usize = 0x4004_54d8;

const IPV6_HDR_LEN: usize = 40;
const ETH_HDR_LEN: usize = 14;
const ETHERTYPE_IPV6: [u8; 2] = [0x86, 0xdd];
const TCP_PROTO: u8 = 6;
const TCP_FLAG_FIN: u8 = 0x01;
const TCP_FLAG_PSH: u8 = 0x08;
const TCP_FLAG_CWR: u8 = 0x80;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct VirtioNetHdr {
    pub flags: u8,
    pub gso_type: u8,
    pub hdr_len: u16,
    pub gso_size: u16,
    pub csum_start: u16,
    pub csum_offset: u16,
}

impl VirtioNetHdr {
    pub fn from_bytes(bytes: &[u8; VNET_HDR_LEN]) -> Self {
        Self {
            flags: bytes[0],
            gso_type: bytes[1],
            hdr_len: u16::from_le_bytes([bytes[2], bytes[3]]),
            gso_size: u16::from_le_bytes([bytes[4], bytes[5]]),
            csum_start: u16::from_le_bytes([bytes[6], bytes[7]]),
            csum_offset: u16::from_le_bytes([bytes[8], bytes[9]]),
        }
    }
}

fn ones_complement_acc(mut sum: u64, bytes: &[u8]) -> u64 {
    let mut chunks = bytes.chunks_exact(8);
    for c in &mut chunks {
        sum += u64::from(u16::from_be_bytes([c[0], c[1]]))
            + u64::from(u16::from_be_bytes([c[2], c[3]]))
            + u64::from(u16::from_be_bytes([c[4], c[5]]))
            + u64::from(u16::from_be_bytes([c[6], c[7]]));
    }
    for pair in chunks.remainder().chunks(2) {
        let hi = u64::from(pair[0]);
        let lo = u64::from(pair.get(1).copied().unwrap_or(0));
        sum += (hi << 8) | lo;
    }
    sum
}

fn fold_ones_complement(mut sum: u64) -> u16 {
    while (sum >> 16) != 0 {
        sum = (sum & 0xffff) + (sum >> 16);
    }
    !(sum as u16)
}

/// Finishes Linux `CHECKSUM_PARTIAL` on an Ethernet frame whose pseudo-header
/// ones'-complement sum was pre-seeded at `csum_start + csum_offset`.
pub fn complete_partial_checksum(eth_frame: &mut [u8], hdr: &VirtioNetHdr) -> bool {
    if (hdr.flags & VIRTIO_NET_HDR_F_NEEDS_CSUM) == 0 {
        return true;
    }
    let start = usize::from(hdr.csum_start);
    let offset = usize::from(hdr.csum_offset);
    let Some(csum_pos) = start.checked_add(offset) else {
        return false;
    };
    let Some(csum_end) = csum_pos.checked_add(2) else {
        return false;
    };
    if start >= eth_frame.len() || csum_end > eth_frame.len() {
        return false;
    }
    let mut csum = fold_ones_complement(ones_complement_acc(0, &eth_frame[start..]));
    if csum == 0 {
        csum = 0xffff;
    }
    eth_frame[csum_pos..csum_end].copy_from_slice(&csum.to_be_bytes());
    true
}

/// Processes a `xos0` TX read (with optional 10-byte `VirtioNetHdr` prefix):
/// completes partial checksums, splits `GSO_TCPV6` super-segments using pooled
/// buffers from `take_buf`, intercepts local NDP NS replies via `on_na`, and emits
/// MTU-sized [`Ipv6Packet`]s via `on_packet`.
pub fn process_tap_tx(
    raw: &mut [u8],
    vnet_hdr: bool,
    own: MacAddr,
    take_buf: &mut impl FnMut() -> Vec<u8>,
    mut on_na: impl FnMut(&[u8]),
    mut on_packet: impl FnMut(Ipv6Packet),
) -> Result<(), &'static str> {
    let (hdr, eth_frame) = if vnet_hdr {
        let Some((head, body)) = raw.split_at_mut_checked(VNET_HDR_LEN) else {
            return Err("truncated virtio_net_hdr");
        };
        let hdr = VirtioNetHdr::from_bytes((&*head).try_into().unwrap());
        (hdr, body)
    } else {
        (VirtioNetHdr::default(), raw)
    };

    if eth_frame.get(12..14) != Some(&ETHERTYPE_IPV6) {
        return Ok(());
    }
    if eth_frame.len() < ETH_HDR_LEN + IPV6_HDR_LEN || (eth_frame[14] >> 4) != 6 {
        return Err("truncated IPv6 ethernet frame");
    }

    let dst = MacAddr(eth_frame[0..6].try_into().unwrap());
    let src = MacAddr(eth_frame[6..12].try_into().unwrap());
    let gso = hdr.gso_type & !VIRTIO_NET_HDR_GSO_ECN;

    if gso == VIRTIO_NET_HDR_GSO_NONE {
        if !complete_partial_checksum(eth_frame, &hdr) {
            return Err("invalid vnet partial checksum bounds");
        }
        let ip = &eth_frame[ETH_HDR_LEN..];
        if let Some(na) = ndp::synthesize_na_for_ns_parts(src, ip, own) {
            on_na(&na);
            return Ok(());
        }
        let mut data = take_buf();
        data.clear();
        data.extend_from_slice(ip);
        on_packet(Ipv6Packet { dst, src, data });
        return Ok(());
    }

    if gso != VIRTIO_NET_HDR_GSO_TCPV6 {
        return Err("unsupported vnet gso type");
    }

    let mss = usize::from(hdr.gso_size);
    if mss == 0 {
        return Err("zero vnet gso_size");
    }
    let l4_off = if (hdr.flags & VIRTIO_NET_HDR_F_NEEDS_CSUM) != 0 {
        if hdr.csum_offset != 16 {
            return Err("unexpected TCP csum_offset in TSO6");
        }
        usize::from(hdr.csum_start)
    } else {
        if eth_frame[ETH_HDR_LEN + 6] != TCP_PROTO {
            return Err("non-TCP next header in TSO6");
        }
        ETH_HDR_LEN + IPV6_HDR_LEN
    };
    if l4_off < ETH_HDR_LEN + IPV6_HDR_LEN || l4_off + 20 > eth_frame.len() {
        return Err("invalid TSO6 L4 header offset");
    }
    let tcp_hdr_len = usize::from(eth_frame[l4_off + 12] >> 4) * 4;
    let payload_off = l4_off + tcp_hdr_len;
    if tcp_hdr_len < 20 || payload_off > eth_frame.len() {
        return Err("invalid TSO6 TCP header length");
    }

    let ip_l4 = l4_off - ETH_HDR_LEN;
    let ip_ext_len = ip_l4 - IPV6_HDR_LEN;
    let base_seq = u32::from_be_bytes(eth_frame[l4_off + 4..l4_off + 8].try_into().unwrap());
    let base_flags = eth_frame[l4_off + 13];
    // IPv6 pseudo-header invariant terms: src (16 B) + dst (16 B) + protocol (6).
    let pseudo_base = ones_complement_acc(0, &eth_frame[22..54]) + u64::from(TCP_PROTO);

    let total_payload = eth_frame.len() - payload_off;
    if total_payload == 0 {
        let mut data = take_buf();
        data.clear();
        data.extend_from_slice(&eth_frame[ETH_HDR_LEN..]);
        let tcp_len = tcp_hdr_len;
        data[4..6].copy_from_slice(&((ip_ext_len + tcp_len) as u16).to_be_bytes());
        data[ip_l4 + 16..ip_l4 + 18].fill(0);
        let csum = fold_ones_complement(ones_complement_acc(
            pseudo_base + tcp_len as u64,
            &data[ip_l4..],
        ));
        data[ip_l4 + 16..ip_l4 + 18].copy_from_slice(&csum.to_be_bytes());
        on_packet(Ipv6Packet { dst, src, data });
        return Ok(());
    }

    let mut offset = 0usize;
    while offset < total_payload {
        let end = (offset + mss).min(total_payload);
        let chunk_len = end - offset;
        let is_first = offset == 0;
        let is_last = end == total_payload;
        let tcp_len = tcp_hdr_len + chunk_len;
        let ip_payload_len = ip_ext_len + tcp_len;

        let mut data = take_buf();
        data.clear();
        data.reserve(ip_l4 + tcp_len);
        data.extend_from_slice(&eth_frame[ETH_HDR_LEN..payload_off]);
        data.extend_from_slice(&eth_frame[payload_off + offset..payload_off + end]);

        data[4..6].copy_from_slice(&(ip_payload_len as u16).to_be_bytes());
        data[ip_l4 + 4..ip_l4 + 8].copy_from_slice(&base_seq.wrapping_add(offset as u32).to_be_bytes());

        let mut flags = base_flags;
        if !is_last {
            flags &= !(TCP_FLAG_FIN | TCP_FLAG_PSH);
        }
        if !is_first {
            flags &= !TCP_FLAG_CWR;
        }
        data[ip_l4 + 13] = flags;
        data[ip_l4 + 16..ip_l4 + 18].fill(0);

        let csum = fold_ones_complement(ones_complement_acc(
            pseudo_base + tcp_len as u64,
            &data[ip_l4..],
        ));
        data[ip_l4 + 16..ip_l4 + 18].copy_from_slice(&csum.to_be_bytes());

        on_packet(Ipv6Packet { dst, src, data });
        offset = end;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::Ipv6Addr;

    const SRC: MacAddr = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x55]);
    const DST: MacAddr = MacAddr([0x02, 0xaa, 0xbb, 0xcc, 0xdd, 0xee]);

    fn pseudo_sum(src_ip: Ipv6Addr, dst_ip: Ipv6Addr, next: u8, l4_len: u32) -> u16 {
        let mut sum = ones_complement_acc(0, &src_ip.octets());
        sum = ones_complement_acc(sum, &dst_ip.octets());
        sum = ones_complement_acc(sum, &l4_len.to_be_bytes());
        sum += u64::from(next);
        while (sum >> 16) != 0 {
            sum = (sum & 0xffff) + (sum >> 16);
        }
        sum as u16
    }

    #[test]
    fn partial_checksum_completes_tcp_and_udp_zero() {
        let s_ip = SRC.link_local();
        let d_ip = DST.link_local();
        // UDP length 16 with seeded pseudo-header sum whose complement is 0 -> converts to 0xffff.
        let mut frame = vec![0u8; 14 + 40 + 16];
        frame[0..6].copy_from_slice(&DST.0);
        frame[6..12].copy_from_slice(&SRC.0);
        frame[12..14].copy_from_slice(&ETHERTYPE_IPV6);
        frame[14] = 0x60;
        frame[18..20].copy_from_slice(&16u16.to_be_bytes());
        frame[20] = 17;
        frame[21] = 64;
        frame[22..38].copy_from_slice(&s_ip.octets());
        frame[38..54].copy_from_slice(&d_ip.octets());
        // UDP src=5353 dst=5353 len=16 csum=pseudo_sum
        frame[54..56].copy_from_slice(&5353u16.to_be_bytes());
        frame[56..58].copy_from_slice(&5353u16.to_be_bytes());
        frame[58..60].copy_from_slice(&16u16.to_be_bytes());
        frame[60..62].copy_from_slice(&pseudo_sum(s_ip, d_ip, 17, 16).to_be_bytes());

        // Pick payload bytes in [62..70] so total sum hits 0xffff -> complement 0 -> 0xffff.
        let cur = ones_complement_acc(0, &frame[54..]);
        let need = fold_ones_complement(cur);
        frame[62..64].copy_from_slice(&need.to_be_bytes());

        let hdr = VirtioNetHdr {
            flags: VIRTIO_NET_HDR_F_NEEDS_CSUM,
            gso_type: VIRTIO_NET_HDR_GSO_NONE,
            hdr_len: 62,
            gso_size: 0,
            csum_start: 54,
            csum_offset: 6,
        };
        assert!(complete_partial_checksum(&mut frame, &hdr));
        assert_eq!(&frame[60..62], &[0xff, 0xff]);
        assert_eq!(ndp::internet_checksum(s_ip, d_ip, 17, &frame[54..]), 0);
    }

    #[test]
    fn tso6_splits_super_segment_with_valid_seq_flags_and_csum() {
        let s_ip = SRC.link_local();
        let d_ip = DST.link_local();
        let payload: Vec<u8> = (0..3200u32).map(|i| (i.wrapping_mul(17) & 0xff) as u8).collect();
        let mut raw = vec![0u8; VNET_HDR_LEN + 14 + 40 + 32 + payload.len()];
        // virtio_net_hdr: NEEDS_CSUM + GSO_TCPV6 | GSO_ECN, mss=1378, csum_start=54, csum_offset=16
        raw[0] = VIRTIO_NET_HDR_F_NEEDS_CSUM;
        raw[1] = VIRTIO_NET_HDR_GSO_TCPV6 | VIRTIO_NET_HDR_GSO_ECN;
        raw[2..4].copy_from_slice(&(14u16 + 40 + 32).to_le_bytes());
        raw[4..6].copy_from_slice(&1378u16.to_le_bytes());
        raw[6..8].copy_from_slice(&54u16.to_le_bytes());
        raw[8..10].copy_from_slice(&16u16.to_le_bytes());

        let eth = &mut raw[VNET_HDR_LEN..];
        eth[0..6].copy_from_slice(&DST.0);
        eth[6..12].copy_from_slice(&SRC.0);
        eth[12..14].copy_from_slice(&ETHERTYPE_IPV6);
        eth[14] = 0x60;
        eth[18..20].copy_from_slice(&((32 + payload.len()) as u16).to_be_bytes());
        eth[20] = TCP_PROTO;
        eth[21] = 64;
        eth[22..38].copy_from_slice(&s_ip.octets());
        eth[38..54].copy_from_slice(&d_ip.octets());
        // 32-byte TCP header (with timestamps option)
        eth[54..56].copy_from_slice(&40000u16.to_be_bytes());
        eth[56..58].copy_from_slice(&19100u16.to_be_bytes());
        eth[58..62].copy_from_slice(&1000u32.to_be_bytes()); // seq = 1000
        eth[62..66].copy_from_slice(&5000u32.to_be_bytes());
        eth[66] = 8 << 4; // data offset = 32 bytes
        eth[67] = TCP_FLAG_CWR | TCP_FLAG_PSH | TCP_FLAG_FIN | 0x10; // CWR+ACK+PSH+FIN
        eth[68..70].copy_from_slice(&65535u16.to_be_bytes());
        eth[86..].copy_from_slice(&payload);

        let mut pkts = Vec::new();
        process_tap_tx(
            &mut raw,
            true,
            SRC,
            &mut || Vec::with_capacity(2048),
            |_| panic!("unexpected NA"),
            |p| pkts.push(p),
        )
        .unwrap();

        // 3200 / 1378 -> 1378, 1378, 444
        assert_eq!(pkts.len(), 3);
        let mut reassembled = Vec::new();
        let expected_lens = [1378usize, 1378, 444];
        let expected_seqs = [1000u32, 2378, 3756];
        let expected_flags = [
            TCP_FLAG_CWR | 0x10,
            0x10,
            0x10 | TCP_FLAG_PSH | TCP_FLAG_FIN,
        ];
        for (i, p) in pkts.iter().enumerate() {
            assert_eq!(p.dst, DST);
            assert_eq!(p.src, SRC);
            assert_eq!(p.data.len(), 40 + 32 + expected_lens[i]);
            assert_eq!(u16::from_be_bytes([p.data[4], p.data[5]]) as usize, 32 + expected_lens[i]);
            assert_eq!(u32::from_be_bytes(p.data[44..48].try_into().unwrap()), expected_seqs[i]);
            assert_eq!(p.data[53], expected_flags[i]);
            assert_eq!(ndp::internet_checksum(s_ip, d_ip, TCP_PROTO, &p.data[40..]), 0);
            reassembled.extend_from_slice(&p.data[40 + 32..]);
        }
        assert_eq!(reassembled, payload);
    }
}
