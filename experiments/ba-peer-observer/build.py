#!/usr/bin/env python3
"""Build a PASSTHRU peer observation candidate in isolated build-host paths."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = (Path(__import__("os").environ["HOUJI_LEGACY_ROOT"]))
PACKAGE = Path(__file__).resolve().parent
UPSTREAM = ROOT / "upstream"
SOURCE = ROOT / "src/ba-observer/wlan"
WORK = ROOT / "src/ba-peer-observer/wlan"
OUT = ROOT / "out/ba-peer-observer-wlan"
MODULE = Path("qcacld-3.0/qca_cld3_kiwi_v2.ko")
REFERENCE = ROOT / "artifacts/wideband-ring" / MODULE.name
DP = Path("qca-wifi-host-cmn/dp/wifi3.0")

env = os.environ.copy()
env["PATH"] = f"{ROOT}/toolchains/clang-r487747c/bin:{env['PATH']}"
env["TMPDIR"] = str(ROOT / "tmp")


def digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(args):
    argv = [str(x) for x in args]
    print("+", argv, flush=True)
    subprocess.run(argv, env=env, check=True)


assert digest(REFERENCE) == "481e3474ea6b42acab5bb5a35b5be7b5e291aa975d7422034ca4e750e857d71c"
pinned = {
    "dp_htt.c": "8cd44a6afe77ab56a61229bdc3b7f37080fe6ae3b496613c7f0f87fa5299ef41",
    "dp_main.c": "f4da1e0fea18639de679af917abfa2f5e8b1ab3b1c8a17bf4d196e4a2b7706e5",
    "dp_peer.c": "1659e27d2c61d340b0bd31e6e7ea4d00cf0ae6506b56076bda0d6353e2878a48",
    "dp_rx.c": "8c564351cba5f150beea8568076c3a95e07044ae57a2446c6ddb4d4bc94a5e13",
    "dp_rx_tid.c": "63f72f2b52b167c317603f12777b4967bc4871ea7dcb18e3561650bd2f5ddccb",
}
unmodified = [DP / name for name in ("dp_tx.c", "dp_pt_rate_observer.h", "dp_pt_ba_observer.h")]
unmodified += [Path("qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c")]
sources = [DP / name for name in pinned] + unmodified
before = {str(path): digest(SOURCE / path) for path in sources}
before[str(MODULE)] = digest(SOURCE / MODULE)
assert all(before[str(DP / name)] == sha for name, sha in pinned.items())
assert before[str(MODULE)] == "57f6d2b8433ac0d1f865d02a7ec7f33e2ffcdabc6e80074f04d3b0bf3090f0ea"
refresh = sys.argv[1:] == ["--refresh"]
assert refresh or not sys.argv[1:], "usage: build.py [--refresh]"
if refresh:
    assert (OUT / ".config").is_file() and (OUT / "Module.symvers").is_file()
    assert (WORK / DP / "dp_pt_ba_peer_state.h").is_file()
else:
    assert not WORK.exists() and not OUT.exists(), "refusing to reuse an existing build directory"
print("SOURCE_RECEIPTS", json.dumps(before), flush=True)
if refresh:
    for name in pinned:
        shutil.copyfile(SOURCE / DP / name, WORK / DP / name)
else:
    run(["mkdir", "-p", WORK.parent])
    run(["cp", "-a", "--reflink=auto", SOURCE, WORK])
assert (WORK / "qcacld-3.0/cmn").resolve() == WORK / "qca-wifi-host-cmn"
(WORK / MODULE).unlink(missing_ok=True)
run(["patch", "--batch", "--fuzz=0", "-p1", "-d", WORK, "-i", PACKAGE / "passthru-peer-observe.patch"])
for name in ("dp_pt_ba_peer.h", "dp_pt_ba_peer_state.h"):
    shutil.copyfile(PACKAGE / name, WORK / DP / name)
assert all(digest(WORK / path) == before[str(path)] for path in unmodified)

config = UPSTREAM / "kernel/configs"
if not refresh:
    OUT.mkdir()
    shutil.copyfile(config / "wlan.config", OUT / ".config")
common = ["make", "-C", ROOT / "src/kernel", f"O={OUT}", "ARCH=arm64", "LLVM=1",
          "LLVM_IAS=1", "KERNELRELEASE=6.1.68", "PAHOLE=/usr/bin/pahole"]
if not refresh:
    run(common + ["olddefconfig"])
    run(common + ["-j4", "modules_prepare"])
    shutil.copyfile(config / "kernel.symvers", OUT / "Module.symvers")
values = dict(
    KERNEL=ROOT / "src/kernel", KERNEL_OUT=OUT, WLAN=WORK,
    EXTRA_SYMVERS=ROOT / "out/wlan-build-extra.symvers",
    IPA_INCLUDE=UPSTREAM / "kernel/dataipa-include",
    EXTERNAL_INCLUDE=UPSTREAM / "kernel/external-include",
    PAHOLE="/usr/bin/pahole",
)
args = json.loads((UPSTREAM / "kernel/build-commands.json").read_text())["wlan"]
for key, value in values.items():
    args = [a.replace("${" + key + "}", str(value)) for a in args]
run(args + ["KERNELRELEASE=6.1.68"])
assert all(digest(SOURCE / path) == sha for path, sha in before.items())
assert digest(WORK / MODULE) != before[str(MODULE)]
print("BA_PEER_OBSERVER_BUILD_PASS", digest(WORK / MODULE), flush=True)
