use std::net::Ipv6Addr;

use owly_awdl::{Ipv6Packet, MacAddr};

const HEADER_LEN: usize = 40;
const UDP: u8 = 17;
const ICMPV6: u8 = 58;
const MDNS_PORT: u16 = 5353;
const NS_TYPE: u8 = 135;
const NA_TYPE: u8 = 136;

/// Internet checksum over IPv6 pseudo-header and upper-layer payload.
pub fn internet_checksum(src: Ipv6Addr, dst: Ipv6Addr, next_header: u8, message: &[u8]) -> u16 {
    let mut sum: u32 = 0;
    let mut add = |bytes: &[u8]| {
        for pair in bytes.chunks(2) {
            let hi = u32::from(pair[0]);
            let lo = u32::from(pair.get(1).copied().unwrap_or(0));
            sum += (hi << 8) | lo;
        }
    };
    add(&src.octets());
    add(&dst.octets());
    add(&(message.len() as u32).to_be_bytes());
    add(&[0, 0, 0, next_header]);
    add(message);
    while sum >> 16 != 0 {
        sum = (sum & 0xffff) + (sum >> 16);
    }
    !(sum as u16)
}

/// ICMPv6 checksum over IPv6 pseudo-header and message payload.
pub fn icmpv6_checksum(src: Ipv6Addr, dst: Ipv6Addr, message: &[u8]) -> u16 {
    internet_checksum(src, dst, ICMPV6, message)
}

fn check_unicast_mdns_response(data: &[u8], own_mac: MacAddr) -> Option<(Ipv6Addr, usize)> {
    if data.len() < HEADER_LEN + 20 || (data[0] >> 4) != 6 || data[6] != UDP {
        return None;
    }
    let payload_len = usize::from(u16::from_be_bytes([data[4], data[5]]));
    if payload_len < 20 || data.len() < HEADER_LEN + payload_len {
        return None;
    }
    let src_ip = Ipv6Addr::from(<[u8; 16]>::try_from(&data[8..24]).ok()?);
    let dst_ip = Ipv6Addr::from(<[u8; 16]>::try_from(&data[24..40]).ok()?);
    if dst_ip != own_mac.link_local() {
        return None;
    }

    let udp = &data[HEADER_LEN..HEADER_LEN + payload_len];
    let dport = u16::from_be_bytes([udp[2], udp[3]]);
    let udp_len = usize::from(u16::from_be_bytes([udp[4], udp[5]]));
    if dport != MDNS_PORT || udp_len < 20 || udp_len > payload_len {
        return None;
    }
    // Only fan out DNS responses (QR=1 in DNS flags byte udp[10]), never queries or malformed checksums.
    if (udp[10] & 0x80) == 0 || (udp[6] == 0 && udp[7] == 0) {
        return None;
    }
    if internet_checksum(src_ip, dst_ip, UDP, &udp[..udp_len]) != 0 {
        return None;
    }
    Some((src_ip, udp_len))
}

fn rewrite_mdns_payload_dst(data: &mut [u8], src_ip: Ipv6Addr, udp_len: usize) {
    let mdns_group = Ipv6Addr::new(0xff02, 0, 0, 0, 0, 0, 0, 0xfb);
    data[24..40].copy_from_slice(&mdns_group.octets());
    data[HEADER_LEN + 6..HEADER_LEN + 8].copy_from_slice(&[0, 0]);
    let mut sum = internet_checksum(
        src_ip,
        mdns_group,
        UDP,
        &data[HEADER_LEN..HEADER_LEN + udp_len],
    );
    if sum == 0 {
        sum = 0xffff;
    }
    data[HEADER_LEN + 6..HEADER_LEN + 8].copy_from_slice(&sum.to_be_bytes());
}

/// Zero-copy unicast mDNS response check + rewrite: returns `(dst, data)` untouched for all
/// non-mDNS packets, only copying into `scratch` when an mDNS unicast response must be fanned out.
pub fn maybe_fanout_mdns_slice<'a>(
    dst: MacAddr,
    data: &'a [u8],
    own_mac: MacAddr,
    scratch: &'a mut Vec<u8>,
) -> (MacAddr, &'a [u8]) {
    let Some((src_ip, udp_len)) = check_unicast_mdns_response(data, own_mac) else {
        return (dst, data);
    };
    scratch.clear();
    scratch.extend_from_slice(data);
    rewrite_mdns_payload_dst(scratch, src_ip, udp_len);
    (MacAddr([0x33, 0x33, 0, 0, 0, 0xfb]), scratch.as_slice())
}

