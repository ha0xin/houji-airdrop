#!/usr/bin/env python3
"""Exercise the full host flow path with isolated fake ADB and sockets."""
from contextlib import ExitStack
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
TOKEN = "1234567890abcdef" * 2
BOOT = "08cd09e6-be57-403d-9b6f-f48164dfb59e"
MAC = "02:00:00:00:01:01"
PHONE = "02:00:00:00:01:02"
INDEX = 37
RATE_NO_COMPLETION = (HERE / "device-flow1" / "after.txt").read_text()
RATE_COMPLETION = RATE_NO_COMPLETION.replace(
    "completed=0 valid=0", "completed=5 valid=5", 1)
ROWS = [f"[  2.{tid:02}] PT_REO_DMA_FREE generation=1 tid={tid} path=2 flush_status=4294967295"
        for tid in (0, 16)]
FINAL = {
    **runner.REQUIRED, "generation": 1, "vdev": 0, "peer_id": 1,
    "reo_enabled": 1, "reo_steps": 7, "reo_duplicates": 0, "reo_conflict": 0,
    "setup_issued": 1, "setup_returned": 1, "setup_status": 0, "route_submit": 0,
    "nonqos_setup": 0, "tid0_setup": 0, "nonqos_flags": 4103, "tid0_flags": 7,
    "reo_queue_posted": 10001, "reo_queue_tail": 10001, "reo_qref_write": 0,
    "reo_queue_error": 0, "reo_queue_duplicates": 0, "reo_qref_clear": 0,
    "reo_dma_detached": 10001, "reo_dma_duplicates": 0, "reo_dma_conflict": 0,
    "flow_enabled": 1, "flow_ready": 0, "flow_opened": 1, "flow_stop": 1,
    "flow_status": 0, "flow_cpu_quiet": 1, "flow_tx_quiet": 1,
    "flow_quiesce_status": 0, "flow_tx_status": 0, "flow_tx_first": 3, "flow_tx_last": 0,
    "flow_be_pass": 15, "flow_raw_qos_submit": 13, "flow_buf_cfg": 4096,
    "flow_tlv_size": 272, "flow_capacity_max": 4096,
}
DRAIN = dict(phase=5, error=0, closing=1, peer_requested=1, peer_started=1, peer_result=0)


def fields(marker, values):
    return marker + " " + " ".join(f"{key}={value}" for key, value in values.items())


