"""Two concurrent, independently hashed TCP streams over the existing AWDL lease."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
import threading
import time


def install(runtime):
    root, state, wait_for = runtime["root"], runtime["STATE"], runtime["wait_for"]

    def run(samples, token, completion, index, *, size, pairs=1, io_timeout=100,
            require_mif=False, require_psf=False, phase=None, **unused):
        def ready():
            if root(f"test ! -f {completion} || cat {completion}").strip():
                raise RuntimeError("radio exited before duplex readiness")
            if root(f"cat {state}/token").strip() != token:
                return None
            if phase is not None and root(
                f"test ! -f {state}/phy-ready-{token} || cat {state}/phy-ready-{token}"
            ).strip() != str(phase):
                return None
            log = root(f"test ! -f {state}/runner.log || cat {state}/runner.log")
            if require_mif and ("OWLY_PEERS count=1" not in log.splitlines() or
                               not re.search(r"^OWLY_MIF_SUBMITTED_VHT_CONTAINER=.+$", log, re.M)):
                return None
            if require_psf and not re.search(r"^OWLY_PSF_SUBMITTED_VHT_CONTAINER=.+$", log, re.M):
                return None
            server = root(f"test ! -f {state}/server.log || cat {state}/server.log")
            return re.search(r"^HASH_READY (fe80:[0-9a-f:]+)$", server, re.M)

        phone = wait_for(ready, 45).group(1)
        phone_mac = root("cat /sys/class/net/wonder0/address").strip().lower()
        if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", phone_mac):
            raise RuntimeError("unexpected radio address")
        payload = bytes(range(256)) * (size // 256)
        expected = hashlib.sha256(payload).digest()
        with ThreadPoolExecutor(max_workers=2) as pool:
            for pair in range(pairs):
                barrier = threading.Barrier(2, timeout=10)

                def transfer(mode):
                    sample = pair * 2 + mode + 1
                    barrier.wait()
                    start = time.time_ns()
                    row = runtime["transfer"](phone, index, mode, size, payload,
                                              expected, sample, io_timeout=io_timeout)
                    row.update(mac_wall_start_ns=start, mac_wall_end_ns=time.time_ns(),
                               duplex_pair=pair + 1)
                    return row

                futures = [pool.submit(transfer, mode) for mode in (0, 1)]
                rows = [f.result() for f in futures]
                start = min(row["mac_wall_start_ns"] for row in rows)
                end = max(row["mac_wall_end_ns"] for row in rows)
                overlap = min(row["mac_wall_end_ns"] for row in rows) - max(
                    row["mac_wall_start_ns"] for row in rows)
                for row in rows:
                    row.update(duplex_pair_wall_seconds=(end - start) / 1e9,
                               duplex_overlap_seconds=max(0, overlap) / 1e9)
                    if overlap <= 0 and row["status"] == "verified_mac":
                        row.update(status="failed", error="duplex streams did not overlap")
                    samples.append(row)
                    print(json.dumps(row, ensure_ascii=False), flush=True)
        if all(row["status"] == "verified_mac" for row in samples):
            wait_for(lambda: root(f"cat {state}/server.log").count("\nHASH_OK ") >= pairs * 2, 3)
        return phone_mac

    runtime["run_flow_samples"] = run
