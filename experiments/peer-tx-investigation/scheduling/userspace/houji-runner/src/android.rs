use std::ffi::CStr;
use std::fs::{self, OpenOptions};
use std::io;
use std::mem::{size_of, zeroed};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::fs::OpenOptionsExt;
use std::process::{Command, Stdio};
use std::time::Instant;

use owly_awdl::driver::{self, Air, Netif, Received};
use owly_awdl::frame::Frame;
use owly_awdl::{Channel, Config, Ipv6Packet, MacAddr, Presence, RadioConstraints};
use tokio::io::unix::AsyncFd;
use tokio::signal::unix::{SignalKind, signal};
use tokio::sync::watch;

use crate::{radiotap, rx_gap::RxGaps};

const MONITOR: &CStr = c"wonder0";
const TUN: &str = "owly0";
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

struct Monitor {
    fd: AsyncFd<OwnedFd>,
    rx: Vec<u8>,
    tx: Vec<u8>,
    peer: MacAddr,
    own: MacAddr,
    rx_gaps: RxGaps,
    rx_invalid: DropCount,
    tx_fatal: DropCount,
    tx_backpressure: u64,
    tx_submitted: u64,
    psf_reported: bool,
    mif_reported: bool,
    tx_addba_reported: bool,
    rx_addba_resp_reported: bool,
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
            rx_gaps: RxGaps::default(),
            rx_invalid: DropCount::default(),
            tx_fatal: DropCount::default(),
            tx_backpressure: 0,
            tx_submitted: 0,
            psf_reported: false,
            mif_reported: false,
            tx_addba_reported: false,
            rx_addba_resp_reported: false,
            tx_amsdu_mpdu: 0,
            tx_amsdu_msdu: 0,
        })
    }

    fn report_first_tx_addba(&mut self, bytes: &[u8]) {
        if self.tx_addba_reported || bytes.len() < 33 || bytes[0] != 0xd0 {
            return;
        }
        if bytes[24] == 3 && bytes[25] == 0 {
            let token = bytes[26];
            let params = u16::from_le_bytes([bytes[27], bytes[28]]);
            let ssn = u16::from_le_bytes([bytes[31], bytes[32]]) >> 4;
            println!("OWLY_TX_ADDBA_REQ_SENT token={token} params=0x{params:04x} ssn={ssn}");
            self.tx_addba_reported = true;
        }
    }

    fn report_first_rx_addba_resp(&mut self, bytes: &[u8]) {
        if self.rx_addba_resp_reported || bytes.len() < 33 || bytes[0] != 0xd0 {
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
            self.rx_addba_resp_reported = true;
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
        self.rx_gaps.report();
        tracing::info!(air_rx_invalid = self.rx_invalid.0,
                       air_rx_kernel_packets = if stats_ok { stats.tp_packets as u64 } else { u64::MAX },
                       air_rx_kernel_drops = if stats_ok { stats.tp_drops as u64 } else { u64::MAX },
                       air_tx_fatal = self.tx_fatal.0,
                       air_tx_backpressure = self.tx_backpressure, air_tx_submitted = self.tx_submitted,
                       air_tx_amsdu_mpdu = self.tx_amsdu_mpdu, air_tx_amsdu_msdu = self.tx_amsdu_msdu,
                       "monitor totals");
    }
}

