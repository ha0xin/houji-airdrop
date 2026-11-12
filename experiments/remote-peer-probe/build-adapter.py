#!/usr/bin/env python3
"""Compile the PASSTHRU peer WMA/DP/owner diagnostic in isolated build-host paths."""
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
SOURCE = ROOT / "src/remote-peer-caps/wlan"
WORK = ROOT / "src/remote-peer-adapter/wlan"
OUT = ROOT / "out/remote-peer-adapter-wlan"
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
source_hashes = {
    "qcacld-3.0/core/wma/src/wma_dev_if.c": "adbf3e7b7449e296acf97cd14f70247b22ca07d59d0d3be7afdb1c5d8e8f97f8",
    str(DP / "dp_peer.c"): "839becb26a3d00953b22608b6f19b6f2b2882008c7b63a9b5e3c6b28122c7d6b",
    str(DP / "dp_main.c"): "2fca26e3fdd46ee5d497602d45c25d6e23641b99c187048c46b2e0c0b4f98197",
    "qca-wifi-host-cmn/umac/cmn_services/obj_mgr/src/wlan_objmgr_peer_obj.c": "de57e3e305c3224eda49b9eab394ae2b8795d8f78de5452cfca4897db72d0216",
    "qcacld-3.0/core/hdd/src/wlan_hdd_passthru_owner.c": "90d832c78a8cc69d70f9a591dc235139bdf5a5321c1b28e5a7e7c89609533d3f",
    "qcacld-3.0/core/hdd/src/wlan_hdd_passthru_runtime.c": "590a2ff27ace2e59dec2715ba97e2012d05dea3ba892a19b57a1c5db719fb1c1",
    "qcacld-3.0/core/hdd/inc/wlan_hdd_passthru_owner.h": "68350e45dad27e041cdb951c2294cefadd937caaa78b75bb0fe879ee9fa6c5ae",
    "qcacld-3.0/core/wma/inc/wma_api.h": "bc073eb885e007606991ac6a3e2b67820b802306f24a50c2be69edd3853662df",
    str(MODULE): "2450ca5abe4332dab0a66cfab6538c786894dee429b4b76978437e98f96aa811",
}
before = {name: digest(SOURCE / name) for name in source_hashes}
assert before == source_hashes
refresh = sys.argv[1:] == ["--refresh"]
assert refresh or not sys.argv[1:], "usage: build-adapter.py [--refresh]"
if refresh:
    assert (OUT / ".config").is_file() and (OUT / "Module.symvers").is_file()
    for target in before:
        if target != str(MODULE):
            shutil.copyfile(SOURCE / target, WORK / target)
    (WORK / "qca-wifi-host-cmn/qdf/inc/qdf_pt_remote_peer.h").unlink(missing_ok=True)
else:
    assert not WORK.exists() and not OUT.exists(), "refusing to reuse an existing build directory"
    run(["mkdir", "-p", WORK.parent])
    run(["cp", "-a", "--reflink=auto", SOURCE, WORK])
assert (WORK / "qcacld-3.0/cmn").resolve() == WORK / "qca-wifi-host-cmn"
(WORK / MODULE).unlink(missing_ok=True)
print("SOURCE_RECEIPTS", json.dumps(before), flush=True)
for patch in ("remote-peer-wma-stage1.patch", "remote-peer-dp-stage2.patch",
              "remote-peer-host-prep-stage3.patch", "remote-peer-owner-stage4.patch"):
    run(["patch", "--batch", "--fuzz=0", "-p1", "-d", WORK, "-i", PACKAGE / patch])

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
print("PT_PEER_ADAPTER_BUILD_PASS", digest(WORK / MODULE), flush=True)
