#!/usr/bin/env python3
"""Build houji_wonder_bridge.ko, qca_cld3_kiwi_v2.ko, and wonder.ko directly from driver/ and BYOB archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

REPO = Path(__file__).resolve().parents[1]
DRIVER = REPO / "driver"

WONDER_KCONFIG_APPEND = """
CONFIG_WONDER_SUPPORT=m
# CONFIG_WONDER_SYNA_SUPPORT is not set
# CONFIG_WONDER_TX_DEBUG is not set
# CONFIG_WONDER_RX_DEBUG is not set
# CONFIG_WONDER_RX_FILTER_SUPPORT is not set
"""

WLAN_KCFLAGS = " ".join(
    [
        "-D__ANDROID_COMMON_KERNEL__",
        "-DWLAN_FEATURE_11BE_SUPP_NONWPA3",
        "-DWLAN_FEATURE_MULTI_P2P",
        "-DWLAN_FEATURE_FAST_CONNECT",
        "-Wno-format",
        "-fstrict-flex-arrays=0",
        "-I{external_inc}",
        "-DCONFIG_IPA_WDI_UNIFIED_API=1",
    ]
)

WLAN_MAKE_VARS = [
    "CONFIG_QCA_CLD_WLAN=m",
    "MODNAME=qca_cld3_kiwi_v2",
    "CHIP_NAME=kiwi_v2",
    "WLAN_CTRL_NAME=wlan",
    "HOUJI_WMI_C3_PROFILE=1",
    "WLAN_PROFILE=kiwi_v2",
    "WLAN_COMMON_ROOT=cmn",
    "CONFIG_ARCH_PINEAPPLE=y",
    "CONFIG_IPA_OUT_OF_TREE=y",
    "CONFIG_BUS_AUTO_SUSPEND=y",
    "CONFIG_CNSS2=m",
    "CONFIG_CNSS2_QMI=y",
    "CONFIG_CNSS2_SMMU_DB_SUPPORT=y",
    "CONFIG_CNSS2_SSR_DRIVER_DUMP=y",
    "CONFIG_CNSS_GENL=m",
    "CONFIG_CNSS_HW_SECURE_DISABLE=y",
    "CONFIG_CNSS_HW_SECURE_SMEM=y",
    "CONFIG_CNSS_OUT_OF_TREE=y",
    "CONFIG_CNSS_PLAT_IPC_QMI_SVC=m",
    "CONFIG_CNSS_QMI_SVC=m",
    "CONFIG_CNSS_UTILS=m",
    "CONFIG_WCNSS_MEM_PRE_ALLOC=m",
    "CONFIG_DRIVER_PASSTHRU_SESSION=y",
    "CONFIG_DRIVER_PASSTHRU_DP=y",
    "CONFIG_DRIVER_PASSTHRU_OWNER=y",
    "CONFIG_DRIVER_PASSTHRU_MODE=y",
    "CONFIG_DRIVER_PASSTHRU_RUNTIME=y",
    "CONFIG_DEBUG_INFO_BTF_MODULES=",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inside(root: Path, rel: str) -> Path:
    path = root / rel
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes source root: {rel}")
    return path


def required_env_path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"BYOB: set {name}; see docs/guides/byob.md")
    path = Path(value).expanduser().resolve()
    if not path.exists():
        sys.exit(f"BYOB: {name} does not exist: {path}")
    return path


def extract_single_root(archive: Path, dest: Path) -> Path:
    dest.mkdir(parents=True)
    with tarfile.open(archive) as tf:
        if not hasattr(tarfile, "data_filter"):
            sys.exit("Python with tarfile.data_filter required (Python 3.12+ recommended)")
        tf.extractall(dest, filter="data")
    entries = list(dest.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        sys.exit(f"Expected one top-level source directory in {archive}")
    return entries[0]


def prepare_sources(kernel_root: Path, wlan_root: Path, sources: dict) -> None:
    k_meta = sources["kernel"]
    w_meta = sources["wlan"]

    for rel, expected in k_meta["baseline_changed"].items():
        target = inside(kernel_root, rel)
        if expected is None and not target.exists():
            continue
        if not target.is_file() or sha256_file(target) not in (expected, k_meta["overlay_files"][rel]):
            sys.exit(f"Unexpected kernel baseline file: {rel}")

    for rel, expected in w_meta["unmodified_files"].items():
        target = inside(wlan_root, rel)
        if not target.is_file() or sha256_file(target) != expected:
            sys.exit(f"Unexpected WLAN baseline file: {rel}")

    for src_dir, dst_dir, expected_map in (
        (DRIVER / "kernel-overlay", kernel_root, k_meta["overlay_files"]),
        (DRIVER / "wlan", wlan_root, w_meta["overlay_files"]),
    ):
        for rel, expected in expected_map.items():
            src_file = src_dir / rel
            if not src_file.is_file() or sha256_file(src_file) != expected:
                sys.exit(f"Driver overlay integrity mismatch: {src_file}")
            out_file = inside(dst_dir, rel)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, out_file)

    for rel, target in w_meta["symlinks"].items():
        link = wlan_root / rel
        if link.is_symlink():
            if str(link.readlink()) != target:
                sys.exit(f"Unexpected symlink in WLAN tree: {rel}")
        elif link.exists():
            sys.exit(f"Expected symlink or absent path in WLAN tree: {rel}")
        else:
            link.symlink_to(target, target_is_directory=True)

    print(
        f"DRIVER_SOURCES_READY: verified {len(w_meta['unmodified_files'])} baseline + "
        f"{len(w_meta['overlay_files'])} unified WLAN overlay files",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify BYOB inputs and host tools without building")
    parser.add_argument("--keep-debug", action="store_true", help="keep full DWARF debug sections instead of llvm-strip --strip-debug")
    args = parser.parse_args()

    kernel_archive = required_env_path("HOUJI_KERNEL_ARCHIVE")
    wlan_archive = required_env_path("HOUJI_WLAN_ARCHIVE")
    clang_dir = required_env_path("HOUJI_CLANG_DIR")

    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
        sys.exit("Build driver on Linux x86_64 with the Android Clang toolchain; see docs/guides/building.md")

    for tool in ("make", "flex", "bison", "pahole", "perl"):
        if not shutil.which(tool):
            sys.exit(f"Build dependency missing: {tool}")

    for bin_name in ("clang", "ld.lld", "llvm-strip"):
        if not (clang_dir / "bin" / bin_name).is_file():
            sys.exit(f"HOUJI_CLANG_DIR must contain bin/{bin_name}")

    sources = json.loads((DRIVER / "sources.json").read_text())
    expected_kernel_sha = sources["kernel"]["sha256"]
    actual_kernel_sha = sha256_file(kernel_archive)
    if actual_kernel_sha != expected_kernel_sha:
        sys.exit(
            f"HOUJI_KERNEL_ARCHIVE SHA-256 mismatch: expected {expected_kernel_sha}, got {actual_kernel_sha}"
        )

    if args.check:
        print("Driver BYOB inputs and host toolchain verified.")
        return

    work = REPO / "build/driver"
    if work.exists():
        sys.exit(f"Build workspace already exists: {work}. Move or remove it before a clean rebuild.")
    work.mkdir(parents=True)
    (work / "tmp").mkdir()

    jobs = int(os.environ.get("HOUJI_BUILD_JOBS", str(min(os.cpu_count() or 4, 8))))
    pahole = shutil.which("pahole")
    assert pahole is not None
    vermagic_rel = sources["kernel"]["kernelrelease_vermagic"]

    env = os.environ.copy()
    env.update(
        PATH=f"{clang_dir / 'bin'}{os.pathsep}{env['PATH']}",
        TMPDIR=str(work / "tmp"),
        WONDER_SOURCE=str(work / "wonder"),
    )

    def run(cmd: list[str | Path]) -> None:
        strs = [str(c) for c in cmd]
        print("+", " ".join(strs), flush=True)
        subprocess.run(strs, env=env, check=True)

    kernel_root = extract_single_root(kernel_archive, work / "kernel")
    wlan_root = extract_single_root(wlan_archive, work / "wlan")
    prepare_sources(kernel_root, wlan_root, sources)

    for sub in ("bridge", "wonder"):
        shutil.copytree(DRIVER / sub, work / sub)

    cfg_dir = DRIVER / "configs"
    base_config = (cfg_dir / "kernel.config").read_text()

    def prepare_kbuild(out_dir: Path, extra_cfg: str = "", kconfig_file: Path | None = None) -> None:
        out_dir.mkdir(parents=True)
        (out_dir / ".config").write_text(base_config + extra_cfg)
        base_cmd: list[str | Path] = [
            "make",
            "-C",
            kernel_root,
            f"O={out_dir}",
            "ARCH=arm64",
            "LLVM=1",
            "LLVM_IAS=1",
            f"KERNELRELEASE={vermagic_rel}",
            f"PAHOLE={pahole}",
        ]
        if kconfig_file is not None:
            base_cmd.append(f"KBUILD_KCONFIG={kconfig_file}")
        run([*base_cmd, "olddefconfig"])
        run([*base_cmd, f"-j{jobs}", "modules_prepare"])
        shutil.copy2(cfg_dir / "kernel.symvers", out_dir / "Module.symvers")

    out_main = work / "out-main"
    out_wonder = work / "out-wonder"
    prepare_kbuild(out_main)
    prepare_kbuild(out_wonder, WONDER_KCONFIG_APPEND, cfg_dir / "Kconfig.wonder")

    # 1. Build houji_wonder_bridge.ko first so its exported symbol CRCs feed both wlan and wonder.
    run(
        [
            "make",
            "-C",
            kernel_root,
            f"O={out_main}",
            "ARCH=arm64",
            "LLVM=1",
            "LLVM_IAS=1",
            f"KERNELRELEASE={vermagic_rel}",
            f"PAHOLE={pahole}",
            "PAHOLE_FLAGS=--btf_gen_floats -j2",
            "LD=ld.lld --thinlto-jobs=2",
            f"-j{min(jobs, 4)}",
            f"M={work / 'bridge'}",
            "KCFLAGS=-D__ANDROID_COMMON_KERNEL__",
            "modules",
        ]
    )

    bridge_symvers = (work / "bridge/Module.symvers").read_text().splitlines()
    bridge_syms = {line.split()[1] for line in bridge_symvers if line.strip()}
    vendor_symvers = [
        line
        for line in (cfg_dir / "vendor.symvers").read_text().splitlines()
        if line.strip() and line.split()[1] not in bridge_syms
    ]
    merged_symvers = work / "merged-vendor.symvers"
    merged_symvers.write_text("\n".join([*vendor_symvers, *bridge_symvers]) + "\n")

    # 2. Build qca_cld3_kiwi_v2.ko from unified driver/wlan overlay.
    ipa_inc = DRIVER / "include/dataipa"
    ext_inc = DRIVER / "include/external"
    run(
        [
            "make",
            "-C",
            kernel_root,
            f"O={out_main}",
            "ARCH=arm64",
            "LLVM=1",
            "LLVM_IAS=1",
            f"KERNELRELEASE={vermagic_rel}",
            f"PAHOLE={pahole}",
            "PAHOLE_FLAGS=--btf_gen_floats -j1",
            "LD=ld.lld --thinlto-jobs=2",
            f"-j{jobs}",
            f"M={wlan_root / 'qcacld-3.0'}",
            f"WLAN_ROOT={wlan_root / 'qcacld-3.0'}",
            f"WLAN_COMMON_INC={wlan_root / 'qca-wifi-host-cmn'}",
            f"WLAN_FW_API={wlan_root / 'fw-api'}",
            f"WLAN_PLATFORM_INC={wlan_root / 'platform/inc'}",
            f"DATA_IPA_INC={ipa_inc}",
            f"DATA_IPA_UAPI_INC={ipa_inc / 'uapi'}",
            f"KBUILD_EXTRA_SYMBOLS={merged_symvers}",
            f"KCFLAGS={WLAN_KCFLAGS.format(external_inc=ext_inc)}",
            *WLAN_MAKE_VARS,
            "modules",
        ]
    )

    # 3. Build wonder.ko.
    run(
        [
            "make",
            "-C",
            kernel_root,
            f"O={out_wonder}",
            "ARCH=arm64",
            "LLVM=1",
            "LLVM_IAS=1",
            f"KERNELRELEASE={vermagic_rel}",
            f"KBUILD_KCONFIG={cfg_dir / 'Kconfig.wonder'}",
            f"PAHOLE={pahole}",
            "PAHOLE_FLAGS=--btf_gen_floats -j1",
            "LD=ld.lld --thinlto-jobs=1",
            f"-j{min(jobs, 4)}",
            f"M={work / 'wonder'}",
            f"KBUILD_EXTRA_SYMBOLS={merged_symvers}",
            "modules",
        ]
    )

    built_modules = {
        "bridge": work / "bridge/houji_wonder_bridge.ko",
        "wlan": wlan_root / "qcacld-3.0/qca_cld3_kiwi_v2.ko",
        "wonder": work / "wonder/wonder.ko",
    }
    for label, mod_path in built_modules.items():
        if not mod_path.is_file():
            sys.exit(f"Expected built module missing ({label}): {mod_path}")

    dest = REPO / "dist/driver"
    dest.mkdir(parents=True, exist_ok=True)
    llvm_strip = clang_dir / "bin/llvm-strip"
    for mod_path in built_modules.values():
        out_mod = dest / mod_path.name
        shutil.copy2(mod_path, out_mod)
        if not args.keep_debug:
            run([llvm_strip, "--strip-debug", out_mod])

    print(f"DRIVER_BUILD_PASS: {dest}")


if __name__ == "__main__":
    main()
