#!/usr/bin/env python3
"""Recover same-job DMA receipts after a failed first dmesg collection."""
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("runner", HERE.parent / "run-remote-peer.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def stamp(value):
    rounded = re.sub(r"(\.\d{6})\d+", r"\1", value)
    return datetime.strptime(rounded, "%Y-%m-%d %H:%M:%S.%f %z").timestamp()


def main():
    directory = Path(sys.argv[1]).resolve()
    original = json.loads((directory / "result.json").read_text())
    token = original["token"]
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("unexpected radio job token")
    if original["verified"] or not original["restored"] or original["radio_exit"]:
        raise ValueError("expected a completed job with failed evidence collection")
    root = runner.root
    state = runner.STATE
    lines = root(f"""set -e
set -o pipefail
if false | true; then exit 1; fi
cat {state}/token
cut -d' ' -f1 /proc/uptime
date +%s.%N
cut -d' ' -f1 /proc/uptime
stat -c '%y' {state}/peer-final.txt {state}/exit-{token}
cat {state}/exit-{token}
""").splitlines()
    current_token, uptime_before, wall, uptime_after, final_time, exit_time, exit_code = lines
    if current_token != token or exit_code != "0":
        raise RuntimeError("current device state is not the original completed job")
    boot, rows = runner.reo_log_snapshot()
    if boot != original["reo"]["boot_id"]:
        raise RuntimeError("device rebooted since original result")
    offsets = [float(wall) - float(uptime_after), float(wall) - float(uptime_before)]
    start, end = stamp(final_time), stamp(exit_time)
    rows_by_time = []
    for row in rows:
        match = re.match(r"^\[\s*(\d+\.\d+)\]", row)
        if not match:
            raise RuntimeError("dmesg row has no kernel timestamp")
        event_uptime = float(match[1])
        window = [event_uptime + offset for offset in offsets]
        if start <= window[0] <= window[1] <= end:
            rows_by_time.append({"log": row, "uptime_s": event_uptime, "wall_epoch_bounds": window})

    adjudicated = runner.reo_device_result(
        (boot, []), (boot, [row["log"] for row in rows_by_time]), original["final"])
    full_verified = adjudicated["verified"] and runner.verified(
        original["initial"], original["terminal"], original["final"], original["drain"],
        original["radio_exit"], original["restored"])
    receipt = {
        "token": token, "boot_id": boot, "module_sha256": original["module_sha256"],
        "original_before_snapshot_valid": False,
        "android_pipefail_failure_propagated": True,
        "original_snapshot_error": "/system/bin/printf: Argument list too long",
        "source": "same-boot dmesg filtered by the original token's final and exit file mtimes",
        "final_snapshot_wall": final_time, "exit_file_wall": exit_time,
        "wall_minus_uptime_bounds_s": offsets, "events": rows_by_time,
        "all_current_matching_lines": len(rows),
        "reo": adjudicated, "verified": full_verified,
    }
    output = directory / "post-unload-dma-audit.json"
    with output.open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"verified": full_verified, "output": str(output)}))
    return 0 if full_verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
