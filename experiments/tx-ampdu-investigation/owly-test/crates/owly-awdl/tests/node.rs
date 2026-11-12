//! `Node` against frames crafted as an iPhone sends them: what it takes a
//! peer for, what it advertises (AWDL-12), when it sends (AWDL-16), and
//! what it hands the IP stack.

use owly_awdl::channel::{Encoding, SLOTS};
use owly_awdl::election::Election;
use owly_awdl::frame::{self, subtype};
use owly_awdl::sync::{SLOT_US, UNICAST_GUARD_US};
use owly_awdl::tlv::{self, HtCapabilities, Own};
use owly_awdl::{
    Action, Channel, Config, Input, Ipv6Packet, MacAddr, Node, Presence, RadioConstraints,
    Sequence, TU, Time,
};

const US: MacAddr = MacAddr([0x02, 0, 0, 0, 0, 0x01]);
const PHONE: MacAddr = MacAddr([0x86, 0x08, 0x04, 0x8a, 0x2b, 0xf4]);
const START: Time = Time(10_000_000);

fn node(channel: u8, presence: Presence) -> Node {
    let radio = RadioConstraints {
        channel: Channel(channel),
        presence,
    };
    Node::new(Config::new(US, "owly", radio), START)
}

fn seq(channels: [u8; SLOTS]) -> Sequence {
    let mut s = Sequence::empty(Encoding::OpClass);
    for (slot, c) in s.slots.iter_mut().zip(channels) {
        if c != 0 {
            *slot = Channel(c).slot();
        }
    }
    s
}

/// An iPhone's action frame: master of its own tree with metric 600, at the
/// start of its AW `aw_counter`'s slot.
fn phone_frame(kind: u8, sequence: &Sequence, aw_counter: u16, next_tu: u16) -> Vec<u8> {
    frame_from(PHONE, kind, sequence, aw_counter, next_tu)
}

/// The same frame from any address.
fn frame_from(
    mac: MacAddr,
    kind: u8,
    sequence: &Sequence,
    aw_counter: u16,
    next_tu: u16,
) -> Vec<u8> {
    let mut e = Election::own(mac);
    e.self_metric = 600;
    e.master_metric = 600;
    let own = Own {
        addr: mac,
        name: "iPhone",
        devclass: tlv::devclass::IOS,
        channel: sequence.channel(0),
        master_channel: Channel(149),
        next_tu,
        aw_counter,
        psf_interval_tu: 110,
        sequence,
        election: &e,
        ht: Some(HtCapabilities::ONE_STREAM),
        advertise_vht80_1ss: false,
    };
    frame::action_frame(mac, 1, kind, 0, |out| {
        tlv::put_all(out, &own, kind == subtype::MIF)
    })
}

fn feed(n: &mut Node, at: Time, bytes: &[u8]) -> Vec<Action> {
    n.handle(Input::Frame {
        rx_time: at,
        rssi: Some(-40),
        bytes,
    })
    .collect()
}

fn tick(n: &mut Node, at: Time) -> Vec<Action> {
    n.handle(Input::Timer(at)).collect()
}

/// Has the phone become our master: a MIF, then the election's tick.
fn meet(n: &mut Node, sequence: &Sequence) -> Time {
    let t = START.plus(100_000);
    let a = feed(n, t, &phone_frame(subtype::MIF, sequence, 400, 64));
    assert!(
        a.iter()
            .any(|a| matches!(a, Action::PeerUp(p) if p.mac == PHONE))
    );
    let t = START.plus(1_000_000);
    // Heard again just before the tick, so its phase is current.
    feed(n, t, &phone_frame(subtype::PSF, sequence, 400 + 4 * 14, 64));
    tick(n, t);
    assert_eq!(n.election().sync, PHONE);
    t
}

fn tcp_syn() -> Ipv6Packet {
    let mut seg = vec![0u8; 20];
    seg[12] = 5 << 4;
    seg[13] = 0x02;
    let mut data = vec![0x60, 0, 0, 0, 0, 20, 6, 64];
    data.extend(PHONE.link_local().octets());
    data.extend(US.link_local().octets());
    data.extend(seg);
    Ipv6Packet {
        dst: US,
        src: PHONE,
        data,
    }
}

fn data_from_phone(p: &Ipv6Packet) -> Vec<u8> {
    frame::data_frame(PHONE, p.dst, 9, 9, &p.data)
}

fn transmits(actions: &[Action]) -> Vec<&frame::Frame> {
    actions
        .iter()
        .filter_map(|a| match a {
            Action::Transmit { frame, .. } => Some(frame),
            _ => None,
        })
        .collect()
}

const PHONE_SEQ: [u8; SLOTS] = [149, 149, 149, 0, 0, 0, 0, 0, 6, 149, 149, 0, 0, 0, 0, 0];

