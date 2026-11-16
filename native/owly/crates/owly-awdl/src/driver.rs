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
    ActionRef, Config, Input, Ipv6Packet, MacAddr, Node, PeerCaps, PeerInfo, Time, UNICAST_QUEUE,
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
const DRAIN_BATCH: usize = 64;
const AIR_DRAIN_BATCH: usize = 256;
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

/// Result of awaiting [`Air::wait_or_recv`].
pub enum AirRx {
    /// Legacy transports deliver an owned frame and drain more via `try_recv`.
    Frame(Received),
    /// Zero-copy transports signal that frames are ready to process in-place via `drain_rx`.
    Ready,
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

    /// Awaits incoming air readiness (zero-copy transports return [`AirRx::Ready`] and
    /// dispatch slices synchronously in [`Air::drain_rx`]; default falls back to [`Air::recv`]).
    fn wait_or_recv(&mut self) -> impl Future<Output = io::Result<AirRx>> {
        async move { Ok(AirRx::Frame(self.recv().await?)) }
    }

    /// Synchronous in-place drain of up to `limit` available RX frames as borrowed 802.11 slices.
    fn drain_rx<F>(&mut self, _limit: usize, _on_frame: F) -> io::Result<()>
    where
        F: FnMut(Option<i8>, &[u8]),
    {
        Ok(())
    }

    /// Hands a frame to the radio without waiting, at its rate and with or
    /// without an ACK as it says. Gives it back when the radio's queue is
    /// full, to be tried again; a frame the radio refuses for any other
    /// reason is the radio's to log, and dropped.
    fn send(&mut self, frame: Frame) -> Result<(), Frame>;

    /// Whether this radio supports coalesced batch transmission via `send_batch`.
    fn supports_batch(&self) -> bool {
        false
    }

    /// Sends frames from the front of `frames` until empty or full, returning `(sent, queue_full)`.
    /// Accepted frames' `bytes` buffers may be returned into `recycled` for reuse.
    fn send_batch(
        &mut self,
        frames: &mut VecDeque<(Time, Frame)>,
        _recycled: &mut Vec<Vec<u8>>,
    ) -> (usize, bool) {
        let mut sent = 0;
        while let Some((at, frame)) = frames.pop_front() {
            match self.send(frame) {
                Ok(()) => sent += 1,
                Err(rejected) => {
                    frames.push_front((at, rejected));
                    return (sent, true);
                }
            }
        }
        (sent, false)
    }
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

    /// Zero-copy packet delivery borrowing raw IPv6 bytes directly from an RX frame buffer.
    fn deliver_slice(&mut self, dst: MacAddr, src: MacAddr, data: &[u8]) {
        self.deliver(Ipv6Packet {
            dst,
            src,
            data: data.to_vec(),
        });
    }

    /// Accepts cleared TX `Ipv6Packet.data` buffers back into the host interface's reuse pool.
    fn recycle_packet_buf(&mut self, _buf: Vec<u8>) {}

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
                pending_tx: VecDeque::new(),
                recycled_frames: Vec::new(),
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
    pending_tx: VecDeque<(Time, Frame)>,
    recycled_frames: Vec<Vec<u8>>,
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

/// Disjoint mutable borrow over everything in [`Driver`] except `air`,
/// letting `air.drain_rx` drive zero-copy `Node::handle_with` and `Netif::deliver_slice`.
struct DriverStep<'a, N> {
    node: &'a mut Node,
    epoch: &'a Instant,
    netif: &'a mut N,
    peers: &'a watch::Sender<Vec<PeerInfo>>,
    unicast: &'a VecDeque<(Time, Frame)>,
    pending_tx: &'a mut VecDeque<(Time, Frame)>,
    over_cap_stalled_reads: &'a mut usize,
    overlap: &'a mut Option<(MacAddr, u8)>,
    timer: &'a mut Time,
    lateness: &'a mut Lateness,
}

fn publish_peers_from(node: &Node, peers: &watch::Sender<Vec<PeerInfo>>) {
    let valid: Vec<PeerInfo> = node.peers().filter(|p| p.valid).map(|p| p.info()).collect();
    peers.send_if_modified(|p| {
        let changed = *p != valid;
        if changed {
            p.clone_from(&valid);
        }
        changed
    });
}

