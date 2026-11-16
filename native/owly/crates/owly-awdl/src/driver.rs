//! The driver (the `driver` feature): a [`Node`] run on any radio that can
//! inject and capture 802.11 frames, on std and tokio.
//!
//! A backend brings such a radio up, then hands the driver its two ends:
//! the [`Air`], where the node's frames go out and the peers' come in, and
//! the [`Netif`], the host's network interface for the link (a TAP device
//! on Linux), where IPv6 packets come and go. A monitor interface on the
//! computer and a radio elsewhere, an ESP32 over USB say, differ only in
//! their `Air`.
//!
//! [`spawn`] runs the node on a thread of its own with a single-threaded
//! tokio runtime, so nothing else in the process delays its timers. It logs
//! how late they fire.

use std::collections::VecDeque;
use std::future::Future;
use std::io;
use std::string::{String, ToString};
use std::time::{Duration, Instant};
use std::vec::Vec;

use tokio::sync::watch;

use crate::frame::Frame;
use crate::{
    Action, Config, Input, Ipv6Packet, MacAddr, Node, PeerCaps, PeerInfo, Time, UNICAST_QUEUE,
};

/// Unicast the radio couldn't take is kept for its peer's next slot, as
/// legacy kept it (patch `owl-07`: dropped, 8383 frames in one 34 MB receive
/// on an Intel card, among them the TCP ACKs the phone was waiting for, so
/// uploads died just short of the end), for at most this long: the node's
/// own expiry for packets waiting on a slot.
const UNICAST_HOLD_US: u64 = 2_000_000;
/// Multicast the radio couldn't take is tried again while its slot lasts,
/// for at most this long.
const RETRY_US: u64 = 100_000;
/// How soon to try again once the radio's queue was full.
const RETRY_PAUSE: Duration = Duration::from_micros(500);
/// Bounded non-blocking drain per wakeup so bursts amortize syscalls without starving timers.
const DRAIN_BATCH: usize = 32;
/// Maximum queued retry frames and wall-clock microseconds per flush_retries pass.
const FLUSH_BUDGET_FRAMES: usize = 64;
const FLUSH_BUDGET_US: u64 = 2_000;
/// Cap consecutive netif reads dropped on already-saturated peers when global queue is full.
const OVER_CAP_STALLED_BURST: usize = 32;
const STATS_EVERY: Duration = Duration::from_secs(10);

/// A frame heard on the air.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Received {
    /// From the 802.11 MAC header on, without FCS.
    pub bytes: Vec<u8>,
    /// dBm, when the radio reports it.
    pub rssi: Option<i8>,
}

/// Where the node's frames go out and the peers' come in: a radio that
/// injects and captures 802.11 frames, wrapped however it needs them
/// (radiotap, on a Linux monitor interface).
pub trait Air {
    /// The next frame heard. Dropping the future before it's done loses no
    /// frame, since the driver races it against its timers.
    fn recv(&mut self) -> impl Future<Output = io::Result<Received>>;

    /// Non-blocking pull used to drain queued frames before returning to timers.
    fn try_recv(&mut self) -> io::Result<Option<Received>> {
        Ok(None)
    }

    /// Hands a frame to the radio without waiting, at its rate and with or
    /// without an ACK as it says. Gives it back when the radio's queue is
    /// full, to be tried again; a frame the radio refuses for any other
    /// reason is the radio's to log, and dropped.
    fn send(&mut self, frame: Frame) -> Result<(), Frame>;
}

/// The host's end of the link: the network interface its IPv6 sockets use
/// (a TAP device on Linux, a Wintun adapter on Windows).
pub trait Netif {
    /// The next IPv6 packet the host sends on the link. Dropping the future
    /// loses no packet, as with [`Air::recv`].
    fn recv(&mut self) -> impl Future<Output = io::Result<Ipv6Packet>>;

    /// Non-blocking pull used to batch pending outbound packets without extra async wakes.
    fn try_recv(&mut self) -> io::Result<Option<Ipv6Packet>> {
        Ok(None)
    }

    /// Hands the host a packet from a peer. One it can't take now is
    /// dropped, as a full queue would drop it.
    fn deliver(&mut self, packet: Ipv6Packet);

    /// A peer turned valid, and may be reached from now on: an interface
    /// that keeps neighbour entries gets one for its link-local address
    /// (NET-2).
    fn peer_up(&mut self, _mac: MacAddr) {}

    /// Same as [`Netif::peer_up`], carrying decoded HT/VHT capabilities.
    fn peer_up_caps(&mut self, mac: MacAddr, _caps: PeerCaps) {
        self.peer_up(mac);
    }

    /// A valid peer updated its advertised HT/VHT capabilities.
    fn peer_caps(&mut self, _mac: MacAddr, _caps: PeerCaps) {}

    /// A peer went away.
    fn peer_down(&mut self, _mac: MacAddr) {}
}

/// A node running on its own thread.
pub struct Running {
    stop: watch::Sender<bool>,
    failed: watch::Receiver<Option<String>>,
    thread: Option<std::thread::JoinHandle<()>>,
}

