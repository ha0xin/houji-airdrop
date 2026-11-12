#!/usr/bin/env python3
"""Initialize the fixed-channel PASSTHRU lifecycle and capture peer services."""
import json
from pathlib import Path
import re
import shlex
import sys
import uuid

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parent / "owly-evaluation" / "houji-runner"
sys.path.insert(0, str(RUNNER))
from bench import BUSYBOX, RADIO, ROOT, STATE, ensure_restored, root, wait_for  # noqa: E402


def parse_snapshot(log, marker):
    fields = ("schema", "enabled", "sampled", "create_conf", "sync_delete",
              "fw_unmap_conf", "dp_unmap_conf")
    pattern = "^" + marker + " " + " ".join(field + r"=([01])" for field in fields) + "$"
    rows = re.findall(pattern, log, re.M)
    if len(rows) != 1:
        raise ValueError(f"expected one {marker} snapshot")
    return dict(zip(fields, map(int, rows[0])))


def main():
    output = HERE / "device-read1"
    output.mkdir(exist_ok=False)
    token = uuid.uuid4().hex
    receipt = {"token": token, "channel": 149, "throughput_test": False, "rx_ba": False}
    (output / "stock-before.txt").write_text(ensure_restored())
    expected = json.loads((HERE / "audit-stripped.json").read_text())["candidate_sha256"]
    module = ROOT + "/qca_cld3_kiwi_v2-peer-caps.ko"
    receipt["module_sha256"] = root(f"sha256sum {module}").split()[0]
    if receipt["module_sha256"] != expected:
        raise RuntimeError("staged WLAN does not match the ABI receipt")
    job = f"{STATE}/job-{token}.log"
    completion = f"{STATE}/exit-{token}"
    command = shlex.join(["sh", RADIO, "caps", token])
    launch = shlex.quote(f'{command}; rc=$?; echo "$rc" >{completion}; exit "$rc"')
    try:
        root(f"{BUSYBOX} setsid sh -c {launch} </dev/null >{job} 2>&1 &")
        print(f"PEER_CAPS_JOB token={token}", flush=True)
        receipt["radio_exit"] = int(wait_for(
            lambda: root(f"test ! -f {completion} || cat {completion}").strip(), 400))
        log = root(f"cat {job}")
        (output / "lifecycle.log").write_text(log)
        (output / "stock-after.txt").write_text(ensure_restored())
        receipt["restored"] = True
        if receipt["radio_exit"]:
            raise RuntimeError(f"initialization exited with {receipt['radio_exit']}")
        receipt["before"] = parse_snapshot(log, "PEER_CAPS_BEFORE")
        receipt["after"] = parse_snapshot(log, "PEER_CAPS_AFTER")
        if receipt["before"]["sampled"] or not receipt["after"]["sampled"]:
            raise RuntimeError("unexpected capability sample transition")
        print(json.dumps(receipt["after"], sort_keys=True), flush=True)
        return 0
    except Exception as error:
        receipt["error"] = str(error)
        raise
    finally:
        (output / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
