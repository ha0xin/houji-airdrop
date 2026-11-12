#!/usr/bin/env python3
"""ABI and read-only parameter audit for the 8320-byte RX capacity + RX PHY observer module."""
from pathlib import Path
import json
import subprocess

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2] / "wideband" / "ring" / "qca_cld3_kiwi_v2.ko"
MOD = HERE / "out" / "qca_cld3_kiwi_v2-rx-capacity-8320.ko"
OUT = HERE / "audit-rx-capacity-8320.json"
BASE_SHA = "481e3474ea6b42acab5bb5a35b5be7b5e291aa975d7422034ca4e750e857d71c"


def sha256(path):
    return subprocess.check_output(["shasum", "-a", "256", str(path)], text=True).split()[0]


def strings_of(path):
    return subprocess.check_output(["strings", "-a", str(path)], text=True).splitlines()


def main():
    base_sha, mod_sha = sha256(BASE), sha256(MOD)
    if base_sha != BASE_SHA:
        raise SystemExit(f"Unexpected baseline SHA: {base_sha}")

    remote = subprocess.check_output(
        ["ssh", "build-host", "python3 -", BASE_SHA, mod_sha],
        input=r"""import json, struct, subprocess, sys
base = "/work/houji-build/artifacts/wideband-ring/qca_cld3_kiwi_v2.ko"
cand = "/work/houji-build/out/rx-capacity-8320-wlan/qca_cld3_kiwi_v2-rx-capacity-8320.ko"
llvm = "/work/houji-build/toolchains/clang-r487747c/bin"

def sh(*args):
    return subprocess.check_output(args, text=True)

def sha(p):
    return sh("sha256sum", p).split()[0]

assert sha(base) == sys.argv[1]
assert sha(cand) == sys.argv[2]

def sections(p):
    out = {}
    for line in sh(f"{llvm}/llvm-readelf", "-SW", p).splitlines():
        parts = line.strip().split()
        if parts and parts[0].startswith("[") and parts[0].endswith("]") and len(parts) >= 7:
            idx = parts[0][1:-1]
            if idx.isdigit():
                out[parts[1]] = (int(parts[4], 16), int(parts[5], 16))
    return out

def sec_bytes(p, name):
    off, size = sections(p)[name]
    with open(p, "rb") as f:
        f.seek(off)
        return f.read(size)

def modinfo(p):
    raw = sec_bytes(p, ".modinfo").split(b"\0")
    return [x.decode("latin1") for x in raw if x]

def versions(p):
    data = sec_bytes(p, "__versions")
    out = []
    for off in range(0, len(data), 64):
        crc, name = struct.unpack_from("<Q56s", data, off)
        name = name.split(b"\0", 1)[0].decode("latin1")
        if name:
            out.append((name, f"0x{crc:08x}"))
    return out

def ksymtab(p):
    out = []
    for sec in ("__ksymtab", "__ksymtab_gpl"):
        if sec in sections(p):
            for line in sh(f"{llvm}/llvm-objdump", "-r", f"--section={sec}", p).splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[0][0] in "0123456789abcdef":
                    out.append((sec, parts[2]))
    return out

def params(p):
    out = []
    for line in sh(f"{llvm}/llvm-nm", "-n", p).splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2].startswith("__param_"):
            out.append(parts[2])
    return sorted(out)

bm, cm = modinfo(base), modinfo(cand)
bv, cv = versions(base), versions(cand)
bk, ck = ksymtab(base), ksymtab(cand)
bp, cp = params(base), params(cand)
print(json.dumps({
    "modinfo_equal": bm == cm,
    "modinfo_added": [x for x in cm if x not in bm],
    "modinfo_removed": [x for x in bm if x not in cm],
    "versions_equal": bv == cv,
    "versions_count": len(cv),
    "ksymtab_equal": bk == ck,
    "ksymtab_count": len(ck),
    "params_added": [x for x in cp if x not in bp],
    "params_removed": [x for x in bp if x not in cp],
    "vermagic": [x for x in cm if x.startswith("vermagic=")],
}))
""",
        text=True,
    )
    info = json.loads(remote)
    s = set(strings_of(MOD))
    required_strings = [
        "\x06PT_RX_CAPACITY cfg_bytes=%u",
        "\x06PT_RX_CAPACITY pool_bytes=%u align=%u entries=%u",
        "\x06PT_RX_CAPACITY map_bytes=%u linear=%u",
        "\x06PT_RX_CAPACITY htt_bytes=%u mac_id=%d host_submit_status=%d",
        "\x06PT_RX_CAPACITY raw_osif_bytes=%u tlv=%u pad=%u linear_span=%u",
        "\x06PT_RX_CAPACITY large_mpdu_bytes=%u tlv=%u pad=%u linear_span=%u",
        "schema=1 enabled=%u matched=%lld fcs=%lld layout=%lld not_done=%lld ppdu_mismatch=%lld range=%lld outside_scope=%lld valid=%lld retry=%lld amsdu=%lld gt3895=%lld ampdu=unknown",
    ]
    report = {
        "baseline_module": "tested_wideband_ring",
        "baseline_sha256": base_sha,
        "candidate_module": str(MOD.relative_to(HERE)),
        "candidate_sha256": mod_sha,
        "abi": info,
        "checks": {
            "versions_equal": info["versions_equal"],
            "ksymtab_equal": info["ksymtab_equal"],
            "params_removed_empty": info["params_removed"] == [],
            "params_added_expected": info["params_added"] == [
                "__param_pt_rx_capacity_4224",
                "__param_pt_rx_phy_peer",
                "__param_pt_rx_phy_stats",
                "__param_str_pt_rx_capacity_4224",
                "__param_str_pt_rx_phy_peer",
                "__param_str_pt_rx_phy_stats",
            ],
            "modinfo_removed_empty": info["modinfo_removed"] == [],
            "modinfo_added_expected": info["modinfo_added"] == [
                "parmtype=pt_rx_capacity_4224:bool"
            ],
            "required_strings_present": all(any(item.lstrip("\x06") in line for line in s) for item in required_strings),
        },
    }
    OUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not all(report["checks"].values()):
        raise SystemExit("RX 8320 capacity + PHY audit failed")


if __name__ == "__main__":
    main()
