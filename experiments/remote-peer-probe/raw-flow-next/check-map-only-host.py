#!/usr/bin/env python3
"""Exercise the create/map-only host path with isolated ADB fixtures."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("runner", HERE.parent / "run-remote-peer.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
RATE = (HERE / "device-self-flow1" / "after.txt").read_text()
TOKEN = "2" * 32
BOOT = "08cd09e6-be57-403d-9b6f-f48164dfb59e"
STATES = json.loads((HERE / "device-ast1" / "result.json").read_text())
INITIAL = dict(STATES["initial"], flow_map_only=1)
REO_ZERO = {key: 0 for key in ("reo_steps", "setup_issued", "setup_returned",
            "reo_queue_posted", "reo_queue_tail", "reo_dma_detached")}
READY = runner.parse_line((HERE / "device-ast1" / "lifecycle.log").read_text(), "PEER_FLOW_READY")
READY.update(REO_ZERO, flow_map_only=1)
FINAL = dict(STATES["final"], **REO_ZERO, flow_map_only=1)
FINAL.update(runner.REQUIRED)
FINAL.update(flow_tx_quiet=1, flow_tx_status=0, flow_tx_first=0, flow_tx_last=0)
DRAIN = dict(STATES["drain"], phase=5, error=0, peer_result=0)


def line(marker, fields):
    return marker + " " + " ".join(f"{key}={value}" for key, value in fields.items())


def check(final, snapshot_after, expected, completed=31):
    device_log = "\n".join((
        line("PEER_TRIAL_BEFORE", INITIAL), line("PEER_FLOW_READY", READY),
        line("PEER_TRIAL_TERMINAL", final), line("PEER_TRIAL_FINAL", final),
        line("PEER_TRIAL_DRAIN", DRAIN),
    ))
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        module = folder / "test.ko"
        module.write_bytes(b"fixture")
        digest = hashlib.sha256(module.read_bytes()).hexdigest()
        (folder / "audit-map-only.json").write_text(json.dumps({"candidate_sha256": digest}))
        out = folder / "result"

        def root(command):
            if command == f"sha256sum {runner.MODULE}":
                return digest + "  fixture\n"
            if "setsid" in command and "remote-flow" in command:
                return ""
            if f"/exit-{TOKEN}" in command:
                return "0"
            if f"/job-{TOKEN}.log" in command:
                return device_log
            if command == f"cat {runner.STATE}/token":
                return TOKEN
            if "after.txt" in command:
                return RATE.replace("completed=31", f"completed={completed}", 1)
            if "runner.log" in command or "before.txt" in command or command.startswith("rm -f"):
                return ""
            raise AssertionError(command)

        def unexpected(*args, **kwargs):
            raise AssertionError("map-only path attempted TCP")

        argv = ["runner", "--map-only", str(module), str(out)]
        with patch.multiple(
            runner, FLOW=folder, root=root, ensure_restored=lambda: "stock\n",
            stage=lambda *args: None, current_awdl=lambda: ("02:00:00:00:01:01", 5),
            transfer=unexpected, stock_vht_capabilities=lambda: "stock phy\n",
        ), patch.object(runner.uuid, "uuid4", return_value=type("ID", (), {"hex": TOKEN})()), \
                patch.object(runner, "map_only_log_snapshot", side_effect=[(BOOT, [], 0.0, 9.0), snapshot_after]), \
                patch.object(sys, "argv", argv):
            assert (runner.main() == 0) == expected
        receipt = json.loads((out / "result.json").read_text())
        assert receipt["verified"] == expected
        assert receipt["ast"]["tqm_completed"] == completed


check(FINAL, (BOOT, [], 0.0, 15.0), True)
check(dict(FINAL, reo_steps=1), (BOOT, [], 0.0, 15.0), False)
check(dict(FINAL, slot_removed=0), (BOOT, [], 0.0, 15.0), False)
check(dict(FINAL, generation=2), (BOOT, [], 0.0, 15.0), False)
check(FINAL, (BOOT, [], 0.0, 15.0), False, completed=0)
check(FINAL, (BOOT, ["[ 10.0] PT_REO_DMA_FREE generation=1 tid=0 path=2"], 0.0, 15.0), False)
check(FINAL, ("1" * 36, [], 0.0, 15.0), False)
check(FINAL, (BOOT, [], 10.0, 15.0), False)
assert not runner.map_only_dma_result((BOOT, ["old record"], 0.0, 9.0), (BOOT, [], 0.0, 15.0))["verified"]
with tempfile.TemporaryDirectory() as name:
    script = runner.prepare_harness(Path(name), map_only=True)
    assert runner.digest(script) == runner.MAP_ONLY_HARNESS_SHA
print("map-only requires real ready/cleanup, unchanged boot and zero observed REO activity; exact harness SHA verified")
