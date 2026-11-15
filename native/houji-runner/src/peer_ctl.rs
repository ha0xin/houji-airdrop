#[cfg(any(target_os = "android", test))]
use owly_awdl::MacAddr;
use std::collections::BTreeSet;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Sender};
use std::thread::{self, JoinHandle};

const SYSFS_MULTI_FLAG: &str = "/sys/module/qca_cld3_kiwi_v2/parameters/pt_remote_multi";
const SYSFS_PEER_CTL: &str = "/sys/module/qca_cld3_kiwi_v2/parameters/pt_remote_peer_ctl";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PeerOp {
    Add(MacAddr),
    Del(MacAddr),
    Shutdown,
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
/// stall slot scheduling.
pub struct KernelPeerSync {
    tx: Option<Sender<PeerOp>>,
    worker: Option<JoinHandle<()>>,
}

impl KernelPeerSync {
    pub fn disabled() -> Self {
        Self {
            tx: None,
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
        let (tx, rx) = mpsc::channel();
        let worker = thread::Builder::new()
            .name("owly-pt-peer-ctl".to_string())
            .spawn(move || worker_loop(rx, sink))
            .expect("spawn owly-pt-peer-ctl thread");
        Self {
            tx: Some(tx),
            worker: Some(worker),
        }
    }

    pub fn is_enabled(&self) -> bool {
        self.tx.is_some()
    }

    pub fn peer_up(&self, mac: MacAddr) {
        if let Some(tx) = &self.tx {
            let _ = tx.send(PeerOp::Add(mac));
        }
    }

    pub fn peer_down(&self, mac: MacAddr) {
        if let Some(tx) = &self.tx {
            let _ = tx.send(PeerOp::Del(mac));
        }
    }
}

impl Drop for KernelPeerSync {
    fn drop(&mut self) {
        if let Some(tx) = self.tx.take() {
            let _ = tx.send(PeerOp::Shutdown);
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

fn worker_loop<S: CtlSink>(rx: mpsc::Receiver<PeerOp>, mut sink: S) {
    let mut active = BTreeSet::<MacAddr>::new();

    while let Ok(op) = rx.recv() {
        match op {
            PeerOp::Add(mac) => {
                if active.contains(&mac) {
                    continue;
                }
                let cmd = format!("add {mac}\n");
                match sink.write_cmd(&cmd) {
                    Ok(()) => {
                        active.insert(mac);
                        println!("OWLY_PT_PEER_CTL op=add active={} rc=ok", active.len());
                    }
                    Err(e) => {
                        println!("OWLY_PT_PEER_CTL op=add active={} rc=err({e})", active.len());
                    }
                }
            }
            PeerOp::Del(mac) => {
                if !active.contains(&mac) {
                    continue;
                }
                let cmd = format!("del {mac}\n");
                match sink.write_cmd(&cmd) {
                    Ok(()) => {
                        active.remove(&mac);
                        println!("OWLY_PT_PEER_CTL op=del active={} rc=ok", active.len());
                    }
                    Err(e) => {
                        println!("OWLY_PT_PEER_CTL op=del active={} rc=err({e})", active.len());
                    }
                }
            }
            PeerOp::Shutdown => break,
        }
    }

    if !active.is_empty() {
        match sink.write_cmd("clear\n") {
            Ok(()) => {
                active.clear();
                println!("OWLY_PT_PEER_CTL op=clear active=0 rc=ok");
            }
            Err(e) => {
                println!(
                    "OWLY_PT_PEER_CTL op=clear active={} rc=err({e})",
                    active.len()
                );
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};
    use std::time::{SystemTime, UNIX_EPOCH};

    struct VecSink(Arc<Mutex<Vec<String>>>);

    impl CtlSink for VecSink {
        fn write_cmd(&mut self, cmd: &str) -> io::Result<()> {
            self.0.lock().unwrap().push(cmd.to_string());
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
        sync.peer_down(m1);
        sync.peer_down(m3); // unknown peer skipped
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

        sync.peer_up(MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x01]));
        sync.peer_down(MacAddr([0x02, 0x11, 0x22, 0x33, 0x44, 0x01]));
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
        sync.peer_down(m1);
        drop(sync);

        assert_eq!(
            log.lock().unwrap().clone(),
            vec!["add 02:11:22:33:44:01\n", "del 02:11:22:33:44:01\n",]
        );
        let _ = fs::remove_dir_all(&dir);
    }
}
