#!/usr/bin/env python3
"""One fixed-channel remote-peer transaction using the isolated WLAN lifecycle."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

HERE = Path(__file__).resolve().parent
REO = HERE / "reo-next"
FLOW = HERE / "raw-flow-next"
CAPACITY = HERE / "vht-rx-next"
OUTGOING = CAPACITY / "outgoing-advert"
PSF = CAPACITY / "outgoing-psf"
RX_PHY = CAPACITY / "rx-phy-next"
RUNNER = HERE.parent / "owly-evaluation" / "houji-runner"
sys.path.insert(0, str(RUNNER))
from bench import BASE, BUSYBOX, MIB, ROOT, STATE, adb, current_awdl, ensure_restored, root, transfer, wait_for

RADIO = ROOT + "/radio-remote-peer.sh"
MODULE = ROOT + "/qca_cld3_kiwi_v2-remote-peer.ko"
SHA = ROOT + "/remote-peer.sha"
OUTGOING_APP = ROOT + "/houji-owly-outgoing-vht"
OUTGOING_SHA = ROOT + "/outgoing-vht.sha"
OUTGOING_HARNESS_SHA = "df40147c2e25019b7651a496dc41bcb5b5d2bc3edcabf18b0a8b2791ed154150"
RX_PHY_HARNESS_SHA = "fccfdde53badc02deef3737105111a5701f98b38df2f752fed4795af0de159c9"
PSF_HARNESS_SHA = "cae9dac0250998065abfc5723c6976ebed892cd78b3bc007020aead637fccf73"
CAPACITY_HARNESS_SHA = "af9caf9cfa5beb4758c8964dfd94cddc719c7d05b80fbf6a7260d4deeb99363d"
MAP_ONLY_HARNESS_SHA = "e53fbee3f500996eb48bf5f9754c2ba91f43107b771e3498462ce7eca49a8488"
SELF_FLOW_HARNESS_SHA = "9b31b7f7f2805144dddf14930eb537f692ef95368dace2c6d5f6c72f3942eec7"
AST_HARNESS_SHA = "361c615f7479a919bae1fc7032728f79f875596cf3144698b0c1667bfe6ac2a4"
FLOW_HARNESS_SHA = "5f79c112b0d7eb9c153d75a3441a4aa47f3e535d9a959b50a06be49957a4da60"
REO_HARNESS_SHA = "f89e09c77dd514e92f21dce457b7426f436600ea913cd2c2b7a783a4a9e5ef0b"
HARNESS_SHA = "97204c7841d340fe571d6c756f7cf25a8d83b189e7f87090e2c0672cdbefec64"
PATCHES = ("peer-cross-boot-harness.patch", "peer-reboot-rehearsal-harness.patch",
           "remote-peer-device-harness.patch")
REQUIRED = {
    "result": 3, "obj_attached": 1, "obj_created": 1, "dp_created": 1,
    "create_issued": 1, "create_returned": 1, "create_submit": 0,
    "create_confirm": 1, "create_status": 0, "create_conflict": 0,
    "htt_map": 1, "slot": 1, "delete_issued": 1, "delete_returned": 1,
    "delete_submit": 0, "delete_confirm": 1, "htt_unmap": 1, "slot_removed": 1,
    "dp_released": 1, "obj_released": 1, "dp_conflict": 0, "compensation": 0,
}


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def harness_sha(reo=False, flow=False, ast=False, self_flow=False, map_only=False, capacity=False, outgoing=False, psf=False, phy=False):
    if phy:
        return RX_PHY_HARNESS_SHA
    if psf:
        return PSF_HARNESS_SHA
    if outgoing:
        return OUTGOING_HARNESS_SHA
    if capacity:
        return CAPACITY_HARNESS_SHA
    if map_only:
        return MAP_ONLY_HARNESS_SHA
    if self_flow:
        return SELF_FLOW_HARNESS_SHA
    if ast:
        return AST_HARNESS_SHA
    return FLOW_HARNESS_SHA if flow else REO_HARNESS_SHA if reo else HARNESS_SHA


def prepare_harness(folder, reo=False, flow=False, ast=False, self_flow=False, map_only=False, capacity=False, outgoing=False, psf=False, phy=False):
    source = folder / "radio-owly.sh"
    shutil.copyfile(RUNNER / source.name, source)
    patches = [HERE / patch for patch in PATCHES]
    if reo or flow or ast or self_flow or map_only:
        patches.append(REO / "device-reo-harness.patch")
    if flow or ast or self_flow or map_only:
        patches.append(FLOW / "device-flow-harness.patch")
    if ast:
        patches.append(FLOW / "device-ast-harness.patch")
    if self_flow:
        patches.append(FLOW / "device-self-flow-harness.patch")
    if map_only:
        patches.append(FLOW / "device-map-only-harness.patch")
    if capacity:
        patches = [HERE / PATCHES[0], CAPACITY / "device-rx-capacity-harness.patch",
                   CAPACITY / "device-rx-capacity-length-harness.patch"]
    if outgoing:
        patches.append(OUTGOING / "device-outgoing-vht-harness.patch")
    if psf:
        if not outgoing or not capacity:
            raise ValueError("PSF requires the capacity and outgoing VHT harness")
        patches.append(PSF / "device-outgoing-psf-harness.patch")
    if phy:
        if not outgoing or not capacity or psf:
            raise ValueError("RX PHY requires the capacity and MIF-only app")
        patches.append(RX_PHY / "device-rx-phy-harness.patch")
    for patch in patches:
        subprocess.run(["git", "apply", str(patch)], cwd=folder, check=True)
    subprocess.run(["sh", "-n", str(source)], check=True)
    if digest(source) != harness_sha(reo, flow, ast, self_flow, map_only, capacity, outgoing, psf, phy):
        raise RuntimeError("assembled harness SHA differs from the reviewed source")
    return source


def stage(module, expected, reo=False, flow=False, ast=False, self_flow=False,
          map_only=False, capacity=False, outgoing=None, psf=False, phy=False):
    staging = adb("shell", "mktemp -d /data/local/tmp/owly-peer-stage.XXXXXX").strip()
    if not re.fullmatch(r"/data/local/tmp/owly-peer-stage[.][a-zA-Z0-9]+", staging):
        raise RuntimeError("unexpected device staging directory")
    try:
        with tempfile.TemporaryDirectory(prefix="owly-peer-harness.") as temporary:
            folder = Path(temporary)
            script = prepare_harness(folder, reo, flow, ast, self_flow, map_only, capacity, bool(outgoing), psf, phy)
            sha = folder / "remote-peer.sha"
            sha.write_text(expected + "\n")
            files = [(script, "runner.sh"), (module, "wlan.ko"), (sha, "remote-peer.sha")]
            if outgoing:
                app, app_hash = outgoing
                sidecar = folder / "outgoing-vht.sha"
                sidecar.write_text(app_hash + "\n")
                files.extend(((app, "owly-app"), (sidecar, "outgoing-vht.sha")))
            for path, dest in files:
                adb("push", str(path), f"{staging}/{dest}")
        install = f"""set -eu
