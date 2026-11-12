#!/usr/bin/env python3
"""Read stock recovery prerequisites or resume a recorded trial after a normal boot."""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "owly-evaluation" / "houji-runner"))
from bench import BASE, BUSYBOX, DEVICE, RADIO, ROOT, STATE, adb, ensure_restored, root

BOOT = "/proc/sys/kernel/random/boot_id"
NOTE = "/sys/module/qca_cld3_kiwi_v2/notes/.note.gnu.build-id"
FIRMWARE = "/vendor/firmware_mnt/image/kiwi/amss20.bin"
NATIVE = "/data/local/tmp/xos/native"
STOCK_KO = "/vendor_dlkm/lib/modules/qca_cld3_kiwi_v2.ko"
STOCK_KO_SHA = "d3fbe4f1b62f8c3533609945b617508e8a585660f5640aeeda08f8508416137b"
RECOVERY_HARNESS_SHA = "252ececb6ef0dd583baea607851c178fdf2787702cee4e9889f60249068505d6"
TRIAL_RADIO = f"{ROOT}/radio-remote-peer.sh"
TRIAL_HARNESS_SHA = "97204c7841d340fe571d6c756f7cf25a8d83b189e7f87090e2c0672cdbefec64"
CAPACITY_SMOKE_HARNESS_SHA = "4dcb5bc218350eefc4378a942c1b8a44f12f8e5ab76c8e3f8fe840fb12572592"
OUTGOING_HARNESS_SHA = "df40147c2e25019b7651a496dc41bcb5b5d2bc3edcabf18b0a8b2791ed154150"
RX_PHY_HARNESS_SHA = "fccfdde53badc02deef3737105111a5701f98b38df2f752fed4795af0de159c9"
PSF_HARNESS_SHA = "cae9dac0250998065abfc5723c6976ebed892cd78b3bc007020aead637fccf73"
CAPACITY_HARNESS_SHA = "af9caf9cfa5beb4758c8964dfd94cddc719c7d05b80fbf6a7260d4deeb99363d"
MAP_ONLY_HARNESS_SHA = "e53fbee3f500996eb48bf5f9754c2ba91f43107b771e3498462ce7eca49a8488"
SELF_FLOW_HARNESS_SHA = "9b31b7f7f2805144dddf14930eb537f692ef95368dace2c6d5f6c72f3942eec7"
AST_HARNESS_SHA = "361c615f7479a919bae1fc7032728f79f875596cf3144698b0c1667bfe6ac2a4"
FLOW_HARNESS_SHA = "5f79c112b0d7eb9c153d75a3441a4aa47f3e535d9a959b50a06be49957a4da60"
REO_HARNESS_SHA = "f89e09c77dd514e92f21dce457b7426f436600ea913cd2c2b7a783a4a9e5ef0b"
STOCK = {
    NOTE: "a3e9bd62646444e1ecbe0f421c95ec46e6caf69a78323efb2f6ebe00e6b2bd8b",
    FIRMWARE: "4db7b0e1aa7e4ec071708359c9d0db8d3d7872390cd8e88ffa0bbb30d560bcc8",
}
ORIGINAL_NATIVE = {
    f"{NATIVE}/radio.sh": "ea4d1f19d820b23fc82bc9f657192dc5d3bb4ca7fdb6244b2ddd2b2493f24b62",
    f"{NATIVE}/../libxos.so": "0a1b6401bcbde7d50f1e503b2f581940c6f40d4c3a701a1aa1a03ee04bbfffab",
    f"{NATIVE}/xos-bridge.zip": "07b4b951a805ebd2860f9432f553dfca9db42f50b34a400a40e1f8a2ae419333",
}


def check_hashes(expected):
    for path, sha in expected.items():
        got = root("sha256sum " + shlex.quote(path)).split()[0]
        if got != sha:
            raise RuntimeError(f"SHA mismatch for {path}")


def snapshot():
    payload = root(f"""{BUSYBOX} flock -n {ROOT}/job.lock {BUSYBOX} flock -n \
 {NATIVE}/supervisor.lock sh -c 'set -e
 test "$(cat {STATE}/status)" = RESTORE_FAILED
 test ! -e {NATIVE}/saved.env
 test ! -e {BASE}/state/saved.env
 cat {STATE}/purpose {STATE}/token {STATE}/candidate.sha {STATE}/saved.env'""")
    purpose, token, candidate, *saved = payload.splitlines()
    candidate_sha, candidate_path = candidate.split(" ", 1)
    if not re.fullmatch(r"[A-Za-z0-9-]+", token):
        raise ValueError("invalid session token")
    if not re.fullmatch(r"[0-9a-f]{64}", candidate_sha):
        raise ValueError("invalid candidate hash")
    if purpose == "rehearsal":
        if not token.startswith("rehearsal-") or (candidate_path, candidate_sha) != (STOCK_KO, STOCK_KO_SHA):
            raise ValueError("invalid stock rehearsal marker")
    elif purpose == "capacity":
        if candidate_path != f"{ROOT}/qca_cld3_kiwi_v2-remote-peer.ko":
            raise ValueError("unexpected capacity candidate path")
    elif purpose != "trial" or not re.fullmatch(r"/data/local/tmp/[A-Za-z0-9_/-]+[.]ko", candidate_path):
        raise ValueError("invalid candidate path or purpose")
    check_hashes({candidate_path: candidate_sha})
    radio = RADIO if purpose == "rehearsal" else TRIAL_RADIO
    harness = root("sha256sum " + shlex.quote(radio)).split()[0]
    if harness not in ((RECOVERY_HARNESS_SHA,) if purpose == "rehearsal"
                       else (CAPACITY_HARNESS_SHA, CAPACITY_SMOKE_HARNESS_SHA, OUTGOING_HARNESS_SHA, PSF_HARNESS_SHA, RX_PHY_HARNESS_SHA) if purpose == "capacity" else (TRIAL_HARNESS_SHA, REO_HARNESS_SHA, FLOW_HARNESS_SHA, AST_HARNESS_SHA, SELF_FLOW_HARNESS_SHA, MAP_ONLY_HARNESS_SHA)):
        raise RuntimeError("unknown recovery harness")
    return {"purpose": purpose, "token": token, "candidate": candidate_sha,
            "path": candidate_path, "harness": harness,
            "saved": dict(line.split("=", 1) for line in saved)}