// spec: AWDL-12
#[test]
fn a_fixed_radio_advertises_its_channel_in_every_slot() {
    let mut n = node(6, Presence::Fixed);
    meet(&mut n, &seq(PHONE_SEQ));
    assert_eq!(n.sequence().channels(), [6; SLOTS]);
}

// spec: AWDL-12
#[test]
fn a_shared_radio_advertises_only_the_masters_slots_on_its_channel() {
    let mut n = node(6, Presence::Share);
    meet(&mut n, &seq(PHONE_SEQ));
    let mut want = [0; SLOTS];
    want[8] = 6;
    assert_eq!(n.sequence().channels(), want);
    assert_eq!(n.no_overlap(), None);
    // A master with no slot on our channel: what we had stands, and the
    // host hears which channel the master is on instead.
    let mut n = node(44, Presence::Share);
    meet(&mut n, &seq(PHONE_SEQ));
    assert_eq!(n.sequence().channels(), [44; SLOTS]);
    assert!(n.stats().no_overlap > 0);
    assert_eq!(n.no_overlap(), Some((PHONE, 149)));
}

// spec: AWDL-17
#[test]
fn a_peer_is_valid_once_it_has_sent_a_mif_with_its_version() {
    let mut n = node(6, Presence::Fixed);
    let s = seq(PHONE_SEQ);
    let a = feed(&mut n, START, &phone_frame(subtype::PSF, &s, 400, 64));
    assert!(!a.iter().any(|a| matches!(a, Action::PeerUp(_))));
    assert!(!n.peer(PHONE).unwrap().valid);
    let a = feed(
        &mut n,
        START.plus(1000),
        &phone_frame(subtype::MIF, &s, 400, 63),
    );
    let up: Vec<_> = a
        .iter()
        .filter(|a| matches!(a, Action::PeerUp(_)))
        .collect();
    assert_eq!(up.len(), 1);
    let Action::PeerUp(info) = up[0] else {
        unreachable!()
    };
    assert_eq!(
        (info.mac, info.name.as_str(), info.devclass, info.rssi),
        (PHONE, "iPhone", 2, Some(-40))
    );
    // Only once.
    let a = feed(
        &mut n,
        START.plus(2000),
        &phone_frame(subtype::MIF, &s, 400, 62),
    );
    assert!(!a.iter().any(|a| matches!(a, Action::PeerUp(_))));
}

// spec: SEC-11
#[test]
fn spoofed_peers_cannot_fill_the_table() {
    let mut n = node(6, Presence::Fixed);
    let s = seq(PHONE_SEQ);
    let fake = |i: usize| MacAddr([0x02, 0xff, 0, 0, (i >> 8) as u8, i as u8]);
    for i in 0..owly_awdl::MAX_PEERS {
        let at = START.plus(i as u64 * 1000);
        feed(&mut n, at, &frame_from(fake(i), subtype::MIF, &s, 400, 64));
    }
    // The phone takes the place of the fake heard from longest ago.
    let at = START.plus(owly_awdl::MAX_PEERS as u64 * 1000);
    let a = feed(&mut n, at, &phone_frame(subtype::MIF, &s, 400, 64));
    assert!(
        a.iter()
            .any(|a| matches!(a, Action::PeerDown(m) if *m == fake(0)))
    );
    assert!(
        a.iter()
            .any(|a| matches!(a, Action::PeerUp(p) if p.mac == PHONE))
    );
    assert!(n.peer(fake(0)).is_none());
    assert!(n.peer(fake(1)).is_some());
    assert_eq!(n.stats().peers_evicted, 1);
}

// spec: AWDL-18
#[test]
fn a_syn_before_the_mif_is_held_until_the_peer_is_valid() {
    let mut n = node(6, Presence::Fixed);
    let s = seq(PHONE_SEQ);
    feed(&mut n, START, &phone_frame(subtype::PSF, &s, 400, 64));
    let syn = tcp_syn();
    let a = feed(&mut n, START.plus(10), &data_from_phone(&syn));
    assert!(!a.iter().any(|a| matches!(a, Action::Deliver(_))), "{a:?}");
    // Even from a peer we've never heard.
    let a = feed(&mut n, START.plus(20), &data_from_phone(&syn));
    assert!(!a.iter().any(|a| matches!(a, Action::Deliver(_))));
    let a = feed(
        &mut n,
        START.plus(30),
        &phone_frame(subtype::MIF, &s, 400, 63),
    );
    let up = a
        .iter()
        .position(|a| matches!(a, Action::PeerUp(_)))
        .expect("up");
    let delivered: Vec<usize> = a
        .iter()
        .enumerate()
        .filter(|(_, a)| matches!(a, Action::Deliver(p) if *p == syn))
        .map(|(i, _)| i)
        .collect();
    assert_eq!(delivered.len(), 1, "the duplicate is held once");
    assert!(up < delivered[0], "the neighbour entry comes first");
}

