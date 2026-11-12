#!/usr/bin/env python3
"""Verify the diagnostic candidate against both pre-existing WLAN binaries."""
import hashlib
import json
from pathlib import Path
import struct
import sys

from elftools.elf.elffile import ELFFile

root = (Path(__import__("os").environ["HOUJI_LEGACY_ROOT"]))
name = "qca_cld3_kiwi_v2.ko"
module = Path("qcacld-3.0") / name
candidate = Path(sys.argv[1]) if len(sys.argv) == 2 else root / "src/rx-capacity-4224/wlan" / module
references = {
    "tested_wideband_ring": root / "artifacts/wideband-ring" / name,
    "wideband_source_tree_module": root / "src/wideband/wlan" / module,
}
new_params = ("pt_rx_capacity_4224",)


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
                # struct kernel_param's three pointers precede its u16 perm.
                section = elf.get_section(s["st_shndx"])
                assert section.name == "__param" and s["st_size"] >= 26
                permissions[param] = oct(struct.unpack_from("<H", section.data(), s["st_value"] + 24)[0])
        flag = symbols.get_symbol_by_name("pt_rx_capacity_4224") or []
        initial = None
        if flag:
            section = elf.get_section(flag[0]["st_shndx"])
            initial = 0 if section["sh_type"] == "SHT_NOBITS" else section.data()[flag[0]["st_value"]]
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
            observer_default=initial,
            versions=versions,
            exports=exports,
            undefined=sorted(s.name for s in symbols.iter_symbols() if s["st_shndx"] == "SHN_UNDEF" and s.name),
            module_size=elf.get_section_by_name(".gnu.linkonce.this_module")["sh_size"],
            module_relocations=relocs,
            code=code,
        )


def compare(ref, trial):
    same = ("machine", "vermagic", "depends", "module_name", "versions", "exports",
            "undefined", "module_size", "module_relocations")
    return {field: ref[field] == trial[field] for field in same}


new = inspect(candidate)
old = {label: inspect(path) for label, path in references.items()}
abi_checks = {label: compare(ref, new) for label, ref in old.items()}
checks = {
    "aarch64": new["machine"] == "EM_AARCH64",
    "capacity_param_0400": new["params"] == dict.fromkeys(new_params, "0o400"),
    "capacity_default_off": new["observer_default"] == 0,
    "candidate_code_changed": new["code"] != old["wideband_source_tree_module"]["code"],
    "abi_matches_both_references": all(ok for row in abi_checks.values() for ok in row.values()),
}
receipt = {
    "checks": checks,
    "abi_by_reference": abi_checks,
    "reference_executable_sections_match_each_other": (
        old["tested_wideband_ring"]["code"] == old["wideband_source_tree_module"]["code"]
    ),
    "reference_sha256": {label: ref["sha256"] for label, ref in old.items()},
    "candidate_sha256": new["sha256"],
    "candidate_vermagic": new["vermagic"],
    "candidate_depends": new["depends"],
    "candidate_import_count": len(new["versions"]),
    "candidate_param_permissions": new["params"],
    "candidate_flag_initial_byte": new["observer_default"],
    "device_validation": "not performed",
}
print(json.dumps(receipt, indent=2))
assert all(checks.values())
