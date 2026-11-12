#!/usr/bin/env python3
"""Exercise the capacity smoke and reject incomplete or inconsistent evidence."""
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
BOOT = "9f1f8471-09ec-4818-af05-bc97e5552751"
TOKEN = "3" * 32
APP_HASH = "e" * 64
WIRE = "bf0c00000000fcff0000fcff0000"
MAC = "02:00:00:00:01:01"
PHONE = "02:00:00:00:01:02"
MANIFEST = (runner.RUNNER / "dist" / "mss" / "SHA256SUMS").read_text()
NEW = [
    "[ 10.1] PT_RX_CAPACITY cfg_bytes=4224",
    "[ 10.2] PT_RX_CAPACITY pool_bytes=4224 align=128 entries=4096",
    "[ 10.3] PT_RX_CAPACITY htt_bytes=4224 mac_id=1 host_submit_status=0",
    "[ 10.4] PT_RX_CAPACITY map_bytes=4224 linear=4351",
    "[ 10.5] PT_RX_CAPACITY map_bytes=4224 linear=4224",
    "[ 10.6] PT_RX_CAPACITY raw_osif_bytes=1467 tlv=272 pad=3 linear_span=4351",
]
OLD = ["[ 1.0] PT_RX_CAPACITY cfg_bytes=4224"]


