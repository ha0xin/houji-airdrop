use std::collections::{BTreeMap, VecDeque};
use std::ffi::CStr;
use std::fs::{self, OpenOptions};
use std::io;
use std::mem::{size_of, zeroed};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::ptr;
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, Instant};

use owly_awdl::Time;
use owly_awdl::driver::{self, Air, AirRx, Netif, Received};
use owly_awdl::frame::Frame;
use owly_awdl::{
    Channel, Config, Ipv6Packet, MacAddr, PeerCaps, PeerInfo, Presence, RadioConstraints,
};
use tokio::io::unix::AsyncFd;
use tokio::signal::unix::{SignalKind, signal};
use tokio::sync::{mpsc, watch};
use tokio::task::JoinHandle;

use crate::{
    net_util::{NeighBackend, NeighWorker, RX_SKIP_LIMIT, SkipBudget},
    peer_ctl::KernelPeerSync,
    radiotap,
    rx_gap::RxGaps,
    vnet,
};

const MONITOR: &CStr = c"wonder0";
const TUN: &str = "owly0";
const TAP: &str = "xos0";
const TAP_CSTR: &CStr = c"xos0";
const PACKET_BUFFER: usize = 16 * 1024;
const TAP_VNET_BUFFER: usize = 72 * 1024;
const TX_POOL_INIT: usize = 128;
const TX_POOL_MAX: usize = 512;
const RX_BATCH_CAP: usize = 32;
const TX_BATCH_CAP: usize = 32;
const TP_RING_FRAME_SIZE: usize = 16 * 1024;
const TP_RING_FRAME_NR: usize = 2048;
const ETH_P_ALL: u16 = 3;
const PACKET_RX_RING: libc::c_int = 5; // linux/if_packet.h
const PACKET_STATISTICS: libc::c_int = 6; // linux/if_packet.h
const PACKET_VERSION: libc::c_int = 10; // linux/if_packet.h
const PACKET_IGNORE_OUTGOING: libc::c_int = 23; // linux/if_packet.h
const TPACKET_V2: libc::c_int = 1;
const TP_STATUS_KERNEL: u32 = 0;
const TP_STATUS_USER: u32 = 1 << 0;
const MONITOR_RCVBUF_BYTES: libc::c_int = 8 * 1024 * 1024;
const MONITOR_SNDBUF_BYTES: libc::c_int = 4 * 1024 * 1024;

#[repr(C)]
struct TpacketReq {
    tp_block_size: libc::c_uint,
    tp_block_nr: libc::c_uint,
    tp_frame_size: libc::c_uint,
    tp_frame_nr: libc::c_uint,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct Tpacket2Hdr {
    tp_status: u32,
    tp_len: u32,
    tp_snaplen: u32,
    tp_mac: u16,
    tp_net: u16,
    tp_sec: u32,
    tp_nsec: u32,
    tp_vlan_tci: u16,
    tp_vlan_tpid: u16,
    tp_padding: [u8; 4],
}

#[repr(C)]
#[derive(Default)]
struct TpacketStats {
    tp_packets: libc::c_uint,
    tp_drops: libc::c_uint,
}

#[derive(Default)]
struct DropCount(u64);

impl DropCount {
    fn record(&mut self, operation: &str, error: &dyn std::fmt::Display) {
        self.0 += 1;
        if self.0 == 1 || self.0 % 1024 == 0 {
            tracing::warn!(operation, %error, drops = self.0);
        }
    }
}

fn syscall(result: libc::ssize_t) -> io::Result<usize> {
    if result < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(result as usize)
    }
}

fn set_socket_buf(
    raw: libc::c_int,
    force_opt: libc::c_int,
    std_opt: libc::c_int,
    bytes: libc::c_int,
) -> io::Result<libc::c_int> {
    let rc = unsafe {
        libc::setsockopt(
            raw,
            libc::SOL_SOCKET,
            force_opt,
            (&raw const bytes).cast(),
            size_of::<libc::c_int>() as _,
        )
    };
    if rc < 0 {
        syscall(
            unsafe {
                libc::setsockopt(
                    raw,
                    libc::SOL_SOCKET,
                    std_opt,
                    (&raw const bytes).cast(),
                    size_of::<libc::c_int>() as _,
                )
            }
            as libc::ssize_t,
        )?;
    }
    let mut actual: libc::c_int = 0;
    let mut len: libc::socklen_t = size_of::<libc::c_int>() as _;
    syscall(
        unsafe {
            libc::getsockopt(
                raw,
                libc::SOL_SOCKET,
                std_opt,
                (&raw mut actual).cast(),
                &raw mut len,
            )
        }
        as libc::ssize_t,
    )?;
    Ok(actual)
}

fn register(fd: OwnedFd) -> io::Result<AsyncFd<OwnedFd>> {
    // Both callers set O_NONBLOCK before registration. OwnedFd closes
    // only after AsyncFd deregisters it.
    unsafe { AsyncFd::register(fd) }.map_err(Into::into)
}

fn set_sock_timeval(raw: libc::c_int, opt: libc::c_int, dur: Duration) -> io::Result<()> {
    let timeout = libc::timeval {
        tv_sec: dur.as_secs() as _,
        tv_usec: dur.subsec_micros() as _,
    };
    syscall(
        unsafe {
            libc::setsockopt(
                raw,
                libc::SOL_SOCKET,
                opt,
                (&raw const timeout).cast(),
                size_of::<libc::timeval>() as _,
            )
        } as libc::ssize_t,
    )?;
    Ok(())
}

fn write_packet(fd: &OwnedFd, packet: &[u8]) -> io::Result<()> {
    loop {
        // The fd stays open and the buffer lives for the duration of write.
        match syscall(unsafe { libc::write(fd.as_raw_fd(), packet.as_ptr().cast(), packet.len()) })
        {
            Ok(n) if n == packet.len() => return Ok(()),
            Ok(_) => {
                return Err(io::Error::new(
                    io::ErrorKind::WriteZero,
                    "short packet write",
                ));
            }
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        }
    }
}

fn write_iovec(fd: &OwnedFd, iov: &[libc::iovec], expected: usize, label: &str) -> io::Result<()> {
    loop {
        // Buffers stay live for the synchronous writev syscall.
        match syscall(unsafe { libc::writev(fd.as_raw_fd(), iov.as_ptr(), iov.len() as _) }) {
            Ok(n) if n == expected => return Ok(()),
            Ok(_) => {
                return Err(io::Error::new(io::ErrorKind::WriteZero, label));
            }
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        }
    }
}

fn write_tap_ethernet(
    fd: &OwnedFd,
    vnet_hdr: bool,
    hdr: &[u8; 14],
    payload: &[u8],
) -> io::Result<()> {
    if vnet_hdr {
        let total = vnet::VNET_HDR_LEN + hdr.len() + payload.len();
        let iov = [
            libc::iovec {
                iov_base: vnet::ZERO_VNET_HDR.as_ptr().cast_mut().cast(),
                iov_len: vnet::VNET_HDR_LEN,
            },
            libc::iovec {
                iov_base: hdr.as_ptr().cast_mut().cast(),
                iov_len: hdr.len(),
            },
            libc::iovec {
                iov_base: payload.as_ptr().cast_mut().cast(),
                iov_len: payload.len(),
            },
        ];
        write_iovec(fd, &iov, total, "short tap packet write")
    } else {
        let total = hdr.len() + payload.len();
        let iov = [
            libc::iovec {
                iov_base: hdr.as_ptr().cast_mut().cast(),
                iov_len: hdr.len(),
            },
            libc::iovec {
                iov_base: payload.as_ptr().cast_mut().cast(),
                iov_len: payload.len(),
            },
        ];
        write_iovec(fd, &iov, total, "short tap packet write")
    }
}

fn write_tap_raw(fd: &OwnedFd, vnet_hdr: bool, eth_frame: &[u8]) -> io::Result<()> {
    if vnet_hdr {
        let total = vnet::VNET_HDR_LEN + eth_frame.len();
        let iov = [
            libc::iovec {
                iov_base: vnet::ZERO_VNET_HDR.as_ptr().cast_mut().cast(),
                iov_len: vnet::VNET_HDR_LEN,
            },
            libc::iovec {
                iov_base: eth_frame.as_ptr().cast_mut().cast(),
                iov_len: eth_frame.len(),
            },
        ];
        write_iovec(fd, &iov, total, "short tap raw write")
    } else {
        write_packet(fd, eth_frame)
    }
}