/// Starts a node with `cfg` on a thread of its own, between the [`Air`] and
/// [`Netif`] that `open` makes, publishing its valid peers on `peers`.
/// `open` runs on that thread, inside its runtime, so it can register
/// descriptors with it; when it fails, so does this.
pub fn spawn<A, N, F>(
    cfg: Config,
    open: F,
    peers: watch::Sender<Vec<PeerInfo>>,
) -> io::Result<Running>
where
    A: Air + 'static,
    N: Netif + 'static,
    F: FnOnce() -> io::Result<(A, N)> + Send + 'static,
{
    let (stop, stopped) = watch::channel(false);
    let (failed_tx, failed) = watch::channel(None);
    let (opened_tx, opened) = std::sync::mpsc::sync_channel(1);
    let thread = std::thread::Builder::new()
        .name("owly-awdl".into())
        .spawn(move || {
            let rt = match tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
            {
                Ok(rt) => rt,
                Err(e) => {
                    let _ = opened_tx.send(Err(e));
                    return;
                }
            };
            let _entered = rt.enter();
            let (air, netif) = match open() {
                Ok(ends) => {
                    let _ = opened_tx.send(Ok(()));
                    ends
                }
                Err(e) => {
                    let _ = opened_tx.send(Err(e));
                    return;
                }
            };
            let result = rt.block_on(run(Driver {
                node: Node::new(cfg, Time(1)),
                epoch: Instant::now(),
                air,
                netif,
                peers,
                stopped,
                unicast: VecDeque::new(),
                multicast: VecDeque::new(),
                full: false,
                over_cap_stalled_reads: 0,
                overlap: None,
                tx: TxStats::default(),
                timer: Time(1),
                lateness: Lateness::default(),
            }));
            if let Err(why) = result {
                tracing::warn!("AWDL stopped: {why}");
                failed_tx.send_replace(Some(why));
            }
        })?;
    let running = Running {
        stop,
        failed,
        thread: Some(thread),
    };
    match opened.recv() {
        Ok(Ok(())) => Ok(running),
        Ok(Err(e)) => Err(e),
        Err(_) => Err(io::Error::other("the AWDL thread ended before it started")),
    }
}

impl Running {
    /// Why the node stopped by itself, once it has: the radio or the host's
    /// interface failed.
    pub fn failed(&self) -> watch::Receiver<Option<String>> {
        self.failed.clone()
    }

