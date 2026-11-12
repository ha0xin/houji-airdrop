use std::ffi::CStr;
use std::fs::{self, OpenOptions};
use std::io;
use std::mem::{size_of, zeroed};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::fs::OpenOptionsExt;
use std::process::{Command, Stdio};

use owly_awdl::driver::{self, Air, Netif, Received};
use owly_awdl::frame::Frame;
use owly_awdl::{Channel, Config, Ipv6Packet, MacAddr, Presence, RadioConstraints};
use tokio::io::unix::AsyncFd;
use tokio::signal::unix::{SignalKind, signal};
use tokio::sync::watch;

use crate::radiotap;

const MONITOR: &CStr = c"wonder0";
const TUN: &str = "owly0";
const PACKET_BUFFER: usize = 16 * 1024;
const ETH_P_ALL: u16 = 3;
const PACKET_IGNORE_OUTGOING: libc::c_int = 23; // linux/if_packet.h

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
    rx_invalid: DropCount,
    tx_fatal: DropCount,
    tx_backpressure: u64,
    tx_submitted: u64,
    tx_amsdu_mpdu: u64,
    tx_amsdu_msdu: u64,
}

impl Monitor {
    fn open(peer: MacAddr) -> io::Result<Self> {
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
            rx_invalid: DropCount::default(),
            tx_fatal: DropCount::default(),
            tx_backpressure: 0,
            tx_submitted: 0,
            tx_amsdu_mpdu: 0,
            tx_amsdu_msdu: 0,
        })
    }
}

impl Drop for Monitor {
    fn drop(&mut self) {
        tracing::info!(air_rx_invalid = self.rx_invalid.0, air_tx_fatal = self.tx_fatal.0,
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
                    return Ok(Received {
                        bytes: received.frame.to_vec(),
                        rssi: received.rssi,
                    });
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

#[tokio::main(flavor = "current_thread")]
pub async fn run() -> io::Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let [peer, aggregation, ack] = args.as_slice() else {
        return Err(io::Error::other(
            "usage: houji-owly MAC single|amsdu ack|noack",
        ));
    };
    let peer = parse_mac(peer)?;
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
    cfg.accept_addba = false;
    cfg.amsdu = amsdu;
    cfg.max_frame = if amsdu { 7935 } else { 2304 };
    cfg.rssi_threshold = None;

    let mut terminate = signal(SignalKind::terminate())?;
    let mut interrupt = signal(SignalKind::interrupt())?;
    let (peers, mut observed) = watch::channel(Vec::new());
    let running = driver::spawn(
        cfg,
        move || Ok((Monitor::open(peer)?, Tun::open(addr)?)),
        peers,
    )?;
    let mut failed = running.failed();
    println!("OWLY_COALESCE_US={}", option_env!("HOUJI_OWLY_COALESCE_US").unwrap_or("0"));
    println!(
        "OWLY_READY iface={TUN} addr={} peer={peer} channel=149 amsdu={amsdu} noack={no_ack} ba=false",
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