impl Air for Monitor {
    async fn recv(&mut self) -> io::Result<Received> {
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
                    let Some(received) = self.rx.get(..n).and_then(radiotap::parse) else {
                        self.rx_invalid
                            .record("air rx", &"invalid or truncated radiotap frame");
                        continue;
                    };
                    // The transmitter is Address 2 of every supported AWDL frame.
                    if received.freq.is_some_and(|freq| freq != 5745)
                        || received.frame.get(10..16) != Some(self.peer.0.as_slice())
                    {
                        continue;
                    }
                    let raw = received.frame;
                    if raw.len() >= 1536
                        && raw[0] == 0x88
                        && raw[1] & 0xc7 == 0
                        && raw[22] & 0x0f == 0
                        && raw[4..10] == self.own.0
                        && raw[16..22] == [0x00, 0x25, 0, 0xff, 0x94, 0x73]
                    {
                        self.rx_gaps.record(received.tsft_us, Instant::now());
                    }
                    let bytes = raw.to_vec();
                    let rssi = received.rssi;
                    self.report_first_rx_addba_resp(&bytes);
                    return Ok(Received { bytes, rssi });
                }
                Ok(Err(e)) if e.kind() != io::ErrorKind::Interrupted => return Err(e),
                _ => {}
            }
        }
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
                if frame.bytes.first() == Some(&0x88)
                    && frame.bytes.get(24).is_some_and(|flags| flags & 0x80 != 0)
                {
                    use owly_awdl::frame::{self, Received::Data};
                    if let Ok(Data { header, amsdu: true, body }) = frame::parse(&frame.bytes)
                        && let Ok(msdus) = frame::msdus(&header, true, body)
                    {
                        self.tx_amsdu_mpdu += 1;
                        self.tx_amsdu_msdu += msdus.len() as u64;
                    }
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

struct Tun {
    fd: AsyncFd<OwnedFd>,
    rx: Vec<u8>,
    rx_invalid: DropCount,
    delivery_drops: DropCount,
}

impl Tun {
    fn open(addr: MacAddr) -> io::Result<Self> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
            .open("/dev/net/tun")?;
        // ifreq is a C wire struct. TUNSETIFF consumes the name and flags.
        let mut request: libc::ifreq = unsafe { zeroed() };
        for (slot, byte) in request.ifr_name.iter_mut().zip(TUN.bytes()) {
            *slot = byte as _;
        }
        request.ifr_ifru.ifru_flags = (libc::IFF_TUN | libc::IFF_NO_PI | libc::IFF_TUN_EXCL) as _;
        syscall(
            unsafe { libc::ioctl(file.as_raw_fd(), libc::TUNSETIFF as _, &mut request) } as libc::ssize_t,
        )?;

        ip(&[
            "link",
            "set",
            "dev",
            TUN,
            "mtu",
            "1450",
            "addrgenmode",
            "none",
        ])?;
        let address = format!("{}/64", addr.link_local());
        ip(&["-6", "addr", "add", &address, "dev", TUN, "nodad"])?;
        ip(&["link", "set", "dev", TUN, "up"])?;
        Ok(Self {
            fd: register(file.into())?,
            rx: vec![0; PACKET_BUFFER],
            rx_invalid: DropCount::default(),
            delivery_drops: DropCount::default(),
        })
    }
}

impl Drop for Tun {
    fn drop(&mut self) {
        tracing::info!(tun_rx_invalid = self.rx_invalid.0, tun_delivery_drops = self.delivery_drops.0, "tun totals");
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

impl Netif for Tun {
    async fn recv(&mut self) -> io::Result<Ipv6Packet> {
        loop {
            let mut ready = self.fd.readable().await?;
            let read = ready.try_io(|fd| {
                // The fd stays open and the RX buffer is valid for its full length.
                syscall(unsafe {
                    libc::read(
                        fd.get_ref().as_raw_fd(),
                        self.rx.as_mut_ptr().cast(),
                        self.rx.len(),
                    )
                })
            });
            match read {
                Ok(Ok(0)) => return Err(io::ErrorKind::UnexpectedEof.into()),
                Ok(Ok(n)) => {
                    if let Some(packet) = Ipv6Packet::from_ip(self.rx[..n].to_vec()) {
                        return Ok(packet);
                    }
                    self.rx_invalid
                        .record("tun rx", &"packet has no AWDL IPv6 addresses");
                }
                Ok(Err(e)) if e.kind() != io::ErrorKind::Interrupted => return Err(e),
                _ => {}
            }
        }
    }

    fn deliver(&mut self, packet: Ipv6Packet) {
        if let Err(e) = write_packet(self.fd.get_ref(), &packet.data) {
            self.delivery_drops.record("tun deliver", &e);
        }
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

#[tokio::main(flavor = "current_thread")]
pub async fn run() -> io::Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let [peer, aggregation, ack] = args.as_slice() else {
        return Err(io::Error::other(
            "usage: houji-owly MAC single|amsdu ack|noack",
        ));
    };
    let peer = parse_mac(peer)?;
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
        "Houji-Owly",
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

    let mut terminate = signal(SignalKind::terminate())?;
    let mut interrupt = signal(SignalKind::interrupt())?;
    let (peers, mut observed) = watch::channel(Vec::new());
    let running = driver::spawn(
        cfg,
        move || Ok((Monitor::open(peer, addr)?, Tun::open(addr)?)),
        peers,
    )?;
    let mut failed = running.failed();
    println!("OWLY_COALESCE_US={coalesce_us}");
    println!("OWLY_OUTGOING_VHT={outgoing_vht_mode}");
    println!("OWLY_ACCEPT_ADDBA={accept_addba_raw}");
    println!("OWLY_TX_ADDBA={tx_addba_raw}");
    println!(
        "OWLY_READY iface={TUN} addr={} peer={peer} channel=149 amsdu={amsdu} noack={no_ack} ba={accept_addba}",
        addr.link_local()
    );
    let result = loop {
        tokio::select! {
            _ = terminate.recv() => break Ok(()),
            _ = interrupt.recv() => break Ok(()),
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
    result
}