// spec: AWDL-18
#[test]
fn held_syns_expire() {
    let mut n = node(6, Presence::Fixed);
    feed(&mut n, START, &data_from_phone(&tcp_syn()));
    tick(&mut n, START.plus(3_000_000));
    let a = feed(
        &mut n,
        START.plus(3_100_000),
        &phone_frame(subtype::MIF, &seq(PHONE_SEQ), 400, 63),
    );
    assert!(!a.iter().any(|a| matches!(a, Action::Deliver(_))));
}

// spec: AWDL-17, NET-3
#[test]
fn data_reaches_the_stack_only_from_peers_and_only_for_us() {
    let mut n = node(6, Presence::Fixed);
    let mut packet = Ipv6Packet::echo_request(PHONE, US, 1, 1, b"hi");
    // From a stranger: dropped.
    assert!(
        !feed(&mut n, START, &data_from_phone(&packet))
            .iter()
            .any(|a| matches!(a, Action::Deliver(_)))
    );
    meet(&mut n, &seq(PHONE_SEQ));
    let t = START.plus(1_100_000);
    let a = feed(&mut n, t, &data_from_phone(&packet));
    assert!(a.contains(&Action::Deliver(packet.clone())));
    // To someone else, which a monitor overhears.
    packet.dst = MacAddr([0x02, 9, 9, 9, 9, 9]);
    assert!(
        !feed(&mut n, t, &data_from_phone(&packet))
            .iter()
            .any(|a| matches!(a, Action::Deliver(_)))
    );
    // To a multicast group.
    packet.dst = MacAddr([0x33, 0x33, 0, 0, 0, 0xfb]);
    assert!(feed(&mut n, t, &data_from_phone(&packet)).contains(&Action::Deliver(packet.clone())));
    // Data keeps the peer alive without action frames (patch owl-07).
    let late = t.plus(4_500_000);
    feed(&mut n, late, &data_from_phone(&packet));
    assert!(
        !tick(&mut n, late.plus(1_000_000))
            .iter()
            .any(|a| matches!(a, Action::PeerDown(_)))
    );
    assert!(tick(&mut n, late.plus(5_100_000)).contains(&Action::PeerDown(PHONE)));
}

#[test]
fn what_the_stack_sends_to_nobody_is_dropped_and_to_us_comes_back() {
    let mut n = node(6, Presence::Fixed);
    let p = Ipv6Packet::echo_request(US, PHONE, 1, 1, b"x");
    let a: Vec<Action> = n
        .handle(Input::Outbound {
            now: START,
            packet: p,
        })
        .collect();
    assert!(transmits(&a).iter().all(|f| f.to != PHONE));
    assert_eq!(n.stats().dropped_no_peer, 1);
    let to_self = Ipv6Packet::echo_request(US, US, 1, 1, b"x");
    let a: Vec<Action> = n
        .handle(Input::Outbound {
            now: START,
            packet: to_self.clone(),
        })
        .collect();
    assert!(a.contains(&Action::Deliver(to_self)));
}

// spec: AWDL-16
#[test]
fn unicast_goes_only_in_shared_slots_clear_of_their_edges() {
    let mut n = node(6, Presence::Share);
    let phone = seq(PHONE_SEQ);
    let mut t = meet(&mut n, &phone);
    let p = Ipv6Packet::echo_request(US, PHONE, 1, 1, b"payload");
    let mut sent_at = None;
    n.handle(Input::Outbound { now: t, packet: p })
        .for_each(drop);
    for _ in 0..(32 * SLOT_US / 256) {
        t = t.plus(256);
        let a = tick(&mut n, t);
        if transmits(&a)
            .iter()
            .any(|f| f.to == PHONE && f.bytes.ends_with(b"payload"))
        {
            sent_at = Some(t);
            break;
        }
    }
    let t = sent_at.expect("never sent");
    let c = n.clock();
    assert_eq!(c.slot(t), 8, "slot 8 is the one both are on 6");
    let into = SLOT_US - c.next_slot_us(t);
    assert!(into >= UNICAST_GUARD_US, "{into} µs into the slot");
}