fn record_timer_lateness_at(lateness: &mut Lateness, timer: Time, now: Time) {
    if now >= timer {
        let late = now.since(timer);
        lateness.count += 1;
        lateness.total += late;
        lateness.max = lateness.max.max(late);
    }
}

impl<N: Netif> DriverStep<'_, N> {
    fn now(&self) -> Time {
        Time(self.epoch.elapsed().as_micros() as u64 + 1)
    }

    fn queue_cap(&self) -> usize {
        self.node.config().unicast_queue.max(UNICAST_QUEUE)
    }

    fn peer_backlog(&self, mac: MacAddr) -> usize {
        self.node.peer_backlog(mac) + self.unicast.iter().filter(|(_, f)| f.to == mac).count()
    }

    fn process_input(&mut self, input: Input<'_>) {
        // Any input arriving at or past `self.timer` runs `Node::service` and advances `self.timer`,
        // bypassing `service_timer()`'s `Input::Timer` branch; reset the stalled-read budget here too.
        let timer_due = match &input {
            Input::Timer(_) => true,
            Input::Frame { rx_time, .. } => *rx_time >= *self.timer,
            Input::Outbound { now, .. } => *now >= *self.timer,
        };
        if timer_due {
            *self.over_cap_stalled_reads = 0;
        }
        match &input {
            Input::Timer(_) => {}
            Input::Frame { rx_time, .. } => {
                record_timer_lateness_at(self.lateness, *self.timer, *rx_time);
            }
            Input::Outbound { now, packet } => {
                record_timer_lateness_at(self.lateness, *self.timer, *now);
                let cap = self.queue_cap();
                let total = self.node.backlog() + self.unicast.len();
                if total < cap {
                    *self.over_cap_stalled_reads = 0;
                } else {
                    let valid_count = self.node.peers().filter(|p| p.valid).count().max(1);
                    let per_peer_headroom = (cap / valid_count).max(1);
                    let healthy = !packet.dst.is_multicast()
                        && self.node.peers().any(|p| p.valid && p.mac == packet.dst)
                        && self.peer_backlog(packet.dst) < per_peer_headroom;
                    if healthy {
                        *self.over_cap_stalled_reads = 0;
                    } else {
                        *self.over_cap_stalled_reads =
                            self.over_cap_stalled_reads.saturating_add(1);
                    }
                }
            }
        }
        let mut peer_list_dirty = false;
        self.node.handle_with(input, |a| match a {
            ActionRef::Transmit { at, frame } => self.pending_tx.push_back((at, frame)),
            ActionRef::ArmTimer(t) => *self.timer = t,
            ActionRef::PeerUp(p) => {
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
                peer_list_dirty = true;
            }
            ActionRef::PeerCaps(m, caps) => {
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
                peer_list_dirty = true;
            }
            ActionRef::PeerDown(m) => {
                tracing::info!("remove peer {m}");
                self.netif.peer_down(m);
                peer_list_dirty = true;
            }
            ActionRef::DeliverSlice { dst, src, data } => {
                self.netif.deliver_slice(dst, src, data);
            }
            ActionRef::Deliver(p) => self.netif.deliver(p),
        });
        if peer_list_dirty {
            publish_peers_from(self.node, self.peers);
        }
        self.log_overlap();
        self.node
            .drain_recycled_packets(|buf| self.netif.recycle_packet_buf(buf));
    }

    /// Says when a shared radio's channel stops or starts overlapping the
    /// master's sequence, as legacy's `NO OVERLAP` line did: the phone then
    /// never addresses us, and nothing else explains the silence.
    fn log_overlap(&mut self) {
        let now = self.node.no_overlap();
        if now == *self.overlap {
            return;
        }
        match now {
            Some((master, wants)) => tracing::info!(
                "NO OVERLAP with {master} on ch {} - peer wants ch {wants}",
                self.node.config().radio.channel.0
            ),
            None => tracing::info!("overlap with the master's sequence again"),
        }
        *self.overlap = now;
    }
}

impl<A: Air, N: Netif> Driver<A, N> {
    fn now(&self) -> Time {
        Time(self.epoch.elapsed().as_micros() as u64 + 1)
    }

    fn instant(&self, t: Time) -> tokio::time::Instant {
        tokio::time::Instant::from_std(self.epoch + Duration::from_micros(t.0.saturating_sub(1)))
    }

