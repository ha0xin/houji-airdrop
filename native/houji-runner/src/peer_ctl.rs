#[cfg(any(target_os = "android", test))]
use owly_awdl::{MacAddr, PeerCaps};
use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

const SYSFS_MULTI_FLAG: &str = "/sys/module/qca_cld3_kiwi_v2/parameters/pt_remote_multi";
const SYSFS_PEER_CTL: &str = "/sys/module/qca_cld3_kiwi_v2/parameters/pt_remote_peer_ctl";
const DEFAULT_RETRY_BACKOFF_MS: [u64; 5] = [25, 50, 100, 250, 500];
const DEFAULT_EXHAUSTED_COOLDOWN_MS: u64 = 5_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum AppliedState {
    Absent,
    Present,
}

#[derive(Debug, Clone)]
struct PeerEntry {
    desired: bool,
    applied: AppliedState,
    desired_caps: Option<PeerCaps>,
    applied_caps: Option<PeerCaps>,
    generation: u64,
    retries_used: u8,
    exhausted: bool,
    next_retry: Option<Instant>,
    cooldown_until: Option<Instant>,
}

impl PeerEntry {
    fn has_pending_delta(&self) -> bool {
        match (self.desired, self.applied) {
            (true, AppliedState::Absent) => true,
            (true, AppliedState::Present) => self.applied_caps != self.desired_caps,
            (false, AppliedState::Present) => true,
            (false, AppliedState::Absent) => false,
        }
    }

    fn needs_sync(&self) -> bool {
        !self.exhausted && self.has_pending_delta()
    }

    fn want_add(&self) -> bool {
        self.desired && self.applied == AppliedState::Absent
    }
}

struct SharedState {
    peers: BTreeMap<MacAddr, PeerEntry>,
    shutdown: bool,
    in_flight: Option<MacAddr>,
    backoff_ms: Vec<u64>,
    cooldown_ms: Option<u64>,
}

impl SharedState {
    fn active_count(&self) -> usize {
        self.peers
            .values()
            .filter(|e| e.applied == AppliedState::Present)
            .count()
    }

    #[cfg(test)]
    fn is_idle(&self) -> bool {
        self.in_flight.is_none() && !self.peers.values().any(PeerEntry::needs_sync)
    }
}

struct SyncBus {
    state: Mutex<SharedState>,
    cv: Condvar,
}

trait CtlSink: Send + 'static {
    fn write_cmd(&mut self, cmd: &str) -> io::Result<()>;
}

struct SysfsCtlSink {
    path: PathBuf,
}

impl CtlSink for SysfsCtlSink {
    fn write_cmd(&mut self, cmd: &str) -> io::Result<()> {
        let mut f = OpenOptions::new().write(true).open(&self.path)?;
        f.write_all(cmd.as_bytes())?;
        f.flush()
    }
}

/// Synchronizes dynamically discovered AWDL peers into `pt_remote_peer_ctl` off
/// the `owly-awdl` microsecond timer thread so WMI peer add/del sleeps cannot
/// stall slot scheduling. Uses desired-state reconciliation by MAC so rapid
/// add/del flaps coalesce cleanly and transient sysfs errors recover via bounded
/// exponential backoff.
pub struct KernelPeerSync {
    bus: Option<Arc<SyncBus>>,
    worker: Option<JoinHandle<()>>,
}

impl KernelPeerSync {
    pub fn disabled() -> Self {
        Self {
            bus: None,
            worker: None,
        }
    }

    pub fn from_sysfs() -> Self {
        if env_disabled() {
            println!("OWLY_PT_PEER_CTL enabled=false reason=env");
            return Self::disabled();
        }
        Self::from_paths(Path::new(SYSFS_MULTI_FLAG), Path::new(SYSFS_PEER_CTL))
    }

    fn from_paths(flag_path: &Path, ctl_path: &Path) -> Self {
        let enabled = fs::read_to_string(flag_path)
            .map(|raw| parse_sysfs_bool(&raw))
            .unwrap_or(false);
        if !enabled {
            println!("OWLY_PT_PEER_CTL enabled=false reason=param");
            return Self::disabled();
        }
        println!("OWLY_PT_PEER_CTL enabled=true");
        Self::spawn_with_sink(SysfsCtlSink {
            path: ctl_path.to_path_buf(),
        })
    }

