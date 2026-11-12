#!/usr/bin/env python3
"""TCP file-payload benchmark for the isolated owly0 ↔ awdl0 link."""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import re
import shlex
import signal
import socket
import struct
import subprocess
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

DEVICE = __import__("os").environ["ANDROID_SERIAL"]
BASE = "/data/local/tmp/houji-buildtest-20261110"
ROOT = f"{BASE}/owly"
STATE = f"{ROOT}/state"
RADIO = f"{ROOT}/radio-owly.sh"
BUSYBOX = "/data/adb/ksu/bin/busybox"
ADB = ["adb", "-s", DEVICE]
HERE = Path(__file__).resolve().parent
MIB = 1024 * 1024
TCP_INFO_FIELDS = ("mss", "rtt_current_ms", "rtt_smoothed_ms", "tx_packets",
                   "tx_bytes", "tx_retransmit_packets", "tx_retransmit_bytes",
                   "rx_packets", "rx_bytes", "rx_out_of_order_bytes")
TCP_INFO_SOURCE = HERE / "results" / "mac-tcp-info-v1.c"
TIMELINE_DIR = HERE.parents[1] / "iphone-radio-research"
TIMELINE_SOURCE = TIMELINE_DIR / "mac_tcp_timeline.py"


def adb(*args, timeout=20):
    return subprocess.check_output([*ADB, *args], text=True, stderr=subprocess.STDOUT, timeout=timeout)


