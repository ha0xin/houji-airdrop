#!/usr/bin/env python3
"""Run empirical on-device validations for all three WCN7850 PASSTHRU technical findings."""
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
import time
import uuid

HERE = Path(__file__).resolve().parent
S2SS_DIR = HERE.parent / "remote-peer-probe/vht-rx-next/2ss-sgi-next"
S2SS = runpy.run_path(str(S2SS_DIR / "run-2ss-sgi.py"))
R = S2SS["R"]

STATE, RADIO = R["STATE"], R["RADIO"]
BASE, ROOT, BUSYBOX = R["BASE"], R["ROOT"], R["BUSYBOX"]
MODULE, SHA = R["MODULE"], R["SHA"]
OUTGOING_APP, OUTGOING_SHA = R["OUTGOING_APP"], R["OUTGOING_SHA"]
WONDER_INIT_2SS = S2SS["WONDER_INIT_2SS"]
WONDER_INIT_2SS_SHA = S2SS["WONDER_INIT_2SS_SHA"]
TF_ENV = ROOT + "/three-findings.env"
TF_SHA = ROOT + "/three-findings.sha"
HARNESS_TF_SHA = "7ad2cf233192f38f14c63867f809efbd430e0fa4206ee410add5c0332b5c62c3"

PROFILES = S2SS["PROFILES"]
MODES = S2SS["MODES"]
ADDBA_MODES = S2SS["ADDBA_MODES"]
TX_ADDBA_MODES = {"off": "0", "win64": "1", "win64-2ss": "2"}

adb = R["adb"]
root = R["root"]
wait_for = R["wait_for"]
ensure_restored = R["ensure_restored"]
digest = R["digest"]
MIB = R["MIB"]
write_json = S2SS["write_json"]
session_paths = S2SS["session_paths"]
exit_status = S2SS["exit_status"]
check_live = S2SS["check_live"]
dist_manifest = S2SS["dist_manifest"]
rx_phy_delta = S2SS["rx_phy_delta"]
tx_rate_delta = S2SS["tx_rate_delta"]


def parse_kv_line(line: str) -> dict:
    out = {}
    for token in line.strip().split():
        if "=" in token:
            k, v = token.split("=", 1)
            out[k] = v
    return out


def prepare_tf_harness(folder: Path) -> Path:
    script = S2SS["prepare_2ss_harness"](folder)
    subprocess.run(
        ["git", "apply", str(HERE / "device-three-findings-harness.patch")],
        cwd=folder,
        check=True,
    )
    subprocess.run(["sh", "-n", str(script)], check=True)
    if digest(script) != HARNESS_TF_SHA:
        raise RuntimeError(
            f"assembled three-findings harness SHA {digest(script)} != {HARNESS_TF_SHA}"
        )
    return script


def build_tf_env_text(flags: dict) -> str:
    return (
        f"tf_remote_assoc={int(flags['remote_assoc'])}\n"
        f"tf_remote_ast_override={int(flags.get('remote_ast_override', 1))}\n"
        f"tf_remote_hash_override={int(flags.get('remote_hash_override', 1))}\n"
        f"tf_remote_reo={int(flags['remote_reo'])}\n"
        f"tf_fw_addba={int(flags['fw_addba'])}\n"
        f"tf_tx_ampdu={int(flags['tx_ampdu'])}\n"
        f"tf_dynamic_rate={int(flags['dynamic_rate'])}\n"
        f"tf_sta_coexist={int(flags['sta_coexist'])}\n"
    )


