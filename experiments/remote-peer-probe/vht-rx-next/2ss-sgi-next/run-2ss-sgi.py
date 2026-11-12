#!/usr/bin/env python3
"""Hold one 149 radio lease and run verified 2SS + SGI80 + bounded ADDBA Owly phases against one Mac."""
from datetime import datetime, timedelta
import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import shlex
import subprocess
import tempfile
import uuid

HERE = Path(__file__).resolve().parent
R = runpy.run_path(str(HERE.parents[1] / "run-remote-peer.py"))
STATE, RADIO = R["STATE"], R["RADIO"]
BASE, ROOT, BUSYBOX = R["BASE"], R["ROOT"], R["BUSYBOX"]
MODULE, SHA = R["MODULE"], R["SHA"]
OUTGOING_APP, OUTGOING_SHA = R["OUTGOING_APP"], R["OUTGOING_SHA"]
WONDER_INIT_2SS = ROOT + "/wonder-init-2ss"
WONDER_INIT_2SS_SHA = ROOT + "/wonder-init-2ss.sha"
HARNESS_2SS_SHA = "a07f67fa845b1563610a978b8971dddf853c3bc9b13d27a48dab3781c28c9f03"
PROFILES = ("vht80-sgi", "vht80-2ss-sgi", "vht80-2ss-mcs8-sgi", "vht80-2ss-mcs9-sgi")
MODES = {"off": "0", "mif1ss": "1", "2ss": "2"}
ADDBA_MODES = {"off": "0", "no-amsdu": "1", "amsdu": "2"}

adb = R["adb"]
root = R["root"]
wait_for = R["wait_for"]
ensure_restored = R["ensure_restored"]
digest = R["digest"]
MIB = R["MIB"]


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def session_paths(folder: Path):
    data = json.loads((folder / "session.json").read_text())
    token = data["token"]
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise RuntimeError("invalid session token")
    return data, f"{STATE}/phy-{token}", f"{STATE}/exit-{token}"


def exit_status(completion: str) -> str:
    return root(f"test ! -f {completion} || cat {completion}").strip()


def check_live(token: str, completion: str) -> None:
    rc = exit_status(completion)
    if rc:
        raise RuntimeError(f"radio lease already exited ({rc})")
    if root(f"cat {STATE}/token").strip() != token:
        raise RuntimeError("radio state belongs to a different lease")


def prepare_2ss_harness(folder: Path) -> Path:
    script = R["prepare_harness"](folder, capacity=True, outgoing=True, phy=True)
    subprocess.run(["git", "apply", str(HERE / "device-2ss-harness.patch")], cwd=folder, check=True)
    subprocess.run(["sh", "-n", str(script)], check=True)
    if digest(script) != HARNESS_2SS_SHA:
        raise RuntimeError("assembled 2SS harness SHA differs from HARNESS_2SS_SHA")
    return script


def dist_manifest(dist_dir: Path):
    sums_path = dist_dir / "SHA256SUMS"
    manifest = sums_path.read_text()
    entries = {}
    for row in manifest.splitlines():
        parts = row.split()
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise RuntimeError(f"invalid SHA256SUMS line: {row}")
        target = dist_dir / parts[1]
        if digest(target) != parts[0]:
            raise RuntimeError(f"dist checksum mismatch for {parts[1]}")
        entries[parts[1]] = (target, parts[0])
    for required in ("houji-owly", "wonder-init", "owly-hashprobe"):
        if required not in entries:
            raise RuntimeError(f"missing {required} in {sums_path}")
    return entries, manifest


