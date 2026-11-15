#!/usr/bin/env python3
"""Deploy audited xos0 VHT80 2SS backend (owly-xosd) and AIDL XosBridge for stock MIShare."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DIST_DIR = HERE / "dist"

STOCK_AMSS_SHA = "4db7b0e1aa7e4ec071708359c9d0db8d3d7872390cd8e88ffa0bbb30d560bcc8"
STOCK_NOTE_SHA = "a3e9bd62646444e1ecbe0f421c95ec46e6caf69a78323efb2f6ebe00e6b2bd8b"

MANIFEST = json.loads((HERE / "runtime-manifest.json").read_text())
AUDIT = json.loads((REPO / MANIFEST["audit_file"]).read_text())
BLOBS_DIR = Path(os.environ.get("HOUJI_BLOBS_DIR", str(REPO / "blobs/local")))

assert MANIFEST["blobs"]["qca_cld3_kiwi_v2"]["sha256"] == AUDIT["candidate_sha256"]
assert int(MANIFEST["audit_import_count"]) == int(AUDIT["candidate_import_count"]) == 567


def resolve_serial() -> str:
    env_serial = os.environ.get("ANDROID_SERIAL", "").strip()
    if env_serial:
        return env_serial
    out = subprocess.check_output(["adb", "devices"], text=True, timeout=15)
    devices = [
        parts[0]
        for line in out.splitlines()[1:]
        if len(parts := line.split()) >= 2 and parts[1] == "device"
    ]
    if len(devices) == 1:
        return devices[0]
    if not devices:
        raise SystemExit("No authorized ADB device found; connect the phone or set ANDROID_SERIAL.")
    raise SystemExit(f"Multiple ADB devices found ({', '.join(devices)}); set ANDROID_SERIAL.")


def adb(*args: str, timeout: int = 30) -> str:
    return subprocess.check_output(
        ["adb", "-s", resolve_serial(), *args],
        text=True,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def root(command: str, timeout: int = 30) -> str:
    return adb("shell", f"su -c {shlex.quote(command)}", timeout=timeout)


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def ensure_stock_idle() -> str:
    return root(
        f"""set -e
test ! -e /sys/module/houji_wonder_bridge
test ! -e /sys/module/wonder
test ! -e /sys/class/net/owly0
test ! -e /sys/class/net/wonder0
test ! -e /sys/class/net/xos0
test ! -e /data/local/tmp/owly-app/state/saved.env
test ! -e /data/local/tmp/xos/native/saved.env
test ! -e /data/local/tmp/xos/owly/state/saved.env
test "$(getprop sys.xos.exclusive)" = 0
echo '{STOCK_NOTE_SHA}  /sys/module/qca_cld3_kiwi_v2/notes/.note.gnu.build-id' | sha256sum -c -
echo '{STOCK_AMSS_SHA}  /vendor/firmware_mnt/image/kiwi/amss20.bin' | sha256sum -c -
! ip -6 rule show | grep -qE '^1087[012]:'
echo STOCK_IDLE_VERIFIED"""
    ).strip()


def resolve_blob(key: str) -> Path:
    entry = MANIFEST["blobs"][key]
    raw = os.environ.get(entry["env"])
    path = Path(raw) if raw else BLOBS_DIR / entry["file"]
    if not path.is_file():
        raise SystemExit(
            f"Missing input {key}: {path} (env {entry['env']} or {BLOBS_DIR / entry['file']}). "
            "See docs/guides/byob.md; no device changes made."
        )
    actual = digest(path)
    if actual != entry["sha256"]:
        raise SystemExit(
            f"SHA-256 mismatch for {key} ({path}): expected {entry['sha256']}, got {actual}"
        )
    return path


files = {
    "xos-bridge.zip": DIST_DIR / "xos-bridge.zip",
    "bridge-supervisor.sh": HERE / "bridge-supervisor.sh",
    "radio.sh": HERE / "radio.sh",
    "owly-xosd": DIST_DIR / "owly-xosd",
    "wonder-init": DIST_DIR / "wonder-init",
    "qca_cld3_kiwi_v2-three-findings.ko": resolve_blob("qca_cld3_kiwi_v2"),
    "firmware/ezif.bin": resolve_blob("ezif"),
    "radio/houji_wonder_bridge.ko": resolve_blob("houji_wonder_bridge"),
    "radio/wonder.ko": resolve_blob("wonder"),
}
for name, path in files.items():
    if not path.is_file():
        raise SystemExit(
            f"Missing build output {name}: {path}. Run `bash scripts/build-xos.sh` on build host first; no device changes made."
        )

ensure_stock_idle()

native = "/data/local/tmp/xos/native"
remote = "/data/local/tmp/xos/owly"
busybox = "/data/adb/ksu/bin/busybox"

print(
    root(
        f"""set -e
