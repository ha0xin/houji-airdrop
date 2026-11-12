#!/usr/bin/env python3
"""Run the pinned single-direction Owly profile and its restoration checks."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-name", default="optimized-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
parser.add_argument("--rounds", type=int, choices=range(1, 11), default=5)
parser.add_argument("--check", action="store_true", help="Only verify pinned local artifacts; do not contact the phone")
args = parser.parse_args()
profile = json.loads((HERE / "optimized-profile.json").read_text())
for name, expected in profile["sha256"].items():
    actual = hashlib.sha256((HERE / name).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"Profile artifact changed: {name}. Revalidate before updating the profile.")
print("OPTIMIZED_PROFILE_ARTIFACTS_VERIFIED", flush=True)
if not args.check:
    command = [sys.executable, str(HERE / "run-ezif-rx-adapter.py"),
               "--run-name", args.run_name, "--rounds", str(args.rounds)]
    for name, value in profile["arguments"].items():
        command.extend(["--" + name, str(value)])
    subprocess.run(command, check=True)