def stage_2ss(module: Path, expected_mod: str, app_entry, winit_entry) -> None:
    app_path, app_sha = app_entry
    winit_path, winit_sha = winit_entry
    staging = adb("shell", "mktemp -d /data/local/tmp/owly-2ss-stage.XXXXXX").strip()
    if not re.fullmatch(r"/data/local/tmp/owly-2ss-stage[.][a-zA-Z0-9]+", staging):
        raise RuntimeError("unexpected device staging directory")
    try:
        with tempfile.TemporaryDirectory(prefix="owly-2ss-harness.") as temporary:
            folder = Path(temporary)
            script = prepare_2ss_harness(folder)
            mod_sidecar = folder / "remote-peer.sha"
            mod_sidecar.write_text(expected_mod + "\n")
            app_sidecar = folder / "outgoing-vht.sha"
            app_sidecar.write_text(app_sha + "\n")
            winit_sidecar = folder / "wonder-init-2ss.sha"
            winit_sidecar.write_text(winit_sha + "\n")
            files = [
                (script, "runner.sh"),
                (module, "wlan.ko"),
                (mod_sidecar, "remote-peer.sha"),
                (app_path, "owly-app"),
                (app_sidecar, "outgoing-vht.sha"),
                (winit_path, "wonder-init-2ss"),
                (winit_sidecar, "wonder-init-2ss.sha"),
            ]
            for path, dest in files:
                adb("push", str(path), f"{staging}/{dest}")
        install = f"""set -eu
test ! -e {STATE}/saved.env
test ! -e {BASE}/state/saved.env
test ! -e /data/local/tmp/xos/native/saved.env
test ! -e {RADIO}
test ! -e {MODULE}
test ! -e {SHA}
test ! -e {OUTGOING_APP}
test ! -e {OUTGOING_SHA}
test ! -e {WONDER_INIT_2SS}
test ! -e {WONDER_INIT_2SS_SHA}
cd {staging}
echo '{HARNESS_2SS_SHA}  runner.sh' | sha256sum -c -
echo '{expected_mod}  wlan.ko' | sha256sum -c -
echo '{app_sha}  owly-app' | sha256sum -c -
echo '{winit_sha}  wonder-init-2ss' | sha256sum -c -
test "$(cat remote-peer.sha)" = '{expected_mod}'
test "$(cat outgoing-vht.sha)" = '{app_sha}'
test "$(cat wonder-init-2ss.sha)" = '{winit_sha}'
trap 'rc=$?; if test "$rc" != 0; then rm -f {RADIO} {MODULE} {SHA} {OUTGOING_APP} {OUTGOING_SHA} {WONDER_INIT_2SS} {WONDER_INIT_2SS_SHA}; fi; exit "$rc"' EXIT
cp owly-app {OUTGOING_APP}
cp outgoing-vht.sha {OUTGOING_SHA}
cp wonder-init-2ss {WONDER_INIT_2SS}
cp wonder-init-2ss.sha {WONDER_INIT_2SS_SHA}
cp runner.sh {RADIO}
cp wlan.ko {MODULE}
cp remote-peer.sha {SHA}
chmod 700 {RADIO} {OUTGOING_APP} {WONDER_INIT_2SS}
chmod 600 {MODULE} {SHA} {OUTGOING_SHA} {WONDER_INIT_2SS_SHA}
trap - EXIT
"""
        root(f"{BUSYBOX} flock -n {ROOT}/job.lock sh -c {shlex.quote(install)}")
    finally:
        adb("shell", f"rm -rf {staging}")