fn rx_gaps_enabled() -> bool {
    std::env::var_os("HOUJI_OWLY_RX_GAPS").is_some_and(|v| {
        matches!(
            v.to_str(),
            Some("1" | "true" | "TRUE" | "yes" | "YES" | "on" | "ON")
        )
    })
}

struct TpacketRxRing {
    ptr: *mut u8,
    len: usize,
    frame_size: usize,
    frame_nr: usize,
    cursor: usize,
}

// SAFETY: TpacketRxRing owns a unique MAP_SHARED mapping for the socket lifetime
// and is only mutated on the single `owly-awdl` driver thread after open().
unsafe impl Send for TpacketRxRing {}

impl TpacketRxRing {
    fn try_map(raw: libc::c_int) -> io::Result<Self> {
        let ver = TPACKET_V2;
        syscall(unsafe {
            libc::setsockopt(
                raw,
                libc::SOL_PACKET,
                PACKET_VERSION,
                (&raw const ver).cast(),
                size_of::<libc::c_int>() as _,
            )
        } as libc::ssize_t)?;

        let req = TpacketReq {
            tp_block_size: TP_RING_FRAME_SIZE as _,
            tp_block_nr: TP_RING_FRAME_NR as _,
            tp_frame_size: TP_RING_FRAME_SIZE as _,
            tp_frame_nr: TP_RING_FRAME_NR as _,
        };
        syscall(unsafe {
            libc::setsockopt(
                raw,
                libc::SOL_PACKET,
                PACKET_RX_RING,
                (&raw const req).cast(),
                size_of::<TpacketReq>() as _,
            )
        } as libc::ssize_t)?;

        let len = TP_RING_FRAME_SIZE * TP_RING_FRAME_NR;
        let ptr = unsafe {
            libc::mmap(
                ptr::null_mut(),
                len,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                raw,
                0,
            )
        };
        if ptr == libc::MAP_FAILED {
            return Err(io::Error::last_os_error());
        }
        Ok(Self {
            ptr: ptr.cast(),
            len,
            frame_size: TP_RING_FRAME_SIZE,
            frame_nr: TP_RING_FRAME_NR,
            cursor: 0,
        })
    }

    fn slot_status_ptr(&self, idx: usize) -> *const AtomicU32 {
        // SAFETY: `idx < frame_nr`, each frame slot starts at a multiple of 16 KiB (4-byte aligned).
        unsafe { self.ptr.add(idx * self.frame_size).cast::<AtomicU32>() }
    }

    fn peek_slice(&self) -> Option<&[u8]> {
        // SAFETY: Acquire load pairs with kernel Release store setting TP_STATUS_USER.
        let status = unsafe { (*self.slot_status_ptr(self.cursor)).load(Ordering::Acquire) };
        if (status & TP_STATUS_USER) == 0 {
            return None;
        }
        let base = self.cursor * self.frame_size;
        let hdr = unsafe { ptr::read(self.ptr.add(base).cast::<Tpacket2Hdr>()) };
        let mac = usize::from(hdr.tp_mac);
        let snaplen = hdr.tp_snaplen as usize;
        let Some(end) = mac.checked_add(snaplen) else {
            return Some(&[]);
        };
        if end > self.frame_size {
            return Some(&[]);
        }
        Some(unsafe { std::slice::from_raw_parts(self.ptr.add(base + mac), snaplen) })
    }

    fn release_slot(&mut self) {
        // SAFETY: Release store hands slot ownership back to kernel after all slice reads finish.
        unsafe {
            (*self.slot_status_ptr(self.cursor)).store(TP_STATUS_KERNEL, Ordering::Release);
        }
        self.cursor = (self.cursor + 1) % self.frame_nr;
    }
}

impl Drop for TpacketRxRing {
    fn drop(&mut self) {
        unsafe {
            libc::munmap(self.ptr.cast(), self.len);
        }
    }
}

struct RecvmmsgRx {
    bufs: Vec<Vec<u8>>,
    lens: [usize; RX_BATCH_CAP],
    head: usize,
    tail: usize,
}

impl RecvmmsgRx {
    fn new() -> Self {
        Self {
            bufs: (0..RX_BATCH_CAP).map(|_| vec![0; PACKET_BUFFER]).collect(),
            lens: [0; RX_BATCH_CAP],
            head: 0,
            tail: 0,
        }
    }

    fn fill(&mut self, raw: libc::c_int) -> io::Result<usize> {
        let mut iovs = [libc::iovec {
            iov_base: ptr::null_mut(),
            iov_len: 0,
        }; RX_BATCH_CAP];
        let mut msgs: [libc::mmsghdr; RX_BATCH_CAP] = unsafe { zeroed() };
        for (i, buf) in self.bufs.iter_mut().enumerate() {
            iovs[i].iov_base = buf.as_mut_ptr().cast();
            iovs[i].iov_len = buf.len();
            msgs[i].msg_hdr.msg_iov = &raw mut iovs[i];
            msgs[i].msg_hdr.msg_iovlen = 1 as _;
        }
        let n = syscall(unsafe {
            libc::recvmmsg(
                raw,
                msgs.as_mut_ptr(),
                RX_BATCH_CAP as _,
                libc::MSG_TRUNC | libc::MSG_DONTWAIT,
                ptr::null(),
            )
        } as libc::ssize_t)?;
        for i in 0..n {
            self.lens[i] = (msgs[i].msg_len as usize).min(self.bufs[i].len());
        }
        self.head = 0;
        self.tail = n;
        Ok(n)
    }

    fn peek_slice(&self) -> Option<&[u8]> {
        if self.head < self.tail {
            let idx = self.head;
            Some(&self.bufs[idx][..self.lens[idx]])
        } else {
            None
        }
    }

    fn release_slot(&mut self) {
        self.head += 1;
    }
}

enum RxBackend {
    Ring(TpacketRxRing),
    Recvmmsg(RecvmmsgRx),
}

struct RxTelemetry {
    peer: MacAddr,
    own: MacAddr,
    rx_gaps: Option<RxGaps>,
    rx_invalid: DropCount,
    rx_addba_resp_reported: u8,
}

impl RxTelemetry {
    fn report_first_rx_addba_resp(&mut self, bytes: &[u8]) {
        if self.rx_addba_resp_reported >= 8 || bytes.len() < 33 || bytes[0] != 0xd0 {
            return;
        }
        if bytes[24] == 3 && bytes[25] == 1 {
            let token = bytes[26];
            let status = u16::from_le_bytes([bytes[27], bytes[28]]);
            let params = u16::from_le_bytes([bytes[29], bytes[30]]);
            let win = (params >> 6) & 0x3ff;
            println!(
                "OWLY_TX_ADDBA_RESP_RCVD token={token} status={status} params=0x{params:04x} win={win}"
            );
            self.rx_addba_resp_reported = self.rx_addba_resp_reported.saturating_add(1);
        }
    }

    fn validate<'a>(&mut self, packet: &'a [u8]) -> Option<radiotap::Rx<'a>> {
        let Some(received) = radiotap::parse(packet) else {
            self.rx_invalid
                .record("air rx", &"invalid or truncated radiotap frame");
            return None;
        };
        // The transmitter is Address 2 of every supported AWDL frame.
        if received.freq.is_some_and(|freq| freq != 5745)
            || (self.peer.0 != [0; 6]
                && received.frame.get(10..16) != Some(self.peer.0.as_slice()))
        {
            return None;
        }
        Some(received)
    }

    fn accept(&mut self, received: &radiotap::Rx<'_>) {
        let raw = received.frame;
        if let Some(rx_gaps) = &mut self.rx_gaps
            && raw.len() >= 1536
            && raw[0] == 0x88
            && raw[1] & 0xc7 == 0
            && raw[22] & 0x0f == 0
            && raw[4..10] == self.own.0
            && raw[16..22] == [0x00, 0x25, 0, 0xff, 0x94, 0x73]
        {
            rx_gaps.record(received.tsft_us, Instant::now());
        }
        self.report_first_rx_addba_resp(raw);
    }
}

