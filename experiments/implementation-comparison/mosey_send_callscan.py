#!/usr/bin/env python3
"""Bounded AArch64 direct-BL scanner for one Mosey FFI send call cluster."""
import struct
from pathlib import Path

binary = Path("downloads/oneui9-houji-analysis/extracted/libmosey_daemon_ffi.so")
data = binary.read_bytes()
text_start = 0xECF70  # rabin2 -S: .text paddr == vaddr
text_size = 0x17D3A8
targets = {
    "send@plt": 0x26BC70,
    "send helper entry (candidate)": 0x1E71A4,
}

for name, target in targets.items():
    print(f"target {name} ELF_VA=0x{target:x}")
    found = 0
    for pc in range(text_start, text_start + text_size, 4):
        insn = struct.unpack_from("<I", data, pc)[0]
        if insn & 0xFC000000 != 0x94000000:  # AArch64 BL imm26
            continue
        imm26 = insn & 0x03FFFFFF
        if imm26 & 0x02000000:
            imm26 -= 0x04000000
        if pc + (imm26 << 2) == target:
            found += 1
            print(f"  callsite ELF_VA=file_offset=0x{pc:x} instruction=0x{insn:08x}")
    print(f"  direct_BL_callers={found}")