    fn spawn_with_sink<S: CtlSink>(sink: S) -> Self {
        Self::spawn_with_sink_and_backoff_and_cooldown(
            sink,
            &DEFAULT_RETRY_BACKOFF_MS,
            Some(DEFAULT_EXHAUSTED_COOLDOWN_MS),
        )
    }

    #[cfg(test)]
    fn spawn_with_sink_and_backoff<S: CtlSink>(sink: S, backoff_ms: &[u64]) -> Self {
        Self::spawn_with_sink_and_backoff_and_cooldown(
            sink,
            backoff_ms,
            Some(DEFAULT_EXHAUSTED_COOLDOWN_MS),
        )
    }

    fn spawn_with_sink_and_backoff_and_cooldown<S: CtlSink>(
        sink: S,
        backoff_ms: &[u64],
        cooldown_ms: Option<u64>,
    ) -> Self {
        let bus = Arc::new(SyncBus {
            state: Mutex::new(SharedState {
                peers: BTreeMap::new(),
                shutdown: false,
                in_flight: None,
                backoff_ms: backoff_ms.to_vec(),
                cooldown_ms,
            }),
            cv: Condvar::new(),
        });
        let worker_bus = Arc::clone(&bus);
        let worker = thread::Builder::new()
            .name("owly-pt-peer-ctl".to_string())
            .spawn(move || worker_loop(worker_bus, sink))
            .expect("spawn owly-pt-peer-ctl thread");
        Self {
            bus: Some(bus),
            worker: Some(worker),
        }
    }

    pub fn is_enabled(&self) -> bool {
        self.bus.is_some()
    }

    #[cfg_attr(not(test), allow(dead_code))]
    pub fn is_peer_ready(&self, mac: MacAddr) -> bool {
        let Some(bus) = &self.bus else {
            return true;
        };
        let guard = bus.state.lock().unwrap();
        guard.peers.get(&mac).is_some_and(|e| {
            e.desired
                && e.applied == AppliedState::Present
                && e.applied_caps == e.desired_caps
        })
    }

    #[cfg_attr(not(test), allow(dead_code))]
    pub fn peer_up(&self, mac: MacAddr) {
        self.set_peer_up(mac, None);
    }

    pub fn peer_up_caps(&self, mac: MacAddr, caps: PeerCaps) {
        self.set_peer_up(mac, Some(caps));
    }

    pub fn peer_caps(&self, mac: MacAddr, caps: PeerCaps) {
        let Some(bus) = &self.bus else {
            return;
        };
        let mut guard = bus.state.lock().unwrap();
        let Some(entry) = guard.peers.get_mut(&mac) else {
            return;
        };
        if !entry.desired {
            return;
        }
        if entry.desired_caps != Some(caps) || (entry.applied_caps != Some(caps) && entry.exhausted)
        {
            entry.desired_caps = Some(caps);
            entry.generation = entry.generation.wrapping_add(1);
            entry.retries_used = 0;
            entry.exhausted = false;
            entry.next_retry = None;
            entry.cooldown_until = None;
            bus.cv.notify_all();
        }
    }

    fn set_peer_up(&self, mac: MacAddr, caps: Option<PeerCaps>) {
        let Some(bus) = &self.bus else {
            return;
        };
        let mut guard = bus.state.lock().unwrap();
        let entry = guard.peers.entry(mac).or_insert(PeerEntry {
            desired: false,
            applied: AppliedState::Absent,
            desired_caps: None,
            applied_caps: None,
            generation: 0,
            retries_used: 0,
            exhausted: false,
            next_retry: None,
            cooldown_until: None,
        });
        let caps_changed = entry.desired_caps != caps;
        let stuck = entry.exhausted && entry.has_pending_delta();
        if !entry.desired || caps_changed || stuck {
            entry.desired = true;
            entry.desired_caps = caps;
            entry.generation = entry.generation.wrapping_add(1);
            entry.retries_used = 0;
            entry.exhausted = false;
            entry.next_retry = None;
            entry.cooldown_until = None;
            bus.cv.notify_all();
        }
    }

