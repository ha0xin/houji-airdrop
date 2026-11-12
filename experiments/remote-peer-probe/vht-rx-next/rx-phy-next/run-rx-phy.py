#!/usr/bin/env python3
"""Hold one 149 radio lease and run verified Owly phases against one Mac."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import shlex
import uuid

HERE = Path(__file__).resolve().parent
R = runpy.run_path(str(HERE.parents[1] / "run-remote-peer.py"))
STATE, RADIO = R["STATE"], R["RADIO"]
ROOT, BUSYBOX = R["ROOT"], R["BUSYBOX"]
root, wait_for = R["root"], R["wait_for"]
MIB = R["MIB"]


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def session_paths(folder):
    data = json.loads((folder / "session.json").read_text())
    token = data["token"]
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise RuntimeError("invalid session token")
    return data, f"{STATE}/phy-{token}", f"{STATE}/exit-{token}"


def exit_status(completion):
    return root(f"test ! -f {completion} || cat {completion}").strip()


def check_live(token, completion):
    rc = exit_status(completion)
    if rc:
        raise RuntimeError(f"radio lease already exited ({rc})")
    if root(f"cat {STATE}/token").strip() != token:
        raise RuntimeError("radio state belongs to a different lease")


def start(module, folder):
    audit_path = module.resolve().parent.parent / "audit-rx-capacity-8320.json"
    if not audit_path.is_file():
        audit_path = HERE / "audit-rx-phy.json"
    audit = json.loads(audit_path.read_text())
    expected = audit["candidate_sha256"]
    if not all(audit["checks"].values()) or R["digest"](module) != expected:
        raise RuntimeError("RX PHY module differs from its successful ABI audit")
    peer, ifindex = R["current_awdl"]()
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", peer):
        raise RuntimeError("unexpected live Mac AWDL address")
    outgoing, manifest = R["outgoing_app_manifest"]()
    folder.mkdir()
    (folder / "app.sha256").write_text(manifest)
    token = uuid.uuid4().hex
    session = dict(token=token, module_sha256=expected, app_sha256=outgoing[1],
                   harness_sha256=R["RX_PHY_HARNESS_SHA"], channel=149,
                   peer_sha256=hashlib.sha256(peer.encode()).hexdigest(), ifindex=ifindex)
    write_json(folder / "session.json", session)
    R["stage"](module, expected, capacity=True, outgoing=outgoing, phy=True)
    argv = ["sh", RADIO, "run", token, "vht80-sgi", "amsdu", "ack", peer,
            "0", "0", "1160", "500", "reference", "0", "0"]
    command = shlex.join(argv)
    job = f"{STATE}/job-{token}.log"
    launch = shlex.quote(f'{command}; rc=$?; echo "$rc" >{STATE}/exit-{token}; exit "$rc"')
    root(f"{BUSYBOX} setsid sh -c {launch} </dev/null >{job} 2>&1 &")
    print(f"RX_PHY_JOB token={token} module_sha256={expected}", flush=True)
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
    print("RX_PHY_IDLE", flush=True)


def stats_delta(before, after):
    def parse(line):
        return dict(field.split("=", 1) for field in line.split())

    first, last = parse(before), parse(after)
    if first.keys() != last.keys() or any(last.get(k) != value or first.get(k) != value
                                         for k, value in (("schema", "1"), ("enabled", "1"),
                                                          ("ampdu", "unknown"))):
        raise ValueError("RX PHY observer schema or enable readback changed")
    delta = {}
    for key, end in last.items():
        if key in ("schema", "enabled", "ampdu"):
            continue
        a = [int(x) for x in first[key].split(",")]
        b = [int(x) for x in end.split(",")]
        if len(a) != len(b) or any(start < 0 or finish < start for start, finish in zip(a, b)):
            raise ValueError(f"RX PHY cumulative counter moved backwards: {key}")
        values = [finish - start for start, finish in zip(a, b)]
        delta[key] = values if "," in end else values[0]
    return delta


def replace_idle_app(session, remote, directory):
    (app, sha), manifest = R["outgoing_app_manifest"](directory)
    token = session["token"]
    old_sha = session["app_sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", old_sha) or sha == old_sha:
        raise RuntimeError("invalid baseline or identical diagnostic executable")
    backup = f"{ROOT}/.owly-app-{token}.original"
    next_app = f"{ROOT}/.owly-app-{token}.next"
    installed = R["OUTGOING_APP"]
    adb = R["adb"]
    staging = adb("shell", "mktemp -d /data/local/tmp/owly-gap-stage.XXXXXX").strip()
    if not re.fullmatch(r"/data/local/tmp/owly-gap-stage[.][a-zA-Z0-9]+", staging):
        raise RuntimeError("unexpected diagnostic staging directory")
    try:
        adb("push", str(app), f"{staging}/candidate")
        root(f"""set -eu
