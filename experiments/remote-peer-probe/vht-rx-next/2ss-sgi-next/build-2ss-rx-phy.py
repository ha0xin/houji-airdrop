#!/usr/bin/env python3
"""Compile the PASSTHRU 2SS VHT80 + RX PHY observer module in isolated build-host paths."""
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
SOURCE = ROOT / "src/rx-capacity-4224/wlan"
WORK = ROOT / "src/2ss-rx-phy/wlan"
OUT = ROOT / "out/2ss-rx-phy-wlan"
MODULE = Path("qcacld-3.0/qca_cld3_kiwi_v2.ko")
REFERENCE = ROOT / "artifacts/wideband-ring" / MODULE.name
DP = Path("qca-wifi-host-cmn/dp/wifi3.0")

env = os.environ.copy()
env["PATH"] = f"{ROOT}/toolchains/clang-r487747c/bin:{env['PATH']}"
env["TMPDIR"] = str(ROOT / "tmp")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(args):
    argv = [str(x) for x in args]
    print("+", argv, flush=True)
    subprocess.run(argv, env=env, check=True)


assert digest(REFERENCE) == "481e3474ea6b42acab5bb5a35b5be7b5e291aa975d7422034ca4e750e857d71c"
assert digest(SOURCE / MODULE) == "f31d2cb27de391d74df4d5541bbecdcc917e2f51decfe7535118ca6b09dd7b2c"
inputs = [
    MODULE,
    DP / "dp_rx.c",
    DP / "dp_pt_rate_observer.h",
    Path("qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c"),
    Path("qca-wifi-host-cmn/hal/wifi3.0/hal_rx.h"),
    Path("qca-wifi-host-cmn/hal/wifi3.0/be/hal_be_rx_tlv.h"),
    Path("qca-wifi-host-cmn/hal/wifi3.0/kiwi/hal_kiwi.c"),
]
before = {str(path): digest(SOURCE / path) for path in inputs}
refresh = sys.argv[1:] == ["--refresh"]
assert refresh or not sys.argv[1:], "usage: build-2ss-rx-phy.py [--refresh]"
if refresh:
    assert (OUT / ".config").is_file() and (OUT / "Module.symvers").is_file()
    for target in before:
        if target != str(MODULE):
            shutil.copyfile(SOURCE / target, WORK / target)
    (WORK / DP / "dp_pt_rx_phy.h").unlink(missing_ok=True)
else:
    assert not WORK.exists() and not OUT.exists(), "refusing to reuse an existing build directory"
    run(["mkdir", "-p", WORK.parent])
    run(["cp", "-a", "--reflink=auto", SOURCE, WORK])
assert (WORK / "qcacld-3.0/cmn").resolve() == WORK / "qca-wifi-host-cmn"
(WORK / MODULE).unlink(missing_ok=True)
print("SOURCE_RECEIPTS", json.dumps(before), flush=True)
run(["patch", "--batch", "--fuzz=0", "-p1", "-d", WORK,
     "-i", PACKAGE / "2ss-rx-phy.patch"])

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
print("PT_2SS_RX_PHY_BUILD_PASS", digest(WORK / MODULE), flush=True)