    pub fn peer_down(&self, mac: MacAddr) {
        let Some(bus) = &self.bus else {
            return;
        };
        let mut guard = bus.state.lock().unwrap();
        let in_flight = guard.in_flight == Some(mac);
        let Some(entry) = guard.peers.get_mut(&mac) else {
            return;
        };
        if entry.applied == AppliedState::Absent && !in_flight {
            guard.peers.remove(&mac);
            bus.cv.notify_all();
            return;
        }
        if entry.desired {
            entry.desired = false;
            entry.generation = entry.generation.wrapping_add(1);
            entry.retries_used = 0;
            entry.exhausted = false;
            entry.next_retry = None;
            entry.cooldown_until = None;
            bus.cv.notify_all();
        } else if entry.applied == AppliedState::Present && entry.exhausted {
            entry.generation = entry.generation.wrapping_add(1);
            entry.retries_used = 0;
            entry.exhausted = false;
            entry.next_retry = None;
            entry.cooldown_until = None;
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

impl Drop for KernelPeerSync {
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

fn env_disabled() -> bool {
    matches!(
        std::env::var("HOUJI_OWLY_DYNAMIC_PEERS").ok().as_deref(),
        Some("0" | "false" | "FALSE" | "n" | "N" | "no" | "NO")
    )
}

fn parse_sysfs_bool(raw: &str) -> bool {
    matches!(raw.trim(), "Y" | "y" | "1" | "true" | "TRUE" | "on" | "ON")
}

fn next_due_peer(state: &SharedState, now: Instant) -> Option<(MacAddr, bool, Option<PeerCaps>, u64)> {
    let mut best: Option<(MacAddr, bool, Option<PeerCaps>, u64, u64)> = None;
    for (&mac, entry) in &state.peers {
        if !entry.needs_sync() {
            continue;
        }
        if entry.next_retry.is_some_and(|deadline| deadline > now) {
            continue;
        }
        // Prefer additions over deletions only gently by ordering ready time first, then generation, then MAC.
        let due_key = entry.generation;
        if best.is_none_or(|(best_mac, _, _, _, best_gen)| (due_key, mac) < (best_gen, best_mac)) {
            best = Some((mac, entry.want_add(), entry.desired_caps, entry.generation, due_key));
        }
    }
    best.map(|(mac, want_add, caps, generation, _)| (mac, want_add, caps, generation))
}

fn format_add_cmd(mac: MacAddr, caps: Option<PeerCaps>) -> String {
    match caps {
        Some(c) => format!(
            "add {mac} ht={} vht={} nss={} bw={} mcs={} sgi={} ldpc={} ht_bw40={} ht_sgi20={} ht_sgi40={} ht_ldpc={} vht_sgi80={} vht_ldpc={} max_mpdu={} ampdu_exp={} mpdu_density={} ht_mcs={}\n",
            u8::from(c.ht),
            u8::from(c.vht),
            c.nss,
            c.bw,
            c.max_mcs,
            u8::from(c.sgi),
            u8::from(c.ldpc),
            u8::from(c.ht_bw40),
            u8::from(c.ht_sgi20),
            u8::from(c.ht_sgi40),
            u8::from(c.ht_ldpc),
            u8::from(c.vht_sgi80),
            u8::from(c.vht_ldpc),
            c.max_mpdu,
            c.ampdu_exp,
            c.mpdu_density,
            c.ht_mcs_mask,
        ),
        None => format!("add {mac}\n"),
    }
}

fn next_wakeup(state: &SharedState) -> Option<Instant> {
    state
        .peers
        .values()
        .filter_map(|e| {
            if e.needs_sync() {
                e.next_retry
            } else if e.exhausted && e.has_pending_delta() {
                e.cooldown_until
            } else {
                None
            }
        })
        .min()
}

fn rearm_expired_cooldowns(state: &mut SharedState, now: Instant) {
    for entry in state.peers.values_mut() {
        if entry.exhausted
            && entry.has_pending_delta()
            && entry.cooldown_until.is_some_and(|deadline| deadline <= now)
        {
            entry.generation = entry.generation.wrapping_add(1);
            entry.retries_used = 0;
            entry.exhausted = false;
            entry.next_retry = None;
            entry.cooldown_until = None;
        }
    }
}

fn worker_loop<S: CtlSink>(bus: Arc<SyncBus>, mut sink: S) {
    loop {
        let job = {
            let mut guard = bus.state.lock().unwrap();
            loop {
                if guard.shutdown {
                    break None;
                }
                guard
                    .peers
                    .retain(|_, e| e.desired || e.applied == AppliedState::Present);
                let now = Instant::now();
                rearm_expired_cooldowns(&mut guard, now);
                if let Some((mac, want_add, caps, generation)) = next_due_peer(&guard, now) {
                    guard.in_flight = Some(mac);
                    break Some((mac, want_add, caps, generation));
                }
                let wakeup = next_wakeup(&guard);
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

        let Some((mac, want_add, caps, expected_gen)) = job else {
            break;
        };

        let op_name = if want_add { "add" } else { "del" };
        let cmd = if want_add {
            format_add_cmd(mac, caps)
        } else {
            format!("del {mac}\n")
        };
        let res = sink.write_cmd(&cmd);

        let mut guard = bus.state.lock().unwrap();
        guard.in_flight = None;
        let cooldown_ms = guard.cooldown_ms;
        match res {
            Ok(()) => {
                if let Some(entry) = guard.peers.get_mut(&mac) {
                    if want_add {
                        entry.applied = AppliedState::Present;
                        entry.applied_caps = caps;
                    } else {
                        entry.applied = AppliedState::Absent;
                        entry.applied_caps = None;
                    }
                    if entry.generation == expected_gen {
                        entry.retries_used = 0;
                        entry.exhausted = false;
                        entry.next_retry = None;
                        entry.cooldown_until = None;
                    }
                    if !entry.desired && entry.applied == AppliedState::Absent {
                        guard.peers.remove(&mac);
                    }
                }
                let active = guard.active_count();
                if want_add && let Some(c) = caps {
                    println!(
                        "OWLY_PT_PEER_CTL op={op_name} active={active} ht={} vht={} nss={} bw={} mcs={} sgi={} ldpc={} ht_bw40={} ht_sgi20={} ht_sgi40={} ht_ldpc={} vht_sgi80={} vht_ldpc={} rc=ok",
                        u8::from(c.ht),
                        u8::from(c.vht),
                        c.nss,
                        c.bw,
                        c.max_mcs,
                        u8::from(c.sgi),
                        u8::from(c.ldpc),
                        u8::from(c.ht_bw40),
                        u8::from(c.ht_sgi20),
                        u8::from(c.ht_sgi40),
                        u8::from(c.ht_ldpc),
                        u8::from(c.vht_sgi80),
                        u8::from(c.vht_ldpc),
                    );
                } else {
                    println!("OWLY_PT_PEER_CTL op={op_name} active={active} rc=ok");
                }
            }
            Err(e) => {
                let active = guard.active_count();
                let delay_opt = guard
                    .peers
                    .get(&mac)
                    .filter(|entry| entry.generation == expected_gen)
                    .and_then(|entry| guard.backoff_ms.get(entry.retries_used as usize).copied());
                if let Some(entry) = guard.peers.get_mut(&mac) {
                    if entry.generation == expected_gen {
                        if let Some(delay_ms) = delay_opt {
                            entry.retries_used = entry.retries_used.saturating_add(1);
                            entry.next_retry =
                                Some(Instant::now() + Duration::from_millis(delay_ms));
                            entry.cooldown_until = None;
                            let retry_num = entry.retries_used;
                            println!(
                                "OWLY_PT_PEER_CTL op={op_name} active={active} rc=err({e}) retry={retry_num} backoff_ms={delay_ms}"
                            );
                        } else {
                            entry.exhausted = true;
                            entry.next_retry = None;
                            entry.cooldown_until =
                                cooldown_ms.map(|ms| Instant::now() + Duration::from_millis(ms));
                            println!(
                                "OWLY_PT_PEER_CTL op={op_name} active={active} rc=giveup({e})"
                            );
                        }
                    } else if !entry.desired && entry.applied == AppliedState::Absent {
                        guard.peers.remove(&mac);
                    }
                }
            }
        }
        bus.cv.notify_all();
    }

    let active_on_shutdown = {
        let guard = bus.state.lock().unwrap();
        guard.active_count()
    };

    if active_on_shutdown > 0 {
        match sink.write_cmd("clear\n") {
            Ok(()) => {
                println!("OWLY_PT_PEER_CTL op=clear active=0 rc=ok");
            }
            Err(e) => {
                println!("OWLY_PT_PEER_CTL op=clear active={active_on_shutdown} rc=err({e})");
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc::{self, Receiver, Sender};
    use std::sync::{Arc, Mutex};
    use std::time::{SystemTime, UNIX_EPOCH};

    struct VecSink(Arc<Mutex<Vec<String>>>);

    impl CtlSink for VecSink {
        fn write_cmd(&mut self, cmd: &str) -> io::Result<()> {
            self.0.lock().unwrap().push(cmd.to_string());
            Ok(())
        }
    }

    struct FlakySink {
        log: Arc<Mutex<Vec<String>>>,
        fail_remaining: usize,
    }

    impl CtlSink for FlakySink {
        fn write_cmd(&mut self, cmd: &str) -> io::Result<()> {
            self.log.lock().unwrap().push(cmd.to_string());
            if self.fail_remaining > 0 {
                self.fail_remaining -= 1;
                return Err(io::Error::new(io::ErrorKind::WouldBlock, "transient wmi busy"));
            }
            Ok(())
        }
    }

    struct BlockingFirstWriteSink {
        log: Arc<Mutex<Vec<String>>>,
        entered_first_tx: Option<Sender<()>>,
        release_first_rx: Receiver<()>,
    }

    impl CtlSink for BlockingFirstWriteSink {
        fn write_cmd(&mut self, cmd: &str) -> io::Result<()> {
            if let Some(tx) = self.entered_first_tx.take() {
                let _ = tx.send(());
                let _ = self.release_first_rx.recv();
            }
            self.log.lock().unwrap().push(cmd.to_string());
            Ok(())
        }
    }

    fn temp_dir(name: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("houji_peer_ctl_{name}_{nanos}"));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn test_peer_ctl_add_dedup_del_and_clear() {
        let log = Arc::new(Mutex::new(Vec::new()));
        let sync = KernelPeerSync::spawn_with_sink(VecSink(Arc::clone(&log)));
        assert!(sync.is_enabled());

        let m1 = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x01]);
        let m2 = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x02]);
        let m3 = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x03]);

        sync.peer_up(m1);
        sync.peer_up(m1); // deduplicated
        sync.peer_up(m2);
        sync.wait_until_idle();
        assert!(sync.is_peer_ready(m1));
        assert!(sync.is_peer_ready(m2));
        sync.peer_down(m1);
        sync.peer_down(m3); // unknown peer skipped
        sync.wait_until_idle();
        assert!(!sync.is_peer_ready(m1));
        assert!(sync.is_peer_ready(m2));
        drop(sync);

        let recorded = log.lock().unwrap().clone();
        assert_eq!(
            recorded,
            vec![
                "add 02:11:22:33:44:01\n",
                "add 02:11:22:33:44:02\n",
                "del 02:11:22:33:44:01\n",
                "clear\n",
            ]
        );
    }

    #[test]
    fn test_peer_ctl_disabled_when_flag_n() {
        assert!(!KernelPeerSync::disabled().is_enabled());
        let dir = temp_dir("flag_n");
        let flag = dir.join("pt_remote_multi");
        let ctl = dir.join("pt_remote_peer_ctl");
        fs::write(&flag, "N\n").unwrap();
        fs::write(&ctl, "").unwrap();

        let sync = KernelPeerSync::from_paths(&flag, &ctl);
        assert!(!sync.is_enabled());

        let m1 = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x01]);
        assert!(sync.is_peer_ready(m1));
        sync.peer_up(m1);
        sync.peer_down(m1);
        drop(sync);

        assert_eq!(fs::read_to_string(&ctl).unwrap(), "");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_peer_ctl_non_clear_shutdown() {
        let dir = temp_dir("non_clear");
        let flag = dir.join("pt_remote_multi");
        let ctl = dir.join("pt_remote_peer_ctl");
        fs::write(&flag, "Y\n").unwrap();
        fs::write(&ctl, "").unwrap();

        let log = Arc::new(Mutex::new(Vec::new()));
        // Verify enabled path from temp files works.
        let fs_sync = KernelPeerSync::from_paths(&flag, &ctl);
        assert!(fs_sync.is_enabled());
        drop(fs_sync);
        assert_eq!(fs::read_to_string(&ctl).unwrap(), "");

        // When all added peers have already been removed, shutdown does not issue clear.
        let sync = KernelPeerSync::spawn_with_sink(VecSink(Arc::clone(&log)));
        let m1 = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x01]);
        sync.peer_up(m1);
        sync.wait_until_idle();
        sync.peer_down(m1);
        sync.wait_until_idle();
        drop(sync);

        assert_eq!(
            log.lock().unwrap().clone(),
            vec!["add 02:11:22:33:44:01\n", "del 02:11:22:33:44:01\n"]
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_peer_ctl_retries_transient_error_and_tracks_readiness() {
        let log = Arc::new(Mutex::new(Vec::new()));
        let sync = KernelPeerSync::spawn_with_sink_and_backoff(
            FlakySink {
                log: Arc::clone(&log),
                fail_remaining: 2,
            },
            &[1, 2, 4, 8, 16],
        );

        let m1 = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x10]);
        assert!(!sync.is_peer_ready(m1));
        sync.peer_up(m1);
        sync.wait_until_idle();

        assert!(sync.is_peer_ready(m1));
        drop(sync);

        assert_eq!(
            log.lock().unwrap().clone(),
            vec![
                "add 02:11:22:33:44:10\n",
                "add 02:11:22:33:44:10\n",
                "add 02:11:22:33:44:10\n",
                "clear\n",
            ]
        );
    }