test "$(cat {STATE}/token)" = {token}
test "$(cat {STATE}/status)" = PHY_IDLE
test ! -e {remote}/request
test ! -e {backup}
test ! -e {next_app}
echo '{old_sha}  {installed}' | sha256sum -c -
echo '{sha}  {staging}/candidate' | sha256sum -c -
cp {staging}/candidate {next_app}
chmod 700 {next_app}
echo '{sha}  {next_app}' | sha256sum -c -
ln {installed} {backup}
mv -f {next_app} {installed}
""")
    finally:
        try:
            adb("shell", f"rm -f {staging}/candidate; rmdir {staging}")
        except Exception as error:
            print(f"diagnostic staging cleanup: {error}", flush=True)
    return sha, backup, manifest


def restore_idle_app(session, replacement):
    sha, backup, _ = replacement
    installed = R["OUTGOING_APP"]
    root(f"""set -eu
test "$(cat {STATE}/token)" = {session['token']}
echo '{sha}  {installed}' | sha256sum -c -
echo '{session['app_sha256']}  {backup}' | sha256sum -c -
mv -f {backup} {installed}
""")


def gap_stats(runner_log):
    rows = [line for line in runner_log.splitlines() if line.startswith("OWLY_RX_GAPS ")]
    if len(rows) != 1:
        raise ValueError("expected one RX gap summary")
    return {key: json.loads(value) for key, value in
            re.findall(r"(\w+)=(\d+|\[[\d, ]*\])", rows[0])}


def current_awdl_ip6():
    info = R["subprocess"].check_output(["ifconfig", "awdl0"], text=True)
    addrs = re.findall(r"\binet6 (fe80:[0-9a-f:]+)%awdl0\b", info)
    if len(addrs) != 1 or "status: active" not in info:
        raise RuntimeError("unexpected live Mac awdl0 link-local IPv6 address")
    return addrs[0]


def verify_route_quickack(token, mac_ip6, mode, apply_change):
    change_cmd = (
        "ip -6 route change fe80::/64 dev owly0 table 10075 metric 1024 quickack 1"
        if (mode == "on" and apply_change) else ":"
    )
    raw = root(f"""set -eu
test "$(cat {STATE}/token)" = {shlex.quote(token)}
test "$(cat {STATE}/status)" = OWLY_READY
phone_ip=$(cat {STATE}/route-ip)
case "$phone_ip" in fe80:*) ;; *) exit 2 ;; esac
before_table=$(ip -6 route show table 10075)
{change_cmd}
after_table=$(ip -6 route show table 10075)
get_oif_uid=$(ip -6 route get {shlex.quote(mac_ip6)} from "$phone_ip" oif owly0 uid 0)
get_fibmatch=$(ip -6 route get fibmatch {shlex.quote(mac_ip6)} from "$phone_ip" oif owly0 uid 0)
get_from=$(ip -6 route get {shlex.quote(mac_ip6)} from "$phone_ip")
printf 'PHONE_IP=%s\\n---BEFORE_TABLE---\\n%s\\n---AFTER_TABLE---\\n%s\\n---GET_OIF_UID---\\n%s\\n---GET_FIBMATCH---\\n%s\\n---GET_FROM---\\n%s\\n' \\
  "$phone_ip" "$before_table" "$after_table" "$get_oif_uid" "$get_fibmatch" "$get_from"