/// Rewrite inbound unicast mDNS responses addressed to `own_mac` so Linux
/// delivers them to every socket joined to `ff02::fb` on `xos0` (avoiding
/// system_server's device-bound NsdService socket stealing them from apps).
pub fn fanout_unicast_mdns_response(packet: &mut Ipv6Packet, own_mac: MacAddr) -> bool {
    let Some((src_ip, udp_len)) = check_unicast_mdns_response(&packet.data, own_mac) else {
        return false;
    };
    packet.dst = MacAddr([0x33, 0x33, 0, 0, 0, 0xfb]);
    rewrite_mdns_payload_dst(&mut packet.data, src_ip, udp_len);
    true
}

/// Slice variant of [`synthesize_na_for_ns`] avoiding `Ipv6Packet` ownership cloning.
pub fn synthesize_na_for_ns_parts(src_mac: MacAddr, data: &[u8], own_mac: MacAddr) -> Option<Vec<u8>> {
    if data.len() < HEADER_LEN + 24 || (data[0] >> 4) != 6 || data[6] != ICMPV6 {
        return None;
    }
    let payload_len = usize::from(u16::from_be_bytes([data[4], data[5]]));
    if payload_len < 24 || data.len() < HEADER_LEN + payload_len {
        return None;
    }
    if data[HEADER_LEN] != NS_TYPE || data[HEADER_LEN + 1] != 0 {
        return None;
    }

    let target = Ipv6Addr::from(<[u8; 16]>::try_from(&data[48..64]).ok()?);
    let target_mac = MacAddr::from_link_local(target)?;
    if target_mac == own_mac {
        return None;
    }

    let src_ip = Ipv6Addr::from(<[u8; 16]>::try_from(&data[8..24]).ok()?);
    let (dst_ip, dst_mac, flags) = if src_ip.is_unspecified() {
        (
            Ipv6Addr::new(0xff02, 0, 0, 0, 0, 0, 0, 1),
            MacAddr([0x33, 0x33, 0, 0, 0, 1]),
            [0x20, 0, 0, 0], // Override=1, Solicited=0 (RFC 4861 7.2.4)
        )
    } else {
        (src_ip, src_mac, [0x60, 0, 0, 0]) // Solicited=1, Override=1
    };

    let mut icmp = Vec::with_capacity(32);
    icmp.extend_from_slice(&[NA_TYPE, 0, 0, 0]);
    icmp.extend_from_slice(&flags);
    icmp.extend_from_slice(&target.octets());
    // Target Link-Layer Address option (Type=2, Length=1 unit of 8 bytes).
    icmp.extend_from_slice(&[2, 1]);
    icmp.extend_from_slice(&target_mac.0);
    let sum = icmpv6_checksum(target, dst_ip, &icmp);
    icmp[2..4].copy_from_slice(&sum.to_be_bytes());

    let mut out_data = Vec::with_capacity(HEADER_LEN + icmp.len());
    out_data.extend_from_slice(&[0x60, 0, 0, 0]);
    out_data.extend_from_slice(&(icmp.len() as u16).to_be_bytes());
    out_data.push(ICMPV6);
    out_data.push(255); // RFC 4861 requires hop limit 255 on NA.
    out_data.extend_from_slice(&target.octets());
    out_data.extend_from_slice(&dst_ip.octets());
    out_data.extend_from_slice(&icmp);

    Some(
        Ipv6Packet {
            dst: dst_mac,
            src: target_mac,
            data: out_data,
        }
        .to_ethernet(),
    )
}

/// Answer an OS-emitted ICMPv6 Neighbor Solicitation for an AWDL peer's EUI-64
/// link-local address locally on a L2 TAP interface so TCP/UDP never stalls on NDP.
#[cfg(test)]
pub fn synthesize_na_for_ns(packet: &Ipv6Packet, own_mac: MacAddr) -> Option<Vec<u8>> {
    synthesize_na_for_ns_parts(packet.src, &packet.data, own_mac)
}