    #[test]
    fn test_peer_ctl_exhausts_retry_budget_then_rearms_on_peer_up() {
        let log = Arc::new(Mutex::new(Vec::new()));
        let sync = KernelPeerSync::spawn_with_sink_and_backoff(
            FlakySink {
                log: Arc::clone(&log),
                fail_remaining: 6,
            },
            &[1, 1, 1, 1, 1],
        );

        let m1 = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x11]);
        sync.peer_up(m1);
        sync.wait_until_idle();

        // 1 initial attempt + 5 retries = 6 failures; readiness stays false.
        assert!(!sync.is_peer_ready(m1));
        assert_eq!(log.lock().unwrap().len(), 6);

        // Next peer_up after transient errors subside re-arms reconciliation.
        sync.peer_up(m1);
        sync.wait_until_idle();
        assert!(sync.is_peer_ready(m1));
        drop(sync);

        assert_eq!(log.lock().unwrap().len(), 8); // +1 add +1 clear
    }

    #[test]
    fn test_peer_ctl_coalesces_rapid_add_del_add_while_worker_busy() {
        let log = Arc::new(Mutex::new(Vec::new()));
        let (entered_tx, entered_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let sync = KernelPeerSync::spawn_with_sink_and_backoff(
            BlockingFirstWriteSink {
                log: Arc::clone(&log),
                entered_first_tx: Some(entered_tx),
                release_first_rx: release_rx,
            },
            &[1, 2, 4],
        );

        let m_blocker = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x20]);
        let m_flapping = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x21]);
        let m_cancelled = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x22]);

        sync.peer_up(m_blocker);
        entered_rx.recv().unwrap();

        // While worker is blocked in m_blocker's write, coalesce rapid transitions.
        sync.peer_up(m_flapping);
        sync.peer_down(m_flapping);
        sync.peer_up(m_flapping);

        sync.peer_up(m_cancelled);
        sync.peer_down(m_cancelled);

        release_tx.send(()).unwrap();
        sync.wait_until_idle();
        assert!(sync.is_peer_ready(m_flapping));
        assert!(!sync.is_peer_ready(m_cancelled));
        drop(sync);

        assert_eq!(
            log.lock().unwrap().clone(),
            vec![
                "add 02:11:22:33:44:20\n",
                "add 02:11:22:33:44:21\n",
                "clear\n",
            ]
        );
    }

    #[test]
    fn test_peer_ctl_emits_and_updates_peer_caps() {
        let log = Arc::new(Mutex::new(Vec::new()));
        let sync = KernelPeerSync::spawn_with_sink(VecSink(Arc::clone(&log)));

        let mac = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x55]);
        let initial = PeerCaps {
            ht: true,
            vht: true,
            nss: 1,
            bw: 80,
            max_mcs: 7,
            sgi: false,
            ldpc: false,
            ht_bw40: true,
            ht_sgi20: true,
            ht_sgi40: false,
            ht_ldpc: true,
            vht_sgi80: false,
            vht_ldpc: false,
            max_mpdu: 3895,
            ampdu_exp: 3,
            mpdu_density: 6,
            ht_mcs_mask: 0x00ff,
        };
        let upgraded = PeerCaps {
            ht: true,
            vht: true,
            nss: 2,
            bw: 80,
            max_mcs: 9,
            sgi: true,
            ldpc: true,
            ht_bw40: true,
            ht_sgi20: true,
            ht_sgi40: true,
            ht_ldpc: true,
            vht_sgi80: true,
            vht_ldpc: true,
            max_mpdu: 11454,
            ampdu_exp: 7,
            mpdu_density: 7,
            ht_mcs_mask: 0xffff,
        };

        sync.peer_up_caps(mac, initial);
        sync.wait_until_idle();
        assert!(sync.is_peer_ready(mac));

        // Same caps do not emit duplicate writes.
        sync.peer_caps(mac, initial);
        sync.wait_until_idle();

        // Upgraded caps reconcile via del followed by add with new caps.
        sync.peer_caps(mac, upgraded);
        sync.wait_until_idle();
        assert!(sync.is_peer_ready(mac));

        drop(sync);

        assert_eq!(
            log.lock().unwrap().clone(),
            vec![
                "add 02:11:22:33:44:55 ht=1 vht=1 nss=1 bw=80 mcs=7 sgi=0 ldpc=0 ht_bw40=1 ht_sgi20=1 ht_sgi40=0 ht_ldpc=1 vht_sgi80=0 vht_ldpc=0 max_mpdu=3895 ampdu_exp=3 mpdu_density=6 ht_mcs=255\n",
                "del 02:11:22:33:44:55\n",
                "add 02:11:22:33:44:55 ht=1 vht=1 nss=2 bw=80 mcs=9 sgi=1 ldpc=1 ht_bw40=1 ht_sgi20=1 ht_sgi40=1 ht_ldpc=1 vht_sgi80=1 vht_ldpc=1 max_mpdu=11454 ampdu_exp=7 mpdu_density=7 ht_mcs=65535\n",
                "clear\n",
            ]
        );
    }

    #[test]
    fn test_peer_ctl_recovers_after_cooldown_without_reannouncement() {
        let log = Arc::new(Mutex::new(Vec::new()));
        let sync = KernelPeerSync::spawn_with_sink_and_backoff_and_cooldown(
            FlakySink {
                log: Arc::clone(&log),
                fail_remaining: 6,
            },
            &[1, 1, 1, 1, 1],
            Some(25),
        );

        let m1 = MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x12]);
        sync.peer_up(m1);
        sync.wait_until_idle();

        // Exhausted fast retry budget after 6 attempts.
        assert!(!sync.is_peer_ready(m1));
        assert_eq!(log.lock().unwrap().len(), 6);

        // Even with NO subsequent peer_up / peer_caps change, cooldown re-arms recovery.
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline && !sync.is_peer_ready(m1) {
            thread::sleep(Duration::from_millis(10));
        }
        assert!(sync.is_peer_ready(m1));
        drop(sync);
        assert_eq!(log.lock().unwrap().len(), 8); // 6 failed + 1 recovered add + 1 clear
    }
}
