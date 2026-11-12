#!/usr/bin/env python3
"""Verify the peer diagnostics' opt-in ABI and compare against the tested module."""
import json
from pathlib import Path
import struct
import subprocess
import sys

from elftools.elf.elffile import ELFFile

candidate = Path(sys.argv[1])
prior = Path(__file__).with_name("verify.py")
receipt = json.loads(subprocess.check_output([sys.executable, str(prior), str(candidate)]))
with candidate.open("rb") as stream:
    elf = ELFFile(stream)
    symbols = elf.get_section_by_name(".symtab")

    def symbol(name):
        rows = symbols.get_symbol_by_name(name)
        assert rows and len(rows) == 1, name
        return rows[0]

    for param, expected in (("pt_remote_peer_mac", 0o400),
                            ("pt_remote_peer_run", 0o200),
                            ("pt_remote_peer_stats", 0o400)):
        row = symbol("__param_" + param)
        section = elf.get_section(row["st_shndx"])
        assert section.name == "__param" and row["st_size"] >= 26
        permissions = struct.unpack_from("<H", section.data(), row["st_value"] + 24)[0]
        assert permissions == expected, param
        receipt["candidate_param_permissions"][param] = oct(permissions)

    row = symbol("pt_remote_peer_mac")
    section = elf.get_section(row["st_shndx"])
    assert row["st_size"] == 6
    if section["sh_type"] != "SHT_NOBITS":
        assert section.data()[row["st_value"]:row["st_value"] + 6] == bytes(6)
    receipt["remote_peer_mac_default_empty"] = True

print(json.dumps(receipt, indent=2))