// spec: AWDL-12, AWDL-16
#[test]
fn a_fixed_radio_sends_unicast_in_every_slot_its_peer_is_on_its_channel_without_acks() {
    let mut phone = PHONE_SEQ;
    phone[3] = 6;
    let mut n = node(6, Presence::Fixed);
    let mut t = meet(&mut n, &seq(phone));
    let mut slots = std::collections::BTreeSet::new();
    for i in 0..6 {
        let p = Ipv6Packet::echo_request(US, PHONE, 1, i, b"payload");
        n.handle(Input::Outbound { now: t, packet: p })
            .for_each(drop);
        let mut sent = false;
        for _ in 0..(32 * SLOT_US / 256) {
            t = t.plus(256);
            let a = tick(&mut n, t);
            let ours = transmits(&a)
                .into_iter()
                .find(|f| f.to == PHONE && f.bytes.ends_with(b"payload"))
                .cloned();
            if let Some(f) = ours {
                assert!(f.no_ack, "patch owl-05: sent once, without an ACK");
                slots.insert(n.clock().slot(t));
                sent = true;
                break;
            }
        }
        assert!(sent, "packet {i} never sent");
        // Into the next slot, so the next packet finds the other one.
        t = t.plus(SLOT_US);
    }
    assert_eq!(slots.into_iter().collect::<Vec<_>>(), [3, 8]);
    // A shared radio waits for ACKs.
    let radio = n.config().radio.clone();
    let shared = RadioConstraints {
        presence: Presence::Share,
        ..radio
    };
    assert!(!Config::new(US, "owly", shared).no_ack);
}

#[test]
fn packets_for_a_peer_that_went_are_dropped_with_it() {
    // On 44, where the phone never is, so nothing goes.
    let mut n = node(44, Presence::Share);
    let t = meet(&mut n, &seq(PHONE_SEQ));
    let queued = t.plus(4_500_000);
    for i in 0..3 {
        let p = Ipv6Packet::echo_request(US, PHONE, 1, i, b"payload");
        n.handle(Input::Outbound {
            now: queued,
            packet: p,
        })
        .for_each(drop);
    }
    // Ours, and the node's own neighbour echoes.
    let queued = n.backlog();
    assert!(queued >= 3, "{queued}");
    // The phone times out with them still young: they go with it, rather
    // than holding up the host until they expire.
    assert!(tick(&mut n, t.plus(5_100_000)).contains(&Action::PeerDown(PHONE)));
    assert_eq!(n.backlog(), 0);
    assert_eq!(n.stats().dropped_no_peer, queued as u64);
    assert_eq!(n.stats().dropped_expired, 0);
    assert_eq!(n.no_overlap(), None, "no master, no overlap to miss");
}

// spec: AWDL-16
#[test]
fn multicast_goes_in_slots_0_and_10_or_any_slot_on_a_fixed_radio() {
    for (presence, slots) in [(Presence::Share, &[0usize, 10][..]), (Presence::Fixed, &[])] {
        let mut n = node(6, presence);
        let mut t = START;
        let group = MacAddr([0x33, 0x33, 0, 0, 0, 0xfb]);
        let mut sent = Vec::new();
        for i in 0..40u64 {
            let mut p = Ipv6Packet::echo_request(US, PHONE, 1, i as u16, b"m");
            p.dst = group;
            n.handle(Input::Outbound { now: t, packet: p })
                .for_each(drop);
            let end = t.plus(SLOT_US);
            while t < end {
                for f in transmits(&tick(&mut n, t)) {
                    if f.to == group {
                        sent.push(n.clock().slot(t));
                    }
                }
                t = t.plus(512);
            }
        }
        assert!(!sent.is_empty());
        if !slots.is_empty() {
            assert!(
                sent.iter().all(|s| slots.contains(s)),
                "{presence:?}: {sent:?}"
            );
        } else {
            let distinct: std::collections::BTreeSet<_> = sent.iter().collect();
            assert!(distinct.len() > 2, "{presence:?}: {sent:?}");
        }
    }
}

#[test]
fn psfs_every_110_tu_and_a_mif_every_slot() {
    let mut n = node(6, Presence::Fixed);
    let mut t = START;
    let (mut psf, mut mif) = (Vec::new(), Vec::new());
    while t < START.plus(1_000_000) {
        let a: Vec<Action> = n.handle(Input::Timer(t)).collect();
        for f in transmits(&a) {
            if let Ok(frame::Received::Action { subtype, .. }) = frame::parse(&f.bytes) {
                if subtype == subtype::PSF {
                    psf.push(t)
                } else {
                    mif.push(t)
                }
            }
        }
        let Some(Action::ArmTimer(next)) = a.last() else {
            panic!("no timer")
        };
        assert!(*next > t);
        t = *next;
    }
    assert_eq!(psf.first(), Some(&START.plus(110 * TU)));
    assert!(psf.windows(2).all(|w| w[1].since(w[0]) == 110 * TU));
    assert!((14..=16).contains(&mif.len()), "{} MIFs", mif.len());
    // The first goes at once, as legacy's did, and the rest mid-slot.
    assert!(
        mif[1..]
            .windows(2)
            .all(|w| w[1].since(w[0]).abs_diff(SLOT_US) < 2 * TU)
    );
}

