#!/usr/bin/env python3
"""Build a passive PASSTHRU peer capability observer in isolated build-host paths."""
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
SOURCE = ROOT / "src/ba-peer-observer/wlan"
WORK = ROOT / "src/remote-peer-caps/wlan"
OUT = ROOT / "out/remote-peer-caps-wlan"
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
target = Path("qcacld-3.0/core/wma/src/wma_dev_if.c")
unmodified = [DP / name for name in (
    "dp_tx.c", "dp_rx.c", "dp_peer.c", "dp_htt.c",
    "dp_pt_ba_observer.h", "dp_pt_ba_peer.h", "dp_pt_ba_peer_state.h",
)]
unmodified += [Path("qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c")]
before = {str(path): digest(SOURCE / path) for path in [target, *unmodified, MODULE]}
assert before[str(target)] == "6a4d285e6a188b50e5699ee1a1939472c62d8cbda61e2d35365f74c6f88e28ba"
assert before[str(MODULE)] == "a7c1f27eaef55408f4b6023f02d5e55336f1cb4159f86f3b5e9d36be5314eddd"
refresh = sys.argv[1:] == ["--refresh"]
assert refresh or not sys.argv[1:], "usage: build.py [--refresh]"
if refresh:
    assert (OUT / ".config").is_file() and (OUT / "Module.symvers").is_file()
    assert (WORK / target).is_file()
    shutil.copyfile(SOURCE / target, WORK / target)
else:
    assert not WORK.exists() and not OUT.exists(), "refusing to reuse an existing build directory"
    run(["mkdir", "-p", WORK.parent])
    run(["cp", "-a", "--reflink=auto", SOURCE, WORK])
assert (WORK / "qcacld-3.0/cmn").resolve() == WORK / "qca-wifi-host-cmn"
(WORK / MODULE).unlink(missing_ok=True)
print("SOURCE_RECEIPTS", json.dumps(before), flush=True)
run(["patch", "--batch", "--fuzz=0", "-p1", "-d", WORK, "-i", PACKAGE / "peer-capabilities.patch"])
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
print("PT_PEER_CAPS_BUILD_PASS", digest(WORK / MODULE), flush=True)