struct Monitor {
    fd: AsyncFd<OwnedFd>,
    rx: RxBackend,
    rx_tel: RxTelemetry,
    tx_fatal: DropCount,
    tx_backpressure: u64,
    tx_submitted: u64,
    psf_reported: bool,
    mif_reported: bool,
    tx_addba_reported: u8,
    tx_amsdu_mpdu: u64,
    tx_amsdu_msdu: u64,
}

impl Monitor {
    fn open(peer: MacAddr, own: MacAddr) -> io::Result<Self> {
        // The name is a NUL-terminated C string.
        let index = unsafe { libc::if_nametoindex(MONITOR.as_ptr()) };
        if index == 0 {
            return Err(io::Error::last_os_error());
        }
        let protocol = ETH_P_ALL.to_be();
        // A fresh AF_PACKET socket can be owned by this process if it succeeds.
        let raw = unsafe {
            libc::socket(
                libc::AF_PACKET,
                libc::SOCK_RAW | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
                i32::from(protocol),
            )
        };
        syscall(raw as libc::ssize_t)?;
        let fd = unsafe { OwnedFd::from_raw_fd(raw) };
        let rcvbuf = set_socket_buf(raw, libc::SO_RCVBUFFORCE, libc::SO_RCVBUF, MONITOR_RCVBUF_BYTES)?;
        let sndbuf = set_socket_buf(raw, libc::SO_SNDBUFFORCE, libc::SO_SNDBUF, MONITOR_SNDBUF_BYTES)?;
        println!("OWLY_AF_PACKET_BUF rcvbuf={rcvbuf} sndbuf={sndbuf}");
        // Attach TPACKET_V2 RX ring before bind() so sk_receive_queue cannot race with ring setup.
        let rx = match TpacketRxRing::try_map(raw) {
            Ok(ring) => {
                println!(
                    "OWLY_AF_PACKET_RX mode=tpacket_v2 frame_size={TP_RING_FRAME_SIZE} frames={TP_RING_FRAME_NR}"
                );
                RxBackend::Ring(ring)
            }
            Err(error) => {
                tracing::warn!(%error, "TPACKET_V2 ring setup failed, falling back to recvmmsg");
                println!("OWLY_AF_PACKET_RX mode=recvmmsg batch={RX_BATCH_CAP}");
                RxBackend::Recvmmsg(RecvmmsgRx::new())
            }
        };
        let addr = libc::sockaddr_ll {
            sll_family: libc::AF_PACKET as _,
            sll_protocol: protocol,
            sll_ifindex: index as _,
            sll_hatype: 0,
            sll_pkttype: 0,
            sll_halen: 0,
            sll_addr: [0; 8],
        };
        // bind copies the correctly sized AF_PACKET address from the stack.
        syscall(
            unsafe {
                libc::bind(
                    raw,
                    (&raw const addr).cast(),
                    size_of::<libc::sockaddr_ll>() as _,
                )
            }
            as libc::ssize_t,
        )?;
        let enabled: libc::c_int = 1;
        // Skip locally injected frames. The RX source filter below is independent.
        syscall(
            unsafe {
                libc::setsockopt(
                    raw,
                    libc::SOL_PACKET,
                    PACKET_IGNORE_OUTGOING,
                    (&raw const enabled).cast(),
                    size_of::<libc::c_int>() as _,
                )
            }
            as libc::ssize_t,
        )?;
        Ok(Self {
            fd: register(fd)?,
            rx,
            rx_tel: RxTelemetry {
                peer,
                own,
                rx_gaps: rx_gaps_enabled().then(RxGaps::default),
                rx_invalid: DropCount::default(),
                rx_addba_resp_reported: 0,
            },
            tx_fatal: DropCount::default(),
            tx_backpressure: 0,
            tx_submitted: 0,
            psf_reported: false,
            mif_reported: false,
            tx_addba_reported: 0,
            tx_amsdu_mpdu: 0,
            tx_amsdu_msdu: 0,
        })
    }

    fn report_first_tx_addba(&mut self, bytes: &[u8]) {
        if self.tx_addba_reported >= 8 || bytes.len() < 33 || bytes[0] != 0xd0 {
            return;
        }
        if bytes[24] == 3 && bytes[25] == 0 {
            let token = bytes[26];
            let params = u16::from_le_bytes([bytes[27], bytes[28]]);
            let ssn = u16::from_le_bytes([bytes[31], bytes[32]]) >> 4;
            println!("OWLY_TX_ADDBA_REQ_SENT token={token} params=0x{params:04x} ssn={ssn}");
            self.tx_addba_reported = self.tx_addba_reported.saturating_add(1);
        }
    }

    fn report_first_submitted_action(&mut self, bytes: &[u8]) {
        use owly_awdl::{frame, tlv};

        if (self.psf_reported && self.mif_reported) || bytes.first() != Some(&0xd0) {
            return;
        }
        let Ok(frame::Received::Action { subtype, tlvs, .. }) = frame::parse(bytes) else {
            return;
        };
        let (label, reported) = match subtype {
            frame::subtype::PSF if !self.psf_reported => ("PSF", &mut self.psf_reported),
            frame::subtype::MIF if !self.mif_reported => ("MIF", &mut self.mif_reported),
            _ => return,
        };
        let mut ht = None;
        let mut container = None;
        for entry in tlv::tlvs(tlvs) {
            let Ok(entry) = entry else { return };
            if entry.kind == tlv::kind::HT_CAPABILITIES {
                ht = Some(entry.value);
            } else if entry.kind == tlv::kind::IEEE80211_CONTAINER
                && container.replace(entry.value).is_some()
            {
                return;
            }
        }
        let hex_or_absent = |opt: Option<&[u8]>| {
            opt.map_or_else(
                || "absent".to_owned(),
                |value| value.iter().map(|byte| format!("{byte:02x}")).collect::<String>(),
            )
        };
        println!("OWLY_{label}_SUBMITTED_HT_CAPABILITIES={}", hex_or_absent(ht));
        println!(
            "OWLY_{label}_SUBMITTED_VHT_CONTAINER={}",
            hex_or_absent(container)
        );
        *reported = true;
    }

    fn note_tx_sent(&mut self, bytes: &[u8]) {
        self.tx_submitted += 1;
        self.report_first_submitted_action(bytes);
        self.report_first_tx_addba(bytes);
        if let Some(msdus) = count_amsdu_subframes(bytes) {
            self.tx_amsdu_mpdu += 1;
            self.tx_amsdu_msdu += msdus;
        }
    }

    fn send_one_raw(fd: &OwnedFd, frame: &Frame) -> io::Result<()> {
        let mut hdr = [0u8; 16];
        let hdr_len = radiotap::write_tx_header(frame.rate, frame.no_ack, &mut hdr);
        let total = hdr_len + frame.bytes.len();
        let iov = [
            libc::iovec {
                iov_base: hdr.as_ptr().cast_mut().cast(),
                iov_len: hdr_len,
            },
            libc::iovec {
                iov_base: frame.bytes.as_ptr().cast_mut().cast(),
                iov_len: frame.bytes.len(),
            },
        ];
        write_iovec(fd, &iov, total, "short air frame write")
    }
}

impl Drop for Monitor {
    fn drop(&mut self) {
        let mut stats = TpacketStats::default();
        let mut len: libc::socklen_t = size_of::<TpacketStats>() as _;
        let stats_ok = unsafe {
            libc::getsockopt(
                self.fd.get_ref().as_raw_fd(),
                libc::SOL_PACKET,
                PACKET_STATISTICS,
                (&raw mut stats).cast(),
                &raw mut len,
            )
        } == 0;
        if let Some(rx_gaps) = &self.rx_tel.rx_gaps {
            rx_gaps.report();
        }
        tracing::info!(air_rx_invalid = self.rx_tel.rx_invalid.0,
                       air_rx_kernel_packets = if stats_ok { stats.tp_packets as u64 } else { u64::MAX },
                       air_rx_kernel_drops = if stats_ok { stats.tp_drops as u64 } else { u64::MAX },
                       air_tx_fatal = self.tx_fatal.0,
                       air_tx_backpressure = self.tx_backpressure, air_tx_submitted = self.tx_submitted,
                       air_tx_amsdu_mpdu = self.tx_amsdu_mpdu, air_tx_amsdu_msdu = self.tx_amsdu_msdu,
                       "monitor totals");
    }
}

