#!/usr/bin/env python3
"""Isolate the official EZIF firmware variable using the audited TF harness."""
import hashlib
from pathlib import Path
import runpy
import subprocess
import tempfile
import threading
import time
import json
import argparse
import re

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--run-name", default="ezif-rx-adapter-20261111-diag")
parser.add_argument("--remote-reo", type=int, choices=(0, 1, 2), default=0)
parser.add_argument("--tx-addba", choices=("off", "win64"), default="off")
parser.add_argument("--mib", type=int, choices=(1, 16, 64, 128), default=1)
parser.add_argument("--tx-ampdu", type=int, choices=(0, 1), default=1)
parser.add_argument("--firmware", choices=("ezif", "dzf2"), default="ezif")
parser.add_argument("--duplex", action="store_true")
parser.add_argument("--sta-coexist", type=int, choices=(0, 1), default=0)
parser.add_argument("--startup-only", action="store_true",
                    help="Collect startup diagnostics then release without file transfer")
parser.add_argument("--rx-ezif-marked", action="store_true")
parser.add_argument("--coex-5g-override", action="store_true")
parser.add_argument("--coalesce-us", type=int, choices=(0, 250, 500, 1000, 2000, 4000))
parser.add_argument("--rx-window", type=int, choices=(1, 64, 256))
parser.add_argument("--rounds", type=int, choices=range(1, 11), default=1,
                    help="Repeat verified transfer phases within the same radio session")
parser.add_argument("--userspace-dist", help="Use a preserved userspace directory with SHA256SUMS for comparison")
args = parser.parse_args()
if args.rx_ezif_marked and args.firmware != "ezif":
    parser.error("--rx-ezif-marked requires verified EZIF firmware")
if args.coex_5g_override and (not args.sta_coexist or not args.rx_ezif_marked):
    parser.error("--coex-5g-override requires --sta-coexist 1 --rx-ezif-marked")
if not args.run_name.replace("-", "").replace("_", "").isalnum():
    raise ValueError("run-name must be a single safe directory name")
ns = runpy.run_path(str(HERE / "run-three-findings.py"))
g = ns["start"].__globals__
if args.userspace_dist:
    selected_dist = Path(args.userspace_dist).resolve()
    original_manifest = g["dist_manifest"]
    original_manifest(selected_dist)
    g["dist_manifest"] = lambda _: original_manifest(selected_dist)
OLD = "13f01cb46efbe498c854352bfc253fa3665c14a14c37a0a6ed6a609a48a8709c"
NEW = "37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f"
FW = HERE.parent / "tx-ampdu-investigation/firmware-acquisition/official-ezif-artifacts/ezif-official-fwd-amss20.bin"
REMOTE = g["ROOT"] + "/ezif-official-37d40904.bin"
if args.firmware == "dzf2":
    NEW = OLD
    FW = HERE.parents[1] / "blobs/local/dzf2-amss20.bin"
    REMOTE = g["ROOT"] + "/dzf2-verified-13f01cb4.bin"
original_prepare = g["prepare_tf_harness"]
with tempfile.TemporaryDirectory() as tmp:
    original = original_prepare(Path(tmp)).read_text()
assert original.count("CANDIDATE=" + OLD) == 1
assert original.count('"$RADIO/amss20.bin"') == 3
modified = original.replace("CANDIDATE=" + OLD, "CANDIDATE=" + NEW)
modified = modified.replace('"$RADIO/amss20.bin"', '"' + REMOTE + '"')
if args.mib == 128:
    for ba in ("0", "1", "2"):
        small = f"2:2:{ba}:6:67108864"
        assert modified.count(small) == 1
        modified = modified.replace(small, small + f"|2:2:{ba}:6:134217728")
assert modified.count('bounded insmod "$qca" "$@"') == 1
modified = modified.replace('bounded insmod "$qca" "$@"',
                            'bounded insmod "$qca" "$@" pt_rx_msdu_buf=1')
if args.rx_ezif_marked:
    modified = modified.replace('pt_rx_msdu_buf=1', 'pt_rx_msdu_buf=1 pt_rx_ezif_marked=1')
if args.coex_5g_override:
    modified = modified.replace('pt_rx_msdu_buf=1', 'pt_rx_msdu_buf=1 pt_coex_5g_override=1')
if args.rx_window is not None:
    modified = modified.replace('pt_rx_msdu_buf=1', f'pt_rx_msdu_buf=1 pt_remote_rx_window={args.rx_window}')
if args.coalesce_us is not None:
    modified = modified.replace('set -eu\n', f'set -eu\nexport OWLY_COALESCE_US={args.coalesce_us}\n', 1)
if args.duplex:
    duplex_dir = HERE.parent / "duplex-optimization"
    probe = duplex_dir / "owly-hashprobe-duplex"
    probe_sha = g["digest"](probe)
    remote_probe = g["ROOT"] + "/owly-hashprobe-duplex"
    assert modified.count(' ./owly-hashprobe "$@"') == 1
    modified = modified.replace(' ./owly-hashprobe "$@"',
                                f' check {probe_sha} {remote_probe}\n {remote_probe} "$@"')
    runpy.run_path(str(duplex_dir / "duplex_flows.py"))["install"](g["R"])
g["HARNESS_TF_SHA"] = hashlib.sha256(modified.encode()).hexdigest()


def prepare(folder):
    script = folder / "radio-owly.sh"
    script.write_text(modified)
    subprocess.run(["sh", "-n", str(script)], check=True)
    return script


g["prepare_tf_harness"] = prepare
assert g["digest"](FW) == NEW
folder = HERE / "runs" / args.run_name
if folder.exists():
    raise RuntimeError("Use a fresh run directory; existing evidence is preserved")
