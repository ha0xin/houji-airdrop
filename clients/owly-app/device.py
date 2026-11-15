#!/usr/bin/env python3
"""ADB, root shell, checksum, and stock-idle checks decoupled from legacy experiments."""
import hashlib
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess

STOCK_AMSS_SHA = "4db7b0e1aa7e4ec071708359c9d0db8d3d7872390cd8e88ffa0bbb30d560bcc8"
STOCK_NOTE_SHA = "a3e9bd62646444e1ecbe0f421c95ec46e6caf69a78323efb2f6ebe00e6b2bd8b"


def resolve_serial() -> str:
    env_serial = os.environ.get("ANDROID_SERIAL", "").strip()
    if env_serial:
        return env_serial
    out = subprocess.check_output(["adb", "devices"], text=True, timeout=15)
    devices = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    if len(devices) == 1:
        return devices[0]
    if not devices:
        raise SystemExit("No authorized ADB device found; connect the phone or set ANDROID_SERIAL.")
    raise SystemExit(f"Multiple ADB devices found ({', '.join(devices)}); set ANDROID_SERIAL.")


def adb(*args: str, timeout: int = 30) -> str:
    return subprocess.check_output(
        ["adb", "-s", resolve_serial(), *args],
        text=True,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def root(command: str, timeout: int = 30) -> str:
    return adb("shell", f"su -c {shlex.quote(command)}", timeout=timeout)


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def current_awdl() -> tuple[str, int]:
    info = subprocess.check_output(["ifconfig", "awdl0"], text=True)
    mac = re.search(r"\bether ([0-9a-f:]{17})", info)
    if not mac or "status: active" not in info:
        raise RuntimeError("Mac awdl0 is not active")
    return mac.group(1), socket.if_nametoindex("awdl0")


def ensure_stock_idle() -> str:
    return root(
        f"""set -e
test ! -e /sys/module/houji_wonder_bridge
test ! -e /sys/module/wonder
test ! -e /sys/class/net/owly0
test ! -e /sys/class/net/wonder0
test ! -e /sys/class/net/xos0
test ! -e /data/local/tmp/owly-app/state/saved.env
test ! -e /data/local/tmp/xos/native/saved.env
test ! -e /data/local/tmp/xos/owly/state/saved.env
test "$(getprop sys.xos.exclusive)" = 0
echo '{STOCK_NOTE_SHA}  /sys/module/qca_cld3_kiwi_v2/notes/.note.gnu.build-id' | sha256sum -c -
echo '{STOCK_AMSS_SHA}  /vendor/firmware_mnt/image/kiwi/amss20.bin' | sha256sum -c -
! ip -6 rule show | grep -qE '^1087[012]:'
echo STOCK_IDLE_VERIFIED"""
    ).strip()