def root(command, timeout=20):
    return adb("shell", f"su -c {shlex.quote(command)}", timeout=timeout)


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def wait_for(check, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if value := check():
            return value
        time.sleep(0.4)
    raise TimeoutError("device state deadline")


def deploy(variant, observer=None):
    stage = adb("shell", "mktemp -d /data/local/tmp/owly-stage.XXXXXX").strip()
    if not re.fullmatch(r"/data/local/tmp/owly-stage\.[a-zA-Z0-9]+", stage):
        raise ValueError("unexpected staging directory")
    files = ["houji-owly", "wonder-init", "owly-hashprobe", "SHA256SUMS", "radio-owly.sh"]
    observer_name = None
    if observer is not None:
        observer_dir, observer_name = {
            "aggregate": ("ba-observer", "qca_cld3_kiwi_v2-observer.ko"),
            "peer": ("ba-peer-observer", "qca_cld3_kiwi_v2-peer-observer.ko"),
            "caps": ("remote-peer-probe", "qca_cld3_kiwi_v2-peer-caps.ko"),
        }[observer]
        files.append(observer_name)
    try:
        for name in files:
            dist = HERE / "dist" if variant == "baseline" else HERE / "dist" / variant
            if name == observer_name:
                source = HERE.parents[1] / observer_dir / "qca_cld3_kiwi_v2.ko"
            else:
                source = HERE / name if name.endswith(".sh") else dist / name
            adb("push", str(source), f"{stage}/{name}")
        install = f"""set -eu
test ! -e {STATE}/saved.env
test ! -e {BASE}/state/saved.env
test ! -e /sys/module/houji_wonder_bridge
cd {stage}
sha256sum -c SHA256SUMS
for f in {' '.join(files)}; do
  cp "$f" {ROOT}/"$f"
done
chmod 700 {ROOT}/houji-owly {ROOT}/wonder-init {ROOT}/owly-hashprobe {RADIO}
chmod 600 {ROOT}/SHA256SUMS
{"chmod 600 " + ROOT + "/" + observer_name if observer_name else ""}
cd {ROOT}
sha256sum -c SHA256SUMS
sh -n {RADIO}
"""
        root(f"""set -eu
test ! -L {ROOT}
mkdir -p {ROOT} {STATE}
chown 0:0 {ROOT} {STATE}
chmod 700 {ROOT} {STATE}
{BUSYBOX} flock -n {ROOT}/job.lock sh -c {shlex.quote(install)}
""")
    finally:
        adb("shell", f"rm -rf {stage}")


def current_awdl():
    info = subprocess.check_output(["ifconfig", "awdl0"], text=True)
    mac = re.search(r"\bether ([0-9a-f:]{17})", info)
    if not mac or "status: active" not in info:
        raise RuntimeError("Mac awdl0 is not active")
    return mac.group(1), socket.if_nametoindex("awdl0")


def pull_log(name):
    return root(f"test ! -f {STATE}/{name} || cat {STATE}/{name}")


def ensure_restored():
    return root(f"""set -e
test "$(cat {STATE}/status)" = STOCK_RESTORED
test ! -e {STATE}/saved.env
test ! -e {BASE}/state/saved.env
test ! -e /data/local/tmp/xos/native/saved.env
test ! -e /sys/module/houji_wonder_bridge
test ! -e /sys/module/wonder
test ! -e /sys/class/net/owly0
test ! -e /sys/class/net/wonder0
test ! -e /sys/class/net/xos0
test "$(getprop sys.xos.exclusive)" = 0
! ip -6 rule show | grep -qE '^1087[012]:'
cmd wifi status | grep -q 'Wifi is connected to'
echo STOCK_RESTORED WIFI_CONNECTED NO_EXPERIMENT_INTERFACE_OR_RULES
service check com.domestic.xoscomm.IDomesticXOsComm/default | grep ': found'
p=$(cat /data/local/tmp/xos/native/supervisor.pid)
tr '\\0' ' ' < /proc/$p/cmdline | grep -F 'bridge-supervisor149.sh locked'
echo
sha256sum /sys/module/qca_cld3_kiwi_v2/notes/.note.gnu.build-id \\
 /vendor/firmware_mnt/image/kiwi/amss20.bin /data/local/tmp/xos/native/radio.sh \\
 /data/local/tmp/xos/libxos.so /data/local/tmp/xos/native/xos-bridge.zip
""")


def load_tcp_info(path):
    library = ctypes.CDLL(str(path.resolve()))
    sample = library.awdl_tcp_info_sample
    sample.argtypes = (ctypes.c_int, ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t)
    sample.restype = ctypes.c_int

    def snapshot(sock):
        values = (ctypes.c_uint64 * len(TCP_INFO_FIELDS))()
        error = sample(sock.fileno(), values, len(values))
        if error:
            return {"errno": error, "error": os.strerror(error)}
        return dict(zip(TCP_INFO_FIELDS, values))

    return snapshot


def tcp_info_interval(before, after):
    interval = {"before": before, "after": after}
    if "errno" not in before and "errno" not in after:
        interval["delta"] = {
            key: after[key] - before[key] if after[key] >= before[key] else None
            for key in TCP_INFO_FIELDS[3:]
        }
    return interval


def load_tcp_timeline(path):
    spec = importlib.util.spec_from_file_location("houji_mac_tcp_timeline", TIMELINE_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    snapshot = module.load_v2(path.resolve())

    def timeline(sock, origin_ns):
        return module.TcpTimeline(sock, snapshot, origin_ns)

    return timeline


def transfer(ip, index, mode, size, payload, expected, sample, tcp_info=None, tcp_timeline=None,
             io_timeout=100):
    receiver = hashlib.sha256()
    row = {"direction": "Mac→小米" if mode == 0 else "小米→Mac",
           "sample": sample, "mode": mode, "bytes": size, "status": "failed"}
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            sock.settimeout(12)
            sock.setsockopt(socket.SOL_SOCKET, 0x1104, 1)  # macOS option used by the prior scoped AWDL probe
            sock.setsockopt(socket.IPPROTO_IPV6, 125, index)  # IPV6_BOUND_IF
            sock.connect((ip, 18870, 0, index))
            sock.settimeout(io_timeout)
            before = tcp_info(sock) if tcp_info else None
            start_ns = time.monotonic_ns()
            timeline = tcp_timeline(sock, start_ns) if tcp_timeline else None
            try:
                with timeline if timeline else nullcontext():
                    sock.sendall(struct.pack(">III", mode, size, sample))
                    if mode == 0:
                        sock.sendall(payload)
                        got = sock.makefile("rb").read(32)
                    else:
                        left = size
                        while left:
                            data = sock.recv(min(65536, left))
                            if not data:
                                raise EOFError(f"probe closed with {left} bytes left")
                            receiver.update(data)
                            left -= len(data)
                        got = receiver.digest()
                    if got != expected:
                        raise ValueError(f"SHA256 mismatch ({got.hex()})")
                    if mode == 1:
                        sock.sendall(got)
                    elapsed = (time.monotonic_ns() - start_ns) / 1e9
            finally:
                if timeline:
                    row["mac_tcp_timeline"] = timeline.result()
                if tcp_info:
                    row["mac_tcp_info"] = tcp_info_interval(before, tcp_info(sock))
            row.update(status="verified_mac", seconds=elapsed, MBps=size / elapsed / 1e6,
                       sha256=expected.hex())
    except (OSError, EOFError, ValueError) as err:
        row["error"] = f"{type(err).__name__}: {err}"
    return row


def measure(args):
    tcp_info = load_tcp_info(args.tcp_info_dylib) if args.tcp_info_dylib else None
    tcp_timeline = load_tcp_timeline(args.tcp_timeline) if args.tcp_timeline else None
    directory = HERE / "results" / args.name
    directory.mkdir(exist_ok=False, parents=True)
    peer, index = current_awdl()
    size = args.mib * MIB
    payload = bytes(range(256)) * (size // 256)
    expected = hashlib.sha256(payload).digest()
    token = uuid.uuid4().hex
    summary = dict(channel=149, profile=args.profile, aggregation=args.aggregation,
                   ack=args.ack, tcp_mss=args.mss, tun_queue=args.queue_len,
                   rx_block_ack=False, ba_observer=args.ba_observer,
                   vht_amsdu=args.vht_amsdu, mac_awdl=peer, samples=[])
    if tcp_info:
        summary["mac_tcp_info"] = {
            "abi": "awdl_tcp_info_sample/10",
            "helper_sha256": hashlib.sha256(args.tcp_info_dylib.read_bytes()).hexdigest(),
            "reference_source_sha256": hashlib.sha256(TCP_INFO_SOURCE.read_bytes()).hexdigest(),
            "field_source": "Darwin SDK netinet/tcp.h, tcp_connection_info",
            "units": {"mss": "bytes", "rtt": "ms", "packet_counters": "packets", "byte_counters": "bytes"},
        }
    if tcp_timeline:
        summary["mac_tcp_timeline"] = {
            "abi": "awdl_tcp_info_sample_v2/14",
            "helper_sha256": hashlib.sha256(args.tcp_timeline.read_bytes()).hexdigest(),
            "reference_source_sha256": hashlib.sha256((TIMELINE_DIR / "mac-tcp-info.c").read_bytes()).hexdigest(),
            "sampler_source_sha256": hashlib.sha256(TIMELINE_SOURCE.read_bytes()).hexdigest(),
            "field_source": "Darwin SDK netinet/tcp.h, tcp_connection_info",
            "units": {"mss": "bytes", "rtt": "ms", "offset": "ns", "windows": "bytes",
                      "packet_counters": "packets", "byte_counters": "bytes"},
        }
    log = f"{STATE}/job-{token}.log"
    command = shlex.join(["sh", RADIO, "run", token, args.profile, args.aggregation, args.ack,
                          peer, str(args.pairs * 2), str(size), str(args.mss), str(args.queue_len),
                          args.ba_observer, str(int(args.vht_amsdu))])
    completion = f"{STATE}/exit-{token}"
    launcher = shlex.quote(f'{command}; rc=$?; echo "$rc" >{completion}; exit "$rc"')

    def exit_code():
        return root(f"test ! -f {completion} || cat {completion}").strip()

    def owns_logs():
        return pull_log("token").strip() == token

    launched = False
    try:
        root(f"{BUSYBOX} setsid sh -c {launcher} </dev/null >{log} 2>&1 &")
        launched = True
        (directory / "device-build.sha256").write_text(root(f"cat {ROOT}/SHA256SUMS"))

        def ready():
            if result := exit_code():
                raise RuntimeError(f"radio exited before ready, rc={result}")
            if not owns_logs():
                return None
            return re.search(r"^HASH_READY (fe80:[0-9a-f:]+)$", pull_log("server.log"), re.M)

        phone = wait_for(ready, 45).group(1)
        summary["phone_owly0"] = phone
        print(f"READY profile={args.profile} aggregation={args.aggregation} awdl0={peer}", flush=True)
        for pair in range(1, args.pairs + 1):
            for mode in (0, 1):
                row = transfer(phone, index, mode, size, payload, expected,
                               (pair - 1) * 2 + mode + 1, tcp_info, tcp_timeline)
                row["pair"] = pair
                summary["samples"].append(row)
                save(directory / "results.json", summary)
                report = row
                if timeline := row.get("mac_tcp_timeline"):
                    report = {**row, "mac_tcp_timeline": {
                        "status": timeline["status"], "samples": len(timeline["samples"]),
                    }}
                print(json.dumps(report, ensure_ascii=False), flush=True)
        # Allow the server to finish verification and write its final HASH_OK.
        time.sleep(1.2)
    except (RuntimeError, TimeoutError, subprocess.SubprocessError) as err:
        summary["test_error"] = f"{type(err).__name__}: {err}"
    finally:
        if launched:
            try:
                root(f"sh {RADIO} stop {token}")
                result = wait_for(exit_code, 100)
                summary["radio_exit"] = int(result)
                (directory / "restored.txt").write_text(ensure_restored())
                summary["restored"] = True
            except (OSError, subprocess.SubprocessError, ValueError) as err:
                summary["restore_error"] = f"{type(err).__name__}: {err}"
                summary["restored"] = False
            finally:
                try:
                    owned_logs = owns_logs()
                except subprocess.SubprocessError:
                    owned_logs = False
                if owned_logs:
                    for name in ("runner.log", "server.log", "before.txt", "after.txt", "stock-reg.txt"):
                        try:
                            (directory / name).write_text(pull_log(name))
                        except subprocess.SubprocessError:
                            pass
                try:
                    (directory / "lifecycle.log").write_text(root(f"cat {log}"))
                except subprocess.SubprocessError:
                    pass
        server = directory / "server.log"
        receipts = re.findall(
            r"^HASH_OK sample=(\d+) mode=(\d+) bytes=(\d+) seconds=([.\d]+) sha256=([a-f0-9]+)$",
            server.read_text() if server.exists() else "", re.M,
        )
        by_sample = {int(sample): (int(mode), int(length), float(seconds), digest)
                     for sample, mode, length, seconds, digest in receipts}
        summary["server_sha_receipts"] = len(receipts)
        for row in summary["samples"]:
            if row["status"] != "verified_mac":
                continue
            receipt = by_sample.get(row["sample"])
            if receipt and (row["mode"], row["bytes"], row["sha256"]) == (
                receipt[0], receipt[1], receipt[3],
            ):
                row.update(status="verified", phone_seconds=receipt[2])
            else:
                row.update(status="unconfirmed", error="server hash receipt missing or disagrees")
        save(directory / "results.json", summary)
    return (summary.get("restored") is True and summary.get("radio_exit") == 0
            and len(summary["samples"]) == args.pairs * 2
            and all(row["status"] == "verified" for row in summary["samples"]))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("deploy")
    install.add_argument("--variant", choices=("baseline", "coalesce", "mss", "mss-vht"), default="baseline")
    observers = install.add_mutually_exclusive_group()
    observers.add_argument("--include-ba-observer", dest="observer", action="store_const", const="aggregate")
    observers.add_argument("--include-ba-peer-observer", dest="observer", action="store_const", const="peer")
    observers.add_argument("--include-peer-caps", dest="observer", action="store_const", const="caps")
    trial = sub.add_parser("measure")
    trial.add_argument("name")
    trial.add_argument("profile", choices=("ht20", "vht80", "vht80-sgi"))
    trial.add_argument("aggregation", choices=("single", "amsdu"))
    trial.add_argument("ack", choices=("ack", "noack"))
    trial.add_argument("--mib", type=int, choices=range(1, 129), default=64)
    trial.add_argument("--mss", type=int, choices=(0, 1160), default=0)
    trial.add_argument("--queue-len", type=int, choices=(500, 2048), default=500)
    trial.add_argument("--ba-observer", choices=("reference", "off", "on", "peer"), default="reference")
    trial.add_argument("--vht-amsdu", action="store_true")
    telemetry = trial.add_mutually_exclusive_group()
    telemetry.add_argument("--tcp-info-dylib", type=Path, metavar="PATH")
    telemetry.add_argument("--tcp-timeline", type=Path, metavar="DYLIB")
    trial.add_argument("--pairs", type=int, choices=range(1, 5), default=2)
    args = parser.parse_args()
    if args.command == "deploy":
        deploy(args.variant, args.observer)
        return 0
    if args.queue_len != 500 and args.mss != 1160:
        parser.error("queue control requires the MSS 1160 comparison")
    if args.ba_observer != "reference" and (
        args.profile, args.aggregation, args.ack, args.mss, args.queue_len
    ) != ("vht80-sgi", "amsdu", "ack", 1160, 500):
        parser.error("BA observer comparison requires VHT80 SGI, A-MSDU, ACK, MSS 1160 and queue 500")
    if args.vht_amsdu and (
        args.profile, args.aggregation, args.ack, args.mss, args.queue_len, args.ba_observer
    ) != ("vht80-sgi", "amsdu", "ack", 1160, 500, "reference"):
        parser.error("VHT A-MSDU comparison requires VHT80 SGI, A-MSDU, ACK, MSS 1160, queue 500 and reference WLAN")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.name):
        parser.error("invalid trial name")
    return 0 if measure(args) else 1


if __name__ == "__main__":
    def interrupt(_number, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    raise SystemExit(main())
