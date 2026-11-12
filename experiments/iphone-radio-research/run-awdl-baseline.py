#!/usr/bin/env python3
"""Run one native AWDL pair over a separately managed USB SSH forwarding port."""

import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import secrets
import select
import shlex
import subprocess
import tempfile
import time


HERE = Path(__file__).resolve().parent
SSH = [
    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR", "-p", "22226", "root@127.0.0.1",
]
BUILD = [
    "-O2", "-fobjc-arc", "-fblocks", "-Wall", "-Wextra", "-Werror",
    "-framework", "Network", "-framework", "Foundation", str(HERE / "awdl-hashprobe.m"),
]
HASH_ROW = re.compile(
    r"^HASH_OK sample=(\d+) mode=(\d+) bytes=(\d+) seconds=([\d.]+)"
    r" MBps=([\d.]+) path=awdl0 sha256=([0-9a-f]{64})$",
    re.MULTILINE,
)
HASH_CLOCK = re.compile(
    r"^HASH_CLOCK sample=(\d+) mode=(\d+) start_mono_ns=(\d+) end_mono_ns=(\d+)"
    r" start_wall_ns=(\d+) end_wall_ns=(\d+)$",
    re.MULTILINE,
)
TCP_REPORT_FIELDS = {
    "duration_ms", "sent_app_bytes", "received_app_bytes", "sent_tcp_bytes",
    "received_tcp_bytes", "retransmitted_tcp_bytes", "duplicate_tcp_bytes",
    "out_of_order_tcp_bytes", "smoothed_rtt_ms", "min_rtt_ms",
}


def run(args, timeout=8, **kwargs):
    return subprocess.run(args, timeout=timeout, capture_output=True, check=True, **kwargs)


def remote(args, **kwargs):
    return run(SSH + [shlex.join(args)], **kwargs).stdout.decode()


def address(device):
    tool = "/var/jb/usr/sbin/ifconfig" if device else "/sbin/ifconfig"
    args = [tool, "awdl0"]
    output = remote(args) if device else run(args).stdout.decode()
    addresses = [
        str(ipaddress.IPv6Address(word))
        for word in re.findall(r"^\s+inet6 ([0-9a-fA-F:]+)(?:%awdl0)?\s", output, re.MULTILINE)
        if ipaddress.IPv6Address(word).is_link_local
    ]
    if len(addresses) != 1 or "status: active" not in output:
        raise RuntimeError("AWDL interface unavailable or link-local address ambiguous")
    return addresses[0]


def counters(device):
    tool = "/var/jb/usr/sbin/netstat" if device else "/usr/sbin/netstat"
    args = [tool, "-I", "awdl0", "-b", "-d", "-n"]
    output = remote(args) if device else run(args).stdout.decode()
    headings = output.splitlines()[0].split()
    values = next(line.split() for line in output.splitlines() if "<Link#" in line)
    return {name: int(values[headings.index(name)]) for name in ("Ibytes", "Obytes", "Ierrs", "Oerrs")}


def verified_row(output, sample, mode, size, digest):
    matches = HASH_ROW.findall(output)
    if len(matches) != 1:
        return None
    sample_got, mode_got, size_got, seconds, mbps, sha = matches[0]
    if (int(sample_got), int(mode_got), int(size_got), sha) != (sample, mode, size, digest):
        return None
    if float(seconds) <= 0:
        return None
    row = dict(seconds=float(seconds), MBps=float(mbps), sha256=sha, path="awdl0")
    clocks = HASH_CLOCK.findall(output)
    if len(clocks) == 1:
        clock_sample, clock_mode, start_mono, end_mono, start_wall, end_wall = map(int, clocks[0])
        if (clock_sample, clock_mode) == (sample, mode) and start_mono < end_mono and start_wall < end_wall:
            row["transfer_clock"] = dict(start_mono_ns=start_mono, end_mono_ns=end_mono,
                                         start_wall_ns=start_wall, end_wall_ns=end_wall)
    return row


def tcp_report(output):
    rows = [line.split()[1:] for line in output.splitlines() if line.startswith("TCP_REPORT ")]
    if len(rows) != 1 or not all("=" in field for field in rows[0]):
        return {"status": "unavailable"}
    values = dict(field.split("=", 1) for field in rows[0])
    status = values.pop("status", None)
    if status in {"unavailable", "timed_out", "unexpected_path", "incomplete_counters", "transfer_failed"}:
        return {"status": status}
    if (status != "collected" or values.pop("path", None) != "awdl0" or
            set(values) != TCP_REPORT_FIELDS or not all(value.isdigit() for value in values.values())):
        return {"status": "invalid"}
    return {"status": "collected", "path": "awdl0", **{key: int(value) for key, value in values.items()}}