print(g["ensure_restored"](), flush=True)
if args.coex_5g_override:
    wifi = g["adb"]("shell", "cmd", "wifi", "status")
    freq = re.search(r"Frequency:\s*(\d+)MHz", wifi)
    if not freq or not (5180 <= int(freq[1]) <= 5240 or 5745 <= int(freq[1]) <= 5825):
        raise RuntimeError("5GHz override probe requires connected non-DFS 5GHz STA")
if args.duplex:
    probe_stage = "/data/local/tmp/owly-duplex-stage"
    g["adb"]("push", str(probe), probe_stage)
    g["root"](f"set -eu; cp {probe_stage} {remote_probe}; chmod 700 {remote_probe}; "
              f"echo '{probe_sha}  {remote_probe}' | sha256sum -c -")
    g["adb"]("shell", "rm", probe_stage)
staging = "/data/local/tmp/tf-firmware-" + NEW[:8] + ".bin"
g["adb"]("push", str(FW), staging)
g["root"](f"set -eu; cp {staging} {REMOTE}; chown system:system {REMOTE}; "
          f"chmod 440 {REMOTE}; chcon u:object_r:firmware_file:s0 {REMOTE}")
g["adb"]("shell", "rm", staging)
g["root"](f"echo '{NEW}  {REMOTE}' | sha256sum -c -")
flags = dict(remote_assoc=1, remote_ast_override=1, remote_hash_override=1,
             remote_reo=args.remote_reo, fw_addba=0, tx_ampdu=args.tx_ampdu, dynamic_rate=0, sta_coexist=args.sta_coexist)
finished = threading.Event()
bad_rx = threading.Event()
observations = []


def watch_rx():
    while not finished.wait(0.4):
        try:
            stats = g["root"]("cat /sys/module/qca_cld3_kiwi_v2/parameters/pt_rx_msdu_stats 2>/dev/null || true")
            if not stats.strip():
                continue
            observations.append(dict(time_ns=time.time_ns(), stats=stats.strip()))
            kv = g["parse_kv_line"](stats)
            # Known ordinary peers have their own other_peer counter. Unknown
            # peers are not benign: the legacy MSDU-buffer exception handler
            # can trigger firmware recovery after its threshold is exceeded.
            # Keep the strict guard until their metadata/ownership is understood.
            fatal_keys = ("peer_miss", "bad_desc", "dropped")
            if any(int(kv.get(k, 0)) for k in fatal_keys):
                bad_rx.set()
                session = json.loads((folder / "session.json").read_text())
                g["root"](f": > {g['STATE']}/stop-{session['token']}")
                print("RX_GUARD_STOP " + stats.strip(), flush=True)
                return
        except Exception as error:
            observations.append(dict(time_ns=time.time_ns(), error=str(error)))


watcher = threading.Thread(target=watch_rx, daemon=True)
watcher.start()
try:
    g["start"](HERE / "qca_cld3_kiwi_v2-three-findings.ko", folder,
               "vht80-2ss-mcs9-sgi", flags)
    if args.coex_5g_override:
        live_wifi = g["adb"]("shell", "cmd", "wifi", "status")
        (folder / "wifi-at-ready.txt").write_text(live_wifi)
        if f"Frequency: {freq[1]}MHz" not in live_wifi:
            raise RuntimeError("STA disconnected or changed frequency during 5GHz probe startup")
    (folder / "firmware.sha256").write_text(NEW + "  " + args.firmware + " amss20.bin\n")
    (folder / "firmware-live.txt").write_text(g["root"](
        "sha256sum /vendor/firmware_mnt/image/kiwi/amss20.bin"))
    if args.rx_window is not None:
        actual_window = g["root"]("cat /sys/module/qca_cld3_kiwi_v2/parameters/pt_remote_rx_window").strip()
        if actual_window != str(args.rx_window):
            raise RuntimeError("RX window readback mismatch")
        (folder / "rx-window.txt").write_text(actual_window + "\n")
    if bad_rx.is_set():
        raise RuntimeError("RX guard stopped the short probe")
    for phase in range(1, 1 if args.startup_only else args.rounds + 1):
        if bad_rx.is_set():
            raise RuntimeError("RX guard stopped the sustained probe")
        g["sample"](folder, "2ss", args.mib, "amsdu", args.tx_addba)
        result = json.loads((folder / str(phase) / "result.json").read_text())
        if not result.get("verified"):
            raise RuntimeError("transfer phase did not pass verification")
        if args.coalesce_us is not None:
            if f"OWLY_COALESCE_US={args.coalesce_us}" not in (folder / str(phase) / "runner.log").read_text().splitlines():
                raise RuntimeError("runtime coalescing value was not confirmed")
    (folder / "rx-stats-final.txt").write_text(g["root"](
        "cat /sys/module/qca_cld3_kiwi_v2/parameters/pt_rx_msdu_stats"))
finally:
    finished.set()
    watcher.join(timeout=25)
    if folder.exists():
        (folder / "rx-observations.json").write_text(json.dumps(observations, indent=2))
        (folder / "tuning.json").write_text(json.dumps(vars(args), indent=2))
        try:
            adb_prefix = g["adb"].__globals__["ADB"]
            kernel = subprocess.check_output(
                [*adb_prefix, "shell", "su -c dmesg"], timeout=20)
            (folder / "kernel-before-stop.bin").write_bytes(kernel)
            (folder / "kernel-before-stop.txt").write_text(kernel.decode("utf-8", errors="replace"))
        except Exception as error:
            (folder / "kernel-capture-error.txt").write_text(str(error))
    try:
        if (folder / "session.json").exists():
            g["stop"](folder)
    finally:
        restored = g["ensure_restored"]()
        print(restored, flush=True)
        if folder.exists():
            (folder / "stock-after.txt").write_text(restored)