fn count_amsdu_subframes(bytes: &[u8]) -> Option<u64> {
    if bytes.first() != Some(&0x88) || !bytes.get(24).is_some_and(|flags| flags & 0x80 != 0) {
        return None;
    }
    let mut rest = bytes.get(26..)?;
    let mut count = 0u64;
    while !rest.is_empty() {
        let len = u16::from_be_bytes([*rest.get(12)?, *rest.get(13)?]) as usize;
        let next = 14 + len;
        if rest.len() < next {
            return None;
        }
        count += 1;
        rest = &rest[next..];
        if !rest.is_empty() {
            let pad = (4 - (next % 4)) % 4;
            rest = rest.get(pad..)?;
        }
    }
    (count > 0).then_some(count)
}

impl Air for Monitor {
    async fn recv(&mut self) -> io::Result<Received> {
        let mut out = None;
        self.wait_or_recv().await?;
        self.drain_rx(1, |rssi, bytes| {
            out = Some(Received {
                bytes: bytes.to_vec(),
                rssi,
            });
        })?;
        if let Some(r) = out {
            return Ok(r);
        }
        Err(io::Error::new(io::ErrorKind::WouldBlock, "no frame ready"))
    }

    async fn wait_or_recv(&mut self) -> io::Result<AirRx> {
        let mut skip = SkipBudget::new(RX_SKIP_LIMIT);
        loop {
            match &mut self.rx {
                RxBackend::Ring(ring) => {
                    let mut skipped = 0;
                    while let Some(raw) = ring.peek_slice() {
                        if self.rx_tel.validate(raw).is_some() {
                            return Ok(AirRx::Ready);
                        }
                        ring.release_slot();
                        skipped += 1;
                        if skipped >= RX_SKIP_LIMIT {
                            break;
                        }
                    }
                    if skipped >= RX_SKIP_LIMIT {
                        skip.note_skip().await;
                        continue;
                    }
                }
                RxBackend::Recvmmsg(batch) => {
                    let mut skipped = 0;
                    while let Some(raw) = batch.peek_slice() {
                        if self.rx_tel.validate(raw).is_some() {
                            return Ok(AirRx::Ready);
                        }
                        batch.release_slot();
                        skipped += 1;
                        if skipped >= RX_SKIP_LIMIT {
                            break;
                        }
                    }
                    if skipped >= RX_SKIP_LIMIT {
                        skip.note_skip().await;
                        continue;
                    }
                    match batch.fill(self.fd.get_ref().as_raw_fd()) {
                        Ok(0) => return Err(io::ErrorKind::UnexpectedEof.into()),
                        Ok(_) => continue,
                        Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                        Err(e) if e.kind() != io::ErrorKind::WouldBlock => return Err(e),
                        Err(_) => {}
                    }
                }
            }
            let mut guard = self.fd.readable().await?;
            guard.clear_ready();
        }
    }

    fn drain_rx<F>(&mut self, limit: usize, mut on_frame: F) -> io::Result<()>
    where
        F: FnMut(Option<i8>, &[u8]),
    {
        let mut delivered = 0usize;
        let mut skipped = 0usize;
        match &mut self.rx {
            RxBackend::Ring(ring) => {
                while delivered < limit && skipped < RX_SKIP_LIMIT {
                    let Some(raw) = ring.peek_slice() else {
                        break;
                    };
                    if let Some(received) = self.rx_tel.validate(raw) {
                        self.rx_tel.accept(&received);
                        on_frame(received.rssi, received.frame);
                        delivered += 1;
                        skipped = 0;
                    } else {
                        skipped += 1;
                    }
                    ring.release_slot();
                }
            }
            RxBackend::Recvmmsg(batch) => {
                let raw_fd = self.fd.get_ref().as_raw_fd();
                while delivered < limit && skipped < RX_SKIP_LIMIT {
                    if batch.peek_slice().is_none() {
                        match batch.fill(raw_fd) {
                            Ok(0) => return Err(io::ErrorKind::UnexpectedEof.into()),
                            Ok(_) => {}
                            Err(e) if e.kind() == io::ErrorKind::WouldBlock => break,
                            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                            Err(e) => return Err(e),
                        }
                    }
                    let Some(raw) = batch.peek_slice() else {
                        break;
                    };
                    if let Some(received) = self.rx_tel.validate(raw) {
                        self.rx_tel.accept(&received);
                        on_frame(received.rssi, received.frame);
                        delivered += 1;
                        skipped = 0;
                    } else {
                        skipped += 1;
                    }
                    batch.release_slot();
                }
            }
        }
        Ok(())
    }

    fn send(&mut self, frame: Frame) -> Result<(), Frame> {
        match Self::send_one_raw(self.fd.get_ref(), &frame) {
            Ok(()) => {
                self.note_tx_sent(&frame.bytes);
                Ok(())
            }
            Err(e) if matches!(e.raw_os_error(), Some(libc::EAGAIN) | Some(libc::ENOBUFS)) => {
                self.tx_backpressure += 1;
                Err(frame)
            }
            Err(e) => {
                self.tx_fatal.record("air tx", &e);
                Ok(())
            }
        }
    }

    fn supports_batch(&self) -> bool {
        true
    }

    fn send_batch(
        &mut self,
        frames: &mut VecDeque<(Time, Frame)>,
        recycled: &mut Vec<Vec<u8>>,
    ) -> (usize, bool) {
        let mut total_sent = 0usize;
        let raw_fd = self.fd.get_ref().as_raw_fd();
        while !frames.is_empty() {
            let batch_len = frames.len().min(TX_BATCH_CAP);
            let mut hdrs = [[0u8; 16]; TX_BATCH_CAP];
            let mut iovs = [[libc::iovec {
                iov_base: ptr::null_mut(),
                iov_len: 0,
            }; 2]; TX_BATCH_CAP];
            let mut msgs: [libc::mmsghdr; TX_BATCH_CAP] = unsafe { zeroed() };

            for (i, (_, frame)) in frames.iter().take(batch_len).enumerate() {
                let hdr_len = radiotap::write_tx_header(frame.rate, frame.no_ack, &mut hdrs[i]);
                iovs[i][0].iov_base = hdrs[i].as_ptr().cast_mut().cast();
                iovs[i][0].iov_len = hdr_len;
                iovs[i][1].iov_base = frame.bytes.as_ptr().cast_mut().cast();
                iovs[i][1].iov_len = frame.bytes.len();
                msgs[i].msg_hdr.msg_iov = iovs[i].as_mut_ptr();
                msgs[i].msg_hdr.msg_iovlen = 2 as _;
            }

            let res = syscall(unsafe {
                libc::sendmmsg(raw_fd, msgs.as_mut_ptr(), batch_len as _, 0)
            } as libc::ssize_t);
            match res {
                Ok(n) if n > 0 => {
                    for _ in 0..n {
                        if let Some((_, mut frame)) = frames.pop_front() {
                            self.note_tx_sent(&frame.bytes);
                            frame.bytes.clear();
                            recycled.push(frame.bytes);
                            total_sent += 1;
                        }
                    }
                    if n < batch_len {
                        // Kernel stopped at frame `n`; probe head to distinguish backpressure vs fatal.
                        if let Some((_, head)) = frames.front() {
                            match Self::send_one_raw(self.fd.get_ref(), head) {
                                Ok(()) => {
                                    let (_, mut frame) = frames.pop_front().unwrap();
                                    self.note_tx_sent(&frame.bytes);
                                    frame.bytes.clear();
                                    recycled.push(frame.bytes);
                                    total_sent += 1;
                                }
                                Err(e)
                                    if matches!(
                                        e.raw_os_error(),
                                        Some(libc::EAGAIN) | Some(libc::ENOBUFS)
                                    ) =>
                                {
                                    self.tx_backpressure += 1;
                                    return (total_sent, true);
                                }
                                Err(e) => {
                                    self.tx_fatal.record("air tx", &e);
                                    let (_, mut frame) = frames.pop_front().unwrap();
                                    frame.bytes.clear();
                                    recycled.push(frame.bytes);
                                }
                            }
                        }
                    }
                }
                Ok(_) => break,
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(e)
                    if matches!(e.raw_os_error(), Some(libc::EAGAIN) | Some(libc::ENOBUFS)) =>
                {
                    self.tx_backpressure += 1;
                    return (total_sent, true);
                }
                Err(e) => {
                    self.tx_fatal.record("air tx", &e);
                    if let Some((_, mut frame)) = frames.pop_front() {
                        frame.bytes.clear();
                        recycled.push(frame.bytes);
                    }
                }
            }
        }
        (total_sent, false)
    }
}

