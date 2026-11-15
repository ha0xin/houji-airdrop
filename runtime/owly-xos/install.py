#!/usr/bin/env python3
"""Deploy audited xos0 VHT80 2SS backend and AIDL XosBridge for stock MIShare."""
import json
import os
from pathlib import Path
import shlex
import sys
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OWLY_DIR = REPO / "clients/owly-app"
sys.path.insert(0, str(OWLY_DIR))

from device import adb, digest, ensure_stock_idle, root  # noqa: E402

MANIFEST = json.loads((OWLY_DIR / "runtime-manifest.json").read_text())
AUDIT = json.loads((REPO / MANIFEST["audit_file"]).read_text())
BLOBS_DIR = Path(os.environ.get("HOUJI_BLOBS_DIR", str(REPO / "blobs/local")))

assert MANIFEST["blobs"]["qca_cld3_kiwi_v2"]["sha256"] == AUDIT["candidate_sha256"]
assert int(MANIFEST["audit_import_count"]) == int(AUDIT["candidate_import_count"]) == 567


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
    "xos-bridge.zip": HERE / "dist/xos-bridge.zip",
    "bridge-supervisor.sh": HERE / "bridge-supervisor.sh",
    "radio.sh": HERE / "radio.sh",
    "houji-owly-app": OWLY_DIR / "dist/houji-owly-app",
    "wonder-init": OWLY_DIR / "dist/wonder-init",
    "qca_cld3_kiwi_v2-three-findings.ko": resolve_blob("qca_cld3_kiwi_v2"),
    "firmware/ezif.bin": resolve_blob("ezif"),
    "radio/houji_wonder_bridge.ko": resolve_blob("houji_wonder_bridge"),
    "radio/wonder.ko": resolve_blob("wonder"),
}
for name, path in files.items():
    if not path.is_file():
        raise SystemExit(f"Missing build output {name}: {path}. Build on host first; no device changes made.")

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
    f"chmod 700 {native} {remote} {remote}/state"
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