def check_full(folder, natural_exit, ast=False):
    candidate = folder / "candidate.ko"
    candidate.write_bytes(b"fixture")
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    (folder / ("audit-ast.json" if ast else "audit-flow.json")).write_text(json.dumps({"candidate_sha256": digest}))
    result = folder / "result"
    server_lines = ["HASH_READY 2001:db8::2f"]
    calls = []
    stopped = False
    final = {**FINAL, **({
        "ast_enabled": 1, "ast_map_seen": 1, "ast_self_proven": 1,
        "ast_distinct": 1, "ast_conflict": 0, "ast_self_idx": 0,
        "ast_self_hash": 0x54, "ast_remote_idx": 0, "ast_remote_hash": 0x23,
        "ast_tx_idx": 0, "ast_tx_cache": 4, "ast_tx_samples": 5, "ast_tx_mismatch": 0,
    } if ast else {})}
    device_log = "\n".join((
        fields("PEER_TRIAL_BEFORE", {"generation": 0, "result": 0}),
        fields("PEER_TRIAL_TERMINAL", final),
        fields("PEER_TRIAL_FINAL", final),
        fields("PEER_TRIAL_DRAIN", DRAIN),
    ))

    def root(command):
        nonlocal stopped
        if command.startswith("sha256sum "):
            return digest + "  module\n"
        if command.startswith("cat ") and command.endswith("/token"):
            return TOKEN
        if "exit-" + TOKEN in command and "test ! -f" in command:
            return "0" if stopped or (natural_exit and len(server_lines) == 3) else ""
        if "server.log" in command:
            return "\n".join(server_lines) + "\n"
        if "wonder0/address" in command:
            return PHONE
        if "setsid" in command and "remote-flow" in command:
            calls.append("launch")
            return ""
        if command == f"sh {runner.RADIO} stop {TOKEN}":
            stopped = True
            calls.append("stop")
            return ""
        if "job-" + TOKEN in command:
            return device_log
        if "after.txt" in command:
            return RATE_COMPLETION
        if command.startswith("test ! -f") or command.startswith("rm -f"):
            return ""
        raise AssertionError(f"unexpected ADB fixture call {command!r}")

    def transfer(ip, index, mode, size, payload, expected, sample, *, io_timeout):
        assert ip == "2001:db8::2f" and index == INDEX and io_timeout == 12
        assert size == len(payload) == runner.MIB and hashlib.sha256(payload).digest() == expected
        calls.append(f"transfer-{mode}")
        server_lines.append(f"HASH_OK sample={sample} mode={mode} bytes={size} seconds=0.125 sha256={expected.hex()}")
        return dict(status="verified_mac", sha256=expected.hex(), sample=sample, mode=mode, bytes=size)

    def schedule(samples, mac):
        assert mac == PHONE and len(samples) == 2
        assert stopped or natural_exit
        calls.append("schedule")
        return [{"sample": row["sample"]} for row in samples]

    with ExitStack() as stack:
        stack.enter_context(patch.multiple(
            runner, FLOW=folder, root=root, current_awdl=lambda: (MAC, INDEX),
            ensure_restored=lambda: "stock\n", stage=lambda *args: None,
            stock_vht_capabilities=lambda: "stock phy\n", transfer=transfer,
            read_mac_schedule=schedule))
        stack.enter_context(patch.object(runner.uuid, "uuid4",
                                         return_value=type("ID", (), {"hex": TOKEN})()))
        stack.enter_context(patch.object(runner, "reo_log_snapshot",
                                         side_effect=[(BOOT, []), (BOOT, ROWS)]))
        stack.enter_context(patch.object(sys, "argv",
                                         ["runner", "--ast" if ast else "--flow", str(candidate), str(result)]))
        assert runner.main() == 0
    receipt = json.loads((result / "result.json").read_text())
    assert receipt["verified"] and receipt["flow_hash_verified"] and receipt["reo"]["dma_cleanup_verified"]
    if ast:
        assert receipt["ast"] == {"tqm_completed": 5, "status": "self_descriptor_and_tqm_completion", "verified": True}
        for changed, status in (
            ({"ast_remote_hash": 0x24}, "descriptor_pairs_indistinguishable"),
            ({"ast_self_proven": 0}, "self_slot_not_proven"),
            ({"ast_conflict": 1}, "map_conflict"),
            ({"ast_tx_cache": 3}, "self_descriptor_not_proven"),
            ({"ast_tx_samples": 0}, "self_descriptor_not_proven"),
            ({"ast_tx_mismatch": 1}, "self_descriptor_not_proven"),
        ):
            observed = runner.ast_device_result({**final, **changed}, RATE_COMPLETION)
            assert not observed["verified"] and observed["status"] == status, observed
        observed = runner.ast_device_result(final, RATE_NO_COMPLETION)
        assert not observed["verified"] and observed["status"] == "no_tqm_completion_observed"
        assert observed["tqm_completed"] == 0
        observed = runner.ast_device_result(final, RATE_COMPLETION + RATE_COMPLETION)
        assert not observed["verified"] and observed["tqm_completed"] is None
    assert calls == ["launch", "transfer-0", "transfer-1"] + ([] if natural_exit else ["stop"]) + ["schedule"]


for ast in (False, True):
    for mode in (False, True):
        with tempfile.TemporaryDirectory() as name:
            check_full(Path(name), natural_exit=mode, ast=ast)
print("flow and AST main exercised with both transfers and explicit stop, and with natural completion skipping stop")
