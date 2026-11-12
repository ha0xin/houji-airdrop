#!/usr/bin/env python3
"""Isolate the official EZIF firmware variable using the audited TF harness."""
import hashlib
from pathlib import Path
import runpy
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
ns = runpy.run_path(str(HERE / "run-three-findings.py"))
g = ns["start"].__globals__
OLD = "13f01cb46efbe498c854352bfc253fa3665c14a14c37a0a6ed6a609a48a8709c"
NEW = "37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f"
FW = HERE.parent / "tx-ampdu-investigation/firmware-acquisition/official-ezif-artifacts/ezif-official-fwd-amss20.bin"
REMOTE = g["ROOT"] + "/ezif-official-37d40904.bin"
original_prepare = g["prepare_tf_harness"]
with tempfile.TemporaryDirectory() as tmp:
    original = original_prepare(Path(tmp)).read_text()
assert original.count("CANDIDATE=" + OLD) == 1
assert original.count('"$RADIO/amss20.bin"') == 3
modified = original.replace("CANDIDATE=" + OLD, "CANDIDATE=" + NEW)
modified = modified.replace('"$RADIO/amss20.bin"', '"' + REMOTE + '"')
g["HARNESS_TF_SHA"] = hashlib.sha256(modified.encode()).hexdigest()


def prepare(folder):
    script = folder / "radio-owly.sh"
    script.write_text(modified)
    subprocess.run(["sh", "-n", str(script)], check=True)
    return script


g["prepare_tf_harness"] = prepare
assert g["digest"](FW) == NEW
folder = HERE / "runs/ezif-firmware-20261111-retry"
if folder.exists():
    raise RuntimeError("Use a fresh run directory; existing evidence is preserved")
print(g["ensure_restored"](), flush=True)
staging = "/data/local/tmp/ezif-official-37d40904.bin"
g["adb"]("push", str(FW), staging)
g["root"](f"set -eu; cp {staging} {REMOTE}; chown system:system {REMOTE}; "
          f"chmod 440 {REMOTE}; chcon u:object_r:firmware_file:s0 {REMOTE}")
g["adb"]("shell", "rm", staging)
g["root"](f"echo '{NEW}  {REMOTE}' | sha256sum -c -")
flags = dict(remote_assoc=1, remote_ast_override=1, remote_hash_override=1,
             remote_reo=0, fw_addba=0, tx_ampdu=1, dynamic_rate=0, sta_coexist=0)
try:
    g["start"](HERE / "qca_cld3_kiwi_v2-three-findings.ko", folder,
               "vht80-2ss-mcs9-sgi", flags)
    (folder / "firmware.sha256").write_text(NEW + "  official EZIF amss20.bin\n")
    (folder / "firmware-live.txt").write_text(g["root"](
        "sha256sum /vendor/firmware_mnt/image/kiwi/amss20.bin"))
    g["sample"](folder, "2ss", 1, "amsdu", "win64")
    g["sample"](folder, "2ss", 16, "amsdu", "win64")
finally:
    try:
        if (folder / "session.json").exists():
            g["stop"](folder)
    finally:
        restored = g["ensure_restored"]()
        print(restored, flush=True)
        if folder.exists():
            (folder / "stock-after.txt").write_text(restored)