fn open_netif_fd(name: &str, flags: libc::c_short) -> io::Result<OwnedFd> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
        .open("/dev/net/tun")?;
    // ifreq is a C wire struct. TUNSETIFF consumes the name and flags.
    let mut request: libc::ifreq = unsafe { zeroed() };
    for (slot, byte) in request.ifr_name.iter_mut().zip(name.bytes()) {
        *slot = byte as _;
    }
    request.ifr_ifru.ifru_flags = flags;
    syscall(unsafe { libc::ioctl(file.as_raw_fd(), libc::TUNSETIFF as _, &mut request) } as libc::ssize_t)?;
    Ok(file.into())
}

async fn read_netif_frame(fd: &AsyncFd<OwnedFd>, buf: &mut [u8]) -> io::Result<usize> {
    loop {
        let mut ready = fd.readable().await?;
        let read = ready.try_io(|fd| {
            // The fd stays open and the RX buffer is valid for its full length.
            syscall(unsafe { libc::read(fd.get_ref().as_raw_fd(), buf.as_mut_ptr().cast(), buf.len()) })
        });
        match read {
            Ok(Ok(0)) => return Err(io::ErrorKind::UnexpectedEof.into()),
            Ok(Ok(n)) => return Ok(n),
            Ok(Err(e)) if e.kind() != io::ErrorKind::Interrupted => return Err(e),
            _ => {}
        }
    }
}

fn read_netif_nonblocking(fd: &OwnedFd, buf: &mut [u8]) -> io::Result<Option<usize>> {
    loop {
        match syscall(unsafe { libc::read(fd.as_raw_fd(), buf.as_mut_ptr().cast(), buf.len()) }) {
            Ok(0) => return Err(io::ErrorKind::UnexpectedEof.into()),
            Ok(n) => return Ok(Some(n)),
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => return Ok(None),
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        }
    }
}

struct Tun {
    fd: AsyncFd<OwnedFd>,
    rx: Vec<u8>,
    rx_invalid: DropCount,
    delivery_drops: DropCount,
}

impl Tun {
    fn open(addr: MacAddr) -> io::Result<Self> {
        let fd = open_netif_fd(
            TUN,
            (libc::IFF_TUN | libc::IFF_NO_PI | libc::IFF_TUN_EXCL) as _,
        )?;
        ip(&["link", "set", "dev", TUN, "mtu", "1450", "addrgenmode", "none"])?;
        let address = format!("{}/64", addr.link_local());
        ip(&["-6", "addr", "add", &address, "dev", TUN, "nodad"])?;
        ip(&["link", "set", "dev", TUN, "up"])?;
        Ok(Self {
            fd: register(fd)?,
            rx: vec![0; PACKET_BUFFER],
            rx_invalid: DropCount::default(),
            delivery_drops: DropCount::default(),
        })
    }

    async fn recv(&mut self) -> io::Result<Ipv6Packet> {
        let mut skip = SkipBudget::new(RX_SKIP_LIMIT);
        loop {
            let n = read_netif_frame(&self.fd, &mut self.rx).await?;
            if let Some(packet) = Ipv6Packet::from_ip(self.rx[..n].to_vec()) {
                return Ok(packet);
            }
            self.rx_invalid
                .record("tun rx", &"packet has no AWDL IPv6 addresses");
            skip.note_skip().await;
        }
    }

    fn try_recv(&mut self) -> io::Result<Option<Ipv6Packet>> {
        for _ in 0..RX_SKIP_LIMIT {
            let Some(n) = read_netif_nonblocking(self.fd.get_ref(), &mut self.rx)? else {
                return Ok(None);
            };
            if let Some(packet) = Ipv6Packet::from_ip(self.rx[..n].to_vec()) {
                return Ok(Some(packet));
            }
            self.rx_invalid
                .record("tun rx", &"packet has no AWDL IPv6 addresses");
        }
        Ok(None)
    }

    fn deliver(&mut self, packet: Ipv6Packet) {
        self.deliver_slice(&packet.data);
    }

    fn deliver_slice(&mut self, data: &[u8]) {
        if let Err(e) = write_packet(self.fd.get_ref(), data) {
            self.delivery_drops.record("tun deliver", &e);
        }
    }
}

impl Drop for Tun {
    fn drop(&mut self) {
        tracing::info!(
            tun_rx_invalid = self.rx_invalid.0,
            tun_delivery_drops = self.delivery_drops.0,
            "tun totals"
        );
    }
}

enum TapInject {
    Outbound(Ipv6Packet),
    InboundTap(Vec<u8>),
}

/// Persistent `NETLINK_ROUTE` socket driven off the async loop by [`NeighWorker`].
struct RtnlNeigh {
    fd: OwnedFd,
    seq: u32,
}

impl RtnlNeigh {
    const NLM_F_REQUEST: u16 = 0x1;
    const NLM_F_ACK: u16 = 0x4;
    const NLM_F_REPLACE: u16 = 0x100;
    const NLM_F_CREATE: u16 = 0x400;
    const NLMSG_ERROR: u16 = 2;
    const RTM_NEWNEIGH: u16 = 28;
    const RTM_DELNEIGH: u16 = 29;
    const NDA_DST: u16 = 1;
    const NDA_LLADDR: u16 = 2;
    const NUD_PERMANENT: u16 = 0x80;
    const HEADER_LEN: usize = 16;

    fn open() -> io::Result<Self> {
        let raw = unsafe {
            libc::socket(
                libc::AF_NETLINK,
                libc::SOCK_RAW | libc::SOCK_CLOEXEC,
                libc::NETLINK_ROUTE,
            )
        };
        syscall(raw as libc::ssize_t)?;
        let fd = unsafe { OwnedFd::from_raw_fd(raw) };
        let mut addr: libc::sockaddr_nl = unsafe { zeroed() };
        addr.nl_family = libc::AF_NETLINK as _;
        syscall(
            unsafe {
                libc::bind(
                    raw,
                    (&raw const addr).cast(),
                    size_of::<libc::sockaddr_nl>() as _,
                )
            } as libc::ssize_t,
        )?;
        set_sock_timeval(raw, libc::SO_SNDTIMEO, Duration::from_secs(2))?;
        set_sock_timeval(raw, libc::SO_RCVTIMEO, Duration::from_millis(500))?;
        Ok(Self { fd, seq: 0 })
    }