test ! -e {STATE}/saved.env
test ! -e {BASE}/state/saved.env
test ! -e /data/local/tmp/xos/native/saved.env
test ! -e {RADIO}
test ! -e {MODULE}
test ! -e {SHA}
cd {staging}
echo '{harness_sha(reo, flow, ast, self_flow, map_only, capacity, bool(outgoing), psf, phy)}  runner.sh' | sha256sum -c -
echo '{expected}  wlan.ko' | sha256sum -c -
"""
        if outgoing:
            install += f"""test ! -e {OUTGOING_APP}
test ! -e {OUTGOING_SHA}
echo '{app_hash}  owly-app' | sha256sum -c -
test "$(cat outgoing-vht.sha)" = '{app_hash}'
trap 'rc=$?; if test "$rc" != 0; then rm -f {RADIO} {MODULE} {SHA} {OUTGOING_APP} {OUTGOING_SHA}; fi; exit "$rc"' EXIT
cp owly-app {OUTGOING_APP}
cp outgoing-vht.sha {OUTGOING_SHA}
chmod 700 {OUTGOING_APP}
chmod 600 {OUTGOING_SHA}
"""
        install += f"""cp runner.sh {RADIO}
cp wlan.ko {MODULE}
cp remote-peer.sha {SHA}
chmod 700 {RADIO}
chmod 600 {MODULE} {SHA}
"""
        if outgoing:
            install += "trap - EXIT\n"
        root(f"{BUSYBOX} flock -n {ROOT}/job.lock sh -c {shlex.quote(install)}")
    finally:
        adb("shell", f"rm -rf {staging}")


def parse_line(log, marker):
    rows = [line.removeprefix(marker + " ") for line in log.splitlines()
            if line.startswith(marker + " ")]
    if len(rows) != 1:
        return None
    return {k: int(v) if v.isdigit() or v.startswith("-") and v[1:].isdigit() else v
            for k, v in (word.split("=", 1) for word in rows[0].split())}


def verified(initial, terminal, final, drain, radio_exit, restored):
    if radio_exit or not restored or not initial or not terminal or not final or not drain:
        return False
    if initial.get("generation") != 0 or initial.get("result") != 0:
        return False
    if not final.get("generation") or final.get("peer_id", 65535) == 65535:
        return False
    if any(terminal.get(field) != final.get(field) for field in ("generation", "vdev", "peer_id")):
        return False
    return (all(final.get(key) == terminal.get(key) == value for key, value in REQUIRED.items())
            and all(drain.get(key) == value for key, value in
                    {"phase": 5, "error": 0, "closing": 1, "peer_requested": 1,
                     "peer_started": 1, "peer_result": 0}.items()))


def stock_vht_capabilities():
    return root(r"""set -e
phy=$(iw dev wlan0 info | awk '$1 == "wiphy" {print $2}')
case "$phy" in ''|*[!0-9]*) exit 1;; esac
echo "stock_phy=$phy"
info=$(iw phy "phy$phy" info)
printf '%s\n' "$info" | awk '
 /Band [0-9]+:/ { band=$0; capture=0 }
 /VHT Capabilities/ { print band; capture=1 }
 capture { print }
 /VHT TX highest supported/ { capture=0 }
 /5745 MHz/ { print }
'""")


def reo_log_snapshot():
    snapshot = root(r"""set -eo pipefail
cat /proc/sys/kernel/random/boot_id
dmesg | awk '/PT_REO_DMA_(FREE|ERROR) generation=/ {print}'
""").splitlines()
    if not snapshot or not re.fullmatch(r"[0-9a-f-]{36}", snapshot[0]):
        raise RuntimeError("REO dmesg snapshot lacks boot ID")
    if any(not re.search(r"PT_REO_DMA_(?:FREE|ERROR) generation=", row)
           for row in snapshot[1:]):
        raise RuntimeError("unexpected REO log output")
    return snapshot[0], snapshot[1:]


def bounded_kernel_log_snapshot(pattern):
    rows = root(r"""set -eo pipefail
cat /proc/sys/kernel/random/boot_id
dmesg | awk -v pattern=""" + shlex.quote(pattern) + r""" '
 match($0, /^\[[[:space:]]*[0-9]+\.[0-9]+\]/) {
   stamp=substr($0, RSTART, RLENGTH)
   gsub(/[][[:space:]]/, "", stamp)
   if (first == "") first=stamp
   else if (stamp+0 < last+0) nonmonotonic=1
   last=stamp
 }
 $0 ~ pattern {print}
 END {if (first == "" || last == "" || nonmonotonic) exit 2; print first, last}
'
""").splitlines()
    if (len(rows) < 2 or not re.fullmatch(r"[0-9a-f-]{36}", rows[0]) or
            not re.fullmatch(r"\d+\.\d+ \d+\.\d+", rows[-1]) or
            any(not re.search(pattern, row) for row in rows[1:-1])):
        raise RuntimeError("invalid bounded dmesg snapshot")
    first, last = map(float, rows[-1].split())
    if first > last:
        raise RuntimeError("invalid kernel log timestamp bounds")
    return rows[0], rows[1:-1], first, last


