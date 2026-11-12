#!/usr/bin/env python3
"""Check passive capability module against WLAN ABI and inspect its new parameters."""
import json
from pathlib import Path
import struct
import subprocess
import sys

from elftools.elf.elffile import ELFFile

candidate = Path(sys.argv[1])
audit = (Path(__import__("os").environ["HOUJI_LEGACY_ROOT"]) / 'ba-peer-observer/final-input/verify.py')
result = subprocess.run([sys.executable, str(audit), str(candidate)],
                        capture_output=True, text=True)
if result.returncode:
    sys.stderr.write(result.stdout)
    sys.stderr.write(result.stderr)
    result.check_returncode()
receipt = json.loads(result.stdout)
with candidate.open("rb") as stream:
    elf = ELFFile(stream)
    symbols = elf.get_section_by_name(".symtab")

    def symbol(name):
        rows = symbols.get_symbol_by_name(name)
        assert rows and len(rows) == 1, name
        return rows[0]

    for param in ("pt_peer_caps_observe", "pt_peer_caps"):
        row = symbol("__param_" + param)
        section = elf.get_section(row["st_shndx"])
        assert section.name == "__param" and row["st_size"] >= 26
        value = struct.unpack_from("<H", section.data(), row["st_value"] + 24)[0]
        assert value == 0o444, param
        receipt["candidate_param_permissions"][param] = oct(value)

    row = symbol("pt_peer_caps_observe")
    section = elf.get_section(row["st_shndx"])
    initial = 0 if section["sh_type"] == "SHT_NOBITS" else section.data()[row["st_value"]]
    assert initial == 0
    receipt["peer_caps_observer_default"] = initial

print(json.dumps(receipt, indent=2))