#[test]
fn weak_frames_are_ignored_and_known_peers_kept_a_little_longer() {
    let mut n = node(6, Presence::Fixed);
    let f = phone_frame(subtype::MIF, &seq(PHONE_SEQ), 400, 64);
    n.handle(Input::Frame {
        rx_time: START,
        rssi: Some(-66),
        bytes: &f,
    })
    .for_each(drop);
    assert!(n.peer(PHONE).is_none());
    n.handle(Input::Frame {
        rx_time: START,
        rssi: Some(-60),
        bytes: &f,
    })
    .for_each(drop);
    n.handle(Input::Frame {
        rx_time: START.plus(1),
        rssi: Some(-69),
        bytes: &f,
    })
    .for_each(drop);
    assert_eq!(n.peer(PHONE).unwrap().rssi, Some(-69));
    n.handle(Input::Frame {
        rx_time: START.plus(2),
        rssi: Some(-71),
        bytes: &f,
    })
    .for_each(drop);
    assert_eq!(n.peer(PHONE).unwrap().rssi, Some(-69));
}

#[test]
fn a_malformed_frame_changes_nothing() {
    let mut n = node(6, Presence::Fixed);
    let mut f = phone_frame(subtype::MIF, &seq(PHONE_SEQ), 400, 64);
    f.push(21); // half a TLV header
    f.push(2);
    feed(&mut n, START, &f);
    assert!(n.peer(PHONE).is_none());
    assert_eq!(n.stats().rx_malformed, 1);
}

// spec: AWDL-15
#[test]
fn block_ack_sessions_are_accepted_only_when_asked_to() {
    let mut req = Vec::new();
    frame::put_header(&mut req, frame::fc::ACTION, US, PHONE, 3);
    req.extend_from_slice(&[3, 0, 9, 0x02, 0x10, 0, 0, 0, 0]);
    for accept in [false, true] {
        let mut n = node(6, Presence::Fixed);
        let mut cfg = n.config().clone();
        cfg.accept_addba = accept;
        n = Node::new(cfg, START);
        meet(&mut n, &seq(PHONE_SEQ));
        let a = feed(&mut n, START.plus(1_200_000), &req);
        let answered = transmits(&a)
            .iter()
            .any(|f| f.to == PHONE && f.bytes[24] == 3 && f.bytes[25] == 1);
        assert_eq!(answered, accept);
    }
}


#[test]
fn bulk_coalescing_holds_briefly_and_preserves_order() {
    let radio = RadioConstraints {
        channel: Channel(149),
        presence: Presence::Fixed,
    };
    let mut cfg = Config::new(US, "owly", radio);
    cfg.amsdu = true;
    cfg.max_frame = 7935;
    let mut node = Node::new(cfg, START);
    let now = meet(&mut node, &seq([149; SLOTS])).plus(100);
    let first = Ipv6Packet::echo_request(US, PHONE, 5, 1, &[1; 900]);
    let next = Ipv6Packet::echo_request(US, PHONE, 5, 2, &[2; 900]);
    let expected = [first.data.clone(), next.data.clone()];
    for (when, packet) in [(now, first), (now.plus(100), next)] {
        let actions: Vec<_> = node.handle(Input::Outbound { now: when, packet }).collect();
        assert!(transmits(&actions).iter().all(|f| f.to != PHONE));
    }
    assert!(transmits(&tick(&mut node, now.plus(999))).iter().all(|f| f.to != PHONE));
    let actions = tick(&mut node, now.plus(1000));
    let frames = transmits(&actions);
    let frame = frames.iter().find(|f| f.to == PHONE).expect("coalesced frame");
    let frame::Received::Data { header, amsdu, body } = frame::parse(&frame.bytes).unwrap()
        else { panic!("expected data frame") };
    assert!(amsdu);
    let msdus = frame::msdus(&header, amsdu, body).unwrap();
    let payloads: Vec<_> = msdus.iter().map(|p| p.payload).collect();
    assert_eq!(payloads, expected.each_ref().map(Vec::as_slice));
}


fn coalescing_node(max_frame: usize) -> Node {
    let radio = RadioConstraints {
        channel: Channel(149),
        presence: Presence::Fixed,
    };
    let mut cfg = Config::new(US, "owly", radio);
    cfg.amsdu = true;
    cfg.max_frame = max_frame;
    cfg.neighbour_echo = false;
    Node::new(cfg, START)
}

