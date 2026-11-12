#!/usr/bin/env python3
"""Deploy legacy audited artifacts; standalone build outputs need separate validation."""
from pathlib import Path
import os
import hashlib
import json
import runpy
import shlex

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
WORK=REPO / "experiments"
TF=WORK / "three-findings-validation"
g=runpy.run_path(str(TF / "run-three-findings.py"))
adb,root=g["adb"],g["root"]
digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
audit=json.loads((TF/"audit-three-findings.json").read_text())
module=TF/"qca_cld3_kiwi_v2-three-findings.ko"
assert digest(module)==audit["candidate_sha256"] and audit["candidate_import_count"]==567

remote="/data/local/tmp/owly-app"

files={
    "houji-owly-outgoing-vht":HERE/"dist/houji-owly-app",
    "wonder-init-2ss":TF/"dist/wonder-init",
    "qca_cld3_kiwi_v2-remote-peer.ko":module,
    "radio.sh":REPO/"runtime/owly-app/radio.sh",
    "control.sh":REPO/"runtime/owly-app/control.sh",
    "firmware/ezif.bin":Path(os.environ.get("HOUJI_EZIF_FIRMWARE", REPO/"blobs/local/ezif-amss20.bin")),
    "firmware/dzf2.bin":Path(os.environ.get("HOUJI_DZF2_FIRMWARE", REPO/"blobs/local/dzf2-amss20.bin")),
}
for name, path in files.items():
    if not path.is_file():
        raise SystemExit(f"Missing input {name}: {path}. See docs/guides/byob.md; no device changes made.")
assert digest(files['firmware/ezif.bin'])=='37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f'
assert digest(files['firmware/dzf2.bin'])=='13f01cb46efbe498c854352bfc253fa3665c14a14c37a0a6ed6a609a48a8709c'
print(g["ensure_restored"]())
print(root(f"test ! -f {remote}/state/saved.env; mkdir -p {remote}/state {remote}/radio {remote}/firmware; chmod 700 {remote}"))
hashes={}
for name,path in files.items():
    staging="/data/local/tmp/owly-app-stage"
    adb("push",str(path),staging)
    sha=digest(path);hashes[name]=sha
    print(root(f"cp {staging} {remote}/{name}; chmod 700 {remote}/{name}; echo '{sha}  {remote}/{name}' | sha256sum -c -"))
    adb("shell","rm",staging)
helpers={"houji_wonder_bridge.ko":"9a8345d87ec1186a29b413935b4efdbd38566e7e22374e70a7a85510b2970e9a",
         "wonder.ko":"9d60988f896669604ff2e8cdfa1ba91a7047c1ddd2210b1fa494f506f5eb039c"}
for name,sha in helpers.items():
    source=f"/data/local/tmp/houji-buildtest-20261110/wideband-ring/{name}"
    print(root(f"echo '{sha}  {source}' | sha256sum -c -; cp {source} {remote}/radio/{name}"))
    hashes['radio/'+name]=sha
for path,name in [('houji-owly-outgoing-vht','outgoing-vht.sha'),('wonder-init-2ss','wonder-init-2ss.sha'),('qca_cld3_kiwi_v2-remote-peer.ko','remote-peer.sha')]:
    root(f"printf '%s\\n' '{hashes[path]}' > {remote}/{name}")
manifest=''.join(f'{sha}  {name}\n' for name,sha in hashes.items())
root(f"printf %s {shlex.quote(manifest)} > {remote}/installation.sha256")
root(f"printf %s {shlex.quote(manifest)} > {remote}/SHA256SUMS")
(HERE/"evidence/installed-artifacts.json").write_text(json.dumps(hashes,indent=2)+'\n')
print(adb("install","-r",str(HERE/"dist/Owly-AirDrop.apk")))
print(adb("shell","am","start","-n","org.houji.owly/.MainActivity"))
print("STANDALONE_OWLY_APP_INSTALLED")