def validate(values):
    allowed = {
        "saved_scan": r"[01]",
        "saved_cnd": r"running|stopped",
        "saved_hal": r"running|stopped",
        "saved_pci": r"auto|on",
        "saved_boot": r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
    }
    if values.keys() != allowed.keys() or any(
        not re.fullmatch(pattern, values[key]) for key, pattern in allowed.items()
    ):
        raise ValueError("unexpected recovery ledger")


def wait_root_after_boot(old_boot):
    until = time.monotonic() + 120
    while time.monotonic() < until:
        try:
            boot, uid, completed = root(
                f"cat {BOOT}; id -u; getprop sys.boot_completed", timeout=4
            ).splitlines()
            if uid == "0" and completed == "1" and boot != old_boot:
                return boot
        except (subprocess.SubprocessError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError("new boot with ADB and KernelSU root unavailable; unlock the device and resume")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preflight", action="store_true")
    action.add_argument("--capture", metavar="RECEIPT", type=Path)
    action.add_argument("--reboot", metavar="RECEIPT", type=Path)
    action.add_argument("--postboot", metavar="RECEIPT", type=Path)
    args = parser.parse_args()
    if args.preflight:
        check_hashes({**STOCK, **ORIGINAL_NATIVE, STOCK_KO: STOCK_KO_SHA})
        print(ensure_restored(), end="")
        print("BOOT_ID=" + root(f"cat {BOOT}").strip())
        print("SCAN=" + root("settings get global wifi_scan_always_enabled").strip())
        return

    if args.capture:
        check_hashes(ORIGINAL_NATIVE)
        session = snapshot()
        values = session["saved"]
        validate(values)
        if root(f"cat {BOOT}").strip() != values["saved_boot"]:
            raise RuntimeError("ledger is from another boot")
        with args.capture.open("x") as receipt:
            json.dump({"device": DEVICE, **session}, receipt, indent=2)
        return

    receipt = json.loads((args.reboot or args.postboot).read_text())
    if receipt.get("device") != DEVICE:
        raise ValueError("receipt belongs to another device")
    session = {key: value for key, value in receipt.items() if key != "device"}
    values = session["saved"]
    validate(values)
    if session["purpose"] == "rehearsal":
        radio, radio_sha = RADIO, RECOVERY_HARNESS_SHA
    elif session["purpose"] == "capacity":
        radio, radio_sha = TRIAL_RADIO, session.get("harness")
        if radio_sha not in (CAPACITY_HARNESS_SHA, CAPACITY_SMOKE_HARNESS_SHA, OUTGOING_HARNESS_SHA, PSF_HARNESS_SHA, RX_PHY_HARNESS_SHA):
            raise ValueError("unknown capacity recovery harness")
    elif session["purpose"] == "trial":
        radio, radio_sha = TRIAL_RADIO, session.get("harness", TRIAL_HARNESS_SHA)
        if radio_sha not in (TRIAL_HARNESS_SHA, REO_HARNESS_SHA, FLOW_HARNESS_SHA, AST_HARNESS_SHA, SELF_FLOW_HARNESS_SHA, MAP_ONLY_HARNESS_SHA):
            raise ValueError("unknown trial recovery harness")
    else:
        raise ValueError("unknown receipt purpose")
    check_expected = {**ORIGINAL_NATIVE, radio: radio_sha}
    if args.reboot:
        check_hashes(check_expected)
        if snapshot() != {**session, "harness": radio_sha} or root(f"cat {BOOT}").strip() != values["saved_boot"]:
            raise RuntimeError("reboot requires the current failed session and the original boot")
        adb("reboot", timeout=10)
    boot = wait_root_after_boot(values["saved_boot"])
    check_hashes({**check_expected, FIRMWARE: STOCK[FIRMWARE], STOCK_KO: STOCK_KO_SHA})
    if snapshot() != {**session, "harness": radio_sha}:
        raise RuntimeError("post-boot recovery ledger differs from the host receipt")
    print(root("sh " + shlex.quote(radio) + " recover", timeout=100), end="")
    print(ensure_restored(), end="")
    check_hashes({**STOCK, **ORIGINAL_NATIVE, STOCK_KO: STOCK_KO_SHA})
    print("BOOT_ID=" + boot)


if __name__ == "__main__":
    main()