    fn request(
        &mut self,
        ty: u16,
        flags: u16,
        ifindex: u32,
        state: u16,
        addr: [u8; 16],
        mac: Option<[u8; 6]>,
    ) -> io::Result<()> {
        self.seq = self.seq.wrapping_add(1);
        let seq = self.seq;
        let mut msg = [0u8; 96];
        // ndmsg (12 bytes at offset 16)
        msg[16] = libc::AF_INET6 as u8;
        msg[20..24].copy_from_slice(&(ifindex as i32).to_ne_bytes());
        msg[24..26].copy_from_slice(&state.to_ne_bytes());
        // NDA_DST (20 bytes at offset 28)
        msg[28..30].copy_from_slice(&20u16.to_ne_bytes());
        msg[30..32].copy_from_slice(&Self::NDA_DST.to_ne_bytes());
        msg[32..48].copy_from_slice(&addr);
        let total = if let Some(lladdr) = mac {
            // NDA_LLADDR (len 10 + 2 pad = 12 bytes at offset 48)
            msg[48..50].copy_from_slice(&10u16.to_ne_bytes());
            msg[50..52].copy_from_slice(&Self::NDA_LLADDR.to_ne_bytes());
            msg[52..58].copy_from_slice(&lladdr);
            60usize
        } else {
            48usize
        };
        let nflags = flags | Self::NLM_F_REQUEST | Self::NLM_F_ACK;
        msg[0..4].copy_from_slice(&(total as u32).to_ne_bytes());
        msg[4..6].copy_from_slice(&ty.to_ne_bytes());
        msg[6..8].copy_from_slice(&nflags.to_ne_bytes());
        msg[8..12].copy_from_slice(&seq.to_ne_bytes());

        let mut sent = 0;
        while sent < total {
            match syscall(unsafe {
                libc::send(
                    self.fd.as_raw_fd(),
                    msg[sent..].as_ptr().cast(),
                    total - sent,
                    0,
                )
            }) {
                Ok(n) => sent += n,
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(e),
            }
        }

        let deadline = Instant::now() + Duration::from_secs(2);
        let mut ack = [0u8; 512];
        loop {
            if Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "netlink ack deadline exceeded",
                ));
            }
            let n = match syscall(unsafe {
                libc::recv(self.fd.as_raw_fd(), ack.as_mut_ptr().cast(), ack.len(), 0)
            }) {
                Ok(0) => return Err(io::ErrorKind::UnexpectedEof.into()),
                Ok(n) => n,
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => continue,
                Err(e) => return Err(e),
            };
            let mut rest = &ack[..n];
            while rest.len() >= Self::HEADER_LEN {
                let len = u32::from_ne_bytes(rest[0..4].try_into().unwrap()) as usize;
                if len < Self::HEADER_LEN || len > rest.len() {
                    return Err(io::Error::other("truncated netlink ack"));
                }
                let mty = u16::from_ne_bytes(rest[4..6].try_into().unwrap());
                let mseq = u32::from_ne_bytes(rest[8..12].try_into().unwrap());
                let body = &rest[Self::HEADER_LEN..len];
                let step = ((len + 3) & !3).min(rest.len());
                rest = &rest[step..];
                if mseq != seq {
                    continue;
                }
                if mty == Self::NLMSG_ERROR {
                    let errno = body
                        .get(..4)
                        .map(|b| i32::from_ne_bytes(b.try_into().unwrap()))
                        .unwrap_or(-libc::EIO);
                    return if errno == 0 {
                        Ok(())
                    } else {
                        Err(io::Error::from_raw_os_error(-errno))
                    };
                }
            }
        }
    }
}

impl NeighBackend for RtnlNeigh {
    fn replace(&mut self, ifindex: u32, addr: [u8; 16], mac: [u8; 6]) -> io::Result<()> {
        self.request(
            Self::RTM_NEWNEIGH,
            Self::NLM_F_CREATE | Self::NLM_F_REPLACE,
            ifindex,
            Self::NUD_PERMANENT,
            addr,
            Some(mac),
        )
    }

    fn delete(&mut self, ifindex: u32, addr: [u8; 16]) -> io::Result<()> {
        self.request(Self::RTM_DELNEIGH, 0, ifindex, 0, addr, None)
    }
}

/// L2 Ethernet TAP interface (`xos0`) for native MIShare compatibility.
struct Tap {
    fd: AsyncFd<OwnedFd>,
    vnet_hdr: bool,
    rx: Vec<u8>,
    pending_tx: VecDeque<Ipv6Packet>,
    tx_pool: Vec<Vec<u8>>,
    mdns_scratch: Vec<u8>,
    own: MacAddr,
    neigh: NeighWorker,
    rx_invalid: DropCount,
    delivery_drops: DropCount,
    peer_sync: KernelPeerSync,
    inject_tx: mpsc::UnboundedSender<TapInject>,
    inject_rx: mpsc::UnboundedReceiver<TapInject>,
    discovery_tasks: BTreeMap<MacAddr, JoinHandle<()>>,
}

impl Tap {
    fn open(addr: MacAddr, peer_sync: KernelPeerSync) -> io::Result<Self> {
        let tap_flags = (libc::IFF_TAP | libc::IFF_NO_PI | libc::IFF_TUN_EXCL)
            | libc::c_int::from(vnet::IFF_VNET_HDR);
        let fd = open_netif_fd(TAP, tap_flags as libc::c_short)?;
        let vnet_sz: libc::c_int = vnet::VNET_HDR_LEN as _;
        syscall(unsafe {
            libc::ioctl(fd.as_raw_fd(), vnet::TUNSETVNETHDRSZ as _, &raw const vnet_sz)
        } as libc::ssize_t)?;
        let offloads: libc::c_ulong = libc::c_ulong::from(vnet::TUN_F_CSUM | vnet::TUN_F_TSO6);
        syscall(unsafe {
            libc::ioctl(fd.as_raw_fd(), vnet::TUNSETOFFLOAD as _, offloads)
        } as libc::ssize_t)?;
        println!("OWLY_TAP_OFFLOAD vnet_hdr=1 vnet_hdr_sz={vnet_sz} csum=1 tso6=1");

        let mac = addr.to_string();
        ip(&[
            "link",
            "set",
            "dev",
            TAP,
            "address",
            &mac,
            "mtu",
            "1450",
            "txqueuelen",
            "8192",
            "addrgenmode",
            "none",
        ])?;
        let address = format!("{}/64", addr.link_local());
        ip(&["-6", "addr", "add", &address, "dev", TAP, "nodad"])?;
        ip(&["link", "set", "dev", TAP, "up"])?;
        let ifindex = unsafe { libc::if_nametoindex(TAP_CSTR.as_ptr()) };
        if ifindex == 0 {
            return Err(io::Error::last_os_error());
        }
        let rtnl = RtnlNeigh::open()?;
        let neigh = NeighWorker::spawn(ifindex, rtnl);
        let (inject_tx, inject_rx) = mpsc::unbounded_channel();
        Ok(Self {
            fd: register(fd)?,
            vnet_hdr: true,
            rx: vec![0; TAP_VNET_BUFFER],
            pending_tx: VecDeque::with_capacity(64),
            tx_pool: (0..TX_POOL_INIT).map(|_| Vec::with_capacity(2048)).collect(),
            mdns_scratch: Vec::with_capacity(2048),
            own: addr,
            neigh,
            rx_invalid: DropCount::default(),
            delivery_drops: DropCount::default(),
            peer_sync,
            inject_tx,
            inject_rx,
            discovery_tasks: BTreeMap::new(),
        })
    }

    fn handle_inject(&mut self, injected: TapInject) -> Option<Ipv6Packet> {
        match injected {
            TapInject::Outbound(packet) => Some(packet),
            TapInject::InboundTap(eth) => {
                if let Err(e) = write_tap_raw(self.fd.get_ref(), self.vnet_hdr, &eth) {
                    self.delivery_drops.record("tap mdns prompt", &e);
                }
                None
            }
        }
    }

    fn process_rx_frame(&mut self, n: usize) -> Option<Ipv6Packet> {
        let raw = &mut self.rx[..n];
        let vnet_hdr = self.vnet_hdr;
        let own = self.own;
        let fd = &self.fd;
        let tx_pool = &mut self.tx_pool;
        let pending_tx = &mut self.pending_tx;
        let delivery_drops = &mut self.delivery_drops;
        let res = vnet::process_tap_tx(
            raw,
            vnet_hdr,
            own,
            &mut || tx_pool.pop().unwrap_or_else(|| Vec::with_capacity(2048)),
            |na_eth| {
                if let Err(e) = write_tap_raw(fd.get_ref(), vnet_hdr, na_eth) {
                    delivery_drops.record("tap ndp reply", &e);
                }
            },
            |packet| pending_tx.push_back(packet),
        );
        if let Err(msg) = res {
            self.rx_invalid.record("tap rx", &msg);
        }
        self.pending_tx.pop_front()
    }

    async fn recv(&mut self) -> io::Result<Ipv6Packet> {
        if let Some(packet) = self.pending_tx.pop_front() {
            return Ok(packet);
        }
        let mut skip = SkipBudget::new(RX_SKIP_LIMIT);
        loop {
            tokio::select! {
                biased;
                maybe = self.inject_rx.recv() => {
                    let Some(injected) = maybe else {
                        return Err(io::ErrorKind::BrokenPipe.into());
                    };
                    if let Some(packet) = self.handle_inject(injected) {
                        return Ok(packet);
                    }
                    skip.note_skip().await;
                }
                res = read_netif_frame(&self.fd, &mut self.rx) => {
                    let n = res?;
                    if let Some(packet) = self.process_rx_frame(n) {
                        return Ok(packet);
                    }
                    skip.note_skip().await;
                }
            }
        }
    }

