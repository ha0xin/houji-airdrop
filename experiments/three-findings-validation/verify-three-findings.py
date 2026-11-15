#!/usr/bin/env python3
"""Verify the three-findings candidate .ko against tested wideband and capacity WLAN binaries."""
import hashlib
import json
from pathlib import Path
import struct
import sys

from elftools.elf.elffile import ELFFile

root = (Path(__import__("os").environ["HOUJI_LEGACY_ROOT"]))
name = "qca_cld3_kiwi_v2.ko"
module = Path("qcacld-3.0") / name
candidate = Path(sys.argv[1]) if len(sys.argv) == 2 else root / "src/three-findings/wlan" / module
references = {
    "tested_wideband_ring": root / "artifacts/wideband-ring" / name,
    "capacity_source_tree_module": root / "src/rx-capacity-4224/wlan" / name if (root / "src/rx-capacity-4224/wlan" / name).exists() else root / "src/rx-capacity-4224/wlan" / module,
}
new_params = (
    "pt_rx_msdu_buf",
    "pt_rx_ezif_marked",
    "pt_coex_5g_override",
    "pt_rx_msdu_stats",
    "pt_rx_capacity_4224",
    "pt_rx_phy_peer",
    "pt_rx_phy_stats",
    "pt_vht80_2ss",
    "pt_remote_assoc",
    "pt_remote_ast_override",
    "pt_remote_hash_override",
    "pt_ast_selection",
    "pt_remote_reo",
    "pt_remote_rx_window",
    "pt_fw_addba",
    "pt_fw_addba_rc",
    "pt_fw_addba_sent",
    "pt_remote_mac",
    "pt_tx_ampdu",
    "pt_dynamic_rate",
    "pt_sta_coexist",
    "pt_fw_caps",
    "pt_remote_peer_stats",
    "pt_ampdu_peak",
    "pt_remote_multi",
    "pt_remote_peer_ctl",
)
default_off_flags = (
    ("pt_rx_msdu_buf", 1),
    ("pt_rx_ezif_marked", 1),
    ("pt_coex_5g_override", 1),
    ("pt_rx_capacity_4224", 1),
    ("pt_rx_phy_mac", 6),
    ("pt_vht80_2ss", 1),
    ("pt_remote_assoc", 1),
    ("pt_remote_reo", 4),
    ("pt_remote_rx_window", 4),
    ("pt_fw_addba", 1),
    ("pt_remote_mac", 6),
    ("pt_tx_ampdu", 1),
    ("pt_dynamic_rate", 1),
    ("pt_sta_coexist", 1),
    ("pt_remote_multi", 1),
)


def inspect(path):
    with path.open("rb") as f:
        elf = ELFFile(f)
        assert elf.little_endian and elf.elfclass == 64
        symbols = elf.get_section_by_name(".symtab")
        versions = {}
        for crc, raw_name in struct.iter_unpack("<Q56s", elf.get_section_by_name("__versions").data()):
            versions[raw_name.split(b"\0", 1)[0].decode()] = crc
        modinfo = {}
        for row in elf.get_section_by_name(".modinfo").data().split(b"\0"):
            if b"=" in row:
                key, value = row.decode().split("=", 1)
                modinfo.setdefault(key, []).append(value)
        exports = {}
        for s in symbols.iter_symbols():
            if s.name.startswith("__crc_") and isinstance(s["st_shndx"], int):
                section = elf.get_section(s["st_shndx"])
                exports[s.name[6:]] = struct.unpack_from("<I", section.data(), s["st_value"])[0]
        reloc = elf.get_section_by_name(".rela.gnu.linkonce.this_module")
        relocs = [
            (r["r_offset"], r["r_info_type"], symbols.get_symbol(r["r_info_sym"]).name, r["r_addend"])
            for r in reloc.iter_relocations()
        ] if reloc else []
        permissions = {}
        for param in new_params:
            matches = symbols.get_symbol_by_name("__param_" + param) or []
            for s in matches:
                section = elf.get_section(s["st_shndx"])
                assert section.name == "__param" and s["st_size"] >= 26
                permissions[param] = oct(struct.unpack_from("<H", section.data(), s["st_value"] + 24)[0])
        flags = {}
        for flag_name, width in (*default_off_flags, ("pt_remote_ast_override", 1), ("pt_remote_hash_override", 1)):
            matches = symbols.get_symbol_by_name(flag_name) or []
            if matches:
                sym = matches[0]
                assert sym["st_size"] == width
                section = elf.get_section(sym["st_shndx"])
                raw = (bytes(width) if section["sh_type"] == "SHT_NOBITS" else
                       section.data()[sym["st_value"]:sym["st_value"] + width])
                flags[flag_name] = raw.hex()
        code = {
            s.name: hashlib.sha256(s.data()).hexdigest()
            for s in elf.iter_sections() if s["sh_flags"] & 4
        }
        return dict(
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            machine=elf["e_machine"],
            vermagic=modinfo.get("vermagic"),
            depends=modinfo.get("depends"),
            module_name=modinfo.get("name"),
            params=permissions,
            initial_flags=flags,
            versions=versions,
            exports=exports,
            undefined=sorted(s.name for s in symbols.iter_symbols() if s["st_shndx"] == "SHN_UNDEF" and s.name),
            module_size=elf.get_section_by_name(".gnu.linkonce.this_module")["sh_size"],
            module_relocations=relocs,
            code=code,
            plt_got={s.name: s["sh_size"] for s in elf.iter_sections()
                     if s.name in (".plt", ".init.plt", ".got", ".got.plt")},
            debug_sections=[s.name for s in elf.iter_sections()
                            if s.name.startswith(".debug")],
        )


