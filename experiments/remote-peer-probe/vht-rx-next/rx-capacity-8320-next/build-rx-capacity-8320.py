#!/usr/bin/env python3
"""Build the isolated 8320-byte RX-capacity + RX PHY observer module on build-host."""
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
PATCH = HERE / "rx-capacity-8320.patch"
OUT = HERE / "out" / "qca_cld3_kiwi_v2-rx-capacity-8320.ko"
LOG = HERE / "build-rx-capacity-8320.log"
REMOTE_PATCH = "/work/houji-build/out/rx-capacity-8320.patch"
REMOTE = r"""set -euo pipefail
ROOT=/work/houji-build
BASE=$ROOT/src/rx-phy-observer
BASE_OUT=$ROOT/out/rx-phy-observer-wlan
SRC=$ROOT/src/rx-capacity-8320
OUT=$ROOT/out/rx-capacity-8320-wlan
CLANG=$ROOT/toolchains/clang-r487747c/bin
KERNEL=$ROOT/src/kernel

rmdir "$ROOT/out/kernel" 2>/dev/null || true
rm -rf "$SRC" "$OUT"
mkdir -p "$SRC"
cp -al "$BASE/wlan" "$SRC/wlan"
cp -al "$BASE_OUT" "$OUT"
rm -f "$OUT/qca_cld3_kiwi_v2-rx-phy-observer.ko"
for f in \
  qca-wifi-host-cmn/wlan_cfg/wlan_cfg.h \
  qca-wifi-host-cmn/wlan_cfg/wlan_cfg.c \
  qca-wifi-host-cmn/dp/wifi3.0/dp_rx.h \
  qca-wifi-host-cmn/dp/wifi3.0/dp_rx.c \
  qca-wifi-host-cmn/dp/wifi3.0/dp_pt_rx_phy.h \
  qca-wifi-host-cmn/dp/wifi3.0/dp_rx_buffer_pool.c \
  qca-wifi-host-cmn/dp/wifi3.0/be/dp_be.c; do
  cp --remove-destination "$BASE/wlan/$f" "$SRC/wlan/$f"
done

git -C "$SRC/wlan" apply --check /work/houji-build/out/rx-capacity-8320.patch
git -C "$SRC/wlan" apply /work/houji-build/out/rx-capacity-8320.patch
touch -r "$BASE/wlan/qca-wifi-host-cmn/wlan_cfg/wlan_cfg.h" "$SRC/wlan/qca-wifi-host-cmn/wlan_cfg/wlan_cfg.h"
rm -f \
  "$SRC/wlan/qca-wifi-host-cmn/wlan_cfg/wlan_cfg.o" \
  "$SRC/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_rx.o" \
  "$SRC/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_rx_buffer_pool.o" \
  "$SRC/wlan/qca-wifi-host-cmn/dp/wifi3.0/be/dp_be.o" \
  "$SRC/wlan/qcacld-3.0/qca_cld3_kiwi_v2.o" \
  "$SRC/wlan/qcacld-3.0/qca_cld3_kiwi_v2.ko"

export PATH="$CLANG:$PATH"
export TMPDIR="$ROOT/tmp"
python3 - <<'PY'
import json, subprocess
from pathlib import Path
root = (Path(__import__("os").environ["HOUJI_LEGACY_ROOT"]))
up = root / "upstream"
values = dict(
    KERNEL=root / "src/kernel",
    KERNEL_OUT=root / "out/rx-capacity-8320-wlan",
    WLAN=root / "src/rx-capacity-8320/wlan",
    EXTRA_SYMVERS=root / "out/wlan-build-extra.symvers",
    IPA_INCLUDE=up / "kernel/dataipa-include",
    EXTERNAL_INCLUDE=up / "kernel/external-include",
    PAHOLE="/usr/bin/pahole",
)
args = json.loads((up / "kernel/build-commands.json").read_text())["wlan"]
for k, v in values.items():
    args = [a.replace("${" + k + "}", str(v)) for a in args]
subprocess.run(args + ["KERNELRELEASE=6.1.68"], check=True)
PY

"$CLANG/llvm-strip" --strip-debug -o "$OUT/qca_cld3_kiwi_v2-rx-capacity-8320.ko" "$SRC/wlan/qcacld-3.0/qca_cld3_kiwi_v2.ko"
sha256sum "$OUT/qca_cld3_kiwi_v2-rx-capacity-8320.ko"
"""


def main():
    OUT.parent.mkdir(exist_ok=True)
    subprocess.run(["scp", str(PATCH), f"build-host:{REMOTE_PATCH}"], check=True)
    with LOG.open("w") as log:
        subprocess.run(["ssh", "build-host", REMOTE], input="", text=True,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    subprocess.run(["scp", "build-host:/work/houji-build/out/rx-capacity-8320-wlan/qca_cld3_kiwi_v2-rx-capacity-8320.ko",
                    str(OUT)], check=True)
    subprocess.run(["shasum", "-a", "256", str(OUT)], check=True)


if __name__ == "__main__":
    main()