""")
    sections = {}
    current = None
    phone_ip = None
    for line in raw.splitlines():
        if line.startswith("PHONE_IP="):
            phone_ip = line.removeprefix("PHONE_IP=").strip()
        elif line.startswith("---") and line.endswith("---"):
            current = line.strip("-").lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line.strip())
    fe80_before = [r for r in sections.get("before_table", []) if r.startswith("fe80::/64 ")]
    fe80_after = [r for r in sections.get("after_table", []) if r.startswith("fe80::/64 ")]
    checks = [sections.get(k, []) for k in ("get_oif_uid", "get_fibmatch", "get_from")]
    if len(fe80_before) != 1 or len(fe80_after) != 1 or any(len(c) != 1 for c in checks):
        raise RuntimeError(f"unexpected table 10075 route lines: {sections}")
    route_lines = [fe80_after[0], *(c[0] for c in checks)]
    common_ok = all(
        "dev owly0" in line and "metric 1024" in line
        for line in route_lines
    ) and all("table 10075" in c[0] for c in checks)
    if mode == "on":
        before_ok = ("quickack" not in fe80_before[0]) if apply_change else ("quickack 1" in fe80_before[0])
        mode_ok = before_ok and all("quickack 1" in line for line in route_lines)
    else:
        mode_ok = "quickack" not in fe80_before[0] and all("quickack" not in line for line in route_lines)
    if not (common_ok and mode_ok):
        raise RuntimeError(f"route quickack verification failed for mode={mode}: {sections}")
    return {
        "requested": mode,
        "applied_change": bool(mode == "on" and apply_change),
        "phone_ip6": phone_ip,
        "mac_ip6_sha256": hashlib.sha256(mac_ip6.encode()).hexdigest(),
        "before_table_10075": sections.get("before_table", []),
        "after_table_10075": sections.get("after_table", []),
        "get_oif_uid": sections["get_oif_uid"][0],
        "get_fibmatch": sections["get_fibmatch"][0],
        "get_from": sections["get_from"][0],
        "verified": True,
    }


def verify_advertisement(lifecycle, runner_log, app_hash, enabled):
    adv = R["outgoing_vht_result"](lifecycle, runner_log, app_hash, enabled)
    ht_marker = "OWLY_MIF_SUBMITTED_HT_CAPABILITIES="
    ht_wire = [line.removeprefix(ht_marker) for line in runner_log.splitlines() if line.startswith(ht_marker)]
    if ht_wire:
        adv["first_host_submitted_mif_ht_capabilities"] = ht_wire
    wire = adv.get("first_host_submitted_mif_container", [])
    if enabled and wire in (["bf0c00000000fcff0000fcff0000"], ["bf0c01000000fcff0000fcff0000"]):
        adv["vht_max_mpdu"] = 7991 if wire[0].startswith("bf0c01") else 3895
        adv["verified"] = (
            adv.get("matching_app_load", False) and
            adv.get("reported_switch") == ["OWLY_OUTGOING_VHT=true"]
        )
    return adv


def sample(folder, mode, mib, timeline=None, gap_app=None, quickack=None):
    session, phy_dir, completion = session_paths(folder)
    token = session["token"]
    peer, ifindex = R["current_awdl"]()
    if (ifindex != session["ifindex"] or
            hashlib.sha256(peer.encode()).hexdigest() != session["peer_sha256"]):
        raise RuntimeError("Mac AWDL identity changed; use a fresh radio lease")
    mac_ip6 = current_awdl_ip6() if quickack is not None else None
    phases = [int(p.name) for p in folder.iterdir() if p.is_dir() and p.name.isdigit()]
    phase = max(phases, default=0) + 1
    output = folder / str(phase)
    output.mkdir()
    remote = f"{phy_dir}/{phase}"
    pairs, size = (3 if mib == 64 else 1), mib * MIB
    tx = "1" if mode == "mif" else "0"
    result = dict(token=token, phase=phase, mode=mode, bytes_per_sample=size, pairs=pairs, samples=[])
    if quickack is not None:
        result["route_quickack_mode"] = quickack
        result["route_quickack_checks"] = []
    snapshots = {}
    replacement = None
    if timeline is not None:
        from bench import load_tcp_timeline

        result["mac_tcp_timeline_helper_sha256"] = R["digest"](timeline)
        tcp_timeline = load_tcp_timeline(timeline)
    else:
        tcp_timeline = None

    def observe(index, position):
        if replacement and (index, position) == (1, "before"):
            process = root("""ps -A -o PID,ARGS | awk '$2 == "./houji-owly-outgoing-vht" || $2 == "houji-owly-outgoing-vht" { print $1 }'""").split()
            if len(process) != 1 or not process[0].isdigit():
                raise RuntimeError("could not identify the live diagnostic executable")
            running = root(f"sha256sum /proc/{process[0]}/exe").split()[0]
            if running != replacement[0]:
                raise RuntimeError("live diagnostic executable SHA differs from its manifest")
            result["running_gap_app_sha256"] = running
        if quickack is not None and position == "before":
            check = verify_route_quickack(token, mac_ip6, quickack, apply_change=(index == 1))
            check["sample"] = index
            result["route_quickack_checks"].append(check)
        try:
            raw = root("cat /sys/module/qca_cld3_kiwi_v2/parameters/pt_rx_phy_stats")
        except Exception as error:
            result.setdefault("rx_phy_sample_errors", []).append(str(error))
            return
        (output / f"phy-{index}-{position}.txt").write_text(raw)
        snapshots[index, position] = raw

    def idle():
        check_live(token, completion)
        return root(f"test ! -d {remote} || echo READY").strip()

    try:
        wait_for(idle, 20)
        if gap_app is not None:
            replacement = replace_idle_app(session, remote, gap_app)
            (output / "gap-app.sha256").write_text(replacement[2])
        root(f"set -e; test ! -e {remote}/request; "
             f"printf '%s\\n' '{tx} {pairs * 2} {size}' > {remote}/request.tmp; "
             f"mv {remote}/request.tmp {remote}/request")
        phone_mac = R["run_flow_samples"](
            result["samples"], token, completion, ifindex, size=size, pairs=pairs,
            io_timeout=100 if mib == 64 else 25, require_mif=True, phase=phase,
            observe=observe if timeline is not None else None, tcp_timeline=tcp_timeline)

        def completed():
            check_live(token, completion)
            return root(f"test ! -f {remote}/done || echo DONE").strip()

        wait_for(completed, 30)
        logs = {}
        for name in ("before", "after", "runner.log", "server.log"):
            logs[name] = root(f"cat {remote}/{name}")
            (output / name).write_text(logs[name])
        lifecycle = root(f"cat {STATE}/job-{token}.log")
        result["advertisement"] = verify_advertisement(
            lifecycle, logs["runner.log"], session["app_sha256"], mode == "mif")
        try:
            _, cap_rows, _, _ = R["bounded_kernel_log_snapshot"](r"PT_RX_CAPACITY ")
            result["pt_rx_capacity_dmesg"] = cap_rows[-10:]
        except Exception as error:
            result["pt_rx_capacity_dmesg_error"] = str(error)
        result["flow_hash_verified"] = R["verify_flow_hashes"](
            result["samples"], logs["server.log"], size=size, pairs=pairs)
        quickack_ok = (
            quickack is None or (
                len(result.get("route_quickack_checks", [])) == pairs * 2 and
                all(c.get("verified") for c in result["route_quickack_checks"])
            )
        )
        result["verified"] = (
            result["advertisement"]["verified"] and
            result["flow_hash_verified"] and
            quickack_ok
        )
        if not result["verified"]:
            raise RuntimeError("advertisement or paired SHA verification failed")
        try:
            result["rx_phy_delta"] = stats_delta(logs["before"], logs["after"])
            result["rx_phy_has_valid_samples"] = result["rx_phy_delta"].get("valid", 0) > 0
        except ValueError as error:
            result["rx_phy_error"] = str(error)
            result["rx_phy_has_valid_samples"] = False
        result["throughput_by_direction"] = R["flow_speed_by_direction"](result["samples"], pairs)
        if replacement:
            try:
                result["rx_gap"] = gap_stats(logs["runner.log"])
            except ValueError as error:
                result["rx_gap_error"] = str(error)
        if timeline is not None:
            result["rx_phy_by_sample"] = []
            for row in result["samples"]:
                index = row["sample"]
                info = {"sample": index, "direction": row["direction"]}
                try:
                    info["delta"] = stats_delta(snapshots[index, "before"], snapshots[index, "after"])
                except (KeyError, ValueError) as error:
                    info["error"] = str(error)
                result["rx_phy_by_sample"].append(info)
            try:
                result["mac_awdl_schedule"] = R["read_mac_schedule"](result["samples"], phone_mac)
            except Exception as error:
                result["mac_schedule_error"] = str(error)
        result["radio_idle"] = True
    except Exception as error:
        result["error"] = str(error)
        result["verified"] = False
        root(f"sh {RADIO} stop {token}")
        raise
    finally:
        if replacement:
            try:
                restore_idle_app(session, replacement)
                result["baseline_app_restored"] = True
            except Exception as error:
                result["baseline_app_restore_error"] = str(error)
                result["gap_app_backup"] = replacement[1]
                result["verified"] = False
                result["radio_idle"] = False
                try:
                    root(f"sh {RADIO} stop {token}")
                except Exception as stop_error:
                    result["lease_stop_error"] = str(stop_error)
        write_json(output / "result.json", result)
    if not result["verified"]:
        raise RuntimeError("failed to restore the baseline executable after sampling")
    print(json.dumps({k: result[k] for k in ("phase", "verified", "radio_idle")}), flush=True)


def stop(folder):
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
    harness = session["harness_sha256"]
    if (not re.fullmatch(r"[0-9a-f]{64}", expected) or
            not re.fullmatch(r"[0-9a-f]{64}", app) or harness != R["RX_PHY_HARNESS_SHA"]):
        raise RuntimeError("invalid cleanup hashes in lease receipt")
    cleanup = f"""set -eu
