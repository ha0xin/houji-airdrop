#!/usr/bin/env python3
"""Summarize verified phases without averaging per-transfer rates."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("runs", nargs="+", type=Path)
args = parser.parse_args()
for folder in args.runs:
    results = [json.loads(p.read_text()) for p in sorted(folder.glob("[0-9]*/result.json"))]
    rows = [row for result in results for row in result["samples"]]
    directions = {}
    for direction in sorted({r["direction"] for r in rows}):
        samples = [r for r in rows if r["direction"] == direction]
        verified = [r for r in samples if r["status"] == "verified"]
        seconds = sum(r["seconds"] for r in verified)
        directions[direction] = dict(
            verified=len(verified), failed=len(samples) - len(verified),
            bytes=sum(r["bytes"] for r in verified),
            weighted_MBps=sum(r["bytes"] for r in verified) / seconds / 1e6 if seconds else None,
            samples_MBps=[round(r["MBps"], 3) for r in verified],
        )
    restored = folder / "stock-after.txt"
    tuning_path = folder / "tuning.json"
    tuning = json.loads(tuning_path.read_text()) if tuning_path.exists() else None
    output = dict(run=folder.name, phases=len(results), directions=directions,
                  verified=bool(results) and all(r.get("verified") for r in results),
                  stock_restored=restored.exists() and restored.read_text().startswith("STOCK_RESTORED WIFI_CONNECTED"))
    output["expected_phases"] = tuning.get("rounds", 1) if tuning else None
    output["completed"] = bool(tuning and output["verified"] and output["stock_restored"]
                               and len(results) == output["expected_phases"])
    if rows and all("duplex_pair_wall_seconds" in r for r in rows):
        output["duplex_joint_MBps"] = sum(r["bytes"] for r in rows) / sum(
            r["duplex_pair_wall_seconds"] for r in rows if r["mode"] == 0) / 1e6
    print(json.dumps(output, ensure_ascii=False))
