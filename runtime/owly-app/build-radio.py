#!/usr/bin/env python3
"""Validate tracked runtime/owly-app radio scripts instead of regenerating from legacy evidence."""
from pathlib import Path
import subprocess

here = Path(__file__).resolve().parent
radio = here / "radio.sh"
control = here / "control.sh"

text = radio.read_text()
required = (
    "pt_coex_5g_override",
    "pt_rx_ezif_marked",
    "wait_wifi",
    "cmd wifi set-wifi-enabled disabled",
    "OWLY_XOS=/data/local/tmp/xos/owly",
    "bridge-supervisor.sh",
)
missing = [marker for marker in required if marker not in text]
if missing:
    raise SystemExit(f"{radio} is missing expected coexistence/reconnect hooks: {missing}")

for script in (radio, control):
    subprocess.run(["sh", "-n", str(script)], check=True)
print(f"Verified {radio.name} and {control.name}")