fn outgoing(node: &mut Node, now: Time, packet: Ipv6Packet) -> Vec<Action> {
    node.handle(Input::Outbound { now, packet }).collect()
}

fn phone_payloads(actions: &[Action]) -> Vec<Vec<u8>> {
    transmits(actions)
        .into_iter()
        .filter(|frame| frame.to == PHONE)
        .flat_map(|frame| {
            let frame::Received::Data { header, amsdu, body } = frame::parse(&frame.bytes).unwrap()
                else { panic!("expected data frame") };
            frame::msdus(&header, amsdu, body).unwrap().into_iter().map(|m| m.payload.to_vec())
        })
        .collect()
}

fn next_shared(n: &Node, mut t: Time) -> Time {
    for _ in 0..40 {
        let wait = n.send_wait(PHONE, t).unwrap();
        if wait == 0 {
            return t;
        }
        t = t.plus(wait);
    }
    panic!("no open slot found");
}

#[test]
fn a_full_aggregate_sends_before_the_hold_deadline() {
    let mut node = coalescing_node(7935);
    let now = meet(&mut node, &seq([149; SLOTS])).plus(100);
    let first = Ipv6Packet::echo_request(US, PHONE, 5, 1, &[1; 1400]);
    let next = Ipv6Packet::echo_request(US, PHONE, 5, 2, &[2; 1400]);
    let expected = [first.data.clone(), next.data.clone()];
    assert!(phone_payloads(&outgoing(&mut node, now, first)).is_empty());
    let output = outgoing(&mut node, now.plus(100), next);
    assert_eq!(transmits(&output).iter().filter(|f| f.to == PHONE).count(), 1);
    assert_eq!(phone_payloads(&output), expected);
}

#[test]
fn a_later_small_packet_cannot_overtake_an_earlier_packet_that_does_not_fit() {
    let mut node = coalescing_node(2304);
    let start = meet(&mut node, &seq(PHONE_SEQ));
    let closed = (0..1100)
        .map(|i| start.plus(i * 1000))
        .find(|t| node.send_wait(PHONE, *t).unwrap() > 1500)
        .expect("closed window");
    let first = Ipv6Packet::echo_request(US, PHONE, 5, 1, &[1; 1000]);
    let next = Ipv6Packet::echo_request(US, PHONE, 5, 2, &[2; 1300]);
    let small = Ipv6Packet::echo_request(US, PHONE, 5, 3, &[3; 100]);
    let expected = [first.data.clone(), next.data.clone(), small.data.clone()];
    for (i, packet) in [first, next, small].into_iter().enumerate() {
        assert!(phone_payloads(&outgoing(&mut node, closed.plus(i as u64 * 100), packet)).is_empty());
    }
    let open = next_shared(&node, closed.plus(200));
    assert_eq!(phone_payloads(&tick(&mut node, open)), expected);
}

#[test]
fn coalescing_rechecks_the_channel_when_the_hold_expires() {
    let mut node = coalescing_node(7935);
    let start = meet(&mut node, &seq(PHONE_SEQ));
    let before_close = (0..11000)
        .map(|i| start.plus(i * 100))
        .find(|t| node.send_wait(PHONE, *t) == Some(0)
            && node.send_wait(PHONE, t.plus(1000)).unwrap() > 0)
        .expect("end of a shared window");
    let packet = Ipv6Packet::echo_request(US, PHONE, 5, 1, &[1; 900]);
    let expected = [packet.data.clone()];
    assert!(phone_payloads(&outgoing(&mut node, before_close, packet)).is_empty());
    let expired = before_close.plus(1000);
    assert!(phone_payloads(&tick(&mut node, expired)).is_empty());
    let reopen = next_shared(&node, expired);
    assert_eq!(phone_payloads(&tick(&mut node, reopen)), expected);
}


fn tcp_ack_to_phone(ack_num: u32, window: u16, options: &[u8]) -> Ipv6Packet {
    assert_eq!(options.len() % 4, 0);
    let hdr_len = 20 + options.len();
    let mut seg = Vec::with_capacity(hdr_len);
    seg.extend_from_slice(&50000u16.to_be_bytes());
    seg.extend_from_slice(&9000u16.to_be_bytes());
    seg.extend_from_slice(&1u32.to_be_bytes());
    seg.extend_from_slice(&ack_num.to_be_bytes());
    seg.push(((hdr_len / 4) as u8) << 4);
    seg.push(0x10);
    seg.extend_from_slice(&window.to_be_bytes());
    seg.extend_from_slice(&[0, 0, 0, 0]);
    seg.extend_from_slice(options);
    let mut data = vec![0x60, 0, 0, 0];
    data.extend_from_slice(&(seg.len() as u16).to_be_bytes());
    data.extend_from_slice(&[6, 64]);
    data.extend_from_slice(&US.link_local().octets());
    data.extend_from_slice(&PHONE.link_local().octets());
    data.extend_from_slice(&seg);
    Ipv6Packet {
        dst: PHONE,
        src: US,
        data,
    }
}

