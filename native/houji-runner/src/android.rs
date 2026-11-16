use std::collections::BTreeMap;
use std::ffi::CStr;
use std::fs::{self, OpenOptions};
use std::io;
use std::mem::{size_of, zeroed};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use owly_awdl::driver::{self, Air, Netif, Received};
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
};

const MONITOR: &CStr = c"wonder0";
const TUN: &str = "owly0";
const TAP: &str = "xos0";
const TAP_CSTR: &CStr = c"xos0";
const PACKET_BUFFER: usize = 16 * 1024;
const ETH_P_ALL: u16 = 3;
const PACKET_STATISTICS: libc::c_int = 6; // linux/if_packet.h
const PACKET_IGNORE_OUTGOING: libc::c_int = 23; // linux/if_packet.h
const MONITOR_RCVBUF_BYTES: libc::c_int = 8 * 1024 * 1024;
const MONITOR_SNDBUF_BYTES: libc::c_int = 4 * 1024 * 1024;

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

fn write_tap_ethernet(fd: &OwnedFd, hdr: &[u8; 14], payload: &[u8]) -> io::Result<()> {
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
    loop {
        // Both buffers stay live for the synchronous writev syscall.
        match syscall(unsafe { libc::writev(fd.as_raw_fd(), iov.as_ptr(), iov.len() as _) }) {
            Ok(n) if n == total => return Ok(()),
            Ok(_) => {
                return Err(io::Error::new(
                    io::ErrorKind::WriteZero,
                    "short tap packet write",
                ));
            }
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        }
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

struct Monitor {
    fd: AsyncFd<OwnedFd>,
    rx: Vec<u8>,
    tx: Vec<u8>,
    peer: MacAddr,
    own: MacAddr,
    rx_gaps: Option<RxGaps>,
    rx_invalid: DropCount,
    tx_fatal: DropCount,
    tx_backpressure: u64,
    tx_submitted: u64,
    psf_reported: bool,
    mif_reported: bool,
    tx_addba_reported: u8,
    rx_addba_resp_reported: u8,
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
            rx: vec![0; PACKET_BUFFER],
            tx: Vec::with_capacity(PACKET_BUFFER),
            peer,
            own,
            rx_gaps: rx_gaps_enabled().then(RxGaps::default),
            rx_invalid: DropCount::default(),
            tx_fatal: DropCount::default(),
            tx_backpressure: 0,
            tx_submitted: 0,
            psf_reported: false,
            mif_reported: false,
            tx_addba_reported: 0,
            rx_addba_resp_reported: 0,
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
        if let Some(rx_gaps) = &self.rx_gaps {
            rx_gaps.report();
        }
        tracing::info!(air_rx_invalid = self.rx_invalid.0,
                       air_rx_kernel_packets = if stats_ok { stats.tp_packets as u64 } else { u64::MAX },
                       air_rx_kernel_drops = if stats_ok { stats.tp_drops as u64 } else { u64::MAX },
                       air_tx_fatal = self.tx_fatal.0,
                       air_tx_backpressure = self.tx_backpressure, air_tx_submitted = self.tx_submitted,
                       air_tx_amsdu_mpdu = self.tx_amsdu_mpdu, air_tx_amsdu_msdu = self.tx_amsdu_msdu,
                       "monitor totals");
    }
}

impl Monitor {
    fn decode_rx(&mut self, n: usize) -> Option<Received> {
        let Some(received) = self.rx.get(..n).and_then(radiotap::parse) else {
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
        let bytes = raw.to_vec();
        let rssi = received.rssi;
        self.report_first_rx_addba_resp(&bytes);
        Some(Received { bytes, rssi })
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
        let mut skip = SkipBudget::new(RX_SKIP_LIMIT);
        loop {
            let mut ready = self.fd.readable().await?;
            let read = ready.try_io(|fd| {
                // MSG_TRUNC reports the original size even if the buffer was too small.
                syscall(unsafe {
                    libc::recv(
                        fd.get_ref().as_raw_fd(),
                        self.rx.as_mut_ptr().cast(),
                        self.rx.len(),
                        libc::MSG_TRUNC,
                    )
                })
            });
            match read {
                Ok(Ok(0)) => return Err(io::ErrorKind::UnexpectedEof.into()),
                Ok(Ok(n)) => {
                    if let Some(received) = self.decode_rx(n) {
                        return Ok(received);
                    }
                    skip.note_skip().await;
                }
                Ok(Err(e)) if e.kind() != io::ErrorKind::Interrupted => return Err(e),
                _ => {}
            }
        }
    }

    fn try_recv(&mut self) -> io::Result<Option<Received>> {
        for _ in 0..RX_SKIP_LIMIT {
            let read = syscall(unsafe {
                libc::recv(
                    self.fd.get_ref().as_raw_fd(),
                    self.rx.as_mut_ptr().cast(),
                    self.rx.len(),
                    libc::MSG_TRUNC,
                )
            });
            match read {
                Ok(0) => return Err(io::ErrorKind::UnexpectedEof.into()),
                Ok(n) => {
                    if let Some(received) = self.decode_rx(n) {
                        return Ok(Some(received));
                    }
                }
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => return Ok(None),
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(e),
            }
        }
        Ok(None)
    }

    fn send(&mut self, frame: Frame) -> Result<(), Frame> {
        self.tx.clear();
        self.tx
            .extend_from_slice(&radiotap::tx_header(frame.rate, frame.no_ack));
        self.tx.extend_from_slice(&frame.bytes);
        match write_packet(self.fd.get_ref(), &self.tx) {
            Ok(()) => {
                self.tx_submitted += 1;
                self.report_first_submitted_action(&frame.bytes);
                self.report_first_tx_addba(&frame.bytes);
                if let Some(msdus) = count_amsdu_subframes(&frame.bytes) {
                    self.tx_amsdu_mpdu += 1;
                    self.tx_amsdu_msdu += msdus;
                }
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
        if let Err(e) = write_packet(self.fd.get_ref(), &packet.data) {
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
    rx: Vec<u8>,
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
        let fd = open_netif_fd(
            TAP,
            (libc::IFF_TAP | libc::IFF_NO_PI | libc::IFF_TUN_EXCL) as _,
        )?;
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
            rx: vec![0; PACKET_BUFFER],
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
                if let Err(e) = write_packet(self.fd.get_ref(), &eth) {
                    self.delivery_drops.record("tap mdns prompt", &e);
                }
                None
            }
        }
    }

    fn process_rx_frame(&mut self, n: usize) -> Option<Ipv6Packet> {
        let frame = &self.rx[..n];
        let Some(packet) = Ipv6Packet::from_ethernet(frame) else {
            if frame.get(12..14) == Some(&[0x86, 0xdd]) {
                self.rx_invalid
                    .record("tap rx", &"truncated IPv6 ethernet frame");
            }
            return None;
        };
        if let Some(na_eth) = crate::ndp::synthesize_na_for_ns(&packet, self.own) {
            if let Err(e) = write_packet(self.fd.get_ref(), &na_eth) {
                self.delivery_drops.record("tap ndp reply", &e);
            }
            return None;
        }
        Some(packet)
    }

    async fn recv(&mut self) -> io::Result<Ipv6Packet> {
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
        if let Err(e) = write_tap_ethernet(self.fd.get_ref(), &hdr, &packet.data) {
            self.delivery_drops.record("tap deliver", &e);
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
