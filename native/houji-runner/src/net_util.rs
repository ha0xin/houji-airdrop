use std::collections::BTreeMap;
use std::io;
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use owly_awdl::MacAddr;

/// Maximum consecutive filtered RX frames or drained batches processed before yielding.
pub const RX_SKIP_LIMIT: usize = 32;

const NEIGH_RETRY_BACKOFF_MS: [u64; 4] = [25, 50, 100, 250];

/// Bounds consecutive filtered/no-op receive iterations on single-threaded async loops
/// so busy monitor or netif queues cannot starve TX, timers, or signal branches.
#[derive(Debug)]
pub struct SkipBudget {
    skipped: usize,
    limit: usize,
}

impl SkipBudget {
    pub const fn new(limit: usize) -> Self {
        Self { skipped: 0, limit }
    }

    pub async fn note_skip(&mut self) -> bool {
        self.skipped += 1;
        if self.skipped >= self.limit {
            self.skipped = 0;
            tokio::task::yield_now().await;
            true
        } else {
            false
        }
    }
}

/// Netlink neighbour table mutation backend (real `RtnlNeigh` on Android, mock in tests).
pub trait NeighBackend: Send + 'static {
    fn replace(&mut self, ifindex: u32, addr: [u8; 16], mac: [u8; 6]) -> io::Result<()>;
    fn delete(&mut self, ifindex: u32, addr: [u8; 16]) -> io::Result<()>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum NeighDesired {
    Present,
    Absent,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum NeighApplied {
    Absent,
    Present,
    Unknown,
}

#[derive(Debug, Clone)]
struct NeighEntry {
    desired: NeighDesired,
    applied: NeighApplied,
    generation: u64,
    retries_used: u8,
    exhausted: bool,
    next_retry: Option<Instant>,
}

impl NeighEntry {
    fn needs_sync(&self) -> bool {
        if self.exhausted {
            return false;
        }
        match (self.desired, self.applied) {
            (NeighDesired::Present, NeighApplied::Present) => false,
            (NeighDesired::Absent, NeighApplied::Absent) => false,
            _ => true,
        }
    }
}

struct NeighShared {
    entries: BTreeMap<MacAddr, NeighEntry>,
    shutdown: bool,
    in_flight: Option<MacAddr>,
    backoff_ms: Vec<u64>,
}

impl NeighShared {
    #[cfg(test)]
    fn is_idle(&self) -> bool {
        self.in_flight.is_none() && !self.entries.values().any(NeighEntry::needs_sync)
    }
}

struct NeighBus {
    state: Mutex<NeighShared>,
    cv: Condvar,
}

/// Non-blocking desired-state worker for `NETLINK_ROUTE` permanent neighbour updates.
/// Keeps `RTM_NEWNEIGH`/`RTM_DELNEIGH` roundtrips off the `owly-awdl` timer loop.
pub struct NeighWorker {
    bus: Option<Arc<NeighBus>>,
    worker: Option<JoinHandle<()>>,
}

impl NeighWorker {
    pub fn spawn<B: NeighBackend>(ifindex: u32, backend: B) -> Self {
        Self::spawn_with_backoff(ifindex, backend, &NEIGH_RETRY_BACKOFF_MS)
    }

    fn spawn_with_backoff<B: NeighBackend>(ifindex: u32, backend: B, backoff_ms: &[u64]) -> Self {
        let bus = Arc::new(NeighBus {
            state: Mutex::new(NeighShared {
                entries: BTreeMap::new(),
                shutdown: false,
                in_flight: None,
                backoff_ms: backoff_ms.to_vec(),
            }),
            cv: Condvar::new(),
        });
        let worker_bus = Arc::clone(&bus);
        let worker = thread::Builder::new()
            .name("owly-rtnl-neigh".to_string())
            .spawn(move || neigh_worker_loop(ifindex, worker_bus, backend))
            .expect("spawn owly-rtnl-neigh thread");
        Self {
            bus: Some(bus),
            worker: Some(worker),
        }
    }

    pub fn replace(&self, mac: MacAddr) {
        let Some(bus) = &self.bus else {
            return;
        };
        let mut guard = bus.state.lock().unwrap();
        let entry = guard.entries.entry(mac).or_insert(NeighEntry {
            desired: NeighDesired::Absent,
            applied: NeighApplied::Absent,
            generation: 0,
            retries_used: 0,
            exhausted: false,
            next_retry: None,
        });
        if entry.desired != NeighDesired::Present
            || (entry.applied != NeighApplied::Present && entry.exhausted)
        {
            entry.desired = NeighDesired::Present;
            entry.generation = entry.generation.wrapping_add(1);
            entry.retries_used = 0;
            entry.exhausted = false;
            entry.next_retry = None;
            bus.cv.notify_all();
        }
    }

    pub fn delete(&self, mac: MacAddr) {
        let Some(bus) = &self.bus else {
            return;
        };
        let mut guard = bus.state.lock().unwrap();
        let in_flight = guard.in_flight == Some(mac);
        let Some(entry) = guard.entries.get_mut(&mac) else {
            return;
        };
        if entry.applied == NeighApplied::Absent && !in_flight {
            guard.entries.remove(&mac);
            bus.cv.notify_all();
            return;
        }
        if entry.desired != NeighDesired::Absent
            || (entry.applied != NeighApplied::Absent && entry.exhausted)
        {
            entry.desired = NeighDesired::Absent;
            entry.generation = entry.generation.wrapping_add(1);
            entry.retries_used = 0;
            entry.exhausted = false;
            entry.next_retry = None;
            bus.cv.notify_all();
        }
    }

    #[cfg(test)]
    fn wait_until_idle(&self) {
        let Some(bus) = &self.bus else {
            return;
        };
        let mut guard = bus.state.lock().unwrap();
        while !guard.shutdown && !guard.is_idle() {
            guard = bus.cv.wait(guard).unwrap();
        }
    }
}

impl Drop for NeighWorker {
    fn drop(&mut self) {
        if let Some(bus) = self.bus.take() {
            let mut guard = bus.state.lock().unwrap();
            guard.shutdown = true;
            bus.cv.notify_all();
        }
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn next_due_neigh(shared: &NeighShared, now: Instant) -> Option<(MacAddr, NeighDesired, u64)> {
    let mut best: Option<(MacAddr, NeighDesired, u64)> = None;
    for (&mac, entry) in &shared.entries {
        if !entry.needs_sync() {
            continue;
        }
        if entry.next_retry.is_some_and(|deadline| deadline > now) {
            continue;
        }
        if best.is_none_or(|(best_mac, _, best_gen)| {
            (entry.generation, mac) < (best_gen, best_mac)
        }) {
            best = Some((mac, entry.desired, entry.generation));
        }
    }
    best
}

fn next_neigh_wakeup(shared: &NeighShared) -> Option<Instant> {
    shared
        .entries
        .values()
        .filter(|e| e.needs_sync())
        .filter_map(|e| e.next_retry)
        .min()
}

fn neigh_worker_loop<B: NeighBackend>(ifindex: u32, bus: Arc<NeighBus>, mut backend: B) {
    loop {
        let job = {
            let mut guard = bus.state.lock().unwrap();
            loop {
                if guard.shutdown {
                    break None;
                }
                guard
                    .entries
                    .retain(|_, e| e.desired != NeighDesired::Absent || e.applied != NeighApplied::Absent);
                let now = Instant::now();
                if let Some((mac, desired, generation)) = next_due_neigh(&guard, now) {
                    guard.in_flight = Some(mac);
                    break Some((mac, desired, generation));
                }
                let wakeup = next_neigh_wakeup(&guard);
                if wakeup.is_none() {
                    bus.cv.notify_all();
                }
                guard = match wakeup {
                    Some(deadline) => {
                        let wait_for = deadline.saturating_duration_since(now);
                        if wait_for.is_zero() {
                            guard
                        } else {
                            let (g, _) = bus.cv.wait_timeout(guard, wait_for).unwrap();
                            g
                        }
                    }
                    None => bus.cv.wait(guard).unwrap(),
                };
            }
        };

        let Some((mac, desired, expected_gen)) = job else {
            break;
        };

        let addr = mac.link_local().octets();
        let res = match desired {
            NeighDesired::Present => backend.replace(ifindex, addr, mac.0),
            NeighDesired::Absent => match backend.delete(ifindex, addr) {
                Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(()),
                other => other,
            },
        };

        let mut guard = bus.state.lock().unwrap();
        guard.in_flight = None;
        match res {
            Ok(()) => {
                if let Some(entry) = guard.entries.get_mut(&mac) {
                    entry.applied = match desired {
                        NeighDesired::Present => NeighApplied::Present,
                        NeighDesired::Absent => NeighApplied::Absent,
                    };
                    if entry.generation == expected_gen {
                        entry.retries_used = 0;
                        entry.exhausted = false;
                        entry.next_retry = None;
                    }
                    if entry.desired == NeighDesired::Absent
                        && entry.applied == NeighApplied::Absent
                    {
                        guard.entries.remove(&mac);
                    }
                }
            }
            Err(e) => {
                let delay_opt = guard
                    .entries
                    .get(&mac)
                    .filter(|entry| entry.generation == expected_gen)
                    .and_then(|entry| guard.backoff_ms.get(entry.retries_used as usize).copied());
                if let Some(entry) = guard.entries.get_mut(&mac) {
                    entry.applied = NeighApplied::Unknown;
                    if entry.generation == expected_gen {
                        if let Some(delay_ms) = delay_opt {
                            entry.retries_used = entry.retries_used.saturating_add(1);
                            entry.next_retry =
                                Some(Instant::now() + Duration::from_millis(delay_ms));
                            tracing::debug!(
                                %mac,
                                op = ?desired,
                                error = %e,
                                retry = entry.retries_used,
                                backoff_ms = delay_ms,
                                "tap neigh update retry scheduled"
                            );
                        } else {
                            entry.exhausted = true;
                            entry.next_retry = None;
                            match desired {
                                NeighDesired::Present => {
                                    tracing::warn!(%mac, error = %e, "tap neigh replace failed");
                                }
                                NeighDesired::Absent => {
                                    tracing::warn!(%mac, error = %e, "tap neigh delete failed");
                                }
                            }
                        }
                    } else if entry.desired == NeighDesired::Absent
                        && entry.applied == NeighApplied::Absent
                    {
                        guard.entries.remove(&mac);
                    }
                }
            }
        }
        bus.cv.notify_all();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc::{self, Receiver, Sender};
    use std::sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    };

    #[derive(Debug, Clone, PartialEq, Eq)]
    enum NeighCall {
        Replace(u32, [u8; 16], [u8; 6]),
        Delete(u32, [u8; 16]),
    }

    struct MockNeigh {
        log: Arc<Mutex<Vec<NeighCall>>>,
        fail_replace_remaining: usize,
        delete_not_found: bool,
    }

    impl NeighBackend for MockNeigh {
        fn replace(&mut self, ifindex: u32, addr: [u8; 16], mac: [u8; 6]) -> io::Result<()> {
            self.log
                .lock()
                .unwrap()
                .push(NeighCall::Replace(ifindex, addr, mac));
            if self.fail_replace_remaining > 0 {
                self.fail_replace_remaining -= 1;
                return Err(io::Error::new(io::ErrorKind::TimedOut, "netlink ack timeout"));
            }
            Ok(())
        }

        fn delete(&mut self, ifindex: u32, addr: [u8; 16]) -> io::Result<()> {
            self.log.lock().unwrap().push(NeighCall::Delete(ifindex, addr));
            if self.delete_not_found {
                return Err(io::Error::new(io::ErrorKind::NotFound, "not found"));
            }
            Ok(())
        }
    }

    struct BlockingFirstNeigh {
        log: Arc<Mutex<Vec<NeighCall>>>,
        entered_tx: Option<Sender<()>>,
        release_rx: Receiver<()>,
    }

    impl NeighBackend for BlockingFirstNeigh {
        fn replace(&mut self, ifindex: u32, addr: [u8; 16], mac: [u8; 6]) -> io::Result<()> {
            if let Some(tx) = self.entered_tx.take() {
                let _ = tx.send(());
                let _ = self.release_rx.recv();
            }
            self.log
                .lock()
                .unwrap()
                .push(NeighCall::Replace(ifindex, addr, mac));
            Ok(())
        }

        fn delete(&mut self, ifindex: u32, addr: [u8; 16]) -> io::Result<()> {
            self.log.lock().unwrap().push(NeighCall::Delete(ifindex, addr));
            Ok(())
        }
    }

    #[test]
    fn skip_budget_yields_on_limit_and_unblocks_peer_task() {
        let rt = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        rt.block_on(async {
            let peer_ran = Arc::new(AtomicBool::new(false));
            let yields = Arc::new(AtomicUsize::new(0));
            let peer_flag = Arc::clone(&peer_ran);
            let peer = tokio::spawn(async move {
                peer_flag.store(true, Ordering::SeqCst);
            });

            let mut skip = SkipBudget::new(RX_SKIP_LIMIT);
            for idx in 0..RX_SKIP_LIMIT {
                let yielded = skip.note_skip().await;
                if idx + 1 < RX_SKIP_LIMIT {
                    assert!(!yielded);
                    assert!(!peer_ran.load(Ordering::SeqCst));
                } else {
                    assert!(yielded);
                    yields.fetch_add(1, Ordering::SeqCst);
                }
            }
            assert!(peer_ran.load(Ordering::SeqCst));
            assert_eq!(yields.load(Ordering::SeqCst), 1);
            peer.await.unwrap();
        });
    }

    #[test]
    fn neigh_worker_retries_transient_error_and_ignores_not_found_delete() {
        let log = Arc::new(Mutex::new(Vec::new()));
        let worker = NeighWorker::spawn_with_backoff(
            7,
            MockNeigh {
                log: Arc::clone(&log),
                fail_replace_remaining: 2,
                delete_not_found: true,
            },
            &[1, 1, 1],
        );
        let mac = MacAddr([0x02, 0xaa, 0xbb, 0xcc, 0xdd, 0x01]);
        worker.replace(mac);
        worker.wait_until_idle();

        worker.delete(mac);
        worker.wait_until_idle();
        drop(worker);

        let ll = mac.link_local().octets();
        assert_eq!(
            log.lock().unwrap().clone(),
            vec![
                NeighCall::Replace(7, ll, mac.0),
                NeighCall::Replace(7, ll, mac.0),
                NeighCall::Replace(7, ll, mac.0),
                NeighCall::Delete(7, ll),
            ]
        );
    }

    #[test]
    fn neigh_worker_coalesces_flaps_and_never_blocks_caller() {
        let log = Arc::new(Mutex::new(Vec::new()));
        let (entered_tx, entered_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let worker = NeighWorker::spawn_with_backoff(
            9,
            BlockingFirstNeigh {
                log: Arc::clone(&log),
                entered_tx: Some(entered_tx),
                release_rx,
            },
            &[1, 1],
        );

        let blocker = MacAddr([0x02, 0x01, 0x02, 0x03, 0x04, 0x01]);
        let flapped = MacAddr([0x02, 0x01, 0x02, 0x03, 0x04, 0x02]);
        let cancelled = MacAddr([0x02, 0x01, 0x02, 0x03, 0x04, 0x03]);

        worker.replace(blocker);
        entered_rx.recv().unwrap();

        // These return immediately while the worker thread is blocked in netlink.
        worker.replace(flapped);
        worker.delete(flapped);
        worker.replace(flapped);

        worker.replace(cancelled);
        worker.delete(cancelled);

        release_tx.send(()).unwrap();
        worker.wait_until_idle();
        drop(worker);

        assert_eq!(
            log.lock().unwrap().clone(),
            vec![
                NeighCall::Replace(9, blocker.link_local().octets(), blocker.0),
                NeighCall::Replace(9, flapped.link_local().octets(), flapped.0),
            ]
        );
    }

    #[test]
    fn review_neigh_failed_replace_still_issues_idempotent_delete() {
        let log = Arc::new(Mutex::new(Vec::new()));
        let worker = NeighWorker::spawn_with_backoff(
            7,
            MockNeigh {
                log: Arc::clone(&log),
                fail_replace_remaining: 10,
                delete_not_found: true,
            },
            &[1],
        );
        let mac = MacAddr([0x02, 0xaa, 0xbb, 0xcc, 0xdd, 0x99]);
        let ll = mac.link_local().octets();

        // Replace times out after retry budget -> kernel state is Unknown (might exist).
        worker.replace(mac);
        worker.wait_until_idle();

        // Subsequent delete MUST still emit RTM_DELNEIGH rather than discarding Unknown state.
        worker.delete(mac);
        worker.wait_until_idle();
        drop(worker);

        assert_eq!(
            log.lock().unwrap().clone(),
            vec![
                NeighCall::Replace(7, ll, mac.0),
                NeighCall::Replace(7, ll, mac.0),
                NeighCall::Delete(7, ll),
            ]
        );
    }
}