def start(module: Path, folder: Path, profile: str) -> None:
    audit = json.loads((HERE / "audit-2ss-rx-phy.json").read_text())
    expected = audit["candidate_sha256"]
    if not all(audit["checks"].values()) or digest(module) != expected:
        raise RuntimeError("2SS RX PHY module differs from its successful ABI audit")
    stock_before = ensure_restored()
    peer, ifindex = R["current_awdl"]()
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", peer):
        raise RuntimeError("unexpected live Mac AWDL address")
    entries, manifest = dist_manifest(HERE / "dist")
    app_entry = entries["houji-owly"]
    winit_entry = entries["wonder-init"]
    folder.mkdir()
    (folder / "stock-before.txt").write_text(stock_before)
    (folder / "app.sha256").write_text(manifest)
    token = uuid.uuid4().hex
    session = dict(
        token=token,
        profile=profile,
        module_sha256=expected,
        app_sha256=app_entry[1],
        wonder_init_sha256=winit_entry[1],
        harness_sha256=HARNESS_2SS_SHA,
        channel=149,
        peer_sha256=hashlib.sha256(peer.encode()).hexdigest(),
        ifindex=ifindex,
    )
    write_json(folder / "session.json", session)
    stage_2ss(module, expected, app_entry, winit_entry)
    argv = [
        "sh", RADIO, "run", token, profile, "amsdu", "ack", peer,
        "0", "0", "1160", "500", "reference", "0", "0",
    ]
    command = shlex.join(argv)
    job = f"{STATE}/job-{token}.log"
    launch = shlex.quote(f'{command}; rc=$?; echo "$rc" >{STATE}/exit-{token}; exit "$rc"')
    root(f"{BUSYBOX} setsid sh -c {launch} </dev/null >{job} 2>&1 &")
    print(f"2SS_PHY_JOB token={token} profile={profile} module_sha256={expected}", flush=True)
    _, phy_dir, completion = session_paths(folder)

    def started():
        rc = exit_status(completion)
        if rc:
            raise RuntimeError(f"radio lease exited at startup ({rc})")
        return root(f"test ! -d {phy_dir}/1 || echo READY").strip()

    try:
        wait_for(started, 75)
    except Exception:
        root(f"sh {RADIO} stop {token}")
        raise
    finally:
        (folder / "lifecycle-start.log").write_text(root(f"cat {job}"))
    print("2SS_PHY_IDLE", flush=True)


def rx_phy_delta(before: str, after: str) -> dict:
    def parse(line: str) -> dict:
        return dict(field.split("=", 1) for field in line.split())

    first, last = parse(before), parse(after)
    if first.keys() != last.keys() or any(
        last.get(k) != value or first.get(k) != value
        for k, value in (("schema", "1"), ("enabled", "1"), ("ampdu", "unknown"))
    ):
        raise ValueError("RX PHY observer schema or enable readback changed")
    delta = {}
    for key, end in last.items():
        if key in ("schema", "enabled", "ampdu"):
            continue
        a = [int(x) for x in first[key].split(",")]
        b = [int(x) for x in end.split(",")]
        if len(a) != len(b) or any(s < 0 or f < s for s, f in zip(a, b)):
            raise ValueError(f"RX PHY cumulative counter moved backwards: {key}")
        values = [f - s for s, f in zip(a, b)]
        delta[key] = values if "," in end else values[0]
    return delta


def tx_rate_delta(before: str, after: str) -> dict:
    def parse(text: str) -> dict:
        out = {}
        for token in text.split():
            if "=" in token:
                k, v = token.split("=", 1)
                out[k] = v
        return out

    first, last = parse(before), parse(after)
    if first.keys() != last.keys() or first.get("schema") != "1" or first.get("enabled") != "1":
        raise ValueError("TX rate observer schema or enable readback changed")
    delta = {}
    for key, end in last.items():
        if key in ("schema", "enabled"):
            continue
        a = [int(x) for x in first[key].split(",")]
        b = [int(x) for x in end.split(",")]
        if len(a) != len(b) or any(s < 0 or f < s for s, f in zip(a, b)):
            raise ValueError(f"TX rate cumulative counter moved backwards: {key}")
        values = [f - s for s, f in zip(a, b)]
        delta[key] = values if "," in end else values[0]
    return delta


def parse_runner_summary(runner_log: str) -> dict:
    info = {}
    for prefix in (
        "OWLY_AF_PACKET_BUF ",
        "OWLY_COALESCE_US=",
        "OWLY_OUTGOING_VHT=",
        "OWLY_ACCEPT_ADDBA=",
        "OWLY_READY ",
        "OWLY_PSF_SUBMITTED_HT_CAPABILITIES=",
        "OWLY_PSF_SUBMITTED_VHT_CONTAINER=",
        "OWLY_MIF_SUBMITTED_HT_CAPABILITIES=",
        "OWLY_MIF_SUBMITTED_VHT_CONTAINER=",
    ):
        matches = [line for line in runner_log.splitlines() if line.startswith(prefix)]
        if matches:
            info[prefix.strip().rstrip("=")] = matches
    stats_lines = [line for line in runner_log.splitlines() if "STATS tx_action " in line]
    if stats_lines:
        info["last_stats"] = stats_lines[-1]
    monitor_lines = [line for line in runner_log.splitlines() if "monitor totals" in line]
    if monitor_lines:
        info["monitor_totals"] = monitor_lines[-1]
    gap_lines = [line for line in runner_log.splitlines() if line.startswith("OWLY_RX_GAPS ")]
    if gap_lines:
        info["rx_gaps"] = gap_lines[-1]
    return info