/// Build an `_airdrop._tcp.local.` mDNS PTR query over IPv6 link-local UDP (`ff02::fb:5353`).
pub fn build_airdrop_mdns_ptr_query(src_mac: MacAddr, unicast_response: bool) -> Ipv6Packet {
    const MDNS_GROUP: Ipv6Addr = Ipv6Addr::new(0xff02, 0, 0, 0, 0, 0, 0, 0xfb);
    const MDNS_DST_MAC: MacAddr = MacAddr([0x33, 0x33, 0, 0, 0, 0xfb]);
    const DNS_QNAME: &[u8] = b"\x08_airdrop\x04_tcp\x05local\x00";

    let mut dns = Vec::with_capacity(12 + DNS_QNAME.len() + 4);
    dns.extend_from_slice(&[0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0]);
    dns.extend_from_slice(DNS_QNAME);
    dns.extend_from_slice(&[0x00, 0x0c]); // TYPE = PTR (12)
    dns.extend_from_slice(if unicast_response {
        &[0x80, 0x01] // CLASS = IN | QU
    } else {
        &[0x00, 0x01] // CLASS = IN (QM)
    });

    let src_ip = src_mac.link_local();
    let udp_len = (8 + dns.len()) as u16;
    let mut udp = Vec::with_capacity(usize::from(udp_len));
    udp.extend_from_slice(&MDNS_PORT.to_be_bytes());
    udp.extend_from_slice(&MDNS_PORT.to_be_bytes());
    udp.extend_from_slice(&udp_len.to_be_bytes());
    udp.extend_from_slice(&[0, 0]);
    udp.extend_from_slice(&dns);
    let mut sum = internet_checksum(src_ip, MDNS_GROUP, UDP, &udp);
    if sum == 0 {
        sum = 0xffff;
    }
    udp[6..8].copy_from_slice(&sum.to_be_bytes());

    let mut data = Vec::with_capacity(HEADER_LEN + udp.len());
    data.extend_from_slice(&[0x60, 0, 0, 0]);
    data.extend_from_slice(&udp_len.to_be_bytes());
    data.push(UDP);
    data.push(255); // RFC 6762 requires IPv6 hop limit 255 on link-local mDNS.
    data.extend_from_slice(&src_ip.octets());
    data.extend_from_slice(&MDNS_GROUP.octets());
    data.extend_from_slice(&udp);

    Ipv6Packet {
        dst: MDNS_DST_MAC,
        src: src_mac,
        data,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const OWN: MacAddr = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x55]);
    const PEER: MacAddr = MacAddr([0x02, 0xaa, 0xbb, 0xcc, 0xdd, 0xee]);

    fn make_udp(src_ip: Ipv6Addr, dst_ip: Ipv6Addr, sport: u16, dport: u16, dns_flags: u16) -> Ipv6Packet {
        let mut dns = vec![0u8; 12];
        dns[2..4].copy_from_slice(&dns_flags.to_be_bytes());
        let udp_len = (8 + dns.len()) as u16;
        let mut udp = Vec::with_capacity(usize::from(udp_len));
        udp.extend_from_slice(&sport.to_be_bytes());
        udp.extend_from_slice(&dport.to_be_bytes());
        udp.extend_from_slice(&udp_len.to_be_bytes());
        udp.extend_from_slice(&[0, 0]);
        udp.extend_from_slice(&dns);
        let mut sum = internet_checksum(src_ip, dst_ip, UDP, &udp);
        if sum == 0 {
            sum = 0xffff;
        }
        udp[6..8].copy_from_slice(&sum.to_be_bytes());

        let mut data = vec![0x60, 0, 0, 0];
        data.extend_from_slice(&udp_len.to_be_bytes());
        data.push(UDP);
        data.push(255);
        data.extend_from_slice(&src_ip.octets());
        data.extend_from_slice(&dst_ip.octets());
        data.extend_from_slice(&udp);
        Ipv6Packet {
            dst: OWN,
            src: PEER,
            data,
        }
    }

    fn make_ns(src_ip: Ipv6Addr, target_ip: Ipv6Addr) -> Ipv6Packet {
        let dst_ip = Ipv6Addr::new(
            0xff02,
            0,
            0,
            0,
            0,
            1,
            0xff00 | u16::from(target_ip.octets()[13]),
            u16::from_be_bytes([target_ip.octets()[14], target_ip.octets()[15]]),
        );
        let mut icmp = vec![NS_TYPE, 0, 0, 0, 0, 0, 0, 0];
        icmp.extend_from_slice(&target_ip.octets());
        icmp.extend_from_slice(&[1, 1]);
        icmp.extend_from_slice(&OWN.0);
        let sum = icmpv6_checksum(src_ip, dst_ip, &icmp);
        icmp[2..4].copy_from_slice(&sum.to_be_bytes());

        let mut data = vec![0x60, 0, 0, 0];
        data.extend_from_slice(&(icmp.len() as u16).to_be_bytes());
        data.push(ICMPV6);
        data.push(255);
        data.extend_from_slice(&src_ip.octets());
        data.extend_from_slice(&dst_ip.octets());
        data.extend_from_slice(&icmp);
        let o = dst_ip.octets();
        Ipv6Packet {
            dst: MacAddr([0x33, 0x33, o[12], o[13], o[14], o[15]]),
            src: OWN,
            data,
        }
    }

    #[test]
    fn solicited_ns_produces_valid_na() {
        let ns = make_ns(OWN.link_local(), PEER.link_local());
        let eth = synthesize_na_for_ns(&ns, OWN).expect("expected NA");
        let parsed = Ipv6Packet::from_ethernet(&eth).expect("valid IPv6 ethernet");
        assert_eq!(parsed.src, PEER);
        assert_eq!(parsed.dst, OWN);
        assert_eq!(parsed.data[6], ICMPV6);
        assert_eq!(parsed.data[7], 255);
        let (s, d) = parsed.addrs().unwrap();
        assert_eq!(s, PEER.link_local());
        assert_eq!(d, OWN.link_local());
        let icmp = &parsed.data[HEADER_LEN..];
        assert_eq!(icmp.len(), 32);
        assert_eq!(icmp[0], NA_TYPE);
        assert_eq!(&icmp[4..8], &[0x60, 0, 0, 0]);
        assert_eq!(&icmp[8..24], &PEER.link_local().octets());
        assert_eq!(&icmp[24..32], &[2, 1, 0x02, 0xaa, 0xbb, 0xcc, 0xdd, 0xee]);
        assert_eq!(icmpv6_checksum(s, d, icmp), 0);
    }

    #[test]
    fn unsolicited_ns_targets_all_nodes_without_solicited_flag() {
        let ns = make_ns(Ipv6Addr::UNSPECIFIED, PEER.link_local());
        let eth = synthesize_na_for_ns(&ns, OWN).expect("expected NA");
        let parsed = Ipv6Packet::from_ethernet(&eth).unwrap();
        assert_eq!(parsed.dst, MacAddr([0x33, 0x33, 0, 0, 0, 1]));
        let (s, d) = parsed.addrs().unwrap();
        assert_eq!(d, Ipv6Addr::new(0xff02, 0, 0, 0, 0, 0, 0, 1));
        assert_eq!(&parsed.data[HEADER_LEN + 4..HEADER_LEN + 8], &[0x20, 0, 0, 0]);
        assert_eq!(icmpv6_checksum(s, d, &parsed.data[HEADER_LEN..]), 0);
    }

    #[test]
    fn ignores_self_non_eui64_and_non_ns() {
        let self_ns = make_ns(Ipv6Addr::UNSPECIFIED, OWN.link_local());
        assert!(synthesize_na_for_ns(&self_ns, OWN).is_none());

        let non_eui64 = make_ns(OWN.link_local(), "fe80::1".parse().unwrap());
        assert!(synthesize_na_for_ns(&non_eui64, OWN).is_none());

        let echo = Ipv6Packet::echo_request(OWN, PEER, 1, 1, b"hi");
        assert!(synthesize_na_for_ns(&echo, OWN).is_none());

        let mut short = make_ns(OWN.link_local(), PEER.link_local());
        short.data.truncate(50);
        assert!(synthesize_na_for_ns(&short, OWN).is_none());
    }

    #[test]
    fn unicast_mdns_response_rewrites_to_multicast_with_valid_checksum() {
        let mut pkt = make_udp(PEER.link_local(), OWN.link_local(), MDNS_PORT, MDNS_PORT, 0x8400);
        assert!(fanout_unicast_mdns_response(&mut pkt, OWN));
        let mdns_group = Ipv6Addr::new(0xff02, 0, 0, 0, 0, 0, 0, 0xfb);
        assert_eq!(pkt.dst, MacAddr([0x33, 0x33, 0, 0, 0, 0xfb]));
        assert_eq!(pkt.addrs(), Some((PEER.link_local(), mdns_group)));
        assert_eq!(
            internet_checksum(PEER.link_local(), mdns_group, UDP, &pkt.data[HEADER_LEN..]),
            0
        );
    }

    #[test]
    fn ignores_mdns_query_other_ports_multicast_and_bad_checksum() {
        let mut query = make_udp(PEER.link_local(), OWN.link_local(), MDNS_PORT, MDNS_PORT, 0x0000);
        let orig_query = query.clone();
        assert!(!fanout_unicast_mdns_response(&mut query, OWN));
        assert_eq!(query, orig_query);

        let mut other_port = make_udp(PEER.link_local(), OWN.link_local(), MDNS_PORT, 5354, 0x8400);
        let orig_other = other_port.clone();
        assert!(!fanout_unicast_mdns_response(&mut other_port, OWN));
        assert_eq!(other_port, orig_other);

        let mdns_group = Ipv6Addr::new(0xff02, 0, 0, 0, 0, 0, 0, 0xfb);
        let mut mcast = make_udp(PEER.link_local(), mdns_group, MDNS_PORT, MDNS_PORT, 0x8400);
        mcast.dst = MacAddr([0x33, 0x33, 0, 0, 0, 0xfb]);
        let orig_mcast = mcast.clone();
        assert!(!fanout_unicast_mdns_response(&mut mcast, OWN));
        assert_eq!(mcast, orig_mcast);

        let mut bad_sum = make_udp(PEER.link_local(), OWN.link_local(), MDNS_PORT, MDNS_PORT, 0x8400);
        bad_sum.data[HEADER_LEN + 6] ^= 0x01;
        let orig_bad = bad_sum.clone();
        assert!(!fanout_unicast_mdns_response(&mut bad_sum, OWN));
        assert_eq!(bad_sum, orig_bad);
    }

    #[test]
    fn airdrop_mdns_ptr_query_has_valid_headers_wire_dns_and_checksum() {
        let mdns_group = Ipv6Addr::new(0xff02, 0, 0, 0, 0, 0, 0, 0xfb);
        for (mac, qu, qclass) in [(OWN, true, [0x80, 0x01]), (PEER, false, [0x00, 0x01])] {
            let mut pkt = build_airdrop_mdns_ptr_query(mac, qu);
            assert_eq!(pkt.src, mac);
            assert_eq!(pkt.dst, MacAddr([0x33, 0x33, 0, 0, 0, 0xfb]));
            assert_eq!(pkt.data.len(), 85);
            assert_eq!(pkt.data[6], UDP);
            assert_eq!(pkt.data[7], 255);
            assert_eq!(pkt.addrs(), Some((mac.link_local(), mdns_group)));
            let udp = &pkt.data[HEADER_LEN..];
            assert_eq!(&udp[0..4], &[0x14, 0xe9, 0x14, 0xe9]); // 5353 -> 5353
            assert_eq!(u16::from_be_bytes([udp[4], udp[5]]), 45);
            assert_eq!(internet_checksum(mac.link_local(), mdns_group, UDP, udp), 0);
            let dns = &udp[8..];
            assert_eq!(&dns[0..12], &[0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0]);
            assert_eq!(&dns[12..33], b"\x08_airdrop\x04_tcp\x05local\x00");
            assert_eq!(&dns[33..35], &[0x00, 0x0c]);
            assert_eq!(&dns[35..37], &qclass);
            assert!(!fanout_unicast_mdns_response(&mut pkt, OWN));
        }
    }
}
