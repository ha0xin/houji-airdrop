#!/usr/bin/env python3
"""Verify the opt-in self-only flow control and preserve the prior WLAN ABI checks."""
import json
from pathlib import Path
import struct
import subprocess
import sys

from elftools.elf.elffile import ELFFile

candidate = Path(sys.argv[1])
prior = (Path(__import__("os").environ["HOUJI_LEGACY_ROOT"]) / 'tasks/remote-peer-flow/verify-flow.py')
receipt = json.loads(subprocess.check_output([sys.executable, str(prior), str(candidate)]))
with candidate.open("rb") as stream:
    elf = ELFFile(stream)
    symbols = elf.get_section_by_name(".symtab")

    def symbol(name):
        rows = symbols.get_symbol_by_name(name)
        assert rows and len(rows) == 1, name
        return rows[0]

    name = "pt_remote_self_flow"
    param = symbol("__param_" + name)
    section = elf.get_section(param["st_shndx"])
    assert section.name == "__param" and param["st_size"] >= 26
    permissions = struct.unpack_from("<H", section.data(), param["st_value"] + 24)[0]
    assert permissions == 0o400
    receipt["candidate_param_permissions"][name] = oct(permissions)
    flag = symbol(name)
    section = elf.get_section(flag["st_shndx"])
    initial = 0 if section["sh_type"] == "SHT_NOBITS" else section.data()[flag["st_value"]]
    assert initial == 0
    receipt["self_flow_default"] = initial

print(json.dumps(receipt, indent=2))