#[test]
fn cumulative_pure_tcp_acks_coalesce_and_supersede_within_hold_window() {
    let mut node = coalescing_node(3839);
    let now = meet(&mut node, &seq([149; SLOTS])).plus(100);
    let ts_opt = [1, 1, 8, 10, 0, 0, 0, 1, 0, 0, 0, 1];
    let ack1 = tcp_ack_to_phone(1000, 4096, &ts_opt);
    let ack2 = tcp_ack_to_phone(2000, 4095, &ts_opt);
    let ack3 = tcp_ack_to_phone(3000, 4094, &ts_opt);
    assert!(phone_payloads(&outgoing(&mut node, now, ack1)).is_empty());
    assert!(phone_payloads(&outgoing(&mut node, now.plus(300), ack2)).is_empty());
    assert!(phone_payloads(&outgoing(&mut node, now.plus(600), ack3.clone())).is_empty());
    assert_eq!(node.backlog(), 1);
    assert!(phone_payloads(&tick(&mut node, now.plus(999))).is_empty());
    let sent = tick(&mut node, now.plus(1000));
    assert_eq!(transmits(&sent).iter().filter(|f| f.to == PHONE).count(), 1);
    assert_eq!(phone_payloads(&sent), [ack3.data]);
}

#[test]
fn duplicate_and_sack_tcp_acks_are_kept_and_aggregated() {
    let mut node = coalescing_node(3839);
    let now = meet(&mut node, &seq([149; SLOTS])).plus(100);
    let ts_opt = [1, 1, 8, 10, 0, 0, 0, 1, 0, 0, 0, 1];
    let sack_opt = [1, 1, 5, 10, 0, 0, 8, 0, 0, 0, 12, 0];
    let ack1 = tcp_ack_to_phone(1000, 4096, &ts_opt);
    let dup2 = tcp_ack_to_phone(1000, 4096, &ts_opt);
    let sack3 = tcp_ack_to_phone(1000, 4096, &sack_opt);
    let expected = [ack1.data.clone(), dup2.data.clone(), sack3.data.clone()];
    assert!(phone_payloads(&outgoing(&mut node, now, ack1)).is_empty());
    assert!(phone_payloads(&outgoing(&mut node, now.plus(200), dup2)).is_empty());
    assert!(phone_payloads(&outgoing(&mut node, now.plus(400), sack3)).is_empty());
    assert_eq!(node.backlog(), 3);
    let sent = tick(&mut node, now.plus(1000));
    assert_eq!(transmits(&sent).iter().filter(|f| f.to == PHONE).count(), 1);
    assert_eq!(phone_payloads(&sent), expected);
}

#[test]
fn pure_tcp_ack_near_slot_close_flushes_immediately_instead_of_stranding() {
    let mut node = coalescing_node(3839);
    let start = meet(&mut node, &seq(PHONE_SEQ));
    let before_close = (0..11000)
        .map(|i| start.plus(i * 100))
        .find(|t| {
            node.send_wait(PHONE, *t) == Some(0)
                && node.send_wait(PHONE, t.plus(1000)).unwrap() > 0
        })
        .expect("end of a shared window");
    let ts_opt = [1, 1, 8, 10, 0, 0, 0, 1, 0, 0, 0, 1];
    let ack = tcp_ack_to_phone(4000, 4096, &ts_opt);
    let sent = outgoing(&mut node, before_close, ack.clone());
    assert_eq!(phone_payloads(&sent), [ack.data]);
    assert_eq!(node.backlog(), 0);
}