    fn try_recv(&mut self) -> io::Result<Option<Ipv6Packet>> {
        if let Some(packet) = self.pending_tx.pop_front() {
            return Ok(Some(packet));
        }
        for _ in 0..RX_SKIP_LIMIT {
            match self.inject_rx.try_recv() {
                Ok(injected) => {
                    if let Some(packet) = self.handle_inject(injected) {
                        return Ok(Some(packet));
                    }
                }
                Err(mpsc::error::TryRecvError::Empty) => break,
                Err(mpsc::error::TryRecvError::Disconnected) => {
                    return Err(io::ErrorKind::BrokenPipe.into());
                }
            }
        }
        for _ in 0..RX_SKIP_LIMIT {
            let Some(n) = read_netif_nonblocking(self.fd.get_ref(), &mut self.rx)? else {
                return Ok(None);
            };
            if let Some(packet) = self.process_rx_frame(n) {
                return Ok(Some(packet));
            }
        }
        Ok(None)
    }

    fn deliver(&mut self, mut packet: Ipv6Packet) {
        crate::ndp::fanout_unicast_mdns_response(&mut packet, self.own);
        let hdr = packet.ethernet_header();
        if let Err(e) = write_tap_ethernet(self.fd.get_ref(), self.vnet_hdr, &hdr, &packet.data) {
            self.delivery_drops.record("tap deliver", &e);
        }
    }

    fn deliver_slice(&mut self, dst: MacAddr, src: MacAddr, data: &[u8]) {
        let (eff_dst, payload) =
            crate::ndp::maybe_fanout_mdns_slice(dst, data, self.own, &mut self.mdns_scratch);
        let hdr = owly_awdl::ip::ethernet_header_for(eff_dst, src, payload);
        if let Err(e) = write_tap_ethernet(self.fd.get_ref(), self.vnet_hdr, &hdr, payload) {
            self.delivery_drops.record("tap deliver", &e);
        }
    }

    fn recycle_packet_buf(&mut self, mut buf: Vec<u8>) {
        buf.clear();
        if self.tx_pool.len() < TX_POOL_MAX && buf.capacity() <= 4_096 {
            self.tx_pool.push(buf);
        }
    }

    fn start_peer_discovery(&mut self, mac: MacAddr) {
        self.neigh.replace(mac);
        if let Some(prev) = self.discovery_tasks.remove(&mac) {
            prev.abort();
        }
        let _ = self.inject_tx.send(TapInject::Outbound(
            crate::ndp::build_airdrop_mdns_ptr_query(self.own, true),
        ));
        let tx = self.inject_tx.clone();
        let own = self.own;
        let handle = tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(50)).await;
            let _ = tx.send(TapInject::InboundTap(
                crate::ndp::build_airdrop_mdns_ptr_query(mac, false).to_ethernet(),
            ));
            tokio::time::sleep(Duration::from_millis(200)).await;
            let _ = tx.send(TapInject::Outbound(
                crate::ndp::build_airdrop_mdns_ptr_query(own, true),
            ));
            tokio::time::sleep(Duration::from_millis(650)).await;
            let _ = tx.send(TapInject::InboundTap(
                crate::ndp::build_airdrop_mdns_ptr_query(mac, false).to_ethernet(),
            ));
        });
        self.discovery_tasks.insert(mac, handle);
    }

    fn peer_up(&mut self, mac: MacAddr) {
        self.peer_sync.peer_up(mac);
        self.start_peer_discovery(mac);
    }

    fn peer_up_caps(&mut self, mac: MacAddr, caps: PeerCaps) {
        self.peer_sync.peer_up_caps(mac, caps);
        self.start_peer_discovery(mac);
    }

    fn peer_caps(&mut self, mac: MacAddr, caps: PeerCaps) {
        self.peer_sync.peer_caps(mac, caps);
    }

    fn peer_down(&mut self, mac: MacAddr) {
        if let Some(task) = self.discovery_tasks.remove(&mac) {
            task.abort();
        }
        self.peer_sync.peer_down(mac);
        self.neigh.delete(mac);
    }
}

impl Drop for Tap {
    fn drop(&mut self) {
        for task in self.discovery_tasks.values() {
            task.abort();
        }
        self.discovery_tasks.clear();
        tracing::info!(
            tap_rx_invalid = self.rx_invalid.0,
            tap_delivery_drops = self.delivery_drops.0,
            "tap totals"
        );
    }
}

enum HostNetif {
    Tun(Tun),
    Tap(Tap),
}

impl Netif for HostNetif {
    async fn recv(&mut self) -> io::Result<Ipv6Packet> {
        match self {
            Self::Tun(tun) => tun.recv().await,
            Self::Tap(tap) => tap.recv().await,
        }
    }

    fn try_recv(&mut self) -> io::Result<Option<Ipv6Packet>> {
        match self {
            Self::Tun(tun) => tun.try_recv(),
            Self::Tap(tap) => tap.try_recv(),
        }
    }

    fn deliver(&mut self, packet: Ipv6Packet) {
        match self {
            Self::Tun(tun) => tun.deliver(packet),
            Self::Tap(tap) => tap.deliver(packet),
        }
    }

    fn deliver_slice(&mut self, dst: MacAddr, src: MacAddr, data: &[u8]) {
        match self {
            Self::Tun(tun) => tun.deliver_slice(data),
            Self::Tap(tap) => tap.deliver_slice(dst, src, data),
        }
    }

    fn recycle_packet_buf(&mut self, buf: Vec<u8>) {
        if let Self::Tap(tap) = self {
            tap.recycle_packet_buf(buf);
        }
    }

    fn peer_up(&mut self, mac: MacAddr) {
        if let Self::Tap(tap) = self {
            tap.peer_up(mac);
        }
    }

    fn peer_up_caps(&mut self, mac: MacAddr, caps: PeerCaps) {
        if let Self::Tap(tap) = self {
            tap.peer_up_caps(mac, caps);
        }
    }

    fn peer_caps(&mut self, mac: MacAddr, caps: PeerCaps) {
        if let Self::Tap(tap) = self {
            tap.peer_caps(mac, caps);
        }
    }

    fn peer_down(&mut self, mac: MacAddr) {
        if let Self::Tap(tap) = self {
            tap.peer_down(mac);
        }
    }
}

fn ip(args: &[&str]) -> io::Result<()> {
    let status = Command::new("/system/bin/ip")
        .args(args)
        .stdin(Stdio::null())
        .status()?;
    if status.success() {
        Ok(())
    } else {
        Err(io::Error::other(format!(
            "ip {args:?} exited with {status}"
        )))
    }
}

fn parse_mac(value: &str) -> io::Result<MacAddr> {
    let mac: MacAddr = value.trim().parse().map_err(io::Error::other)?;
    if mac.0 == [0; 6] || mac.0[0] & 1 != 0 {
        return Err(io::Error::other("expected a nonzero unicast address"));
    }
    Ok(mac)
}

fn accept_addba_mode() -> io::Result<(u8, bool, bool)> {
    match std::env::var("HOUJI_OWLY_ACCEPT_ADDBA") {
        Err(std::env::VarError::NotPresent) => Ok((0, false, false)),
        Ok(value) if value == "0" => Ok((0, false, false)),
        Ok(value) if value == "1" => Ok((1, true, false)),
        Ok(value) if value == "2" => Ok((2, true, true)),
        _ => Err(io::Error::other("expected HOUJI_OWLY_ACCEPT_ADDBA=0, 1, or 2")),
    }
}

fn tx_addba_mode() -> io::Result<(u8, bool, u16)> {
    match std::env::var("HOUJI_OWLY_TX_ADDBA") {
        Err(std::env::VarError::NotPresent) => Ok((0, false, 64)),
        Ok(value) if value == "0" => Ok((0, false, 64)),
        Ok(value) if value == "1" => Ok((1, true, 64)),
        Ok(value) if value == "2" => Ok((2, true, 256)),
        _ => Err(io::Error::other("expected HOUJI_OWLY_TX_ADDBA=0, 1, or 2")),
    }
}