def measure(mac_probe, iphone_probe, sample, mode, size, digest, port, include_report,
            dylib=None, mss=None):
    mac_addr, iphone_addr = address(False), address(True)
    token = secrets.token_hex(12)
    before = {"mac": counters(False), "iphone": counters(True)}
    timeout_seconds = 67 if size > (16 << 20) else 27
    tail = [str(mode), str(size), str(sample), token, str(port)]
    if include_report:
        tail.append("--report")
    launch = ["/var/jb/usr/bin/timeout", "-k", "1s", f"{timeout_seconds}s", iphone_probe,
              "server", iphone_addr, mac_addr, *tail]
    server = subprocess.Popen(
        SSH + [shlex.join(launch)], bufsize=0, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    client = None
    bsd_row = timeline = None
    bsd_error = None
    leading = []
    ready = False
    client_timed_out = False
    try:
        deadline = time.monotonic() + 10
        while select.select([server.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
            line = server.stdout.readline()
            if not line:
                break
            leading.append(line)
            if line.strip() == b"LISTEN_READY":
                ready = True
                break
        if ready:
            try:
                command = [str(mac_probe), "client", mac_addr, iphone_addr, *tail]
                if dylib is None:
                    client = subprocess.run(command, timeout=timeout_seconds, capture_output=True, text=True)
                else:
                    client = subprocess.run(command + ["--discover-only"], timeout=11,
                                            capture_output=True, text=True)
                    found = re.findall(r"^AWDL_DISCOVERED ifindex=([1-9][0-9]*)$", client.stdout, re.MULTILINE)
                    if client.returncode == 0 and len(found) == 1:
                        from mac_awdl_bsd import transfer

                        bsd_row, timeline = transfer(
                            mac_addr, iphone_addr, int(found[0]), port, mode, sample,
                            size, bytes.fromhex(digest), dylib, mss)
                        if address(False) != mac_addr or address(True) != iphone_addr:
                            bsd_row = None
                            bsd_error = "scoped_address_changed"
                    else:
                        bsd_error = "bonjour_discovery_failed"
            except subprocess.TimeoutExpired:
                client_timed_out = True
            except (OSError, ValueError, RuntimeError) as error:
                if dylib is None:
                    raise
                bsd_error = type(error).__name__
        rest, _ = server.communicate(timeout=timeout_seconds + 2)
    finally:
        if server.poll() is None:
            try:
                server.communicate(timeout=timeout_seconds + 2)
            except subprocess.TimeoutExpired:
                server.kill()
                server.communicate()

    after = {"mac": counters(False), "iphone": counters(True)}
    deltas = {
        endpoint: {key: after[endpoint][key] - before[endpoint][key] for key in before[endpoint]}
        for endpoint in before
    }
    result = dict(sample=sample, mode=mode, bytes=size, port=port,
                  awdl_counter_deltas=deltas, status="failed")
    result["iphone_exit"] = server.returncode
    server_output = (b"".join(leading) + rest).decode(errors="replace")
    result["listener_cancelled"] = "LISTENER_CANCELLED" in server_output.splitlines()
    if include_report:
        result["tcp_report"] = {"iphone": tcp_report(server_output),
                                "mac": tcp_report(client.stdout if client else "")}
    server_row = verified_row(server_output, sample, mode, size, digest)
    if dylib is not None:
        result["mac_tcp_timeline"] = timeline
        result["mac_tcp_timeline_complete"] = bool(timeline and timeline["status"] == "complete")
        if timeline and timeline["samples"]:
            result["mac_effective_mss"] = timeline["samples"][0]["mss"]
        if bsd_error is not None:
            result["bsd_error"] = bsd_error
    if (client is None or client.returncode or server.returncode or not server_row or
            (dylib is not None and bsd_row is None)):
        result["diagnostics"] = diagnostic_lines(server_output)
        if client_timed_out:
            result["diagnostics"].append("Mac client timed out")
        if client is not None:
            result["mac_exit"] = client.returncode
            result["diagnostics"] += diagnostic_lines(client.stdout + client.stderr)
        return result
    client_row = (bsd_row if dylib is not None else
                  verified_row(client.stdout, sample, mode, size, digest))
    sender, receiver = ("mac", "iphone") if mode == 0 else ("iphone", "mac")
    if (client_row and result["listener_cancelled"] and deltas[sender]["Obytes"] >= size and
            deltas[receiver]["Ibytes"] >= size):
        result.update(status="verified", iphone=server_row, mac=client_row)
    return result


def diagnostic_lines(output):
    allowed = re.compile(
        r"^(?:LISTEN_READY|LISTENER_CANCELLED|PATH_REJECT (?:before|after) payload|TRANSFER_REJECT|"
        r"AWDL (?:Bonjour result|listener accept) timed out|"
        r"(?:browse|listener|connection|send|receive) failed(?:: domain=\d+ code=-?\d+)?)$"
    )
    return [line for line in output.splitlines() if allowed.fullmatch(line)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mib", type=int, choices=(1, 16, 64), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixed-port", type=int, metavar="1024..65535",
                        help="use one port for both diagnostic rounds")
    parser.add_argument("--report", action="store_true",
                        help="collect optional Network.framework TCP metrics on each endpoint")
    parser.add_argument("--mac-bsd-timeline", action="store_true",
                        help="use the bound Mac BSD socket and sample TCP_CONNECTION_INFO")
    parser.add_argument("--mac-sender-mss", type=int, choices=(1148,),
                        help="clamp Mac BSD sender to the effective MSS seen with Xiaomi")
    parser.add_argument("--mac-send-trials", type=int, choices=(1, 2, 3),
                        help="run only Mac-to-iPhone with this many independent ports")
    args = parser.parse_args()
    if args.mac_bsd_timeline and args.report:
        parser.error("--mac-bsd-timeline and --report are separate Mac metrics")
    if args.mac_sender_mss and not (args.mac_bsd_timeline and args.mac_send_trials):
        parser.error("--mac-sender-mss requires --mac-bsd-timeline and --mac-send-trials")
    if args.mac_send_trials and args.fixed_port:
        parser.error("--mac-send-trials needs independent ephemeral ports")
    if args.output.exists():
        parser.error("output already exists")
    if args.fixed_port is not None and not 1024 <= args.fixed_port <= 65535:
        parser.error("fixed port must be in 1024..65535")
    size = args.mib << 20
    digest = hashlib.sha256(bytes(range(256)) * (size // 256)).hexdigest()
    report = dict(interface="awdl0", size_mib=args.mib,
                  mss=args.mac_sender_mss or "system-default", samples=[])
    if args.mac_bsd_timeline:
        report["mac_transport"] = "bsd_ipv6_bound_if"
    with tempfile.TemporaryDirectory(prefix="awdl-baseline-") as work:
        mac, iphone = (Path(work) / name for name in ("mac-probe", "iphone-probe"))
        run(["xcrun", "--sdk", "macosx", "clang", *BUILD, "-o", str(mac)], timeout=30)
        dylib = None
        if args.mac_bsd_timeline:
            dylib = Path(work) / "tcp-info.dylib"
            run(["xcrun", "--sdk", "macosx", "clang", "-dynamiclib", "-Wall", "-Wextra",
                 "-Werror", str(HERE / "mac-tcp-info.c"), "-o", str(dylib)], timeout=30)
        run(["xcrun", "--sdk", "iphoneos", "clang", "-target", "arm64-apple-ios15.0",
             *BUILD, "-o", str(iphone)], timeout=30)
        run(["ldid", "-S", str(iphone)])
        if run(["ldid", "-e", str(iphone)]).stdout.strip():
            raise RuntimeError("Unexpected probe entitlements")
        blob = iphone.read_bytes()
        expected = hashlib.sha256(blob).hexdigest()
        directory = remote(["/var/jb/usr/bin/mktemp", "-d", "/var/jb/.awdl-baseline.XXXXXX"]).strip()
        if not re.fullmatch(r"/var/jb/\.awdl-baseline\.[a-zA-Z0-9]{6}", directory):
            raise RuntimeError("Unexpected temporary directory")
        path = directory + "/awdl-hashprobe"
        deployed = False
        try:
            deployed = True
            run(SSH + ["umask 077; cat > " + shlex.quote(path) + "; chmod 700 " + shlex.quote(path)],
                input=blob)
            actual = remote(["/var/jb/usr/bin/sha256sum", path]).split()[0]
            if actual != expected:
                raise RuntimeError("Device probe SHA mismatch before execution")
            report["probe_sha256"] = actual
            modes = [0] * args.mac_send_trials if args.mac_send_trials else [0, 1]
            ports = ([args.fixed_port] * len(modes) if args.fixed_port is not None else
                     secrets.SystemRandom().sample(range(49152, 65536), len(modes)))
            for sample, (mode, port) in enumerate(zip(modes, ports), 1):
                report["samples"].append(measure(mac, path, sample, mode, size, digest,
                                                  port, args.report, dylib, args.mac_sender_mss))
            if remote(["/var/jb/usr/bin/sha256sum", path]).split()[0] != expected:
                raise RuntimeError("Device probe SHA mismatch after execution")
            report["post_execution_probe_sha256_verified"] = True
        finally:
            if deployed:
                run(SSH + ["if test -f " + shlex.quote(path) + "; then /var/jb/usr/bin/unlink "
                           + shlex.quote(path) + "; fi"])
            remote(["/var/jb/usr/bin/rmdir", directory])
            remote(["test", "!", "-e", directory])
            report["temporary_device_directory_removed"] = True
            with args.output.open("x") as output:
                json.dump(report, output, indent=2)
                output.write("\n")
    return 0 if all(
        row["status"] == "verified" and
        (not args.mac_bsd_timeline or row["mac_tcp_timeline_complete"])
        for row in report["samples"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