def write_kernel_log_snapshot(path, snapshot):
    boot, rows, first, last = snapshot
    path.write_text(json.dumps({"boot": boot, "rows": rows, "time_range": [first, last]},
                               indent=2) + "\n")


def map_only_log_snapshot():
    return bounded_kernel_log_snapshot(r"PT_REO_DMA_(FREE|ERROR) generation=")


def kernel_log_timestamps(rows, first, last):
    stamps = []
    for row in rows:
        match = re.match(r"^\[\s*(\d+\.\d+)\]", row)
        if not match:
            return None
        stamp = float(match.group(1))
        if not first <= stamp <= last or (stamps and stamp < stamps[-1]):
            return None
        stamps.append(stamp)
    return stamps


def kernel_log_delta(before, after):
    same_boot = after[0] == before[0]
    old_rows, new_rows = Counter(before[1]), Counter(after[1])
    rows = list((new_rows - old_rows).elements())
    old_stamps = kernel_log_timestamps(before[1], before[2], before[3])
    after_stamps = kernel_log_timestamps(after[1], after[2], after[3])
    if old_stamps is None or after_stamps is None:
        return same_boot, False, rows
    retained = Counter(row for row, stamp in zip(before[1], old_stamps) if stamp >= after[2])
    fresh_stamps = kernel_log_timestamps(rows, after[2], after[3])
    coverage = (same_boot and after[2] <= before[3] <= after[3] and not (retained - new_rows)
                and fresh_stamps is not None and all(stamp > before[3] for stamp in fresh_stamps))
    return same_boot, coverage, rows


def map_only_dma_result(before, after):
    same_boot, coverage, rows = kernel_log_delta(before, after)
    return {"same_boot": same_boot, "kernel_log_coverage": coverage, "new_dma_logs": rows,
            "verified": coverage and not rows}


def outgoing_app_manifest(directory=OUTGOING / "dist"):
    manifest = (directory / "SHA256SUMS").read_text()
    rows = re.findall(r"^([0-9a-f]{64})  ([a-z-]+)$", manifest, re.M)
    files = {name: sha for sha, name in rows}
    if len(rows) != 3 or len(manifest.splitlines()) != 3 or files.keys() != {"houji-owly", "wonder-init", "owly-hashprobe"}:
        raise RuntimeError("invalid outgoing VHT app manifest")
    for name, sha in files.items():
        if digest(directory / name) != sha:
            raise RuntimeError(f"outgoing VHT manifest mismatch for {name}")
        if name != "houji-owly" and digest(RUNNER / "dist" / "mss" / name) != sha:
            raise RuntimeError(f"outgoing VHT fixture changed the stable {name}")
    return (directory / "houji-owly", files["houji-owly"]), manifest


def outgoing_vht_result(lifecycle, runner_log, app_hash, enabled):
    expected_wire = "bf0c00000000fcff0000fcff0000" if enabled else "absent"
    marker = "OWLY_MIF_SUBMITTED_VHT_CONTAINER="
    wire = [line.removeprefix(marker) for line in runner_log.splitlines() if line.startswith(marker)]
    switches = [line for line in runner_log.splitlines() if line.startswith("OWLY_OUTGOING_VHT=")]
    build = [line for line in lifecycle.splitlines() if line.startswith("OUTGOING_VHT_BUILD_SHA=")]
    app_match = build == [f"OUTGOING_VHT_BUILD_SHA={app_hash}"]
    return {"app_sha256": app_hash, "matching_app_load": app_match, "requested": enabled,
            "reported_switch": switches, "first_host_submitted_mif_container": wire,
            "verified": app_match and switches == [f"OWLY_OUTGOING_VHT={str(enabled).lower()}"]
                        and wire == [expected_wire]}


PSF_SCOPE = {"off": ("0", "Off"), "mif": ("1", "Mif"), "both": ("2", "PsfAndMif")}


def outgoing_psf_result(lifecycle, runner_log, app_hash, requested):
    scope = PSF_SCOPE[requested][1]
    expected = "bf0c00000000fcff0000fcff0000"
    expected_psf = expected if requested == "both" else "absent"
    expected_mif = expected if requested != "off" else "absent"
    def rows(prefix, log=runner_log):
        return [line.removeprefix(prefix) for line in log.splitlines() if line.startswith(prefix)]

    app_match = rows("OUTGOING_VHT_BUILD_SHA=", lifecycle) == [app_hash]
    observed = rows("OWLY_OUTGOING_VHT_SCOPE=")
    psf = rows("OWLY_PSF_SUBMITTED_VHT_CONTAINER=")
    mif = rows("OWLY_MIF_SUBMITTED_VHT_CONTAINER=")
    return {"app_sha256": app_hash, "matching_app_load": app_match,
            "requested": requested, "reported_scope": observed,
            "first_host_submitted_psf_container": psf, "first_host_submitted_mif_container": mif,
            "verified": app_match and observed == [scope] and psf == [expected_psf] and mif == [expected_mif]}


