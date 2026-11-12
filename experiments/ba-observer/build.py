#!/usr/bin/env python3
"""Build a PASSTHRU RX observation candidate in isolated build-host paths."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = (Path(__import__("os").environ["HOUJI_LEGACY_ROOT"]))
PACKAGE = Path(__file__).resolve().parent
UPSTREAM = ROOT / "upstream"
SOURCE = ROOT / "src/wideband/wlan"
WORK = ROOT / "src/ba-observer/wlan"
OUT = ROOT / "out/ba-observer-wlan"
MODULE = Path("qcacld-3.0/qca_cld3_kiwi_v2.ko")
REFERENCE = ROOT / "artifacts/wideband-ring" / MODULE.name
DP = Path("qca-wifi-host-cmn/dp/wifi3.0")

env = os.environ.copy()
env["PATH"] = f"{ROOT}/toolchains/clang-r487747c/bin:{env['PATH']}"
env["TMPDIR"] = str(ROOT / "tmp")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    argv = [str(x) for x in args]
    print("+", argv, flush=True)
    subprocess.run(argv, env=env, check=True)


assert digest(REFERENCE) == "481e3474ea6b42acab5bb5a35b5be7b5e291aa975d7422034ca4e750e857d71c"
sources = [DP / name for name in ("dp_rx.c", "dp_tx.c", "dp_pt_rate_observer.h")]
sources += [Path("qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c")]
before = {str(path): digest(SOURCE / path) for path in sources}
before[str(MODULE)] = digest(SOURCE / MODULE)
assert before[str(DP / "dp_rx.c")] == "7e52757528acc7c9a94b3cdb610a6acb87db573ce0a1cf6a1f0785a25289146b"
assert not WORK.exists() and not OUT.exists(), "refusing to reuse an existing build directory"
print("SOURCE_RECEIPTS", json.dumps(before), flush=True)
run(["mkdir", "-p", WORK.parent])
run(["cp", "-a", "--reflink=auto", SOURCE, WORK])
assert (WORK / "qcacld-3.0/cmn").resolve() == WORK / "qca-wifi-host-cmn"
(WORK / MODULE).unlink()
run(["patch", "--batch", "--fuzz=0", "-p1", "-d", WORK, "-i", PACKAGE / "passthru-rx-observe.patch"])
shutil.copyfile(PACKAGE / "dp_pt_ba_observer.h", WORK / DP / "dp_pt_ba_observer.h")
assert all(digest(WORK / path) == before[str(path)] for path in sources[1:])

OUT.mkdir()
config = UPSTREAM / "kernel/configs"
shutil.copyfile(config / "wlan.config", OUT / ".config")
common = ["make", "-C", ROOT / "src/kernel", f"O={OUT}", "ARCH=arm64", "LLVM=1",
          "LLVM_IAS=1", "KERNELRELEASE=6.1.68", "PAHOLE=/usr/bin/pahole"]
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
print("BA_OBSERVER_BUILD_PASS", digest(WORK / MODULE), flush=True)