test ! -e /data/local/tmp/owly-app/state/saved.env
test ! -e /data/local/tmp/houji-buildtest-20261110/state/saved.env
test ! -e {native}/saved.env
test ! -e {remote}/state/saved.env
for f in {native}/bridge.pid {native}/supervisor.pid; do
 p=$(cat "$f" 2>/dev/null || true)
 case "$p" in
  ''|*[!0-9]*) ;;
  *) kill -TERM "$p" 2>/dev/null || true ;;
 esac
done
for i in $(seq 1 30); do
 if {busybox} flock -n {native}/supervisor.lock true 2>/dev/null && \
    ! ps -A -o ARGS | grep -q '^[a]pp_process /system/bin XosBridge '; then
  echo OLD_SUPERVISOR_STOPPED
  exit 0
 fi
 sleep 0.5
done
echo SUPERVISOR_STOP_TIMEOUT >&2
exit 1"""
    ).strip()
)

print(ensure_stock_idle())
root(
    f"mkdir -p {native} {remote}/state {remote}/radio {remote}/firmware && "
    f"chmod 700 {native} {remote} {remote}/state && "
    f"rm -f {remote}/houji-owly-app"
)

hashes = {}
for name, path in files.items():
    staging = "/data/local/tmp/xos-owly-stage"
    adb("push", str(path), staging)
    sha = digest(path)
    hashes[name] = sha
    if name.endswith((".ko", ".bin")):
        mode = "600"
    elif name == "xos-bridge.zip":
        mode = "644"
    else:
        mode = "700"
    print(
        root(
            f"cp {staging} {remote}/{name} && chmod {mode} {remote}/{name} && "
            f"echo '{sha}  {remote}/{name}' | sha256sum -c -"
        ).strip()
    )
    adb("shell", "rm", "-f", staging)

manifest_text = "".join(f"{sha}  {name}\n" for name, sha in sorted(hashes.items()))
root(
    f"printf %s {shlex.quote(manifest_text)} > {remote}/installation.sha256 && "
    f"printf %s {shlex.quote(manifest_text)} > {remote}/SHA256SUMS && "
    f"rm -f {remote}/radio/amss20.bin && "
    f"cp {remote}/firmware/ezif.bin {remote}/radio/amss20.bin && "
    f"chown system:system {remote}/radio/amss20.bin && "
    f"chmod 440 {remote}/radio/amss20.bin && "
    f"chcon u:object_r:firmware_file:s0 {remote}/radio/amss20.bin && "
    f"echo '{hashes['firmware/ezif.bin']}  {remote}/radio/amss20.bin' | sha256sum -c - && "
    f"rm -f {remote}/state/sepolicy.boot_id && "
    f"printf 'STOCK_RESTORED\\n' > {remote}/state/status && "
    f"printf 'STOCK_RESTORED\\n' > {native}/status"
)

root(
    f"{busybox} setsid {busybox} nsenter -t 1 -m /system/bin/sh {remote}/bridge-supervisor.sh "
    f"</dev/null >>{native}/owly-bridge.log 2>&1 &"
)

for _ in range(20):
    status = root(
        f"""if service check com.domestic.xoscomm.IDomesticXOsComm/default | grep -q ': found' && \
   ps -A -o ARGS | grep -q '^[a]pp_process /system/bin XosBridge '; then
 p=$(cat {native}/supervisor.pid 2>/dev/null || true)
 case "$p" in ''|*[!0-9]*) exit 1 ;; esac
 tr '\\0' ' ' < "/proc/$p/cmdline" | grep -Fq '{remote}/bridge-supervisor.sh locked'
 echo READY
fi"""
    ).strip()
    if status == "READY":
        break
    time.sleep(0.5)
else:
    raise SystemExit("Timed out waiting for XosBridge supervisor to register IDomesticXOsComm/default")

# Clear any cached Binder handle held by an idle MiShare process.
root("am force-stop com.miui.mishare.connectivity")

evidence = HERE / "installed-artifacts.json"
evidence.write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n")
print("OWLY_XOS_INSTALLED")
print(root("service check com.domestic.xoscomm.IDomesticXOsComm/default").strip())
