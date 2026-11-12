#!/usr/bin/env python3
"""Collect allowlisted native AWDL scheduler events around one self-owned pair."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
import threading
import time


HERE = Path(__file__).resolve().parent
RUNNER = HERE / "run-awdl-baseline.py"
PROPERTIES = {"IO80211Channel", "IO80211ChannelBandwidth", "IO80211ChannelFrequency"}
NODE = re.compile(r"\+-o\s+(\S+)\s+<class\s+(\w+)")
PROP = re.compile(r'"([^"]+)" = (.+)$')
ROLES = {"en0", "awdl0", "llw0"}
STATE = re.compile(
    r"AWDL ON:\s*\[infra\((\d+)\)\s*(\d+)%\]\s*"
    r"(?:\[sdb:(\d+)\]\s*)?\((\d+)/(\d+)/(\d+)\)\s*"
    r"\[([\d ]+)\]\s*(Infra Priority|AWDL Priority|Low Power|Default|Real Time|Data|Idle)?"
)
RRM = re.compile(r"Channel = (\d+), bmap ((?:[0-9a-fA-F]{2}(?: |$)){1,8})")
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)")
PREDICATE = (
    'process == "kernel" AND (eventMessage CONTAINS "com.apple.p2p.awdl0" '
    'OR eventMessage CONTAINS "Inserting Awdl availability in RRM table")'
)


def run(argv, timeout=8):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=True).stdout


def registry(argv):
    nodes = []
    current = None
    ancestors = []
    for line in run(argv).splitlines():
        node = NODE.search(line)
        if node:
            name, kind = node.groups()
            depth = node.start()
            while ancestors and ancestors[-1][0] >= depth:
                ancestors.pop()
            current = None
            if (name in (ROLES | {"AppleBCMWLANSkywalkInterface",
                                  "AppleBCMWLANCore", "IOSkywalkLegacyEthernet",
                                  "IOSkywalkNetworkBSDClient",
                         "AppleBCMWLANProximityInterface", "AppleBCMWLANLowLatencyInterface"}) or
                    kind in {"AppleBCMWLANProximityInterface", "AppleBCMWLANLowLatencyInterface"}):
                current = {"provider": name, "class": kind,
                           "ancestors": [ancestor for _, ancestor in ancestors]}
                nodes.append(current)
                ancestors.append((depth, name))
        prop = PROP.search(line)
        if current is not None and prop:
            key, value = prop.groups()
            if key in PROPERTIES and value.isdigit():
                current[key] = int(value)
            if key == "BSD Name" and value.strip('"') in ROLES:
                current["interface"] = value.strip('"')
    return nodes


def mac_schedule(start_ns, end_ns, target_macs=()):
    since = datetime.fromtimestamp(start_ns // 1_000_000_000).strftime("%Y-%m-%d %H:%M:%S")
    until = datetime.fromtimestamp(end_ns // 1_000_000_000 + 1).strftime("%Y-%m-%d %H:%M:%S")
    text = run(["/usr/bin/log", "show", "--start", since, "--end", until,
                "--info", "--debug", "--style", "compact", "--predicate", PREDICATE], timeout=25)
    summary = {"schedule": [], "rrm": [], "steering": [], "peer_dumps": [],
               "rows_seen": 0, "phy_or_ba_labels": 0}
    for line in text.splitlines():
        stamp = STAMP.match(line)
        if not stamp:
            continue
        wall = round(datetime.fromisoformat(stamp.group(1)).timestamp() * 1e9)
        if not start_ns <= wall <= end_ns:
            continue
        summary["rows_seen"] += 1
        if re.search(r"\b(?:MCS|NSS|A-MPDU|AMPDU|BlockAck|ADDBA|DELBA)\b", line, re.I):
            summary["phy_or_ba_labels"] += 1
        state = STATE.search(line)
        if state:
            infra, pct, sdb, social1, social2, social3, sequence, label = state.groups()
            slots = [int(ch) for ch in sequence.split()]
            if len(slots) == 16:
                summary["schedule"].append({
                    "wall_ns": wall, "infra_channel": int(infra), "infra_percent_raw": int(pct),
                    "sdb_raw": int(sdb) if sdb else None,
                    "channel_triplet": [int(social1), int(social2), int(social3)],
                    "slots": slots, "state": label,
                })
        if "Inserting Awdl availability in RRM table" in line:
            channels = [{"channel": int(channel), "bitmap": bitmap.strip().split()}
                        for channel, bitmap in RRM.findall(line)]
            if channels:
                summary["rrm"].append({"wall_ns": wall, "channels": channels})
        matched = [role for role, address in target_macs if address in line.lower()]
        if "peerChannelSteer" in line:
            summary["steering"].append({"wall_ns": wall, "matched_roles": matched})
        if "AWDLStateDump" in line:
            fields = {key: int(value) for key, value in
                      re.findall(r"\b(peers|cached|SDB|infraRT|Tx|Rx):\s*(\d+)\b", line)}
            if fields or matched:
                summary["peer_dumps"].append({"wall_ns": wall, "matched_roles": matched, **fields})
    return summary


def awdl_mac(argv):
    match = re.search(r"^\s+ether ((?:[0-9a-f]{2}:){5}[0-9a-f]{2})\b",
                      run(argv), re.I | re.MULTILINE)
    return match.group(1).lower() if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mib", type=int, choices=(16, 64), default=16)
    parser.add_argument("--measurement", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.measurement.exists():
        parser.error("output path already exists")

    runner = runpy.run_path(RUNNER)
    ssh = runner["SSH"]
    options = ["-l", "-w0", "-r", "-c", "AppleBCMWLANCore", "-d", "6"]
    iphone_query = ssh + ["/usr/sbin/ioreg " + " ".join(options)]
    mac_query = ["/usr/sbin/ioreg", *options]
    providers = []
    stop = threading.Event()

    def poll():
        while not stop.is_set() and len(providers) < 300:
            for host, command in (("mac", mac_query), ("iphone", iphone_query)):
                wall, mono = time.time_ns(), time.monotonic_ns()
                try:
                    nodes = registry(command)
                    row = {"providers": nodes}
                except (OSError, subprocess.SubprocessError) as error:
                    row = {"error": type(error).__name__}
                providers.append({"host": host, "wall_ns": wall, "mono_ns": mono, **row})
            stop.wait(0.45)

    roles_before = [("mac", awdl_mac(["/sbin/ifconfig", "awdl0"])),
                    ("iphone", awdl_mac(ssh + ["/var/jb/usr/sbin/ifconfig awdl0"]))]
    start = time.time_ns()
    start_mono = time.monotonic_ns()
    worker = threading.Thread(target=poll, name="awdl-registry-poll")
    worker.start()
    status = None
    try:
        time.sleep(2)
        sys.argv = [str(RUNNER), "--mib", str(args.mib), "--report", "--output", str(args.measurement)]
        status = runner["main"]()
        time.sleep(2)
    finally:
        stop.set()
        worker.join()
    end, end_mono = time.time_ns(), time.monotonic_ns()
    roles_after = [("mac", awdl_mac(["/sbin/ifconfig", "awdl0"])),
                   ("iphone", awdl_mac(ssh + ["/var/jb/usr/sbin/ifconfig awdl0"]))]
    unchanged = {role: bool(before and before == after)
                 for (role, before), (_, after) in zip(roles_before, roles_after)}
    target_macs = [(role, addr) for role, addr in roles_before if unchanged[role]]
    evidence = {"wall_start_ns": start, "wall_end_ns": end,
                "mono_start_ns": start_mono, "mono_end_ns": end_mono,
                "measurement_exit": status, "exact_address_unchanged": unchanged,
                "providers": providers}
    try:
        evidence["mac_kernel_log"] = mac_schedule(start, end, target_macs)
    except (OSError, subprocess.SubprocessError) as error:
        evidence["mac_kernel_log"] = {"error": type(error).__name__}
    with args.output.open("x") as out:
        json.dump(evidence, out, indent=2)
        out.write("\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