#[test]
fn initiate_addba_emits_request_and_contiguous_qos_data_sequences() {
    let radio = RadioConstraints {
        channel: Channel(149),
        presence: Presence::Fixed,
    };
    let mut c = Config::new(US, "owly", radio);
    c.amsdu = true;
    c.no_ack = false;
    c.initiate_addba = true;
    c.initiate_addba_window = 64;
    let mut node = Node::new(c, START);
    let t0 = START.plus(100_000);
    let actions = feed(&mut node, t0, &phone_frame(subtype::MIF, &seq([149; SLOTS]), 400, 64));
    let addba_tx: Vec<_> = transmits(&actions)
        .into_iter()
        .filter(|f| {
            f.to == PHONE && f.bytes.len() == 33 && f.bytes[0] == 0xd0 && f.bytes[24..26] == [3, 0]
        })
        .collect();
    assert_eq!(addba_tx.len(), 1);
    assert_eq!(node.stats().tx_addba_req, 1);
    let token = addba_tx[0].bytes[26];
    let ssn = u16::from_le_bytes([addba_tx[0].bytes[31], addba_tx[0].bytes[32]]) >> 4;
    assert_eq!(ssn, 0);

    let resp = frame::addba_response_with_amsdu(PHONE, US, 7, token, 0x1003, 0, true);
    let _ = feed(&mut node, t0.plus(100), &resp);
    assert_eq!(node.stats().rx_addba_resp, 1);

    let now = START.plus(1_000_000);
    feed(&mut node, now, &phone_frame(subtype::PSF, &seq([149; SLOTS]), 400 + 4 * 14, 64));
    let _ = tick(&mut node, now);

    // Emit PSF/MIF action frames in between to prove action frames do NOT create
    // sequence-number holes in peer's TID 0 QoS sequence stream!
    let p1 = Ipv6Packet::echo_request(US, PHONE, 1, 1, &[0xaa; 64]);
    let sent1 = outgoing(&mut node, now.plus(100), p1);
    let unicast1: Vec<_> = transmits(&sent1)
        .into_iter()
        .filter(|f| f.to == PHONE && f.bytes[0] == 0x88)
        .collect();
    assert_eq!(unicast1.len(), 1);
    let seq1 = u16::from_le_bytes([unicast1[0].bytes[22], unicast1[0].bytes[23]]) >> 4;
    assert_eq!(seq1, 0);

    let _ = tick(&mut node, now.plus(200_000));
    let p2 = Ipv6Packet::echo_request(US, PHONE, 1, 2, &[0xbb; 64]);
    let sent2 = outgoing(&mut node, now.plus(200_100), p2);
    let unicast2: Vec<_> = transmits(&sent2)
        .into_iter()
        .filter(|f| f.to == PHONE && f.bytes[0] == 0x88)
        .collect();
    assert_eq!(unicast2.len(), 1);
    let seq2 = u16::from_le_bytes([unicast2[0].bytes[22], unicast2[0].bytes[23]]) >> 4;
    assert_eq!(seq2, 1);
}

#[test]
fn tx_addba_response_validation_controls_retry() {
    // Observe the real response parser and retry state machine, rather than
    // duplicating the implementation predicate in a helper-only test.
    for (name, token_delta, params, status, timeout, amsdu, accepted) in [
        ("valid", 0u8, 0x1003u16, 0u16, 0u16, true, true),
        ("valid_without_amsdu", 0, 0x1002, 0, 0, false, true),
        ("smaller_window", 0, 0x0802, 0, 0, true, true),
        ("wrong_token", 1, 0x1003, 0, 0, true, false),
        ("wrong_tid", 0, 0x1007, 0, 0, true, false),
        ("zero_window", 0, 0x0003, 0, 0, true, false),
        ("oversize_window", 0, 0x1043, 0, 0, true, false),
        ("delayed_policy", 0, 0x1001, 0, 0, true, false),
        ("rejected", 0, 0x1003, 37, 0, true, false),
        ("unsolicited_amsdu", 0, 0x1003, 0, 0, false, false),
        ("nonzero_timeout", 0, 0x1003, 0, 1, true, false),
    ] {
        let radio = RadioConstraints { channel: Channel(149), presence: Presence::Fixed };
        let mut c = Config::new(US, "owly", radio);
        c.amsdu = amsdu;
        c.no_ack = false;
        c.initiate_addba = true;
        c.initiate_addba_window = 64;
        let mut node = Node::new(c, START);
        let t = START.plus(100_000);
        let actions = feed(&mut node, t, &phone_frame(subtype::MIF, &seq([149; SLOTS]), 400, 64));
        let token = transmits(&actions).into_iter()
            .find(|f| f.bytes.len() == 33 && f.bytes[24..26] == [3, 0])
            .expect("initial ADDBA request").bytes[26];
        let mut response = frame::addba_response_with_amsdu(PHONE, US, 7, token.wrapping_add(token_delta), 0x1003, 0, true);
        response[27..29].copy_from_slice(&status.to_le_bytes());
        response[29..31].copy_from_slice(&params.to_le_bytes());
        response[31..33].copy_from_slice(&timeout.to_le_bytes());
        feed(&mut node, t.plus(100), &response);
        assert_eq!(node.stats().rx_addba_resp, 1, "{name}");
        feed(&mut node, t.plus(300_000), &phone_frame(subtype::MIF, &seq([149; SLOTS]), 414, 64));
        outgoing(&mut node, t.plus(300_100), Ipv6Packet::echo_request(US, PHONE, 1, 1, &[0xaa; 64]));
        assert_eq!(node.stats().tx_addba_req, if accepted { 1 } else { 2 }, "{name}");
    }
}