def compare(ref, trial):
    same = ("machine", "vermagic", "depends", "module_name", "exports",
            "module_size", "module_relocations")
    shared = ref["versions"].keys() & trial["versions"].keys()
    return {
        **{field: ref[field] == trial[field] for field in same},
        "common_import_crc": all(ref["versions"][name] == trial["versions"][name]
                                 for name in shared),
    }


new = inspect(candidate)
old = {label: inspect(path) for label, path in references.items()}
abi_checks = {label: compare(ref, new) for label, ref in old.items()}
capacity = old["capacity_source_tree_module"]
added_versions = new["versions"].keys() - capacity["versions"].keys()
symbol_versions = {}
for path in (root / "upstream/kernel/configs/kernel.symvers",
             root / "out/wlan-build-extra.symvers"):
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] in added_versions:
            symbol_versions.setdefault(fields[1], set()).add(int(fields[0], 16))
new_import_checks = {name: symbol_versions.get(name) == {new["versions"][name]}
                     for name in sorted(added_versions)}
new_undefined = set(new["undefined"]) - set(capacity["undefined"])
expected_flags = {flag_name: "00" * width for flag_name, width in default_off_flags}
expected_flags["pt_remote_ast_override"] = "01"
expected_flags["pt_remote_hash_override"] = "01"
expected_flags["pt_remote_rx_window"] = "40000000"
expected_params = dict.fromkeys(new_params, "0o400")
expected_params["pt_rx_msdu_stats"] = "0o444"
expected_params["pt_remote_peer_ctl"] = "0o600"
checks = {
    "all_567_imports": len(new["versions"]) == 567,
    "plt_got_matches_references": all(new["plt_got"] == ref["plt_got"]
                                      for ref in old.values()),
    "debug_stripped": not new["debug_sections"],
    "aarch64": new["machine"] == "EM_AARCH64",
    "read_only_params": new["params"] == expected_params,
    "defaults_off": new["initial_flags"] == expected_flags,
    "candidate_code_changed": new["code"] != capacity["code"],
    "abi_matches_both_references": all(ok for row in abi_checks.values() for ok in row.values()),
    "new_imports_exported_with_target_crc": (new_undefined <= added_versions and
                                             all(new_import_checks.values())),
}
receipt = {
    "checks": checks,
    "abi_by_reference": abi_checks,
    "reference_sha256": {label: ref["sha256"] for label, ref in old.items()},
    "candidate_sha256": new["sha256"],
    "candidate_vermagic": new["vermagic"],
    "candidate_depends": new["depends"],
    "candidate_import_count": len(new["versions"]),
    "added_imports_crc_verified": new_import_checks,
    "added_undefined": sorted(new_undefined),
    "candidate_param_permissions": new["params"],
    "candidate_initial_flags": new["initial_flags"],
}
print(json.dumps(receipt, indent=2))
assert all(checks.values()), f"ABI audit failed: {checks}"
