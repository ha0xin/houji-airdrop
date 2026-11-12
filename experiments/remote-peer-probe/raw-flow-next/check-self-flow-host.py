#!/usr/bin/env python3
"""Exercise the self-only host path without ADB, sockets, or REO state."""
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
RATE = (HERE / "device-flow1" / "after.txt").read_text()
TOKEN = "1" * 32
FIELDS = {k: 0 for k in runner.REQUIRED}
FIELDS.update(
    generation=0, reo_enabled=0, reo_steps=0, setup_issued=0, setup_returned=0,
    reo_queue_posted=0, reo_queue_tail=0, reo_qref_write=0, reo_dma_detached=0,
    reo_dma_freed=0, flow_self_only=1, flow_enabled=1,
)
INITIAL = dict(FIELDS, result=0, flow_self_generation=0)
READY = dict(FIELDS, result=1, flow_self_generation=1, flow_ready=1, flow_opened=1)
FINAL = dict(
    FIELDS, result=8, flow_self_generation=1, flow_ready=0, flow_opened=1, flow_stop=1,
    flow_status=0, flow_cpu_quiet=1, flow_tx_quiet=1, flow_quiesce_status=0,
    flow_tx_status=0, flow_tx_first=3, flow_tx_last=0,
)
DRAIN = dict(phase=5, error=0, closing=1, peer_requested=1, peer_started=1, peer_result=0)


def line(marker, fields):
    return marker + " " + " ".join(f"{key}={value}" for key, value in fields.items())


def check(completed, final):
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
        (folder / "audit-self-flow.json").write_text(json.dumps({"candidate_sha256": digest}))
        out = folder / "result"
        launched = False

        def root(command):
            nonlocal launched
            if command == f"sha256sum {runner.MODULE}":
                return digest + "  fixture\n"
            if "setsid" in command and "remote-flow" in command:
                assert not launched
                launched = True
                return ""
            assert launched
            if f"/exit-{TOKEN}" in command:
                return "0"
            if f"/job-{TOKEN}.log" in command:
                return device_log
            if command == f"cat {runner.STATE}/token":
                return TOKEN
            if "after.txt" in command:
                return RATE.replace("completed=0 valid=0", f"completed={completed} valid={completed}", 1)
            if "runner.log" in command or "before.txt" in command or command.startswith("rm -f"):
                return ""
            raise AssertionError(command)

        def unexpected(*args, **kwargs):
            raise AssertionError("self-only path tried TCP or REO capture")

        args = ["runner", "--self-flow", str(module), str(out)]
        with patch.multiple(
            runner, FLOW=folder, root=root, ensure_restored=lambda: "stock\n",
            stage=lambda *args: None, current_awdl=lambda: ("02:00:00:00:01:01", 5),
            transfer=unexpected, reo_log_snapshot=unexpected,
        ), patch.object(runner.uuid, "uuid4", return_value=type("ID", (), {"hex": TOKEN})()), \
                patch.object(sys, "argv", args):
            expected = bool(completed and final == FINAL)
            assert (runner.main() == 0) == expected
        receipt = json.loads((out / "result.json").read_text())
        assert receipt["verified"] == expected
        assert receipt["self_flow"]["tqm_completed"] == completed
        assert receipt["self_flow"]["raw_ring_attempts"] == 56


check(3, FINAL)
check(0, FINAL)
check(3, dict(FINAL, create_issued=1))
check(3, dict(FINAL, flow_self_generation=2))
with tempfile.TemporaryDirectory() as name:
    script = runner.prepare_harness(Path(name), self_flow=True)
    assert runner.digest(script) == runner.SELF_FLOW_HARNESS_SHA
print("self-only main requires TQM, consistent generation and zero remote steps; exact harness SHA verified")
