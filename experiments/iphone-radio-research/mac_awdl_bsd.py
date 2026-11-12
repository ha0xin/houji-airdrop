"""Mac IPv6-bound client for the native iPhone hashprobe listener."""

import hashlib
import ipaddress
import socket
import struct
import time

from mac_tcp_timeline import TcpTimeline, load_v2

# Darwin SDK <netinet6/in6.h>, int argument.
IPV6_BOUND_IF = 125
CHUNK = 64 << 10
PATTERN = bytes(range(256)) * (CHUNK // 256)


def recv_exact(sock, length, bounded):
    result = bytearray()
    while len(result) < length:
        bounded()
        chunk = sock.recv(length - len(result))
        if not chunk:
            raise ConnectionError("Unexpected end of probe response")
        result.extend(chunk)
    return bytes(result)


def measure_connected(sock, mode, sample, size, digest, snapshot):
    start_wall = time.time_ns()
    start_mono = time.monotonic_ns()
    timeline = TcpTimeline(sock, snapshot, start_mono)
    with timeline:
        deadline = start_mono + 55_000_000_000

        def bounded():
            remaining = (deadline - time.monotonic_ns()) / 1e9
            if remaining <= 0:
                raise TimeoutError("BSD transfer timed out")
            sock.settimeout(remaining)

        bounded()
        sock.sendall(struct.pack("!III", mode, size, sample))
        if mode == 0:
            for offset in range(0, size, CHUNK):
                bounded()
                sock.sendall(PATTERN[:min(CHUNK, size - offset)])
            bounded()
            actual = recv_exact(sock, 32, bounded)
        else:
            sha = hashlib.sha256()
            for offset in range(0, size, CHUNK):
                bounded()
                sha.update(recv_exact(sock, min(CHUNK, size - offset), bounded))
            actual = sha.digest()
            if actual == digest:
                bounded()
                sock.sendall(actual)
        end_mono = time.monotonic_ns()
        end_wall = time.time_ns()
    if actual != digest:
        raise ValueError("BSD transfer SHA mismatch")
    seconds = (end_mono - start_mono) / 1e9
    return dict(
        seconds=seconds, MBps=size / seconds / 1e6, sha256=actual.hex(),
        transfer_clock=dict(start_wall_ns=start_wall, end_wall_ns=end_wall,
                            start_mono_ns=start_mono, end_mono_ns=end_mono),
    ), timeline.result()


def clamp_sender_mss(sock, snapshot, mss):
    before = snapshot(sock.fileno())["mss"]
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, mss)
    if (sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG) != mss or
            snapshot(sock.fileno())["mss"] != mss):
        raise ValueError("Mac sender MSS clamp did not match the requested effective MSS")
    return before


def transfer(local, peer, ifindex, port, mode, sample, size, digest, dylib, mss=None):
    local, peer = ipaddress.IPv6Address(local), ipaddress.IPv6Address(peer)
    if not local.is_link_local or not peer.is_link_local or local == peer:
        raise ValueError("Expected distinct link-local addresses")

    def validate(sock):
        here, remote = sock.getsockname(), sock.getpeername()
        if (socket.if_nametoindex("awdl0") != ifindex or
                sock.getsockopt(socket.IPPROTO_IPV6, IPV6_BOUND_IF) != ifindex or
                (ipaddress.IPv6Address(here[0].split("%")[0]), here[3]) != (local, ifindex) or
                (ipaddress.IPv6Address(remote[0].split("%")[0]), remote[1], remote[3]) !=
                (peer, port, ifindex)):
            raise ValueError("BSD path does not match both scoped AWDL endpoints")

    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
        sock.settimeout(8)
        sock.setsockopt(socket.IPPROTO_IPV6, IPV6_BOUND_IF, ifindex)
        sock.bind((str(local), 0, 0, ifindex))
        sock.connect((str(peer), port, 0, ifindex))
        validate(sock)
        snapshot = load_v2(dylib)
        before_mss = clamp_sender_mss(sock, snapshot, mss) if mss is not None else None
        row, timeline = measure_connected(sock, mode, sample, size, digest, snapshot)
        if mss is not None:
            row["mss_before_clamp"] = before_mss
            if any(point["mss"] != mss for point in timeline["samples"]):
                timeline["status"] = "mss_changed"
        validate(sock)
    row["path"] = "awdl0"
    return row, timeline