def collect_mac_lqm(start_ns: int, end_ns: int) -> list[str]:
    fmt = "%Y-%m-%d %H:%M:%S"
    t0 = (datetime.fromtimestamp(start_ns / 1e9) - timedelta(seconds=2)).strftime(fmt)
    t1 = (datetime.fromtimestamp(end_ns / 1e9) + timedelta(seconds=2)).strftime(fmt)
    try:
        out = subprocess.check_output(
            [
                "/usr/bin/log", "show", "--style", "compact",
                "--start", t0, "--end", t1,
                "--predicate",
                'eventMessage CONTAINS "LQM-WiFi" OR eventMessage CONTAINS "rxaddba" '
                'OR eventMessage CONTAINS "txaddba" OR eventMessage CONTAINS "BlockAck" '
                'OR eventMessage CONTAINS "awdl"',
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=25,
        )
    except Exception as error:
        return [f"log_show_error: {error}"]
    interesting = []
    for line in out.splitlines():
        if any(
            kw in line
            for kw in (
                "LQM-WiFi", "addba", "ADDBA", "rxBACK", "txAMPDU", "txMPDU",
                "VHT", "NSS", "MCS", "SGI", "bw=", "rate=",
            )
        ):
            interesting.append(line)
    return interesting[-80:]


def verify_2ss_advertisement(lifecycle: str, runner_log: str, app_sha: str, mode: str, addba: str) -> dict:
    mode_num = MODES[mode]
    addba_num = ADDBA_MODES[addba]
    accept_addba = addba != "off"
    psf_ht = [l.split("=", 1)[1] for l in runner_log.splitlines() if l.startswith("OWLY_PSF_SUBMITTED_HT_CAPABILITIES=")]
    psf_vht = [l.split("=", 1)[1] for l in runner_log.splitlines() if l.startswith("OWLY_PSF_SUBMITTED_VHT_CONTAINER=")]
    mif_ht = [l.split("=", 1)[1] for l in runner_log.splitlines() if l.startswith("OWLY_MIF_SUBMITTED_HT_CAPABILITIES=")]
    mif_vht = [l.split("=", 1)[1] for l in runner_log.splitlines() if l.startswith("OWLY_MIF_SUBMITTED_VHT_CONTAINER=")]
    app_ok = f"OUTGOING_VHT_BUILD_SHA={app_sha}" in lifecycle.splitlines()
    switch_ok = (
        f"OWLY_OUTGOING_VHT={mode_num}" in runner_log.splitlines()
        and f"OWLY_ACCEPT_ADDBA={addba_num}" in runner_log.splitlines()
    )
    if mode == "off":
        wire_ok = (
            psf_ht == ["0000ce111bff0000"] and psf_vht == ["absent"]
            and mif_ht == ["0000ce111bff0000"] and mif_vht == ["absent"]
        )
    elif mode == "mif1ss":
        wire_ok = (
            psf_ht == ["0000ce111bff0000"] and psf_vht == ["absent"]
            and mif_ht == ["0000ce111bff0000"] and mif_vht == ["bf0c00000000fcff0000fcff0000"]
        )
    else:
        expected_vht = "bf0c30008003faff0000faff0000" if accept_addba else "bf0c30000000faff0000faff0000"
        wire_ok = (
            psf_ht == ["00006f001fffff0000"] and psf_vht == [expected_vht]
            and mif_ht == ["00006f001fffff0000"] and mif_vht == [expected_vht]
        )
    return {
        "mode": mode,
        "addba": addba,
        "accept_addba": accept_addba,
        "matching_app_load": app_ok,
        "switch_ok": switch_ok,
        "psf_ht": psf_ht,
        "psf_vht": psf_vht,
        "mif_ht": mif_ht,
        "mif_vht": mif_vht,
        "verified": bool(app_ok and switch_ok and wire_ok),
    }


def sample(folder: Path, mode: str, mib: int, addba: str) -> None:
    if addba != "off" and mode != "2ss":
        raise RuntimeError("--addba requires --mode 2ss")
    accept_addba = addba != "off"
    session, phy_dir, completion = session_paths(folder)
    token = session["token"]
    peer, ifindex = R["current_awdl"]()
    if (
        ifindex != session["ifindex"]
        or hashlib.sha256(peer.encode()).hexdigest() != session["peer_sha256"]
    ):
        raise RuntimeError("Mac AWDL identity changed; use a fresh radio lease")
    phases = [int(p.name) for p in folder.iterdir() if p.is_dir() and p.name.isdigit()]
    phase = max(phases, default=0) + 1
    output = folder / str(phase)
    output.mkdir()
    remote = f"{phy_dir}/{phase}"
    pairs, size = (3 if mib == 64 else 1), mib * MIB
    tx_mode = MODES[mode]
    ba_flag = ADDBA_MODES[addba]
    result = dict(
        token=token,
        profile=session["profile"],
        phase=phase,
        mode=mode,
        addba=addba,
        accept_addba=accept_addba,
        bytes_per_sample=size,
        pairs=pairs,
        samples=[],
    )

    def idle():
        check_live(token, completion)
        return root(f"test ! -d {remote} || echo READY").strip()

    import time
    wall_start_ns = time.time_ns()
    try:
        wait_for(idle, 20)
        root(
            f"set -e; test ! -e {remote}/request; "
            f"printf '%s\\n' '{tx_mode} {ba_flag} {pairs * 2} {size}' > {remote}/request.tmp; "
            f"mv {remote}/request.tmp {remote}/request"
        )
        io_timeout = 100 if mib == 64 else 25
        phone_mac = R["run_flow_samples"](
            result["samples"],
            token,
            completion,
            ifindex,
            size=size,
            pairs=pairs,
            io_timeout=io_timeout,
            require_mif=True,
            require_psf=(mode == "2ss"),
            phase=phase,
        )
        result["phone_mac_sha256"] = hashlib.sha256(phone_mac.encode()).hexdigest()
        any_failed = any(row.get("status") != "verified_mac" for row in result["samples"])
        if any_failed:
            result["aborted_incomplete_probe"] = True
            root(f": > {remote}/abort")

        def completed():
            check_live(token, completion)
            return root(f"test ! -f {remote}/done || echo DONE").strip()

        wait_for(completed, 30)
        wall_end_ns = time.time_ns()
        logs = {}
        for name in ("before", "after", "rate-before", "rate-after", "runner.log", "server.log"):
            logs[name] = root(f"cat {remote}/{name}")
            (output / name).write_text(logs[name])
        lifecycle = root(f"cat {STATE}/job-{token}.log")
        result["advertisement"] = verify_2ss_advertisement(
            lifecycle, logs["runner.log"], session["app_sha256"], mode, addba
        )
        result["runner_summary"] = parse_runner_summary(logs["runner.log"])
        result["flow_hash_verified"] = R["verify_flow_hashes"](
            result["samples"], logs["server.log"], size=size, pairs=pairs
        )
        result["rx_phy_delta"] = rx_phy_delta(logs["before"], logs["after"])
        result["tx_rate_delta"] = tx_rate_delta(logs["rate-before"], logs["rate-after"])
        result["throughput_by_direction"] = R["flow_speed_by_direction"](result["samples"], pairs)
        mac_lqm = collect_mac_lqm(wall_start_ns, wall_end_ns)
        (output / "mac-lqm.log").write_text("\n".join(mac_lqm) + ("\n" if mac_lqm else ""))
        result["mac_lqm_tail"] = mac_lqm[-25:]
        result["verified"] = bool(result["advertisement"]["verified"] and result["flow_hash_verified"])
        result["radio_idle"] = True
        if not result["verified"] and not accept_addba:
            raise RuntimeError("advertisement or paired SHA verification failed")
    except Exception as error:
        result["error"] = str(error)
        result["verified"] = False
        result["radio_idle"] = False
        root(f"sh {RADIO} stop {token}")
        raise
    finally:
        write_json(output / "result.json", result)
    print(
        json.dumps(
            {
                "phase": result["phase"],
                "mode": mode,
                "addba": addba,
                "verified": result["verified"],
                "throughput": result.get("throughput_by_direction"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def stop(folder: Path) -> None:
    session, _, completion = session_paths(folder)
    token = session["token"]
    if not exit_status(completion):
        check_live(token, completion)
        root(f"sh {RADIO} stop {token}")
    rc = wait_for(lambda: exit_status(completion), 100)
    lifecycle = root(f"cat {STATE}/job-{token}.log")
    if "OWLY_STATE=STOCK_RESTORED" not in lifecycle.splitlines():
        recover_out = root(f"sh {RADIO} recover")
        lifecycle = lifecycle + "\n" + recover_out
        if "OWLY_STATE=STOCK_RESTORED" in recover_out.splitlines():
            rc = "0"
    (folder / "lifecycle-end.log").write_text(lifecycle)
    if root(f"cat {STATE}/token").strip() != token or "OWLY_STATE=STOCK_RESTORED" not in lifecycle.splitlines():
        raise RuntimeError(f"radio lease did not restore stock ({rc})")
    expected = session["module_sha256"]
    app = session["app_sha256"]
    winit = session["wonder_init_sha256"]
    harness = session["harness_sha256"]
    if (
        not re.fullmatch(r"[0-9a-f]{64}", expected)
        or not re.fullmatch(r"[0-9a-f]{64}", app)
        or not re.fullmatch(r"[0-9a-f]{64}", winit)
        or harness != HARNESS_2SS_SHA
    ):
        raise RuntimeError("invalid cleanup hashes in lease receipt")
    cleanup = f"""set -eu
test "$(cat {STATE}/token)" = {token}
test ! -e {STATE}/saved.env
echo '{harness}  {RADIO}' | sha256sum -c -
echo '{expected}  {MODULE}' | sha256sum -c -
echo '{app}  {OUTGOING_APP}' | sha256sum -c -
echo '{winit}  {WONDER_INIT_2SS}' | sha256sum -c -
test "$(cat {SHA})" = '{expected}'
test "$(cat {OUTGOING_SHA})" = '{app}'
test "$(cat {WONDER_INIT_2SS_SHA})" = '{winit}'
rm -f {RADIO} {MODULE} {SHA} {OUTGOING_APP} {OUTGOING_SHA} {WONDER_INIT_2SS} {WONDER_INIT_2SS_SHA}
"""
    root(f"{BUSYBOX} flock -n {ROOT}/job.lock sh -c {shlex.quote(cleanup)}")
    stock_after = ensure_restored()
    (folder / "stock-after.txt").write_text(stock_after)
    write_json(folder / "stop.json", {"token": token, "radio_exit": int(rc), "restored": True})
    print("2SS_STOCK_RESTORED", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    activate = commands.add_parser("start")
    activate.add_argument("module", type=Path)
    activate.add_argument("folder", type=Path)
    activate.add_argument("--profile", choices=PROFILES, default="vht80-sgi")
    run_sample = commands.add_parser("sample")
    run_sample.add_argument("folder", type=Path)
    run_sample.add_argument("--mode", choices=tuple(MODES), default="2ss")
    run_sample.add_argument("--mib", choices=(1, 16, 64), type=int, default=1)
    run_sample.add_argument("--addba", choices=tuple(ADDBA_MODES), default="off")
    close = commands.add_parser("stop")
    close.add_argument("folder", type=Path)
    args = parser.parse_args()
    if args.action == "start":
        start(args.module, args.folder, args.profile)
    elif args.action == "sample":
        sample(args.folder, args.mode, args.mib, args.addba)
    else:
        stop(args.folder)


if __name__ == "__main__":
    main()