def rx_capacity_result(before, after, lifecycle, module_sha):
    same_boot, coverage, rows = kernel_log_delta(before, after)
    formats = {
        "cfg": r"cfg_bytes=(\d+)",
        "pool": r"pool_bytes=(\d+) align=(\d+) entries=(\d+)",
        "map": r"map_bytes=(\d+) linear=(\d+)",
        "htt": r"htt_bytes=(\d+) mac_id=(\d+) host_submit_status=(-?\d+)",
        "raw": r"raw_osif_bytes=(\d+) tlv=(\d+) pad=(\d+) linear_span=(\d+)",
    }
    observed = {key: [] for key in formats}
    unexpected = []
    for row in rows:
        fields = row.partition("PT_RX_CAPACITY ")[2]
        for key, pattern in formats.items():
            if match := re.fullmatch(pattern, fields):
                observed[key].append(tuple(map(int, match.groups())))
                break
        else:
            unexpected.append(row)
    lifecycle_lines = lifecycle.splitlines()
    owner = (lifecycle_lines.count("RX_CAPACITY_LOAD_READBACK=Y") == 1 and
             lifecycle_lines.count(f"WLAN_BUILD_SHA={module_sha} BA_OBSERVER_REQUEST=reference") == 1)
    verified = (coverage and owner and not unexpected and all(observed.values()) and
                all(size == 4224 for size, in observed["cfg"]) and
                all(size == 4224 and align == 128 and entries > 0
                    for size, align, entries in observed["pool"]) and
                all(size == 4224 and linear >= size for size, linear in observed["map"]) and
                all(size == 4224 and status == 0 for size, _, status in observed["htt"]) and
                all(tlv == 272 and 0 <= pad <= 3 and 26 <= size <= 4224 - tlv - pad and span >= 4224
                    for size, tlv, pad, span in observed["raw"]))
    return {"same_boot": same_boot, "kernel_log_coverage": coverage, "matching_load": owner,
            "boot_before": before[0], "boot_after": after[0],
            "dmesg_time_before": before[2:], "dmesg_time_after": after[2:],
            "dmesg_matching_rows_before": before[1], "dmesg_matching_rows_after": after[1],
            "observed": observed, "unexpected": unexpected, "new_lines": rows,
            "verified": bool(verified)}


def reo_device_result(before, after, final):
    boot, old_rows = before
    if after[0] != boot:
        raise RuntimeError("boot changed before REO DMA receipts were collected")
    rows = list((Counter(after[1]) - Counter(old_rows)).elements())
    result = {"boot_id": boot, "new_dma_logs": rows, "verified": False,
              "dma_cleanup_verified": False, "firmware_queue_execution": "unknown"}
    required = {"reo_enabled": 1, "reo_steps": 7, "reo_duplicates": 0,
                "reo_conflict": 0, "setup_issued": 1, "setup_returned": 1,
                "setup_status": 0, "route_submit": 0, "nonqos_setup": 0, "tid0_setup": 0}
    if not final or any(final.get(key) != value for key, value in required.items()):
        return result
    if any("PT_REO_DMA_ERROR " in line for line in rows):
        return result

    def mask(field):
        return int(str(final[field]), 16)

    nonqos, tid0 = final["nonqos_flags"], final["tid0_flags"]
    if nonqos & 7 != 7 or tid0 & 7 != 7 or tid0 >> 8 != 0 or not 0 < nonqos >> 8 < 32:
        return result
    nonqos_tid = nonqos >> 8
    expected = (1 << nonqos_tid) | 1
    posted, tails, qref = (mask(field) for field in
                           ("reo_queue_posted", "reo_queue_tail", "reo_qref_write"))
    qref_mode = bool(nonqos & 8)
    if qref_mode != bool(tid0 & 8):
        return result
    if ((qref_mode and (qref != expected or posted or tails)) or
            (not qref_mode and (qref or posted != expected or tails != expected))):
        return result
    if (mask("reo_queue_error") or mask("reo_queue_duplicates") or
            qref & ~mask("reo_qref_clear") or mask("reo_dma_detached") != expected or
            mask("reo_dma_duplicates") or mask("reo_dma_conflict")):
        return result
    pattern = (r"PT_REO_DMA_FREE generation=([0-9]+) tid=([0-9]+) "
               r"path=([0-9]+) flush_status=([0-9]+)$")
    free = [tuple(map(int, match.groups())) for row in rows
            if (match := re.search(pattern, row))]
    if (len(free) != 2 or {row[1] for row in free} != {0, nonqos_tid} or
            any(row[0] != final["generation"] or row[2] not in range(4) for row in free)):
        return result
    # hal_reo.h in this build defines SUCCESS=0 and DRAIN=0xff.
    # U32_MAX is this diagnostic's unobserved status, not a HAL result.
    allowed = {0, 255, 4294967295}
    flush_failures = [{"tid": t, "callback_status": s} for _, t, _, s in free
                      if s not in allowed]
    result.update({
        "binding": "host_qref_write" if qref_mode else "wmi_submit_callback_tail",
        "free": [{"generation": g, "tid": t, "path": p,
                  "callback_status": None if s == 4294967295 else s}
                 for g, t, p, s in free],
        "dma_cleanup_verified": True,
        "flush_failures": flush_failures,
        "verified": not flush_failures,
    })
    return result