def check(lines, expected, bad_sha=False, long=False, advert=None, wrong_wire=False,
          post_first=0.0, post_boot=BOOT, psf=None):
    pairs, size, timeout = (3, 64 * runner.MIB, 100) if long else (1, runner.MIB, 12)
    count = pairs * 2
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        module = folder / "test.ko"
        module.write_bytes(b"fixture")
        digest = hashlib.sha256(module.read_bytes()).hexdigest()
        (folder / "audit-rx-capacity.json").write_text(json.dumps({"candidate_sha256": digest}))
        out = folder / "result"
        lifecycle = f"WLAN_BUILD_SHA={digest} BA_OBSERVER_REQUEST=reference\nRX_CAPACITY_LOAD_READBACK=Y\n"
        runner_log = ""
        if advert is not None:
            lifecycle += f"OUTGOING_VHT_BUILD_SHA={APP_HASH}\n"
            switch = "true" if advert == "on" else "false"
            wire = WIRE if advert == "on" and not wrong_wire else "absent"
            runner_log = f"OWLY_OUTGOING_VHT={switch}\nOWLY_MIF_SUBMITTED_VHT_CONTAINER={wire}\nOWLY_PEERS count=1\n"
        if psf is not None:
            lifecycle += f"OUTGOING_VHT_BUILD_SHA={APP_HASH}\n"
            scope = runner.PSF_SCOPE[psf][1]
            psf_wire = WIRE if psf == "both" and not wrong_wire else "absent"
            mif_wire = WIRE if psf != "off" else "absent"
            runner_log = (f"OWLY_OUTGOING_VHT_SCOPE={scope}\n"
                          f"OWLY_PSF_SUBMITTED_VHT_CONTAINER={psf_wire}\n"
                          f"OWLY_MIF_SUBMITTED_VHT_CONTAINER={mif_wire}\nOWLY_PEERS count=1\n")
        server = ["HASH_READY 2001:db8::2f"]
        directions = []

        def root(command):
            if command == f"cat {runner.ROOT}/SHA256SUMS":
                return MANIFEST
            if command == f"sha256sum {runner.MODULE}":
                return digest + "  fixture\n"
            if "setsid" in command:
                launch = f"run {TOKEN} vht80-sgi amsdu ack {MAC} {count} {size} 1160 500 reference 0"
                if advert is not None:
                    launch += " 1" if advert == "on" else " 0"
                if psf is not None:
                    launch += " " + runner.PSF_SCOPE[psf][0]
                assert launch + ";" in command
                return ""
            if f"/exit-{TOKEN}" in command:
                return "0" if len(server) == count + 1 else ""
            if f"/job-{TOKEN}.log" in command:
                return lifecycle
            if command == f"cat {runner.STATE}/token":
                return TOKEN
            if "server.log" in command:
                return "\n".join(server) + "\n"
            if "wonder0/address" in command:
                return PHONE
            if "runner.log" in command:
                return runner_log
            if command.startswith("test ! -f") or "rm -f" in command:
                if "rm -f" in command and (advert is not None or psf is not None):
                    assert runner.OUTGOING_APP in command and runner.OUTGOING_SHA in command and APP_HASH in command
                    assert (runner.PSF_HARNESS_SHA if psf is not None else runner.OUTGOING_HARNESS_SHA) in command
                return ""
            raise AssertionError(command)

        def transfer(ip, index, mode, sample_size, payload, expected, sample, *, io_timeout):
            assert ip == "2001:db8::2f" and index == 5 and sample_size == size and io_timeout == timeout
            assert hashlib.sha256(payload).digest() == expected
            directions.append(mode)
            digest = "0" * 64 if bad_sha and sample == count else expected.hex()
            server.append(f"HASH_OK sample={sample} mode={mode} bytes={size} seconds=0.125 sha256={digest}")
            return dict(status="verified_mac", sha256=expected.hex(), sample=sample, mode=mode, bytes=size,
                        seconds=0.125, MBps=size / 0.125 / 1e6)

        argv = ["runner", "--rx-capacity", str(module), str(out)]
        if long:
            argv.append("--capacity-64")
        if advert is not None:
            argv.extend(("--outgoing-vht", advert))
        if psf is not None:
            argv.extend(("--outgoing-psf", psf))

        def app_manifest(*args):
            assert args == ((runner.PSF / "dist",) if psf is not None else ())
            return (module, APP_HASH), "fixture app manifest\n"

        def stage(*args):
            assert args[-2] == ((module, APP_HASH) if psf is not None or advert is not None else None)
            assert args[-1] == (psf is not None)

        with patch.multiple(
            runner, CAPACITY=folder, root=root, ensure_restored=lambda: "stock\n",
            stage=stage, current_awdl=lambda: (MAC, 5), transfer=transfer,
            read_mac_schedule=lambda *args: [],
            outgoing_app_manifest=app_manifest,
        ), patch.object(runner.uuid, "uuid4", return_value=type("ID", (), {"hex": TOKEN})()), \
                patch.object(runner, "bounded_kernel_log_snapshot",
                             side_effect=[(BOOT, OLD, 0.0, 9.0), (post_boot, lines, post_first, 15.0)]), \
                patch.object(sys, "argv", argv):
            assert (runner.main() == 0) == expected
        receipt = json.loads((out / "result.json").read_text())
        assert directions == [0, 1] * pairs and receipt["verified"] == expected
        if long and expected:
            assert all(r["verified"] == pairs and r["bytes"] == size * pairs and
                       r["seconds"] == 0.125 * pairs and r["weighted_MBps"] == size / 0.125 / 1e6
                       for r in receipt["throughput_by_direction"].values())
        assert receipt["rx_capacity"]["verified"] == (expected or bad_sha or wrong_wire)
        if advert is not None or psf is not None:
            assert receipt["outgoing_vht"]["app_sha256"] == APP_HASH
            assert receipt["outgoing_vht"]["verified"] == (not wrong_wire)


check(OLD + NEW, True)
check(OLD + [line.replace("host_submit_status=0", "host_submit_status=1") for line in NEW], False)
check(OLD + NEW + ["[ 11.0] PT_RX_CAPACITY map_short bytes=4224 linear=4096"], False)
check(OLD + NEW[:-1], False)
check(OLD + [line.replace("pool_bytes=4224", "pool_bytes=4096") for line in NEW], False)
check(NEW, False)
check(NEW, True, post_first=2.0)
check(NEW, False, post_first=9.1)
check(OLD + NEW, False, post_boot="1" * 36)
_, coverage, _ = runner.kernel_log_delta(
    (BOOT, OLD * 2, 0.0, 9.0), (BOOT, OLD + NEW, 1.0, 15.0))