    fn step(&mut self) -> DriverStep<'_, N> {
        DriverStep {
            node: &mut self.node,
            epoch: &self.epoch,
            netif: &mut self.netif,
            peers: &self.peers,
            unicast: &self.unicast,
            pending_tx: &mut self.pending_tx,
            over_cap_stalled_reads: &mut self.over_cap_stalled_reads,
            overlap: &mut self.overlap,
            timer: &mut self.timer,
            lateness: &mut self.lateness,
        }
    }

    fn handle(&mut self, input: Input<'_>) {
        self.step().process_input(input);
        self.flush_pending_tx();
    }

    fn drain_recycled_frame_bufs(&mut self) {
        for buf in self.recycled_frames.drain(..) {
            self.node.recycle_frame_buf(buf);
        }
    }

    fn send_ready_batch(&mut self, ready: &mut VecDeque<(Time, Frame)>) -> usize {
        if ready.is_empty() {
            return 0;
        }
        let (sent, full) = self.air.send_batch(ready, &mut self.recycled_frames);
        self.drain_recycled_frame_bufs();
        if full {
            self.full = true;
            self.tx.again += 1;
            while let Some((at, frame)) = ready.pop_front() {
                if frame.is_action() {
                    self.tx.dropped += 1;
                    self.node.recycle_frame_buf(frame.bytes);
                } else {
                    let queue = if frame.to.is_multicast() {
                        &mut self.multicast
                    } else {
                        &mut self.unicast
                    };
                    queue.push_back((at, frame));
                }
            }
        }
        sent
    }

    fn flush_pending_tx(&mut self) {
        let mut ready = VecDeque::new();
        while let Some((at, frame)) = self.pending_tx.pop_front() {
            if frame.is_action() {
                ready.push_back((at, frame));
                continue;
            }
            let now = self.now();
            let Some(wait) = self.node.send_wait(frame.to, now) else {
                self.tx.dropped += 1;
                self.node.recycle_frame_buf(frame.bytes);
                continue;
            };
            let queue_nonempty = if frame.to.is_multicast() {
                !self.multicast.is_empty()
            } else {
                self.unicast.iter().any(|(_, queued)| queued.to == frame.to)
            };
            if queue_nonempty || self.full || wait > 0 {
                // Keep any in-flight ready batch for the same destination strictly ahead
                // before appending to the deferred queue so per-peer FIFO never inverts.
                if ready.iter().any(|(_, queued)| queued.to == frame.to) {
                    self.send_ready_batch(&mut ready);
                }
                let queue = if frame.to.is_multicast() {
                    &mut self.multicast
                } else {
                    &mut self.unicast
                };
                queue.push_back((at, frame));
            } else {
                ready.push_back((at, frame));
            }
        }
        self.send_ready_batch(&mut ready);
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
                self.node.recycle_frame_buf(frame.bytes);
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
        if self.air.supports_batch() {
            let mut ready = VecDeque::new();
            while let Some((at, frame)) = self.unicast.pop_front() {
                let now = self.now();
                match self.node.send_wait(frame.to, now) {
                    None => {
                        self.tx.dropped += 1;
                        self.node.recycle_frame_buf(frame.bytes);
                    }
                    Some(_) if now.since(at) > UNICAST_HOLD_US => {
                        self.tx.dropped += 1;
                        self.node.recycle_frame_buf(frame.bytes);
                    }
                    Some(wait) => {
                        let already_retained = retained_peers.contains(&frame.to);
                        if wait == 0 && !self.full && !already_retained {
                            if sent + ready.len() >= FLUSH_BUDGET_FRAMES
                                || now.since(started) >= FLUSH_BUDGET_US
                            {
                                retained_peers.push(frame.to);
                                kept.push_back((at, frame));
                                kept.extend(self.unicast.drain(..));
                                break;
                            }
                            ready.push_back((at, frame));
                        } else {
                            if !already_retained {
                                retained_peers.push(frame.to);
                            }
                            kept.push_back((at, frame));
                        }
                    }
                }
            }
            if !ready.is_empty() {
                let (batch_sent, full) = self.air.send_batch(&mut ready, &mut self.recycled_frames);
                self.drain_recycled_frame_bufs();
                sent += batch_sent;
                if full {
                    self.full = true;
                    self.tx.again += 1;
                    kept.extend(ready);
                }
            }
        } else {
            while let Some((at, frame)) = self.unicast.pop_front() {
                let now = self.now();
                match self.node.send_wait(frame.to, now) {
                    None => self.tx.dropped += 1,
                    Some(_) if now.since(at) > UNICAST_HOLD_US => self.tx.dropped += 1,
                    Some(wait) => {
                        let already_retained = retained_peers.contains(&frame.to);
                        if wait == 0 && !self.full && !already_retained {
                            if sent >= FLUSH_BUDGET_FRAMES || now.since(started) >= FLUSH_BUDGET_US
                            {
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
        let valid_count = self.node.peers().filter(|p| p.valid).count();
        if valid_count <= 1 {
            return false;
        }
        let per_peer_headroom = (cap / valid_count).max(1);
        self.node
            .peers()
            .filter(|p| p.valid)
            .any(|p| self.peer_backlog(p.mac) < per_peer_headroom)
    }

    fn publish_peers(&self) {
        publish_peers_from(&self.node, &self.peers);
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

    fn service_timer(&mut self) {
        let now = self.now();
        if now >= self.timer {
            record_timer_lateness_at(&mut self.lateness, self.timer, now);
            self.handle(Input::Timer(now));
        }
    }
    fn drain_air_rx(&mut self, first: AirRx) -> Result<(), String> {
        match first {
            AirRx::Frame(rx) => {
                let now = self.now();
                self.step().process_input(Input::Frame {
                    rx_time: now,
                    rssi: rx.rssi,
                    bytes: &rx.bytes,
                });
                for _ in 1..AIR_DRAIN_BATCH {
                    match self.air.try_recv() {
                        Ok(Some(rx)) => {
                            let now = self.now();
                            self.step().process_input(Input::Frame {
                                rx_time: now,
                                rssi: rx.rssi,
                                bytes: &rx.bytes,
                            });
                        }
                        Ok(None) => break,
                        Err(e) => return Err(e.to_string()),
                    }
                }
            }
            AirRx::Ready => {
                let mut ctx = DriverStep {
                    node: &mut self.node,
                    epoch: &self.epoch,
                    netif: &mut self.netif,
                    peers: &self.peers,
                    unicast: &self.unicast,
                    pending_tx: &mut self.pending_tx,
                    over_cap_stalled_reads: &mut self.over_cap_stalled_reads,
                    overlap: &mut self.overlap,
                    timer: &mut self.timer,
                    lateness: &mut self.lateness,
                };
                self.air
                    .drain_rx(AIR_DRAIN_BATCH, |rssi, bytes| {
                        let now = ctx.now();
                        ctx.process_input(Input::Frame {
                            rx_time: now,
                            rssi,
                            bytes,
                        });
                    })
                    .map_err(|e| e.to_string())?;
            }
        }
        Ok(())
    }

    fn drain_netif_tx(&mut self, first: Ipv6Packet) -> Result<(), String> {
        let now = self.now();
        self.step().process_input(Input::Outbound { now, packet: first });
        for _ in 1..DRAIN_BATCH {
            if !self.takes_outbound() {
                break;
            }
            match self.netif.try_recv() {
                Ok(Some(packet)) => {
                    let now = self.now();
                    self.step().process_input(Input::Outbound { now, packet });
                }
                Ok(None) => break,
                Err(e) => return Err(e.to_string()),
            }
        }
        Ok(())
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
        enum Arm {
            Stop,
            Air(AirRx),
            Netif(Ipv6Packet),
            Timer,
            Stats,
            Peers,
        }
        let takes_outbound = d.takes_outbound();
        let arm = tokio::select! {
            _ = d.stopped.changed() => Arm::Stop,
            r = d.air.wait_or_recv() => Arm::Air(r.map_err(|e| e.to_string())?),
            r = d.netif.recv(), if takes_outbound => Arm::Netif(r.map_err(|e| e.to_string())?),
            _ = tokio::time::sleep_until(wake) => Arm::Timer,
            _ = stats.tick() => Arm::Stats,
            _ = peers.tick() => Arm::Peers,
        };
        match arm {
            Arm::Stop => {
                d.log_stats();
                return Ok(());
            }
            Arm::Air(first) => {
                d.drain_air_rx(first)?;
                d.flush_pending_tx();
            }
            Arm::Netif(first) => {
                d.drain_netif_tx(first)?;
                d.flush_pending_tx();
            }
            Arm::Timer => {}
            Arm::Stats => d.log_stats(),
            Arm::Peers => d.publish_peers(),
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
            pending_tx: VecDeque::new(),
            recycled_frames: Vec::new(),
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
            pending_tx: VecDeque::new(),
            recycled_frames: Vec::new(),
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
            pending_tx: VecDeque::new(),
            recycled_frames: Vec::new(),
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
            pending_tx: VecDeque::new(),
            recycled_frames: Vec::new(),
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

    fn multi_peer_capture_driver(
        us: MacAddr,
        stalled_peer: MacAddr,
        stalled_seq: Sequence,
        healthy_peer: MacAddr,
        start_at: Time,
    ) -> Driver<CaptureAir, NullNet> {
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

        let register = |node: &mut Node, addr: MacAddr, name: &str, ch: u8, seq: &Sequence| {
            let el = Election::own(addr);
            let own = tlv::Own {
                addr,
                name,
                devclass: tlv::devclass::IOS,
                channel: ch,
                master_channel: Channel(ch),
                next_tu: 64,
                aw_counter: 0,
                psf_interval_tu: 110,
                sequence: seq,
                election: &el,
                ht: Some(tlv::HtCapabilities::ONE_STREAM),
                advertise_vht80_1ss: false,
            };
            let bytes = frame::action_frame(addr, 1, frame::subtype::MIF, 0, |out| {
                tlv::put_all(out, &own, true)
            });
            node.handle(Input::Frame {
                rx_time: Time(1),
                rssi: Some(-40),
                bytes: &bytes,
            })
            .for_each(drop);
        };
        register(&mut node, stalled_peer, "stalled", 6, &stalled_seq);
        register(&mut node, healthy_peer, "healthy", 149, &Sequence::fixed(Channel(149)));

        let epoch = Instant::now() - Duration::from_micros(start_at.0.saturating_sub(1));
        let (_, stopped) = watch::channel(false);
        let (peers, _) = watch::channel(Vec::new());
        Driver {
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
            pending_tx: VecDeque::new(),
            recycled_frames: Vec::new(),
            full: false,
            over_cap_stalled_reads: 0,
            overlap: None,
            tx: TxStats::default(),
            timer: Time(1),
            lateness: Lateness::default(),
        }
    }

    #[test]
    fn review_driver_admits_healthy_peer_when_other_peer_queue_is_full() {
        let us = MacAddr([2, 0, 0, 0, 0, 1]);
        let stalled_peer = MacAddr([2, 0, 0, 0, 0, 2]);
        let healthy_peer = MacAddr([2, 0, 0, 0, 0, 3]);
        // Stalled peer occupies ch 6 (no overlap with ch 149 except slot 0); healthy peer fixed on ch 149.
        let mut seq_s = Sequence::fixed(Channel(6));
        seq_s.slots[0] = Channel(149).slot();
        let t_closed = Time(100_000);
        let mut d = multi_peer_capture_driver(us, stalled_peer, seq_s, healthy_peer, t_closed);
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

    #[test]
    fn rx_past_deadline_resets_over_cap_stalled_reads() {
        let us = MacAddr([2, 0, 0, 0, 0, 1]);
        let stalled_peer = MacAddr([2, 0, 0, 0, 0, 2]);
        let healthy_peer = MacAddr([2, 0, 0, 0, 0, 3]);
        let t_start = Time(100_000);
        let mut d = multi_peer_capture_driver(
            us,
            stalled_peer,
            Sequence::fixed(Channel(6)),
            healthy_peer,
            t_start,
        );

        // Fill node backlog with stalled_peer, then exhaust OVER_CAP_STALLED_BURST so takes_outbound() locks false.
        for seq in 0..(UNICAST_QUEUE + OVER_CAP_STALLED_BURST) {
            assert!(d.takes_outbound());
            d.handle(Input::Outbound {
                now: t_start,
                packet: Ipv6Packet::echo_request(us, stalled_peer, 1, seq as u16, &[seq as u8; 8]),
            });
        }
        assert_eq!(d.node.backlog(), UNICAST_QUEUE);
        assert!(!d.takes_outbound());

        // Preempt multiple deadlines with RX frames before service_timer() runs, matching Driver::run() order.
        let mut seq = 0u16;
        while d.timer.0 < 1_000_000 {
            let rx_time = d.timer.plus(2_000);
            d.epoch = Instant::now() - Duration::from_micros(rx_time.0 - 1);
            d.air.epoch = d.epoch;
            let pkt = Ipv6Packet::echo_request(healthy_peer, us, 1, seq, b"rx-preempt-timer");
            let bytes = frame::qos_data_frame(healthy_peer, us, seq, seq, &pkt.data);
            d.handle(Input::Frame {
                rx_time,
                rssi: Some(-40),
                bytes: &bytes,
            });
            // service_timer() now observes now < d.timer and skips Input::Timer; RX must already have rearmed reads.
            d.service_timer();
            d.flush_retries();
            assert!(d.takes_outbound(), "RX servicing due deadline must rearm outbound reads");

            // Healthy peer transmits immediately (node evicts one stalled packet to admit it).
            let sent_before = d.air.sent.len();
            d.handle(Input::Outbound {
                now: rx_time,
                packet: Ipv6Packet::echo_request(us, healthy_peer, 2, seq, b"healthy-ack"),
            });
            assert_eq!(d.air.sent.len(), sent_before + 1);
            assert_eq!(d.node.backlog(), UNICAST_QUEUE - 1);

            // Top stalled_peer back up to cap, then re-exhaust stalled burst until next RX deadline.
            d.handle(Input::Outbound {
                now: rx_time,
                packet: Ipv6Packet::echo_request(us, stalled_peer, 3, seq, b"top-off"),
            });
            assert_eq!(d.node.backlog(), UNICAST_QUEUE);
            for j in 0..OVER_CAP_STALLED_BURST {
                assert!(d.takes_outbound());
                d.handle(Input::Outbound {
                    now: rx_time,
                    packet: Ipv6Packet::echo_request(us, stalled_peer, 3, seq.wrapping_add((j + 1) as u16), b"s"),
                });
            }
            assert!(!d.takes_outbound());
            seq += 1;
        }
        assert!(seq > 5);

        // An Outbound arriving past d.timer also resets the old burst instead of remaining pinned at cap.
        let due_outbound = d.timer.plus(1_000);
        d.handle(Input::Outbound {
            now: due_outbound,
            packet: Ipv6Packet::echo_request(us, stalled_peer, 4, 1, b"single-stalled"),
        });
        assert_eq!(d.over_cap_stalled_reads, 1);
        assert!(d.takes_outbound());
    }

    #[tokio::test]
    async fn zero_copy_batch_air_and_netif_recycle_path() {
        use std::sync::{Arc, Mutex};

        let us = MacAddr([2, 0, 0, 0, 0, 1]);
        let peer = MacAddr([2, 0, 0, 0, 0, 2]);
        let mut cfg = Config::new(
            us,
            "zc",
            RadioConstraints {
                channel: Channel(149),
                presence: Presence::Fixed,
            },
        );
        cfg.neighbour_echo = false;
        cfg.initiate_addba = true;
        cfg.amsdu = true;
        cfg.amsdu_coalesce_us = 0;
        let mut node = Node::new(cfg, Time(1));

        let seq = Sequence::fixed(Channel(149));
        let el = Election::own(peer);
        let own = tlv::Own {
            addr: peer,
            name: "peer",
            devclass: tlv::devclass::IOS,
            channel: 149,
            master_channel: Channel(149),
            next_tu: 64,
            aw_counter: 0,
            psf_interval_tu: 110,
            sequence: &seq,
            election: &el,
            ht: Some(tlv::HtCapabilities::ONE_STREAM),
            advertise_vht80_1ss: false,
        };
        let mif = frame::action_frame(peer, 1, frame::subtype::MIF, 0, |out| {
            tlv::put_all(out, &own, true)
        });
        node.handle(Input::Frame {
            rx_time: Time(1),
            rssi: Some(-35),
            bytes: &mif,
        })
        .for_each(drop);

        struct BatchAir {
            rx_frames: Vec<Vec<u8>>,
            sent_batches: Arc<Mutex<Vec<usize>>>,
            fail_on_second: bool,
        }
        impl Air for BatchAir {
            async fn recv(&mut self) -> io::Result<Received> {
                unreachable!("wait_or_recv / drain_rx path used instead")
            }
            async fn wait_or_recv(&mut self) -> io::Result<AirRx> {
                if self.rx_frames.is_empty() {
                    std::future::pending().await
                } else {
                    Ok(AirRx::Ready)
                }
            }
            fn drain_rx<F>(&mut self, limit: usize, mut on_frame: F) -> io::Result<()>
            where
                F: FnMut(Option<i8>, &[u8]),
            {
                for bytes in self.rx_frames.drain(..self.rx_frames.len().min(limit)) {
                    on_frame(Some(-35), &bytes);
                }
                Ok(())
            }
            fn send(&mut self, _: Frame) -> Result<(), Frame> {
                Ok(())
            }
            fn supports_batch(&self) -> bool {
                true
            }
            fn send_batch(
                &mut self,
                frames: &mut VecDeque<(Time, Frame)>,
                recycled: &mut Vec<Vec<u8>>,
            ) -> (usize, bool) {
                let n = frames.len();
                let accept = if self.fail_on_second && n >= 2 { 1 } else { n };
                self.sent_batches.lock().unwrap().push(accept);
                for _ in 0..accept {
                    if let Some((_, mut f)) = frames.pop_front() {
                        f.bytes.clear();
                        recycled.push(f.bytes);
                    }
                }
                (accept, accept < n)
            }
        }

        #[derive(Default)]
        struct SliceNet {
            delivered_slices: usize,
            recycled_bufs: usize,
        }
        impl Netif for SliceNet {
            async fn recv(&mut self) -> io::Result<Ipv6Packet> {
                std::future::pending().await
            }
            fn deliver(&mut self, _: Ipv6Packet) {
                panic!("owned deliver should not be used for direct RX slice");
            }
            fn deliver_slice(&mut self, dst: MacAddr, src: MacAddr, data: &[u8]) {
                assert_eq!(dst, MacAddr([2, 0, 0, 0, 0, 1]));
                assert_eq!(src, MacAddr([2, 0, 0, 0, 0, 2]));
                assert!(!data.is_empty());
                self.delivered_slices += 1;
            }
            fn recycle_packet_buf(&mut self, _buf: Vec<u8>) {
                self.recycled_bufs += 1;
            }
        }

        let pkt1 = Ipv6Packet::echo_request(peer, us, 1, 1, b"slice-1");
        let pkt2 = Ipv6Packet::echo_request(peer, us, 1, 2, b"slice-2");
        let amsdu = frame::amsdu_frame(peer, us, 7, 1, &[&pkt1.data, &pkt2.data]);

        let epoch = Instant::now() - Duration::from_micros(100_000);
        let (_, stopped) = watch::channel(false);
        let (peers, _) = watch::channel(Vec::new());
        let sent_batches = Arc::new(Mutex::new(Vec::new()));
        let mut d = Driver {
            node,
            epoch,
            air: BatchAir {
                rx_frames: std::vec![amsdu],
                sent_batches: sent_batches.clone(),
                fail_on_second: true,
            },
            netif: SliceNet::default(),
            peers,
            stopped,
            unicast: VecDeque::new(),
            multicast: VecDeque::new(),
            pending_tx: VecDeque::new(),
            recycled_frames: Vec::new(),
            full: false,
            over_cap_stalled_reads: 0,
            overlap: None,
            tx: TxStats::default(),
            timer: Time(1),
            lateness: Lateness::default(),
        };

        let first = d.air.wait_or_recv().await.unwrap();
        assert!(matches!(first, AirRx::Ready));
        d.drain_air_rx(first).unwrap();
        assert_eq!(d.netif.delivered_slices, 2);

        // Two outbound packets coalesce/transmit via send_batch; 2nd triggers partial EAGAIN deferral,
        // while 1st returns its packet buffer to Netif and its frame buffer to Node!
        for seq in 0..2u16 {
            let now = d.now();
            let mut data = Vec::with_capacity(512);
            data.extend_from_slice(&Ipv6Packet::echo_request(us, peer, 2, seq, &[0xaa; 64]).data);
            d.step().process_input(Input::Outbound {
                now,
                packet: Ipv6Packet { dst: peer, src: us, data },
            });
        }
        d.flush_pending_tx();
        assert_eq!(d.netif.recycled_bufs, 2);
        // pending_tx held [ADDBA Req, Data #1, Data #2]; BatchAir accepted 1st and deferred 2 data frames.
        assert_eq!(d.unicast.len(), 2);
        assert!(d.full);
        d.air.fail_on_second = false;
        d.flush_retries();
        assert!(d.unicast.is_empty());
        assert!(!d.full);
    }
}