fn outgoing_vht_profile(accept_addba: bool) -> io::Result<(u8, owly_awdl::VhtProfile)> {
    let mode = match std::env::var("HOUJI_OWLY_OUTGOING_VHT") {
        Err(std::env::VarError::NotPresent) => 0,
        Ok(value) if value == "0" => 0,
        Ok(value) if value == "1" => 1,
        Ok(value) if value == "2" => 2,
        _ => {
            return Err(io::Error::other(
                "expected HOUJI_OWLY_OUTGOING_VHT=0, 1, or 2",
            ));
        }
    };
    if mode != 0 {
        let capacity =
            fs::read_to_string("/sys/module/qca_cld3_kiwi_v2/parameters/pt_rx_capacity_4224")?;
        let vht = fs::read_to_string("/sys/module/qca_cld3_kiwi_v2/parameters/pt_vht80")?;
        if capacity.trim() != "Y" || vht.trim() != "Y" {
            return Err(io::Error::other(
                "outgoing VHT requires the tested RX capacity and VHT80 radio mode",
            ));
        }
    }
    let profile = match mode {
        0 => owly_awdl::VhtProfile::Off,
        1 => owly_awdl::VhtProfile::Mif1ss,
        2 => owly_awdl::VhtProfile::PsfAndMif2ssSgi {
            max_ampdu_exp: if accept_addba { 7 } else { 0 },
        },
        _ => unreachable!(),
    };
    Ok((mode, profile))
}

pub type AppSpawner = Box<
    dyn FnOnce(PathBuf, u32, MacAddr, MacAddr, watch::Receiver<Vec<PeerInfo>>) -> JoinHandle<io::Result<()>>
        + Send,
>;

pub fn run_xos() -> io::Result<()> {
    run_inner(true, None)
}

pub fn run_with_app(spawner: AppSpawner) -> io::Result<()> {
    run_inner(false, Some(spawner))
}

#[tokio::main(flavor = "current_thread")]
async fn run_inner(xos_only: bool, app_spawner: Option<AppSpawner>) -> io::Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let [peer_arg, aggregation, ack] = args.as_slice() else {
        let usage = if xos_only {
            "usage: owly-xosd xos0 single|amsdu ack|noack"
        } else {
            "usage: houji-owly-app MAC|scan|xos0 single|amsdu ack|noack"
        };
        return Err(io::Error::other(usage));
    };
    if xos_only && peer_arg != "xos0" {
        return Err(io::Error::other(
            "usage: owly-xosd xos0 single|amsdu ack|noack",
        ));
    }
    let tap_mode = peer_arg == "xos0";
    let iface_name = if tap_mode { TAP } else { TUN };
    let peer = if peer_arg == "scan" || tap_mode {
        MacAddr([0; 6])
    } else {
        parse_mac(peer_arg)?
    };
    let node_name = match std::env::var("HOUJI_OWLY_NAME") {
        Ok(value) if !value.trim().is_empty() => value,
        _ if tap_mode => "Xiaomi 14".to_owned(),
        _ => "Xiaomi 14 Owly".to_owned(),
    };
    let coalesce_us = match std::env::var("OWLY_COALESCE_US") {
        Ok(raw) => raw.parse::<u64>()
            .ok().filter(|v| *v <= 4_000)
            .ok_or_else(|| io::Error::other("OWLY_COALESCE_US must be 0..=4000"))?,
        Err(std::env::VarError::NotPresent) => 1_000,
        Err(_) => return Err(io::Error::other("OWLY_COALESCE_US is not Unicode")),
    };
    let (accept_addba_raw, accept_addba, accept_addba_amsdu) = accept_addba_mode()?;
    let (tx_addba_raw, initiate_addba, initiate_addba_window) = tx_addba_mode()?;
    let (outgoing_vht_mode, vht_profile) = outgoing_vht_profile(accept_addba || initiate_addba)?;
    let amsdu = match aggregation.as_str() {
        "single" => false,
        "amsdu" => true,
        _ => return Err(io::Error::other("expected single or amsdu")),
    };
    let no_ack = match ack.as_str() {
        "ack" => false,
        "noack" => true,
        _ => return Err(io::Error::other("expected ack or noack")),
    };

    tracing_subscriber::fmt()
        .with_ansi(false)
        .without_time()
        .init();
    let addr = parse_mac(&fs::read_to_string("/sys/class/net/wondertap0/address")?)?;
    if addr == peer {
        return Err(io::Error::other("local PASSTHRU address equals peer"));
    }
    let mut cfg = Config::new(
        addr,
        &node_name,
        RadioConstraints {
            channel: Channel(149),
            presence: Presence::Fixed,
        },
    );
    cfg.no_ack = no_ack;
    cfg.accept_addba = accept_addba;
    cfg.accept_addba_amsdu = accept_addba_amsdu;
    cfg.initiate_addba = initiate_addba;
    cfg.initiate_addba_window = initiate_addba_window;
    if initiate_addba {
        cfg.unicast_queue = 512;
        cfg.burst_limit = 256;
    }
    cfg.advertise_vht80_1ss = vht_profile == owly_awdl::VhtProfile::Mif1ss;
    cfg.vht_profile = vht_profile;
    cfg.amsdu = amsdu;
    cfg.amsdu_coalesce_us = coalesce_us;
    cfg.max_frame = if amsdu { 7935 } else { 2304 };
    cfg.rssi_threshold = None;
    cfg.fast_discovery = true;

    let peer_sync = if tap_mode {
        KernelPeerSync::from_sysfs()
    } else {
        KernelPeerSync::disabled()
    };
    let dynamic_peers = peer_sync.is_enabled();
    if dynamic_peers {
        cfg.max_peers = 8;
    }

    let mut terminate = signal(SignalKind::terminate())?;
    let mut interrupt = signal(SignalKind::interrupt())?;
    let (peers, mut observed) = watch::channel(Vec::new());
    let running = driver::spawn(
        cfg,
        move || {
            let air = Monitor::open(peer, addr)?;
            let netif = if tap_mode {
                HostNetif::Tap(Tap::open(addr, peer_sync)?)
            } else {
                HostNetif::Tun(Tun::open(addr)?)
            };
            Ok((air, netif))
        },
        peers,
    )?;
    let mut failed = running.failed();
    let mut app_task = if !tap_mode
        && let Some(spawner) = app_spawner
        && let Ok(dir) = std::env::var("OWLY_APP_DIR")
    {
        let uid = std::env::var("OWLY_APP_UID")
            .map_err(io::Error::other)?
            .parse::<u32>()
            .map_err(io::Error::other)?;
        let observed = observed.clone();
        Some(spawner(dir.into(), uid, addr, peer, observed))
    } else {
        None
    };
    println!("OWLY_COALESCE_US={coalesce_us}");
    println!("OWLY_OUTGOING_VHT={outgoing_vht_mode}");
    println!("OWLY_ACCEPT_ADDBA={accept_addba_raw}");
    println!("OWLY_TX_ADDBA={tx_addba_raw}");
    println!(
        "OWLY_READY iface={iface_name} addr={} peer={peer} channel=149 amsdu={amsdu} noack={no_ack} ba={accept_addba} dynamic_peers={dynamic_peers}",
        addr.link_local()
    );
    let result = loop {
        tokio::select! {
            _ = terminate.recv() => break Ok(()),
            _ = interrupt.recv() => break Ok(()),
            ended = async { app_task.as_mut().unwrap().await }, if app_task.is_some() => {
                break match ended {
                    Ok(Err(error)) => Err(error),
                    Ok(Ok(())) => Err(io::Error::other("App backend stopped")),
                    Err(error) => Err(io::Error::other(error)),
                };
            }
            update = failed.changed() => {
                let message = failed.borrow().clone().unwrap_or_else(|| format!("driver status closed: {update:?}"));
                break Err(io::Error::other(message));
            }
            Ok(()) = observed.changed() => {
                println!("OWLY_PEERS count={}", observed.borrow_and_update().len());
            }
        }
    };
    running.stop();
    if let Some(task) = app_task { task.abort(); }
    result
}