def stage_tf(
    module: Path,
    expected_mod: str,
    app_entry,
    winit_entry,
    tf_env_text: str,
) -> str:
    app_path, app_sha = app_entry
    winit_path, winit_sha = winit_entry
    tf_env_sha = hashlib.sha256(tf_env_text.encode()).hexdigest()
    staging = adb("shell", "mktemp -d /data/local/tmp/owly-tf-stage.XXXXXX").strip()
    if not re.fullmatch(r"/data/local/tmp/owly-tf-stage[.][a-zA-Z0-9]+", staging):
        raise RuntimeError("unexpected device staging directory")
    try:
        with tempfile.TemporaryDirectory(prefix="owly-tf-harness.") as temporary:
            folder = Path(temporary)
            script = prepare_tf_harness(folder)
            mod_sidecar = folder / "remote-peer.sha"
            mod_sidecar.write_text(expected_mod + "\n")
            app_sidecar = folder / "outgoing-vht.sha"
            app_sidecar.write_text(app_sha + "\n")
            winit_sidecar = folder / "wonder-init-2ss.sha"
            winit_sidecar.write_text(winit_sha + "\n")
            tf_env_file = folder / "three-findings.env"
            tf_env_file.write_text(tf_env_text)
            tf_sha_file = folder / "three-findings.sha"
            tf_sha_file.write_text(tf_env_sha + "\n")
            files = [
                (script, "runner.sh"),
                (module, "wlan.ko"),
                (mod_sidecar, "remote-peer.sha"),
                (app_path, "owly-app"),
                (app_sidecar, "outgoing-vht.sha"),
                (winit_path, "wonder-init-2ss"),
                (winit_sidecar, "wonder-init-2ss.sha"),
                (tf_env_file, "three-findings.env"),
                (tf_sha_file, "three-findings.sha"),
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
test ! -e {TF_ENV}
test ! -e {TF_SHA}
cd {staging}
echo '{HARNESS_TF_SHA}  runner.sh' | sha256sum -c -
echo '{expected_mod}  wlan.ko' | sha256sum -c -
echo '{app_sha}  owly-app' | sha256sum -c -
echo '{winit_sha}  wonder-init-2ss' | sha256sum -c -
echo '{tf_env_sha}  three-findings.env' | sha256sum -c -
test "$(cat remote-peer.sha)" = '{expected_mod}'
test "$(cat outgoing-vht.sha)" = '{app_sha}'
test "$(cat wonder-init-2ss.sha)" = '{winit_sha}'
test "$(cat three-findings.sha)" = '{tf_env_sha}'
trap 'rc=$?; if test "$rc" != 0; then rm -f {RADIO} {MODULE} {SHA} {OUTGOING_APP} {OUTGOING_SHA} {WONDER_INIT_2SS} {WONDER_INIT_2SS_SHA} {TF_ENV} {TF_SHA}; fi; exit "$rc"' EXIT
cp owly-app {OUTGOING_APP}
cp outgoing-vht.sha {OUTGOING_SHA}
cp wonder-init-2ss {WONDER_INIT_2SS}
cp wonder-init-2ss.sha {WONDER_INIT_2SS_SHA}
cp three-findings.env {TF_ENV}
cp three-findings.sha {TF_SHA}
cp runner.sh {RADIO}
cp wlan.ko {MODULE}
cp remote-peer.sha {SHA}
chmod 700 {RADIO} {OUTGOING_APP} {WONDER_INIT_2SS}
chmod 600 {MODULE} {SHA} {OUTGOING_SHA} {WONDER_INIT_2SS_SHA} {TF_ENV} {TF_SHA}
trap - EXIT
"""
        root(f"{BUSYBOX} flock -n {ROOT}/job.lock sh -c {shlex.quote(install)}")
    finally:
        adb("shell", f"rm -rf {staging}")
    return tf_env_sha


def collect_mac_lqm_extended(start_ns: int, end_ns: int) -> list[str]:
    fmt = "%Y-%m-%d %H:%M:%S"
    t0 = (datetime.fromtimestamp(start_ns / 1e9) - timedelta(seconds=2)).strftime(fmt)
    t1 = (datetime.fromtimestamp(end_ns / 1e9) + timedelta(seconds=2)).strftime(fmt)
    try:
        out = subprocess.check_output(
            [
                "/usr/bin/log",
                "show",
                "--style",
                "compact",
                "--start",
                t0,
                "--end",
                t1,
                "--predicate",
                'eventMessage CONTAINS "LQM-WiFi" OR eventMessage CONTAINS "rxaddba" '
                'OR eventMessage CONTAINS "txaddba" OR eventMessage CONTAINS "BlockAck" '
                'OR eventMessage CONTAINS "rxAmpdu" OR eventMessage CONTAINS "txBACK" '
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
                "LQM-WiFi",
                "addba",
                "ADDBA",
                "rxBACK",
                "txBACK",
                "txAMPDU",
                "rxAmpdu",
                "rxMPDU",
                "txMPDU",
                "VHT",
                "NSS",
                "MCS",
                "SGI",
                "bw=",
                "rate=",
            )
        ):
            interesting.append(line)
    return interesting[-100:]


def parse_runner_summary_extended(runner_log: str) -> dict:
    info = S2SS["parse_runner_summary"](runner_log)
    for prefix in (
        "OWLY_TX_ADDBA=",
        "OWLY_TX_ADDBA_REQ_SENT ",
        "OWLY_TX_ADDBA_RESP_RCVD ",
    ):
        matches = [line for line in runner_log.splitlines() if line.startswith(prefix)]
        if matches:
            info[prefix.strip().rstrip("=")] = matches
    return info


def parse_ping_log(ping_text: str) -> dict:
    summary = {"raw_tail": ping_text.splitlines()[-10:]}
    m = re.search(
        r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received,\s+([0-9.]+)%\s+packet loss",
        ping_text,
    )
    if m:
        summary["tx_packets"] = int(m.group(1))
        summary["rx_packets"] = int(m.group(2))
        summary["loss_pct"] = float(m.group(3))
    rtt = re.search(r"rtt min/avg/max/mdev = ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+)", ping_text)
    if rtt:
        summary["rtt_ms"] = {
            "min": float(rtt.group(1)),
            "avg": float(rtt.group(2)),
            "max": float(rtt.group(3)),
            "mdev": float(rtt.group(4)),
        }
    return summary


def start(module: Path, folder: Path, profile: str, flags: dict) -> None:
    audit = json.loads((HERE / "audit-three-findings.json").read_text())
    expected = audit["candidate_sha256"]
    if not all(audit["checks"].values()) or digest(module) != expected:
        raise RuntimeError("candidate module differs from its successful ABI audit")
    stock_before = ensure_restored()
    peer, ifindex = R["current_awdl"]()
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", peer):
        raise RuntimeError("unexpected live Mac AWDL address")
    entries, manifest = dist_manifest(HERE / "dist")
    app_entry = entries["houji-owly"]
    winit_entry = entries["wonder-init"]
    tf_env_text = build_tf_env_text(flags)
    folder.mkdir(parents=True)
    (folder / "stock-before.txt").write_text(stock_before)
    (folder / "app.sha256").write_text(manifest)
    (folder / "three-findings.env").write_text(tf_env_text)
    token = uuid.uuid4().hex
    tf_env_sha = stage_tf(module, expected, app_entry, winit_entry, tf_env_text)
    session = dict(
        token=token,
        profile=profile,
        flags=flags,
        module_sha256=expected,
        app_sha256=app_entry[1],
        wonder_init_sha256=winit_entry[1],
        tf_env_sha256=tf_env_sha,
        harness_sha256=HARNESS_TF_SHA,
        channel=149,
        peer_sha256=hashlib.sha256(peer.encode()).hexdigest(),
        ifindex=ifindex,
    )
    write_json(folder / "session.json", session)
    argv = [
        "sh",
        RADIO,
        "run",
        token,
        profile,
        "amsdu",
        "ack",
        peer,
        "0",
        "0",
        "1160",
        "500",
        "reference",
        "0",
        "0",
    ]
    command = shlex.join(argv)
    job = f"{STATE}/job-{token}.log"
    launch = shlex.quote(f'{command}; rc=$?; echo "$rc" >{STATE}/exit-{token}; exit "$rc"')
    root(f"{BUSYBOX} setsid sh -c {launch} </dev/null >{job} 2>&1 &")
    print(
        f"TF_JOB token={token} profile={profile} flags={json.dumps(flags)} module_sha256={expected}",
        flush=True,
    )
    _, phy_dir, completion = session_paths(folder)

    def started():
        rc = exit_status(completion)
        if rc:
            raise RuntimeError(f"radio lease exited at startup ({rc})")
        return root(f"test ! -d {phy_dir}/1 || echo READY").strip()

    try:
        wait_for(started, 105 if flags["sta_coexist"] else 75)
    except Exception:
        root(f"sh {RADIO} stop {token}")
        raise
    finally:
        (folder / "lifecycle-start.log").write_text(root(f"test ! -f {job} || cat {job}"))

    fw_caps = root(f"test ! -f {STATE}/fw-caps.txt || cat {STATE}/fw-caps.txt")
    peer_setup = root(f"test ! -f {STATE}/peer-setup.txt || cat {STATE}/peer-setup.txt")
    (folder / "fw-caps.txt").write_text(fw_caps)
    (folder / "peer-setup.txt").write_text(peer_setup)
    startup_receipts = {
        "fw_caps": parse_kv_line(fw_caps),
        "remote_peer_setup": parse_kv_line(peer_setup),
    }
    fw_addba = root("cat /sys/module/qca_cld3_kiwi_v2/parameters/pt_fw_addba_sent /sys/module/qca_cld3_kiwi_v2/parameters/pt_fw_addba_rc")
    (folder / "fw-addba.txt").write_text(fw_addba)
    if flags["sta_coexist"]:
        coex_before = root(
            f"test ! -f {STATE}/coexist-wlan0-before.txt || cat {STATE}/coexist-wlan0-before.txt"
        )
        coex_during = root(
            f"test ! -f {STATE}/coexist-wlan0-during-wonder.txt || cat {STATE}/coexist-wlan0-during-wonder.txt"
        )
        (folder / "coexist-wlan0-before.txt").write_text(coex_before)
        (folder / "coexist-wlan0-during-wonder.txt").write_text(coex_during)
        startup_receipts["coexist_wlan0_connected_before_pt"] = "Connected to " in coex_before
        startup_receipts["coexist_wlan0_connected_during_wonder"] = "Connected to " in coex_during
    write_json(folder / "startup-receipts.json", startup_receipts)
    print("TF_IDLE " + json.dumps(startup_receipts), flush=True)


def sample(folder: Path, mode: str, mib: int, addba: str, tx_addba: str) -> None:
    if addba != "off" and mode != "2ss":
        raise RuntimeError("--addba requires --mode 2ss")
    accept_addba = addba != "off"
    session, phy_dir, completion = session_paths(folder)
    token = session["token"]
    flags = session["flags"]
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
    pairs, size = (3 if mib >= 64 else 1), mib * MIB
    tx_mode = MODES[mode]
    ba_flag = ADDBA_MODES[addba]
    tx_ba_flag = TX_ADDBA_MODES[tx_addba]
    result = dict(
        token=token,
        profile=session["profile"],
        flags=flags,
        phase=phase,
        mode=mode,
        addba=addba,
        tx_addba=tx_addba,
        accept_addba=accept_addba,
        bytes_per_sample=size,
        pairs=pairs,
        samples=[],
    )

    def idle():
        check_live(token, completion)
        return root(f"test ! -d {remote} || echo READY").strip()

    wall_start_ns = time.time_ns()
    try:
        wait_for(idle, 20)
        root(
            f"set -e; test ! -e {remote}/request; "
            f"printf '%s\\n' '{tx_mode} {ba_flag} {tx_ba_flag} {pairs * 2} {size}' > {remote}/request.tmp; "
            f"mv {remote}/request.tmp {remote}/request"
        )
        io_timeout = 100 if mib >= 64 else 30
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

        wait_for(completed, 35)
        wall_end_ns = time.time_ns()
        logs = {}
        for name in (
            "before",
            "after",
            "rate-before",
            "rate-after",
            "ampdu-before",
            "ampdu-after",
            "peer-before",
            "peer-after",
            "runner.log",
            "server.log",
        ):
            logs[name] = root(f"cat {remote}/{name}")
            (output / name).write_text(logs[name])
        if flags["sta_coexist"]:
            for cname in (
                "wlan0-link-before",
                "wlan0-link-after",
                "wlan0-ping-target",
                "wlan0-ping.log",
            ):
                txt = root(f"test ! -f {remote}/{cname} || cat {remote}/{cname}")
                logs[cname] = txt
                (output / cname).write_text(txt)
            result["wlan0_coexist"] = {
                "connected_before": "Connected to " in logs["wlan0-link-before"],
                "connected_after": "Connected to " in logs["wlan0-link-after"],
                "link_before": logs["wlan0-link-before"].strip(),
                "link_after": logs["wlan0-link-after"].strip(),
                "ping_target": logs["wlan0-ping-target"].strip(),
                "ping": parse_ping_log(logs["wlan0-ping.log"]),
            }

        lifecycle = root(f"cat {STATE}/job-{token}.log")
        result["advertisement"] = S2SS["verify_2ss_advertisement"](
            lifecycle, logs["runner.log"], session["app_sha256"], mode, addba
        )
        result["runner_summary"] = parse_runner_summary_extended(logs["runner.log"])
        result["flow_hash_verified"] = R["verify_flow_hashes"](
            result["samples"], logs["server.log"], size=size, pairs=pairs
        )
        result["rx_phy_delta"] = rx_phy_delta(logs["before"], logs["after"])
        result["tx_rate_delta"] = tx_rate_delta(logs["rate-before"], logs["rate-after"])
        result["ampdu_peak_before"] = parse_kv_line(logs["ampdu-before"])
        result["ampdu_peak_after"] = parse_kv_line(logs["ampdu-after"])
        result["remote_peer_before"] = parse_kv_line(logs["peer-before"])
        result["remote_peer_after"] = parse_kv_line(logs["peer-after"])
        result["throughput_by_direction"] = R["flow_speed_by_direction"](
            result["samples"], pairs
        )
        mac_lqm = collect_mac_lqm_extended(wall_start_ns, wall_end_ns)
        (output / "mac-lqm.log").write_text(
            "\n".join(mac_lqm) + ("\n" if mac_lqm else "")
        )
        result["mac_lqm_tail"] = mac_lqm[-35:]
        result["verified"] = bool(
            result["advertisement"]["verified"] and result["flow_hash_verified"]
        )
        result["radio_idle"] = True
        if not result["verified"]:
            raise RuntimeError("advertisement or paired SHA verification failed")
    except Exception as error:
        result["error"] = str(error)
        result["verified"] = False
        result["radio_idle"] = False
        root(f"sh {RADIO} stop {token}")
        raise
    finally:
        write_json(output / "result.json", result)
        if not result.get("verified"):
            for name in ("runner.log", "server.log", "wlan0-ping.log",
                         "wlan0-link-before", "wlan0-link-after"):
                try:
                    content = root(f"if test -f {remote}/{name}; then cat {remote}/{name}; elif test -f {STATE}/{name}; then cat {STATE}/{name}; fi")
                    (output / name).write_text(content)
                except Exception as collect_error:
                    result.setdefault("collection_errors", []).append(str(collect_error))
            write_json(output / "result.json", result)
    print(
        json.dumps(
            {
                "phase": result["phase"],
                "mode": mode,
                "addba": addba,
                "tx_addba": tx_addba,
                "verified": result["verified"],
                "throughput": result.get("throughput_by_direction"),
                "tx_rate_delta": {
                    k: result["tx_rate_delta"].get(k)
                    for k in (
                        "completed",
                        "ast_overrides",
                        "remote_peer_compl",
                        "ppdus",
                        "agg_ppdus",
                        "agg_mpdus",
                        "ppdu_bin",
                    )
                },
                "ampdu_peak_after": result.get("ampdu_peak_after"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def stop(folder: Path) -> None:
    session, _, completion = session_paths(folder)
    token = session["token"]
    flags = session["flags"]
    if not exit_status(completion):
        check_live(token, completion)
        root(f"sh {RADIO} stop {token}")
    rc = wait_for(lambda: exit_status(completion), 115)
    lifecycle = root(f"cat {STATE}/job-{token}.log")
    if "OWLY_STATE=STOCK_RESTORED" not in lifecycle.splitlines():
        recover_out = root(f"sh {RADIO} recover")
        lifecycle = lifecycle + "\n" + recover_out
        if "OWLY_STATE=STOCK_RESTORED" in recover_out.splitlines():
            rc = "0"
    (folder / "lifecycle-end.log").write_text(lifecycle)
    peer_teardown = root(
        f"test ! -f {STATE}/last-peer-teardown.txt || cat {STATE}/last-peer-teardown.txt"
    )
    (folder / "peer-teardown.txt").write_text(peer_teardown)
    coex_after_pt = ""
    if flags["sta_coexist"]:
        coex_after_pt = root(
            f"test ! -f {STATE}/coexist-wlan0-after-pt-release.txt || cat {STATE}/coexist-wlan0-after-pt-release.txt"
        )
        (folder / "coexist-wlan0-after-pt-release.txt").write_text(coex_after_pt)
    if (
        root(f"cat {STATE}/token").strip() != token
        or "OWLY_STATE=STOCK_RESTORED" not in lifecycle.splitlines()
    ):
        raise RuntimeError(f"radio lease did not restore stock ({rc})")
    expected = session["module_sha256"]
    app = session["app_sha256"]
    winit = session["wonder_init_sha256"]
    tf_sha = session["tf_env_sha256"]
    harness = session["harness_sha256"]
    if (
        not re.fullmatch(r"[0-9a-f]{64}", expected)
        or not re.fullmatch(r"[0-9a-f]{64}", app)
        or not re.fullmatch(r"[0-9a-f]{64}", winit)
        or not re.fullmatch(r"[0-9a-f]{64}", tf_sha)
        or harness != HARNESS_TF_SHA
    ):
        raise RuntimeError("invalid cleanup hashes in lease receipt")
    cleanup = f"""set -eu
test "$(cat {STATE}/token)" = {token}
test ! -e {STATE}/saved.env
echo '{harness}  {RADIO}' | sha256sum -c -
echo '{expected}  {MODULE}' | sha256sum -c -
echo '{app}  {OUTGOING_APP}' | sha256sum -c -
echo '{winit}  {WONDER_INIT_2SS}' | sha256sum -c -
echo '{tf_sha}  {TF_ENV}' | sha256sum -c -
test "$(cat {SHA})" = '{expected}'
test "$(cat {OUTGOING_SHA})" = '{app}'
test "$(cat {WONDER_INIT_2SS_SHA})" = '{winit}'
test "$(cat {TF_SHA})" = '{tf_sha}'
rm -f {RADIO} {MODULE} {SHA} {OUTGOING_APP} {OUTGOING_SHA} {WONDER_INIT_2SS} {WONDER_INIT_2SS_SHA} {TF_ENV} {TF_SHA}
"""
    root(f"{BUSYBOX} flock -n {ROOT}/job.lock sh -c {shlex.quote(cleanup)}")
    stock_after = ensure_restored()
    (folder / "stock-after.txt").write_text(stock_after)
    stop_receipt = {
        "token": token,
        "radio_exit": int(rc),
        "restored": True,
        "remote_peer_teardown": parse_kv_line(peer_teardown),
    }
    if flags["sta_coexist"]:
        stop_receipt["wlan0_still_connected_after_pt_release"] = (
            "Connected to " in coex_after_pt
        )
    write_json(folder / "stop.json", stop_receipt)
    print("TF_STOCK_RESTORED " + json.dumps(stop_receipt), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)

    activate = commands.add_parser("start")
    activate.add_argument("module", type=Path)
    activate.add_argument("folder", type=Path)
    activate.add_argument("--profile", choices=PROFILES, default="vht80-2ss-mcs9-sgi")
    activate.add_argument("--remote-assoc", choices=(0, 1), type=int, default=0)
    activate.add_argument("--remote-ast-override", choices=(0, 1), type=int, default=1)
    activate.add_argument("--remote-hash-override", choices=(0, 1), type=int, default=1)
    activate.add_argument("--remote-reo", choices=(0, 1, 2), type=int, default=0)
    activate.add_argument("--fw-addba", choices=(0, 1), type=int, default=0)
    activate.add_argument("--tx-ampdu", choices=(0, 1), type=int, default=0)
    activate.add_argument("--dynamic-rate", choices=(0, 1), type=int, default=0)
    activate.add_argument("--sta-coexist", choices=(0, 1), type=int, default=0)

    run_sample = commands.add_parser("sample")
    run_sample.add_argument("folder", type=Path)
    run_sample.add_argument("--mode", choices=tuple(MODES), default="2ss")
    run_sample.add_argument("--mib", choices=(1, 16, 64), type=int, default=16)
    run_sample.add_argument("--addba", choices=tuple(ADDBA_MODES), default="amsdu")
    run_sample.add_argument(
        "--tx-addba", choices=tuple(TX_ADDBA_MODES), default="off"
    )

    close = commands.add_parser("stop")
    close.add_argument("folder", type=Path)

    args = parser.parse_args()
    if args.action == "start":
        flags = dict(
            remote_assoc=args.remote_assoc,
            remote_ast_override=args.remote_ast_override,
            remote_hash_override=args.remote_hash_override,
            remote_reo=args.remote_reo,
            fw_addba=args.fw_addba,
            tx_ampdu=args.tx_ampdu,
            dynamic_rate=args.dynamic_rate,
            sta_coexist=args.sta_coexist,
        )
        start(args.module, args.folder, args.profile, flags)
    elif args.action == "sample":
        sample(args.folder, args.mode, args.mib, args.addba, args.tx_addba)
    else:
        stop(args.folder)


if __name__ == "__main__":
    main()
