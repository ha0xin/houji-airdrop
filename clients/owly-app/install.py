#!/usr/bin/env python3
"""Deploy audited Owly AirDrop runtime artifacts, kernel modules, firmware, and APK."""
import json
import os
from pathlib import Path
import shlex

from device import adb, digest, ensure_stock_idle, root

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
MANIFEST = json.loads((HERE / "runtime-manifest.json").read_text())
AUDIT = json.loads((REPO / MANIFEST["audit_file"]).read_text())
BLOBS_DIR = Path(os.environ.get("HOUJI_BLOBS_DIR", str(REPO / "blobs/local")))

qca_entry = MANIFEST["blobs"]["qca_cld3_kiwi_v2"]
assert qca_entry["sha256"] == AUDIT["candidate_sha256"]
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
    "houji-owly-outgoing-vht": HERE / "dist/houji-owly-app",
    "wonder-init-2ss": HERE / "dist/wonder-init",
    "qca_cld3_kiwi_v2-remote-peer.ko": resolve_blob("qca_cld3_kiwi_v2"),
    "radio.sh": REPO / "runtime/owly-app/radio.sh",
    "control.sh": REPO / "runtime/owly-app/control.sh",
    "firmware/ezif.bin": resolve_blob("ezif"),
    "firmware/dzf2.bin": resolve_blob("dzf2"),
    "radio/houji_wonder_bridge.ko": resolve_blob("houji_wonder_bridge"),
    "radio/wonder.ko": resolve_blob("wonder"),
}
apk_path = HERE / "dist/Owly-AirDrop.apk"
for name, path in {**files, "dist/Owly-AirDrop.apk": apk_path}.items():
    if not path.is_file():
        raise SystemExit(f"Missing build output {name}: {path}. Build on host first; no device changes made.")

remote = "/data/local/tmp/owly-app"
print(ensure_stock_idle())
print(
    root(
        f"test ! -f {remote}/state/saved.env; "
        f"mkdir -p {remote}/state {remote}/radio {remote}/firmware; "
        f"chmod 700 {remote} {remote}/state"
    )
)

hashes = {}
for name, path in files.items():
    staging = "/data/local/tmp/owly-app-stage"
    adb("push", str(path), staging)
    sha = digest(path)
    hashes[name] = sha
    mode = "600" if name.endswith((".ko", ".bin")) else "700"
    print(
        root(
            f"cp {staging} {remote}/{name} && chmod {mode} {remote}/{name} && "
            f"echo '{sha}  {remote}/{name}' | sha256sum -c -"
        ).strip()
    )
    adb("shell", "rm", "-f", staging)

for path, marker in (
    ("houji-owly-outgoing-vht", "outgoing-vht.sha"),
    ("wonder-init-2ss", "wonder-init-2ss.sha"),
    ("qca_cld3_kiwi_v2-remote-peer.ko", "remote-peer.sha"),
):
    root(f"printf '%s\\n' '{hashes[path]}' > {remote}/{marker}")

manifest_text = "".join(f"{sha}  {name}\n" for name, sha in sorted(hashes.items()))
root(f"printf %s {shlex.quote(manifest_text)} > {remote}/installation.sha256")
root(f"printf %s {shlex.quote(manifest_text)} > {remote}/SHA256SUMS")
root(f"if test ! -e {remote}/state/status; then printf 'STOCK_RESTORED\\n' > {remote}/state/status; fi")

evidence_dir = HERE / "evidence"
evidence_dir.mkdir(parents=True, exist_ok=True)
(evidence_dir / "installed-artifacts.json").write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n")

print(adb("install", "-r", str(apk_path)).strip())
print(adb("shell", "am", "start", "-n", "org.houji.owly/.MainActivity").strip())
print("STANDALONE_OWLY_APP_INSTALLED")