assert not coverage
check(OLD + [NEW[1], NEW[0], *NEW[2:]], False)
check(OLD + [NEW[0].replace("[ 10.1]", "[ 9.0]"), *NEW[1:]], False)
check(OLD + NEW, False, bad_sha=True)
check(OLD + NEW, True, long=True)
check(OLD + NEW, False, bad_sha=True, long=True)
check(OLD + NEW, True, advert="off")
check(OLD + NEW, True, advert="on")
check(OLD + NEW, False, advert="on", wrong_wire=True)
for scope in ("off", "mif", "both"):
    check(OLD + NEW, True, psf=scope)
check(OLD + NEW, False, psf="both", wrong_wire=True)
valid_log = f"OWLY_OUTGOING_VHT=true\nOWLY_MIF_SUBMITTED_VHT_CONTAINER={WIRE}\n"
valid_build = f"OUTGOING_VHT_BUILD_SHA={APP_HASH}\n"
for bad_log in ("", valid_log * 2, valid_log.replace("true", "false")):
    assert not runner.outgoing_vht_result(valid_build, bad_log, APP_HASH, True)["verified"]
assert not runner.outgoing_vht_result(valid_build.replace(APP_HASH, "bad"), valid_log, APP_HASH, True)["verified"]
valid_psf_log = (f"OWLY_OUTGOING_VHT_SCOPE=PsfAndMif\n"
                 f"OWLY_PSF_SUBMITTED_VHT_CONTAINER={WIRE}\nOWLY_MIF_SUBMITTED_VHT_CONTAINER={WIRE}\n")
for bad_log in ("", valid_psf_log * 2, valid_psf_log.replace("PsfAndMif", "Mif"),
                valid_psf_log.replace("OWLY_PSF_SUBMITTED_VHT_CONTAINER=", "PSF_IGNORED="),
                valid_psf_log.replace("OWLY_MIF_SUBMITTED_VHT_CONTAINER=", "MIF_IGNORED=")):
    assert not runner.outgoing_psf_result(valid_build, bad_log, APP_HASH, "both")["verified"]
assert not runner.outgoing_psf_result(valid_build.replace(APP_HASH, "bad"), valid_psf_log, APP_HASH, "both")["verified"]
with tempfile.TemporaryDirectory() as name:
    script = runner.prepare_harness(Path(name), capacity=True)
    assert runner.digest(script) == runner.CAPACITY_HARNESS_SHA
with tempfile.TemporaryDirectory() as name:
    script = runner.prepare_harness(Path(name), capacity=True, outgoing=True)
    assert runner.digest(script) == runner.OUTGOING_HARNESS_SHA
with tempfile.TemporaryDirectory() as name:
    script = runner.prepare_harness(Path(name), capacity=True, outgoing=True, psf=True)
    assert runner.digest(script) == runner.PSF_HARNESS_SHA
recovery_spec = importlib.util.spec_from_file_location("recovery", HERE.parent / "recover-peer-host.py")
recovery = importlib.util.module_from_spec(recovery_spec)
recovery_spec.loader.exec_module(recovery)
module_path = runner.MODULE
ledger_lines = f"capacity\n{TOKEN}\n{APP_HASH} {module_path}\nsaved_boot={BOOT}\n"
for harness in (runner.OUTGOING_HARNESS_SHA, runner.PSF_HARNESS_SHA, runner.CAPACITY_HARNESS_SHA, "a" * 64):
    def recovery_root(command):
        if command.startswith("sha256sum"):
            return harness + "  runner\n"
        return ledger_lines

    with patch.multiple(recovery, root=recovery_root, check_hashes=lambda expected: None):
        try:
            snapshot = recovery.snapshot()
        except RuntimeError:
            assert harness == "a" * 64
        else:
            assert harness != "a" * 64 and snapshot["harness"] == harness

print("capacity and outgoing VHT paths verify paired SHA, exact app and MIF/PSF scope and bytes, covered RX evidence, and recovery binding")