def run_flow_samples(samples, token, completion, index, *, size=MIB, pairs=1, io_timeout=12,
                     require_mif=False, require_psf=False, phase=None, observe=None,
                     tcp_timeline=None):
    def ready():
        if root(f"test ! -f {completion} || cat {completion}").strip():
            raise RuntimeError("radio exited before bounded flow became ready")
        if root(f"cat {STATE}/token").strip() != token:
            return None
        if phase is not None:
            ready = f"{STATE}/phy-ready-{token}"
            if root(f"test ! -f {ready} || cat {ready}").strip() != str(phase):
                return None
        if require_mif:
            log = root(f"test ! -f {STATE}/runner.log || cat {STATE}/runner.log")
            if (not re.search(r"^OWLY_MIF_SUBMITTED_VHT_CONTAINER=.+$", log, re.M) or
                    "OWLY_PEERS count=1" not in log.splitlines()):
                return None
            if require_psf and not re.search(r"^OWLY_PSF_SUBMITTED_VHT_CONTAINER=.+$", log, re.M):
                return None
        server = root(f"test ! -f {STATE}/server.log || cat {STATE}/server.log")
        return re.search(r"^HASH_READY (fe80:[0-9a-f:]+)$", server, re.M)

    phone = wait_for(ready, 45).group(1)
    phone_mac = root("cat /sys/class/net/wonder0/address").strip().lower()
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", phone_mac):
        raise RuntimeError("unexpected bounded-flow radio address")
    payload = bytes(range(256)) * (size // 256)
    expected = hashlib.sha256(payload).digest()
    telemetry = {"tcp_timeline": tcp_timeline} if tcp_timeline is not None else {}
    for sample in range(1, pairs * 2 + 1):
        mode = (sample - 1) % 2
        if observe is not None:
            observe(sample, "before")
        start = time.time_ns()
        row = transfer(phone, index, mode, size, payload, expected, sample, io_timeout=io_timeout,
                       **telemetry)
        row.update(mac_wall_start_ns=start, mac_wall_end_ns=time.time_ns())
        samples.append(row)
        if observe is not None:
            observe(sample, "after")
        print(json.dumps({key: value for key, value in row.items() if key != "mac_tcp_timeline"},
                         ensure_ascii=False), flush=True)
    if all(row["status"] == "verified_mac" for row in samples):
        # The final send ACK can precede the Android HASH_OK log line.
        wait_for(lambda: root(f"cat {STATE}/server.log").count("\nHASH_OK ") >= pairs * 2, 3)
    return phone_mac


def verify_flow_hashes(samples, server_log, *, size=MIB, pairs=1):
    pattern = r"^HASH_OK sample=(\d+) mode=(\d+) bytes=(\d+) seconds=([.\d]+) sha256=([a-f0-9]+)$"
    receipts = re.findall(pattern, server_log, re.M)
    complete = len(samples) == len(receipts) == pairs * 2 and "HASH_FAILED " not in server_log
    for sample, row in enumerate(samples, 1):
        mode = (sample - 1) % 2
        same = [receipt for receipt in receipts if
                (int(receipt[0]), int(receipt[1]), int(receipt[2]), receipt[4])
                == (sample, mode, size, row.get("sha256"))]
        if (row["status"] == "verified_mac" and row["sample"] == sample and
                row["mode"] == mode and row["bytes"] == size and len(same) == 1):
            row["status"] = "verified"
            row["phone_seconds"] = float(same[0][3])
        else:
            complete = False
            if row["status"] == "verified_mac":
                row["status"] = "unconfirmed"
                row["error"] = "phone SHA receipt missing or inconsistent"
    return complete


def flow_speed_by_direction(samples, expected_pairs):
    directions = {}
    for mode, direction in enumerate(("Mac→小米", "小米→Mac")):
        successful = [row for row in samples if row["mode"] == mode and row["status"] == "verified"]
        size = sum(row["bytes"] for row in successful)
        elapsed = sum(row["seconds"] for row in successful)
        directions[direction] = {"verified": len(successful), "failed_or_missing": expected_pairs - len(successful),
                                 "bytes": size, "seconds": elapsed,
                                 "weighted_MBps": size / elapsed / 1e6 if elapsed else None,
                                 "peak_sample_MBps": max((row["MBps"] for row in successful), default=None)}
    return directions


def verify_flow_quiet(final):
    expected = {"flow_enabled": 1, "flow_ready": 0, "flow_opened": 1,
                "flow_stop": 1, "flow_status": 0, "flow_cpu_quiet": 1,
                "flow_tx_quiet": 1, "flow_quiesce_status": 0, "flow_tx_status": 0,
                "flow_tx_last": 0}
    return bool(final and all(final.get(k) == v for k, v in expected.items())
                and 0 <= final.get("flow_tx_first", 4294967295) < 4294967295)


def verify_flow_kernel(final):
    return bool(verify_flow_quiet(final) and final.get("flow_be_pass", 0) > 0
                and final.get("flow_raw_qos_submit", 0) > 0
                and final.get("flow_buf_cfg", 0) > 0
                and final.get("flow_tlv_size", 0) > 0
                and final.get("flow_capacity_max", 0) > final.get("flow_tlv_size", 0))


def rate_tqm_completed(rate_log):
    rows = re.findall(
        r"^schema=1 enabled=1 completed=(\d+) valid=\d+ invalid=\d+ tries=\d+ amsdu=\d+$",
        rate_log, re.MULTILINE)
    return int(rows[0]) if len(rows) == 1 else None


def verify_self_flow(receipt, rate_log):
    final, terminal, ready, drain, initial = (
        receipt.get(key) or {} for key in ("final", "terminal", "ready", "drain", "initial"))
    completed = rate_tqm_completed(rate_log)
    ring = re.findall(r"^tx_ring=(\d+(?:,\d+)+)$", rate_log, re.MULTILINE)
    attempts = sum(map(int, ring[0].split(","))) if len(ring) == 1 else None
    pending = final.get("flow_tx_first")
    completion_or_pending = (completed is not None and completed > 0 or
                             type(pending) is int and 0 < pending < 4294967295)
    untouched = ("generation", "obj_attached", "obj_created", "dp_created",
                 "create_issued", "create_returned", "create_confirm", "htt_map", "slot",
                 "delete_issued", "delete_returned", "delete_confirm", "htt_unmap", "slot_removed",
                 "reo_enabled", "reo_steps", "setup_issued", "setup_returned",
                 "reo_queue_posted", "reo_queue_tail", "reo_qref_write", "reo_dma_detached",
                 "reo_dma_freed", "dp_released", "obj_released", "dp_conflict", "compensation")
    generation = ready.get("flow_self_generation", 0)
    # This runner loads a fresh module and activates the owner once.
    isolated = (initial.get("flow_self_generation") == 0 and generation == 1 and
                all(row.get("flow_self_only") == 1 and all(row.get(k) == 0 for k in untouched)
                    for row in (initial, ready, terminal, final)) and
                all(row.get("flow_self_generation") == generation for row in (terminal, final)))
    lifecycle = (isolated and initial.get("result") == 0 and
                 all(ready.get(k) == v for k, v in
                     {"result": 1, "flow_ready": 1, "flow_opened": 1}.items()) and
                 terminal.get("result") == final.get("result") == 8 and
                 verify_flow_quiet(final) and
                 all(drain.get(k) == v for k, v in
                     {"phase": 5, "error": 0, "closing": 1, "peer_requested": 1,
                      "peer_started": 1, "peer_result": 0}.items()))
    return {"remote_steps_absent": isolated, "lifecycle_verified": lifecycle,
            "raw_ring_attempts": attempts, "tqm_completed": completed,
            "raw_completion_or_pending_observed": completion_or_pending,
            "verified": bool(lifecycle and attempts and completed)}


def ast_device_result(final, rate_log):
    """Separate selecting the self pair from seeing TX complete."""
    final = final or {}
    completed = rate_tqm_completed(rate_log)
    result = {"tqm_completed": completed, "verified": False}
    if final.get("ast_enabled") != 1 or final.get("ast_map_seen") != 1:
        return {**result, "status": "no_remote_map_observation"}
    if final.get("ast_conflict") != 0:
        return {**result, "status": "map_conflict"}
    if final.get("ast_self_proven") != 1:
        return {**result, "status": "self_slot_not_proven"}
    self_idx, self_hash, remote_idx, remote_hash = (
        final.get(f"ast_{side}_{field}") for side in ("self", "remote")
        for field in ("idx", "hash"))
    if any(type(value) is not int or not 0 <= value <= 65535
           for value in (self_idx, self_hash, remote_idx, remote_hash)):
        return {**result, "status": "pair_invalid"}
    self_pair = self_idx, self_hash & 0xf
    remote_pair = remote_idx, remote_hash & 0xf
    if final.get("ast_distinct") != 1 or self_pair == remote_pair:
        return {**result, "status": "descriptor_pairs_indistinguishable"}
    actual = final.get("ast_tx_idx"), final.get("ast_tx_cache")
    if actual != self_pair or final.get("ast_tx_samples", 0) <= 0 or final.get("ast_tx_mismatch") != 0:
        return {**result, "status": "self_descriptor_not_proven"}
    if type(completed) is not int or completed <= 0:
        return {**result, "status": "no_tqm_completion_observed"}
    return {**result, "status": "self_descriptor_and_tqm_completion", "verified": True}


def map_only_result(receipt):
    ready, terminal, final = (receipt.get(key) or {} for key in ("ready", "terminal", "final"))
    no_reo_activity = {
        "reo_enabled": 1, "reo_steps": 0, "setup_issued": 0, "setup_returned": 0,
        "reo_conflict": 0, "reo_duplicates": 0, "reo_queue_posted": 0, "reo_queue_tail": 0,
        "reo_queue_error": 0, "reo_queue_duplicates": 0, "reo_qref_write": 0,
        "reo_qref_clear": 0, "reo_dma_detached": 0, "reo_dma_freed": 0,
        "reo_dma_duplicates": 0, "reo_dma_conflict": 0,
    }
    ready_expected = {"result": 1, "flow_ready": 1, "flow_opened": 1, "create_returned": 1,
                      "create_submit": 0, "create_confirm": 1, "create_status": 0,
                      "create_conflict": 0, "htt_map": 1, "slot": 1, "delete_issued": 0,
                      "ast_map_seen": 1, "ast_self_proven": 1, "ast_distinct": 1}
    mapped = (all(ready.get(k) == v for k, v in ready_expected.items()) and
              ready.get("generation") == terminal.get("generation") == final.get("generation") == 1 and
              all(ready.get(key) == terminal.get(key) == final.get(key) for key in ("vdev", "peer_id")))
    quiet_reo = all(row.get("flow_map_only") == 1 and
                    all(row.get(k) == v for k, v in no_reo_activity.items())
                    for row in (ready, terminal, final))
    return {"map_verified_at_ready": mapped, "no_reo_activity_observed": quiet_reo,
            "verified": bool(mapped and quiet_reo and verify_flow_quiet(final))}


def read_mac_schedule(samples, phone_mac):
    path = HERE.parent / "iphone-radio-research" / "observe-native-awdl-schedule.py"
    read = runpy.run_path(str(path))["mac_schedule"]
    return [{"sample": row["sample"], "mac_wall_start_ns": row["mac_wall_start_ns"],
             "mac_wall_end_ns": row["mac_wall_end_ns"],
             **read(row["mac_wall_start_ns"], row["mac_wall_end_ns"],
                    [("xiaomi", phone_mac)])} for row in samples]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", type=Path)
    parser.add_argument("results", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--reo", action="store_true", help="require the setup-only REO candidate")
    modes.add_argument("--flow", action="store_true", help="require the bounded 1 MiB two-way flow candidate")
    modes.add_argument("--ast", action="store_true", help="require the bounded self AST control")
    modes.add_argument("--self-flow", action="store_true", help="bounded action-frame TX without a remote peer")
    modes.add_argument("--map-only", action="store_true", help="bounded action-frame TX with map and no REO setup")
    modes.add_argument("--rx-capacity", action="store_true", help="fixed 4224-byte RX capacity smoke")
    parser.add_argument("--capacity-64", action="store_true", help="three 64 MiB pairs with --rx-capacity")
    adverts = parser.add_mutually_exclusive_group()
    adverts.add_argument("--outgoing-psf", choices=tuple(PSF_SCOPE),
                         help="select the exact advertisement scope on the isolated PSF candidate")
    adverts.add_argument("--outgoing-vht", choices=("off", "on"), help="run the same independent app with VHT advertisement off or on")
    args = parser.parse_args()
    psf = args.outgoing_psf is not None
    advert = psf or args.outgoing_vht is not None
    if advert and not args.rx_capacity:
        parser.error("outgoing advertisements require --rx-capacity")
    if args.capacity_64 and not args.rx_capacity:
        parser.error("--capacity-64 requires --rx-capacity")
    size, pairs, io_timeout = (64 * MIB, 3, 100) if args.capacity_64 else (MIB, 1, 12)
    flow = args.flow or args.ast or args.rx_capacity
    action_only = args.self_flow or args.map_only
    reo = args.reo or args.flow or args.ast
    audit = (CAPACITY / "audit-rx-capacity.json" if args.rx_capacity else
             FLOW / "audit-map-only.json" if args.map_only else
             FLOW / "audit-self-flow.json" if args.self_flow else
             FLOW / "audit-ast.json" if args.ast else FLOW / "audit-flow.json"
             if args.flow else REO / "audit-reo.json"
             if args.reo else HERE / "audit-adapter.json")
    expected = json.loads(audit.read_text())["candidate_sha256"]
    if digest(args.module) != expected:
        raise RuntimeError("module does not match the final ABI audit")
    args.results.mkdir(exist_ok=False)
    initial_stock = ensure_restored()
    (args.results / "stock-before.txt").write_text(initial_stock)
    peer, ifindex = current_awdl()
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", peer):
        raise RuntimeError("unexpected live AWDL address")
    token = uuid.uuid4().hex
    receipt = {"token": token, "module_sha256": expected,
               "harness_sha256": harness_sha(args.reo, args.flow, args.ast, args.self_flow, args.map_only, args.rx_capacity, advert, psf),
               "channel": 149, "rx_ba": False, "throughput_test": args.capacity_64,
               "bounded_hash_flow": flow, "preserve_self_ast": args.ast or args.map_only,
               "self_only_flow": args.self_flow, "remote_map_only_flow": args.map_only,
               "mac_awdl_ifindex": ifindex,
               "mac_awdl_sha256": hashlib.sha256(peer.encode()).hexdigest()}
    outgoing = None
    if args.rx_capacity:
        manifest = (RUNNER / "dist" / "mss" / "SHA256SUMS").read_text()
        if root(f"cat {ROOT}/SHA256SUMS") != manifest:
            raise RuntimeError("deployed Owly payload differs from the stable mss baseline")
        (args.results / "device-build.sha256").write_text(manifest)
        receipt["capacity_configuration"] = {
            "rx_dma_bytes": 4224, "tx_phy_request": "vht80-sgi", "aggregation": "amsdu",
            "ack": True, "mss": 1160, "tun_queue": 500, "tx_vht_amsdu": False,
            "outgoing_vht_advertisement": False, "owly_variant": "mss",
            "tx_vht_extension_present": False, "payload_bytes": size, "pairs": pairs,
        }
        if advert:
            outgoing, app_manifest = outgoing_app_manifest(PSF / "dist") if psf else outgoing_app_manifest()
            (args.results / "outgoing-vht-build.sha256").write_text(app_manifest)
            scope = args.outgoing_psf if psf else ("mif" if args.outgoing_vht == "on" else "off")
            receipt["capacity_configuration"].update(
                outgoing_vht_advertisement=scope != "off", outgoing_vht_scope=scope,
                owly_variant="outgoing-psf" if psf else "outgoing-vht")
    job = f"{STATE}/job-{token}.log"
    completion = f"{STATE}/exit-{token}"
    staged = False
    reo_before = None
    capacity_before = None
    log = ""
    try:
        stage(args.module, expected, args.reo, args.flow, args.ast, args.self_flow, args.map_only,
              args.rx_capacity, outgoing, psf)
        staged = True
        observed = root(f"sha256sum {MODULE}").split()[0]
        if observed != expected or current_awdl() != (peer, ifindex):
            raise RuntimeError("staged module or live AWDL identity changed")
        if reo or args.map_only:
            (args.results / "stock-vht-capabilities.txt").write_text(stock_vht_capabilities())
            reo_before = map_only_log_snapshot() if args.map_only else reo_log_snapshot()
        if args.rx_capacity:
            capacity_before = bounded_kernel_log_snapshot(r"PT_RX_CAPACITY ")
            write_kernel_log_snapshot(args.results / "capacity-dmesg-before.json", capacity_before)
        if current_awdl() != (peer, ifindex):
            raise RuntimeError("live AWDL identity changed before launch")
        if args.rx_capacity:
            argv = ["sh", RADIO, "run", token, "vht80-sgi", "amsdu", "ack", peer,
                    str(pairs * 2), str(size), "1160", "500", "reference", "0"]
            if outgoing:
                argv.append(PSF_SCOPE[scope][0])
        else:
            argv = ["sh", RADIO, "remote-flow" if flow or action_only else "remote", token, peer]
        command = shlex.join(argv)
        launch = shlex.quote(f'{command}; rc=$?; echo "$rc" >{completion}; exit "$rc"')
        root(f"{BUSYBOX} setsid sh -c {launch} </dev/null >{job} 2>&1 &")
        print(f"REMOTE_PEER_JOB token={token} module_sha256={expected}", flush=True)
        phone_mac = None
        if flow:
            receipt["samples"] = []
            try:
                phone_mac = run_flow_samples(receipt["samples"], token, completion, ifindex,
                                             size=size, pairs=pairs, io_timeout=io_timeout,
                                             require_mif=bool(outgoing), require_psf=psf)
            except Exception as error:
                receipt["flow_error"] = str(error)
            finally:
                try:
                    if not root(f"test ! -f {completion} || cat {completion}").strip():
                        root(f"sh {RADIO} stop {token}")
                except Exception as error:
                    receipt["flow_stop_error"] = str(error)
        receipt["radio_exit"] = int(wait_for(
            lambda: root(f"test ! -f {completion} || cat {completion}").strip(), 230))
        log = root(f"cat {job}")
        (args.results / "lifecycle.log").write_text(log)
        for field, marker in (("initial", "PEER_TRIAL_BEFORE"),
                              ("ready", "PEER_FLOW_READY"),
                              ("terminal", "PEER_TRIAL_TERMINAL"),
                              ("final", "PEER_TRIAL_FINAL"),
                              ("drain", "PEER_TRIAL_DRAIN")):
            receipt[field] = parse_line(log, marker)
        if root(f"cat {STATE}/token").strip() != token:
            raise RuntimeError("device state belongs to a different job")
        if flow:
            for name in ("runner.log", "server.log", "before.txt", "after.txt"):
                (args.results / name).write_text(root(f"test ! -f {STATE}/{name} || cat {STATE}/{name}"))
            receipt["flow_hash_verified"] = verify_flow_hashes(
                receipt["samples"], (args.results / "server.log").read_text(), size=size, pairs=pairs)
            if outgoing:
                runner_log = (args.results / "runner.log").read_text()
                receipt["outgoing_vht"] = (
                    outgoing_psf_result(log, runner_log, outgoing[1], scope) if psf else
                    outgoing_vht_result(log, runner_log, outgoing[1], scope != "off")
                )
            if args.capacity_64:
                receipt["throughput_by_direction"] = flow_speed_by_direction(receipt["samples"], pairs)
            if phone_mac:
                try:
                    receipt["mac_awdl_schedule"] = read_mac_schedule(receipt["samples"], phone_mac)
                except Exception as error:
                    receipt["mac_schedule_error"] = str(error)
        if action_only:
            for name in ("runner.log", "before.txt", "after.txt"):
                (args.results / name).write_text(root(f"test ! -f {STATE}/{name} || cat {STATE}/{name}"))
        if args.self_flow:
            receipt["self_flow"] = verify_self_flow(receipt, (args.results / "after.txt").read_text())
        if args.map_only:
            receipt["map_only"] = map_only_result(receipt)
        if args.ast or args.map_only:
            receipt["ast"] = ast_device_result(receipt["final"], (args.results / "after.txt").read_text())
    except Exception as error:
        receipt["error"] = str(error)
    finally:
        if staged:
            try:
                stock = ensure_restored()
                if stock != initial_stock:
                    raise RuntimeError("stock/native fingerprint differs after trial")
                (args.results / "stock-after.txt").write_text(stock)
                receipt["restored"] = True
                if outgoing:
                    cleanup = f"""set -eu
test "$(cat {STATE}/token)" = {token}
test "$(cat {STATE}/status)" = STOCK_RESTORED
test ! -e {STATE}/saved.env
echo '{receipt['harness_sha256']}  {RADIO}' | sha256sum -c -
echo '{expected}  {MODULE}' | sha256sum -c -
test "$(cat {SHA})" = '{expected}'
echo '{outgoing[1]}  {OUTGOING_APP}' | sha256sum -c -
test "$(cat {OUTGOING_SHA})" = '{outgoing[1]}'
rm -f {RADIO} {MODULE} {SHA} {OUTGOING_APP} {OUTGOING_SHA}
"""
                    root(f"{BUSYBOX} flock -n {ROOT}/job.lock sh -c {shlex.quote(cleanup)}")
                else:
                    root(f"rm -f {RADIO} {MODULE} {SHA}")
            except Exception as error:
                receipt["restored"] = False
                receipt["restore_check"] = str(error)
        if reo_before and receipt.get("restored"):
            try:
                if args.map_only:
                    receipt["map_only_dma"] = map_only_dma_result(reo_before, map_only_log_snapshot())
                else:
                    receipt["reo"] = reo_device_result(reo_before, reo_log_snapshot(), receipt.get("final"))
            except Exception as error:
                receipt["reo_error"] = str(error)
        if capacity_before and receipt.get("restored"):
            try:
                capacity_after = bounded_kernel_log_snapshot(r"PT_RX_CAPACITY ")
                write_kernel_log_snapshot(args.results / "capacity-dmesg-after.json", capacity_after)
                receipt["rx_capacity"] = rx_capacity_result(capacity_before, capacity_after, log, expected)
            except Exception as error:
                receipt["rx_capacity_error"] = str(error)
        receipt["verified"] = not receipt.get("error") and verified(
            receipt.get("initial"), receipt.get("terminal"), receipt.get("final"),
            receipt.get("drain"), receipt.get("radio_exit"), receipt.get("restored"))
        if reo:
            receipt["verified"] = receipt["verified"] and receipt.get("reo", {}).get("verified", False)
        if flow and not args.rx_capacity:
            receipt["verified"] = (receipt["verified"] and receipt.get("flow_hash_verified", False)
                                   and not receipt.get("flow_error") and not receipt.get("flow_stop_error")
                                   and verify_flow_kernel(receipt.get("final")))
        if args.ast:
            receipt["verified"] = receipt["verified"] and receipt.get("ast", {}).get("verified", False)
        if args.self_flow:
            receipt["verified"] = (not receipt.get("error") and receipt.get("restored", False)
                                   and receipt.get("radio_exit") == 0
                                   and receipt.get("self_flow", {}).get("verified", False))
        if args.map_only:
            receipt["verified"] = (receipt["verified"] and receipt.get("ast", {}).get("verified", False)
                                   and receipt.get("map_only", {}).get("verified", False)
                                   and receipt.get("map_only_dma", {}).get("verified", False))
        if args.rx_capacity:
            receipt["verified"] = (not receipt.get("error") and receipt.get("restored", False)
                                   and receipt.get("radio_exit") == 0
                                   and receipt.get("flow_hash_verified", False)
                                   and not receipt.get("flow_error") and not receipt.get("flow_stop_error")
                                   and receipt.get("rx_capacity", {}).get("verified", False))
        if outgoing:
            receipt["verified"] = receipt["verified"] and receipt.get("outgoing_vht", {}).get("verified", False)
        (args.results / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps({k: receipt.get(k) for k in ("radio_exit", "restored", "verified")}), flush=True)
    return 0 if receipt["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