test "$(cat {STATE}/token)" = {token}
test ! -e {STATE}/saved.env
echo '{harness}  {RADIO}' | sha256sum -c -
echo '{expected}  {R['MODULE']}' | sha256sum -c -
echo '{app}  {R['OUTGOING_APP']}' | sha256sum -c -
test "$(cat {R['SHA']})" = '{expected}'
test "$(cat {R['OUTGOING_SHA']})" = '{app}'
rm -f {RADIO} {R['MODULE']} {R['SHA']} {R['OUTGOING_APP']} {R['OUTGOING_SHA']}
"""
    root(f"{BUSYBOX} flock -n {ROOT}/job.lock sh -c {shlex.quote(cleanup)}")
    write_json(folder / "stop.json", {"token": token, "radio_exit": int(rc), "restored": True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    activate = commands.add_parser("start")
    activate.add_argument("module", type=Path)
    activate.add_argument("folder", type=Path)
    transfer = commands.add_parser("sample")
    transfer.add_argument("folder", type=Path)
    transfer.add_argument("--mode", choices=("off", "mif"), default="mif")
    transfer.add_argument("--mib", choices=(1, 16, 64), type=int, default=1)
    transfer.add_argument("--tcp-timeline", type=Path)
    transfer.add_argument("--gap-app", type=Path, metavar="DIST")
    transfer.add_argument("--quickack", choices=("off", "on"))
    close = commands.add_parser("stop")
    close.add_argument("folder", type=Path)
    args = parser.parse_args()
    if args.action == "start":
        start(args.module, args.folder)
    elif args.action == "sample":
        if (args.gap_app is not None or args.quickack is not None) and args.tcp_timeline is None:
            parser.error("--gap-app and --quickack require --tcp-timeline for paired progress evidence")
        sample(args.folder, args.mode, args.mib, args.tcp_timeline, args.gap_app, args.quickack)
    else:
        stop(args.folder)


if __name__ == "__main__":
    main()