    /// Stops the node, and waits for its thread, which drops the [`Air`]
    /// and [`Netif`].
    pub fn stop(mut self) {
        let _ = self.stop.send(true);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

impl Drop for Running {
    fn drop(&mut self) {
        let _ = self.stop.send(true);
    }
}

/// How late the node's timers fire, in µs.
#[derive(Default)]
struct Lateness {
    count: u64,
    total: u64,
    max: u64,
}

/// What the radio refused.
#[derive(Default)]
struct TxStats {
    /// Frames it had no room for, each time.
    again: u64,
    /// Frames given up on: action frames at once, others when their peer
    /// went or their time ran out.
    dropped: u64,
}

struct Driver<A, N> {
    node: Node,
    epoch: Instant,
    air: A,
    netif: N,
    peers: watch::Sender<Vec<PeerInfo>>,
    stopped: watch::Receiver<bool>,
    /// Data frames the radio had no room for, with when the node sent them.
    unicast: VecDeque<(Time, Frame)>,
    multicast: VecDeque<(Time, Frame)>,
    /// The radio's queue was full at the last try.
    full: bool,
    /// Consecutive outbound reads absorbed for already-saturated peers while at global queue cap.
    over_cap_stalled_reads: usize,
    /// The no-overlap last logged.
    overlap: Option<(MacAddr, u8)>,
    tx: TxStats,
    timer: Time,
    lateness: Lateness,
}

impl<A: Air, N: Netif> Driver<A, N> {
    fn now(&self) -> Time {
        Time(self.epoch.elapsed().as_micros() as u64 + 1)
    }

    fn instant(&self, t: Time) -> tokio::time::Instant {
        tokio::time::Instant::from_std(self.epoch + Duration::from_micros(t.0.saturating_sub(1)))
    }

    fn handle(&mut self, input: Input) {
        match &input {
            Input::Timer(_) => {
                self.over_cap_stalled_reads = 0;
            }
            Input::Frame { rx_time, .. } => self.record_timer_lateness(*rx_time),
            Input::Outbound { now, packet } => {
                self.record_timer_lateness(*now);
                let cap = self.queue_cap();
                let total = self.node.backlog() + self.unicast.len();
                if total < cap {
                    self.over_cap_stalled_reads = 0;
                } else {
                    let valid_count = self.node.peers().filter(|p| p.valid).count().max(1);
                    let per_peer_headroom = (cap / valid_count).max(1);
                    let healthy = !packet.dst.is_multicast()
                        && self.node.peers().any(|p| p.valid && p.mac == packet.dst)
                        && self.peer_backlog(packet.dst) < per_peer_headroom;
                    if healthy {
                        self.over_cap_stalled_reads = 0;
                    } else {
                        self.over_cap_stalled_reads =
                            self.over_cap_stalled_reads.saturating_add(1);
                    }
                }
            }
        }
        let actions: Vec<Action> = self.node.handle(input).collect();
        for a in actions {
            match a {
                Action::Transmit { at, frame } => self.transmit(at, frame),
                Action::ArmTimer(t) => self.timer = t,
                Action::PeerUp(p) => {
                    tracing::info!(
                        "add peer {} ({}) caps: ht={} vht={} nss={} bw={} mcs={} sgi={} ldpc={}",
                        p.mac,
                        p.name,
                        p.caps.ht,
                        p.caps.vht,
                        p.caps.nss,
                        p.caps.bw,
                        p.caps.max_mcs,
                        p.caps.sgi,
                        p.caps.ldpc
                    );
                    self.netif.peer_up_caps(p.mac, p.caps);
                    self.publish_peers();
                }
                Action::PeerCaps(m, caps) => {
                    tracing::info!(
                        "update peer {m} caps: ht={} vht={} nss={} bw={} mcs={} sgi={} ldpc={}",
                        caps.ht,
                        caps.vht,
                        caps.nss,
                        caps.bw,
                        caps.max_mcs,
                        caps.sgi,
                        caps.ldpc
                    );
                    self.netif.peer_caps(m, caps);
                    self.publish_peers();
                }
                Action::PeerDown(m) => {
                    tracing::info!("remove peer {m}");
                    self.netif.peer_down(m);
                    self.publish_peers();
                }
                Action::Deliver(p) => self.netif.deliver(p),
            }
        }
        self.log_overlap();
    }

    /// Says when a shared radio's channel stops or starts overlapping the
    /// master's sequence, as legacy's `NO OVERLAP` line did: the phone then
    /// never addresses us, and nothing else explains the silence.
    fn log_overlap(&mut self) {
        let now = self.node.no_overlap();
        if now == self.overlap {
            return;
        }
        match now {
            Some((master, wants)) => tracing::info!(
                "NO OVERLAP with {master} on ch {} - peer wants ch {wants}",
                self.node.config().radio.channel.0
            ),
            None => tracing::info!("overlap with the master's sequence again"),
        }
        self.overlap = now;
    }

    fn transmit(&mut self, at: Time, frame: Frame) {
        if frame.is_action() {
            // Built for this moment, its sync TLVs and timestamp with it: by
            // the next try it would be stale, and the next PSF or MIF is
            // never far off. Legacy dropped them too.
            if self.air.send(frame).is_err() {
                self.full = true;
                self.tx.again += 1;
                self.tx.dropped += 1;
            }
            return;
        }
        let now = self.now();
        let Some(wait) = self.node.send_wait(frame.to, now) else {
            self.tx.dropped += 1;
            return;
        };
        let queue_nonempty = if frame.to.is_multicast() {
            !self.multicast.is_empty()
        } else {
            self.unicast.iter().any(|(_, queued)| queued.to == frame.to)
        };
        if queue_nonempty || self.full || wait > 0 {
            // In order behind those already waiting, or deferred when the slot closed mid-batch.
            let queue = if frame.to.is_multicast() {
                &mut self.multicast
            } else {
                &mut self.unicast
            };
            queue.push_back((at, frame));
            return;
        }
        if let Err(frame) = self.air.send(frame) {
            self.full = true;
            self.tx.again += 1;
            let queue = if frame.to.is_multicast() {
                &mut self.multicast
            } else {
                &mut self.unicast
            };
            queue.push_back((at, frame));
        }
    }

    /// Tries again what the radio had no room for, re-checking current time and
    /// slot status per frame within a bounded frame/time budget.
    fn flush_retries(&mut self) {
        let started = self.now();
        let mut sent = 0usize;
        self.full = false;
        while let Some((at, frame)) = self.multicast.pop_front() {
            let now = self.now();
            // Only while its slot lasts.
            if now.since(at) > RETRY_US || !self.node.may_send(frame.to, now) {
                self.tx.dropped += 1;
                continue;
            }
            if sent >= FLUSH_BUDGET_FRAMES || now.since(started) >= FLUSH_BUDGET_US {
                self.multicast.push_front((at, frame));
                break;
            }
            if let Err(frame) = self.air.send(frame) {
                self.multicast.push_front((at, frame));
                self.full = true;
                self.tx.again += 1;
                break;
            }
            sent += 1;
        }
        // Unicast waits for its peer's next slot, strictly FIFO per peer: once any frame for
        // a peer is retained, no later frame for the same peer may overtake it in this pass.
        let mut retained_peers: Vec<MacAddr> = Vec::new();
        let mut kept = VecDeque::new();
        while let Some((at, frame)) = self.unicast.pop_front() {
            let now = self.now();
            match self.node.send_wait(frame.to, now) {
                None => self.tx.dropped += 1,
                Some(_) if now.since(at) > UNICAST_HOLD_US => self.tx.dropped += 1,
                Some(wait) => {
                    let already_retained = retained_peers.contains(&frame.to);
                    if wait == 0 && !self.full && !already_retained {
                        if sent >= FLUSH_BUDGET_FRAMES || now.since(started) >= FLUSH_BUDGET_US {
                            retained_peers.push(frame.to);
                            kept.push_back((at, frame));
                            kept.extend(self.unicast.drain(..));
                            break;
                        }
                        if let Err(frame) = self.air.send(frame) {
                            self.full = true;
                            self.tx.again += 1;
                            retained_peers.push(frame.to);
                            kept.push_back((at, frame));
                        } else {
                            sent += 1;
                        }
                    } else {
                        if !already_retained {
                            retained_peers.push(frame.to);
                        }
                        kept.push_back((at, frame));
                    }
                }
            }
        }
        self.unicast = kept;
        if sent > 0 || self.node.backlog() + self.unicast.len() < self.queue_cap() {
            self.over_cap_stalled_reads = 0;
        }
    }

    /// When to try the frames waiting again, if any are.
    fn retry_at(&self) -> Option<tokio::time::Instant> {
        if self.full || !self.multicast.is_empty() {
            return Some(tokio::time::Instant::now() + RETRY_PAUSE);
        }
        let now = self.now();
        // A peer that's gone (no wait) has its frames dropped at once.
        let wait = self
            .unicast
            .iter()
            .map(|(_, f)| self.node.send_wait(f.to, now).unwrap_or(0))
            .min()?;
        Some(self.instant(now.plus(wait.max(1))))
    }

    fn queue_cap(&self) -> usize {
        self.node.config().unicast_queue.max(UNICAST_QUEUE)
    }

    fn peer_backlog(&self, mac: MacAddr) -> usize {
        self.node.peer_backlog(mac) + self.unicast.iter().filter(|(_, f)| f.to == mac).count()
    }

    /// Whether to read the host's next packet. Stops once the global queue is full
    /// when the radio itself is full or all valid peers are saturated; in multi-peer
    /// mode, keeps reading while at least one valid peer has headroom below its
    /// fair share so a single stalled peer cannot starve healthy peers.
    fn takes_outbound(&self) -> bool {
        let cap = self.queue_cap();
        let total = self.node.backlog() + self.unicast.len();
        if total < cap {
            return true;
        }
        if self.full || self.over_cap_stalled_reads >= OVER_CAP_STALLED_BURST {
            return false;
        }
        let valid_peers: Vec<MacAddr> =
            self.node.peers().filter(|p| p.valid).map(|p| p.mac).collect();
        if valid_peers.len() <= 1 {
            return false;
        }
        let per_peer_headroom = (cap / valid_peers.len()).max(1);
        valid_peers
            .into_iter()
            .any(|mac| self.peer_backlog(mac) < per_peer_headroom)
    }

    fn publish_peers(&self) {
        let peers: Vec<PeerInfo> = self
            .node
            .peers()
            .filter(|p| p.valid)
            .map(|p| p.info())
            .collect();
        self.peers.send_if_modified(|p| {
            let changed = *p != peers;
            if changed {
                p.clone_from(&peers);
            }
            changed
        });
    }

    fn log_stats(&mut self) {
        let s = self.node.stats();
        let l = &self.lateness;
        tracing::info!(
            "STATS tx_action {} tx_unicast {} tx_multicast {} tx_amsdu {} | radio full {} gave up {} | rx_action {} rx_data {} rx_malformed {} rx_dup {} delivered {} | addba req {} resp {} tx_req {} rx_resp {} | dropped no_peer {} queue {} expired {} | syn held {} released {} | no_overlap {} | timer late avg {} µs max {} µs",
            s.tx_action,
            s.tx_unicast,
            s.tx_multicast,
            s.tx_amsdu,
            self.tx.again,
            self.tx.dropped,
            s.rx_action,
            s.rx_data,
            s.rx_malformed,
            s.rx_dup_dropped,
            s.delivered,
            s.rx_addba_req,
            s.tx_addba_resp,
            s.tx_addba_req,
            s.rx_addba_resp,
            s.dropped_no_peer,
            s.dropped_queue_full,
            s.dropped_expired,
            s.syn_held,
            s.syn_released,
            s.no_overlap,
            l.total.checked_div(l.count).unwrap_or(0),
            l.max,
        );
        self.lateness = Lateness::default();
    }

    fn record_timer_lateness(&mut self, now: Time) {
        if now >= self.timer {
            let late = now.since(self.timer);
            let l = &mut self.lateness;
            l.count += 1;
            l.total += late;
            l.max = l.max.max(late);
        }
    }

    fn service_timer(&mut self) {
        let now = self.now();
        if now >= self.timer {
            self.record_timer_lateness(now);
            self.handle(Input::Timer(now));
        }
    }
}

async fn run<A: Air, N: Netif>(mut d: Driver<A, N>) -> Result<(), String> {
    let mut stats = tokio::time::interval(STATS_EVERY);
    let mut peers = tokio::time::interval(Duration::from_secs(1));
    let now = d.now();
    d.handle(Input::Timer(now));
    loop {
        let wake = match d.retry_at() {
            Some(retry) => d.instant(d.timer).min(retry),
            None => d.instant(d.timer),
        };
        tokio::select! {
            _ = d.stopped.changed() => {
                d.log_stats();
                return Ok(());
            }
            r = d.air.recv() => match r {
                Ok(rx) => {
                    let now = d.now();
                    d.handle(Input::Frame { rx_time: now, rssi: rx.rssi, bytes: &rx.bytes });
                    for _ in 1..DRAIN_BATCH {
                        match d.air.try_recv() {
                            Ok(Some(rx)) => {
                                let now = d.now();
                                d.handle(Input::Frame { rx_time: now, rssi: rx.rssi, bytes: &rx.bytes });
                            }
                            Ok(None) => break,
                            Err(e) => return Err(e.to_string()),
                        }
                    }
                }
                Err(e) => return Err(e.to_string()),
            },
            r = d.netif.recv(), if d.takes_outbound() => match r {
                Ok(packet) => {
                    let now = d.now();
                    d.handle(Input::Outbound { now, packet });
                    for _ in 1..DRAIN_BATCH {
                        if !d.takes_outbound() {
                            break;
                        }
                        match d.netif.try_recv() {
                            Ok(Some(packet)) => {
                                let now = d.now();
                                d.handle(Input::Outbound { now, packet });
                            }
                            Ok(None) => break,
                            Err(e) => return Err(e.to_string()),
                        }
                    }
                }
                Err(e) => return Err(e.to_string()),
            },
            _ = tokio::time::sleep_until(wake) => {}
            _ = stats.tick() => d.log_stats(),
            _ = peers.tick() => d.publish_peers(),
        }
        d.service_timer();
        d.flush_retries();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Channel, Presence, RadioConstraints, Sequence, TxRate, channel, election::Election, frame, tlv};

    struct ScriptedAir {
        epoch: Instant,
        times: Vec<Time>,
        slow_first_ms: u64,
    }

    impl Air for ScriptedAir {
        async fn recv(&mut self) -> io::Result<Received> {
            std::future::pending().await
        }

        fn send(&mut self, _: Frame) -> Result<(), Frame> {
            let t = Time(self.epoch.elapsed().as_micros() as u64 + 1);
            self.times.push(t);
            if self.times.len() == 1 && self.slow_first_ms > 0 {
                std::thread::sleep(Duration::from_millis(self.slow_first_ms));
            }
            Ok(())
        }
    }

    struct NullNet;

    impl Netif for NullNet {
        async fn recv(&mut self) -> io::Result<Ipv6Packet> {
            std::future::pending().await
        }
        fn deliver(&mut self, _: Ipv6Packet) {}
    }

    #[test]
    fn times_map_onto_instants() {
        let t = Time(1_000_001);
        let epoch = Instant::now();
        let at = epoch + Duration::from_micros(t.0 - 1);
        assert_eq!(at.duration_since(epoch), Duration::from_secs(1));
    }

    #[test]
    fn flush_retries_defers_remaining_when_window_closes_mid_batch() {
        let us = MacAddr([2, 0, 0, 0, 0, 1]);
        let phone = MacAddr([2, 0, 0, 0, 0, 2]);
        let mut cfg = Config::new(
            us,
            "test",
            RadioConstraints {
                channel: Channel(149),
                presence: Presence::Fixed,
            },
        );
        cfg.neighbour_echo = false;
        let mut node = Node::new(cfg, Time(1));
        let mut seq = Sequence::empty(channel::Encoding::OpClass);
        for (i, s) in seq.slots.iter_mut().enumerate() {
            *s = Channel(if i % 2 == 0 { 149 } else { 6 }).slot();
        }
        let election = Election::own(phone);
        let own = tlv::Own {
            addr: phone,
            name: "peer",
            devclass: tlv::devclass::IOS,
            channel: 149,
            master_channel: Channel(149),
            next_tu: 64,
            aw_counter: 0,
            psf_interval_tu: 110,
            sequence: &seq,
            election: &election,
            ht: Some(tlv::HtCapabilities::ONE_STREAM),
            advertise_vht80_1ss: false,
        };
        let bytes = frame::action_frame(phone, 1, frame::subtype::MIF, 0, |out| {
            tlv::put_all(out, &own, true)
        });
        node.handle(Input::Frame {
            rx_time: Time(1),
            rssi: Some(-40),
            bytes: &bytes,
        })
        .for_each(drop);

        let epoch = Instant::now() - Duration::from_micros(19_999);
        let (_, stopped) = watch::channel(false);
        let (peers, _) = watch::channel(Vec::new());
        let mut d = Driver {
            node,
            epoch,
            air: ScriptedAir {
                epoch,
                times: Vec::new(),
                slow_first_ms: 60,
            },
            netif: NullNet,
            peers,
            stopped,
            unicast: VecDeque::new(),
            multicast: VecDeque::new(),
            full: false,
            over_cap_stalled_reads: 0,
            overlap: None,
            tx: TxStats::default(),
            timer: Time(1),
            lateness: Lateness::default(),
        };
        assert_eq!(d.node.send_wait(phone, d.now()), Some(0));
        for _ in 0..2 {
            d.unicast.push_back((
                d.now(),
                Frame {
                    bytes: Vec::from([0u8; 60]),
                    to: phone,
                    rate: TxRate::BASIC,
                    no_ack: false,
                },
            ));
        }
        d.flush_retries();
        assert_eq!(d.air.times.len(), 1);
        assert_eq!(d.unicast.len(), 1);
    }

    #[test]
    fn flush_retries_respects_frame_budget() {
        let us = MacAddr([2, 0, 0, 0, 0, 1]);
        let phone = MacAddr([2, 0, 0, 0, 0, 2]);
        let mut cfg = Config::new(
            us,
            "test",
            RadioConstraints {
                channel: Channel(149),
                presence: Presence::Fixed,
            },
        );
        cfg.neighbour_echo = false;
        let mut node = Node::new(cfg, Time(1));
        let seq = Sequence::fixed(Channel(149));
        let election = Election::own(phone);
        let own = tlv::Own {
            addr: phone,
            name: "peer",
            devclass: tlv::devclass::IOS,
            channel: 149,
            master_channel: Channel(149),
            next_tu: 64,
            aw_counter: 0,
            psf_interval_tu: 110,
            sequence: &seq,
            election: &election,
            ht: Some(tlv::HtCapabilities::ONE_STREAM),
            advertise_vht80_1ss: false,
        };
        let bytes = frame::action_frame(phone, 1, frame::subtype::MIF, 0, |out| {
            tlv::put_all(out, &own, true)
        });
        node.handle(Input::Frame {
            rx_time: Time(1),
            rssi: Some(-40),
            bytes: &bytes,
        })
        .for_each(drop);

        let epoch = Instant::now() - Duration::from_micros(19_999);
        let (_, stopped) = watch::channel(false);
        let (peers, _) = watch::channel(Vec::new());
        let mut d = Driver {
            node,
            epoch,
            air: ScriptedAir {
                epoch,
                times: Vec::new(),
                slow_first_ms: 0,
            },
            netif: NullNet,
            peers,
            stopped,
            unicast: VecDeque::new(),
            multicast: VecDeque::new(),
            full: false,
            over_cap_stalled_reads: 0,
            overlap: None,
            tx: TxStats::default(),
            timer: Time(1),
            lateness: Lateness::default(),
        };
        for _ in 0..(FLUSH_BUDGET_FRAMES + 10) {
            d.unicast.push_back((
                d.now(),
                Frame {
                    bytes: Vec::from([0u8; 60]),
                    to: phone,
                    rate: TxRate::BASIC,
                    no_ack: false,
                },
            ));
        }
        d.flush_retries();
        assert_eq!(d.air.times.len(), FLUSH_BUDGET_FRAMES);
        assert_eq!(d.unicast.len(), 10);
    }

    #[test]
    fn frame_servicing_overdue_timer_records_lateness() {
        let cfg = Config::new(
            MacAddr([2, 0, 0, 0, 0, 1]),
            "test",
            RadioConstraints {
                channel: Channel(149),
                presence: Presence::Fixed,
            },
        );
        let epoch = Instant::now() - Duration::from_micros(19_999);
        let (_, stopped) = watch::channel(false);
        let (peers, _) = watch::channel(Vec::new());
        let mut d = Driver {
            node: Node::new(cfg, Time(1)),
            epoch,
            air: ScriptedAir {
                epoch,
                times: Vec::new(),
                slow_first_ms: 0,
            },
            netif: NullNet,
            peers,
            stopped,
            unicast: VecDeque::new(),
            multicast: VecDeque::new(),
            full: false,
            over_cap_stalled_reads: 0,
            overlap: None,
            tx: TxStats::default(),
            timer: Time(1),
            lateness: Lateness::default(),
        };
        assert!(d.now().since(d.timer) >= 19_999);
        d.handle(Input::Frame {
            rx_time: d.now(),
            rssi: None,
            bytes: &[],
        });
        d.service_timer();
        assert_eq!(d.lateness.count, 1);
        assert!(d.lateness.max >= 19_999);
    }

    struct CaptureAir {
        epoch: Instant,
        sent: Vec<Vec<u8>>,
    }

    impl Air for CaptureAir {
        async fn recv(&mut self) -> io::Result<Received> {
            std::future::pending().await
        }

        fn send(&mut self, frame: Frame) -> Result<(), Frame> {
            self.sent.push(frame.bytes);
            Ok(())
        }
    }

    #[test]
    fn review_retry_preserves_order_when_peer_window_opens_during_flush() {
        let us = MacAddr([2, 0, 0, 0, 0, 1]);
        let stalled_peer = MacAddr([2, 0, 0, 0, 0, 2]);
        let mut cfg = Config::new(
            us,
            "test",
            RadioConstraints {
                channel: Channel(149),
                presence: Presence::Fixed,
            },
        );
        cfg.neighbour_echo = false;
        let mut node = Node::new(cfg, Time(1));

        // Alternate channels 149 (even) / 6 (odd): slot 0 ends around 65_536 us,
        // slot 1 (ch 6) is active from ~65_536..131_072 us, slot 2 (ch 149) opens ~131_072 us.
        let mut seq = Sequence::empty(channel::Encoding::OpClass);
        for (i, s) in seq.slots.iter_mut().enumerate() {
            *s = Channel(if i % 2 == 0 { 149 } else { 6 }).slot();
        }
        let election = Election::own(stalled_peer);
        let own = tlv::Own {
            addr: stalled_peer,
            name: "peer",
            devclass: tlv::devclass::IOS,
            channel: 149,
            master_channel: Channel(149),
            next_tu: 64,
            aw_counter: 0,
            psf_interval_tu: 110,
            sequence: &seq,
            election: &election,
            ht: Some(tlv::HtCapabilities::ONE_STREAM),
            advertise_vht80_1ss: false,
        };
        let bytes = frame::action_frame(stalled_peer, 1, frame::subtype::MIF, 0, |out| {
            tlv::put_all(out, &own, true)
        });
        node.handle(Input::Frame {
            rx_time: Time(1),
            rssi: Some(-40),
            bytes: &bytes,
        })
        .for_each(drop);

        // Start right before a channel-149 slot boundary (~131_072 us minus 12 ms -> ~119 ms,
        // wait: set epoch so first check is near end of slot 1 and ScriptedAir wait crosses into slot 2).
        // Slot 1 ends at 2 * 64 * 1024 = 131_072 us. Start at 130_000 us so first peer's check is wait>0
        // and by moving a healthy peer ahead or advancing time ~2ms, slot 2 opens mid-flush!
        let healthy_peer = MacAddr([2, 0, 0, 0, 0, 3]);
        let seq_fixed = Sequence::fixed(Channel(149));
        let election_h = Election::own(healthy_peer);
        let own_h = tlv::Own {
            addr: healthy_peer,
            name: "healthy",
            devclass: tlv::devclass::IOS,
            channel: 149,
            master_channel: Channel(149),
            next_tu: 64,
            aw_counter: 0,
            psf_interval_tu: 110,
            sequence: &seq_fixed,
            election: &election_h,
            ht: Some(tlv::HtCapabilities::ONE_STREAM),
            advertise_vht80_1ss: false,
        };
        let bytes_h = frame::action_frame(healthy_peer, 1, frame::subtype::MIF, 0, |out| {
            tlv::put_all(out, &own_h, true)
        });
        node.handle(Input::Frame {
            rx_time: Time(1),
            rssi: Some(-40),
            bytes: &bytes_h,
        })
        .for_each(drop);

        struct DelayHealthyAir {
            epoch: Instant,
            sent: Vec<Vec<u8>>,
            slept: bool,
        }
        impl Air for DelayHealthyAir {
            async fn recv(&mut self) -> io::Result<Received> {
                std::future::pending().await
            }
            fn send(&mut self, frame: Frame) -> Result<(), Frame> {
                let tag = frame.bytes.clone();
                if tag == b"healthy-delay" && !self.slept {
                    self.slept = true;
                    std::thread::sleep(Duration::from_millis(8));
                }
                self.sent.push(tag);
                let _ = self.epoch;
                Ok(())
            }
        }

        // Start 2 ms before slot 2 (131_072 us): at t=129_000 us, stalled_peer waits >0,
        // then healthy_peer sends and sleeps 8ms past 131_072 us, opening stalled_peer's slot!
        let epoch = Instant::now() - Duration::from_micros(129_000);
        let (_, stopped) = watch::channel(false);
        let (peers, _) = watch::channel(Vec::new());
        let mut d = Driver {
            node,
            epoch,
            air: DelayHealthyAir {
                epoch,
                sent: Vec::new(),
                slept: false,
            },
            netif: NullNet,
            peers,
            stopped,
            unicast: VecDeque::new(),
            multicast: VecDeque::new(),
            full: false,
            over_cap_stalled_reads: 0,
            overlap: None,
            tx: TxStats::default(),
            timer: Time(1),
            lateness: Lateness::default(),
        };
        let now = d.now();
        assert_ne!(d.node.send_wait(stalled_peer, now), Some(0));
        d.unicast.push_back((
            now,
            Frame {
                bytes: b"stalled-1".to_vec(),
                to: stalled_peer,
                rate: TxRate::BASIC,
                no_ack: false,
            },
        ));
        d.unicast.push_back((
            now,
            Frame {
                bytes: b"healthy-delay".to_vec(),
                to: healthy_peer,
                rate: TxRate::BASIC,
                no_ack: false,
            },
        ));
        d.unicast.push_back((
            now,
            Frame {
                bytes: b"stalled-2".to_vec(),
                to: stalled_peer,
                rate: TxRate::BASIC,
                no_ack: false,
            },
        ));
        d.flush_retries();

        // stalled-2 MUST NOT overtake stalled-1 even though slot opened during healthy-delay!
        assert_eq!(d.air.sent, std::vec![b"healthy-delay".to_vec()]);
        let remaining: Vec<Vec<u8>> = d.unicast.iter().map(|(_, f)| f.bytes.clone()).collect();
        assert_eq!(
            remaining,
            std::vec![b"stalled-1".to_vec(), b"stalled-2".to_vec()]
        );

        // Next flush drains stalled-1 before stalled-2 in strict FIFO order.
        d.flush_retries();
        assert_eq!(
            d.air.sent,
            std::vec![
                b"healthy-delay".to_vec(),
                b"stalled-1".to_vec(),
                b"stalled-2".to_vec(),
            ]
        );
        assert!(d.unicast.is_empty());
    }

    #[test]
    fn review_driver_admits_healthy_peer_when_other_peer_queue_is_full() {
        let us = MacAddr([2, 0, 0, 0, 0, 1]);
        let stalled_peer = MacAddr([2, 0, 0, 0, 0, 2]);
        let healthy_peer = MacAddr([2, 0, 0, 0, 0, 3]);
        let mut cfg = Config::new(
            us,
            "test",
            RadioConstraints {
                channel: Channel(149),
                presence: Presence::Fixed,
            },
        );
        cfg.neighbour_echo = false;
        let mut node = Node::new(cfg, Time(1));

        // Stalled peer occupies ch 6 (no overlap with ch 149); healthy peer fixed on ch 149.
        let mut seq_s = Sequence::fixed(Channel(6));
        seq_s.slots[0] = Channel(149).slot();
        let el_s = Election::own(stalled_peer);
        let own_s = tlv::Own {
            addr: stalled_peer,
            name: "stalled",
            devclass: tlv::devclass::IOS,
            channel: 6,
            master_channel: Channel(6),
            next_tu: 64,
            aw_counter: 0,
            psf_interval_tu: 110,
            sequence: &seq_s,
            election: &el_s,
            ht: Some(tlv::HtCapabilities::ONE_STREAM),
            advertise_vht80_1ss: false,
        };
        let bytes_s = frame::action_frame(stalled_peer, 1, frame::subtype::MIF, 0, |out| {
            tlv::put_all(out, &own_s, true)
        });
        node.handle(Input::Frame {
            rx_time: Time(1),
            rssi: Some(-40),
            bytes: &bytes_s,
        })
        .for_each(drop);

        let seq_h = Sequence::fixed(Channel(149));
        let el_h = Election::own(healthy_peer);
        let own_h = tlv::Own {
            addr: healthy_peer,
            name: "healthy",
            devclass: tlv::devclass::IOS,
            channel: 149,
            master_channel: Channel(149),
            next_tu: 64,
            aw_counter: 0,
            psf_interval_tu: 110,
            sequence: &seq_h,
            election: &el_h,
            ht: Some(tlv::HtCapabilities::ONE_STREAM),
            advertise_vht80_1ss: false,
        };
        let bytes_h = frame::action_frame(healthy_peer, 1, frame::subtype::MIF, 0, |out| {
            tlv::put_all(out, &own_h, true)
        });
        node.handle(Input::Frame {
            rx_time: Time(1),
            rssi: Some(-40),
            bytes: &bytes_h,
        })
        .for_each(drop);

        let epoch = Instant::now() - Duration::from_micros(100_000);
        let (_, stopped) = watch::channel(false);
        let (peers, _) = watch::channel(Vec::new());
        let mut d = Driver {
            node,
            epoch,
            air: CaptureAir {
                epoch,
                sent: Vec::new(),
            },
            netif: NullNet,
            peers,
            stopped,
            unicast: VecDeque::new(),
            multicast: VecDeque::new(),
            full: false,
            over_cap_stalled_reads: 0,
            overlap: None,
            tx: TxStats::default(),
            timer: Time(1),
            lateness: Lateness::default(),
        };
        // Fill shared queue entirely with stalled_peer packets at slot-closed time (t ~ 100ms, ch 6).
        let t_closed = Time(100_000);
        assert_ne!(d.node.send_wait(stalled_peer, t_closed), Some(0));
        for seq in 0..UNICAST_QUEUE {
            let p = Ipv6Packet::echo_request(us, stalled_peer, 1, seq as u16, &[seq as u8; 16]);
            d.handle(Input::Outbound {
                now: t_closed,
                packet: p,
            });
        }
        assert_eq!(d.node.backlog(), UNICAST_QUEUE);
        // Healthy peer still has 0 backlog -> takes_outbound() MUST stay true!
        assert!(d.takes_outbound());

        // Sending to healthy_peer succeeds immediately despite stalled_peer having filled the queue.
        let before = d.air.sent.len();
        d.handle(Input::Outbound {
            now: t_closed,
            packet: Ipv6Packet::echo_request(us, healthy_peer, 2, 1, b"hello-healthy"),
        });
        assert_eq!(d.air.sent.len(), before + 1);
        // Healthy frame drained out of node, leaving stalled_peer at UNICAST_QUEUE - 1.
        assert_eq!(d.node.backlog(), UNICAST_QUEUE - 1);
        d.handle(Input::Outbound {
            now: t_closed,
            packet: Ipv6Packet::echo_request(us, stalled_peer, 1, 199, &[0; 8]),
        });
        assert_eq!(d.node.backlog(), UNICAST_QUEUE);

        // When only stalled_peer is flooded past cap, over_cap_stalled_reads bounds wasted drops.
        for i in 0..OVER_CAP_STALLED_BURST {
            assert!(d.takes_outbound());
            d.handle(Input::Outbound {
                now: t_closed,
                packet: Ipv6Packet::echo_request(us, stalled_peer, 1, (200 + i) as u16, &[i as u8; 8]),
            });
        }
        assert!(!d.takes_outbound());

        // When radio itself is full (self.full == true) at global cap, admission stops regardless.
        d.handle(Input::Timer(t_closed));
        assert!(d.takes_outbound());
        for i in d.node.backlog()..UNICAST_QUEUE {
            d.handle(Input::Outbound {
                now: t_closed,
                packet: Ipv6Packet::echo_request(us, stalled_peer, 1, (400 + i) as u16, &[1; 8]),
            });
        }
        d.full = true;
        assert!(!d.takes_outbound());
        let _ = d.air.epoch;
    }
}
