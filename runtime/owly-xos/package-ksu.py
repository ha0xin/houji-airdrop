#!/usr/bin/env python3
"""Package Owly-XOS runtime, stock-signed MIShare.apk, and Owly-XOS-Compat.apk into a flashable KernelSU/APatch/Magisk ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import zipfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OWLY_DIR = REPO / "clients/owly-app"
KSU_DIR = HERE / "ksu"
DIST_DIR = HERE / "dist"

VERSION = "v1.0.0"
DEFAULT_ZIP_NAME = f"owly-xos-houji-OS3.0.307-k6.1.138-{VERSION}.zip"
FIXED_ZIP_TIME = (2026, 11, 15, 0, 0, 0)

EXPECTED_KERNEL = "6.1.138-android14-11-g0c3d559bcd85-ab14529422"
EXPECTED_STOCK_AMSS_SHA = "4db7b0e1aa7e4ec071708359c9d0db8d3d7872390cd8e88ffa0bbb30d560bcc8"
EXPECTED_STOCK_BUILD_ID_SHA = "a3e9bd62646444e1ecbe0f421c95ec46e6caf69a78323efb2f6ebe00e6b2bd8b"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest() -> dict:
    manifest = json.loads((OWLY_DIR / "runtime-manifest.json").read_text())
    audit = json.loads((REPO / manifest["audit_file"]).read_text())
    if manifest["blobs"]["qca_cld3_kiwi_v2"]["sha256"] != audit["candidate_sha256"]:
        raise SystemExit("Manifest/audit candidate_sha256 mismatch")
    if int(manifest["audit_import_count"]) != int(audit["candidate_import_count"]) or int(manifest["audit_import_count"]) != 567:
        raise SystemExit("Manifest/audit import count mismatch (expected 567)")
    return manifest


def resolve_blob(manifest: dict, blobs_dir: Path, key: str) -> Path:
    entry = manifest["blobs"][key]
    raw = os.environ.get(entry["env"])
    path = Path(raw) if raw else blobs_dir / entry["file"]
    if not path.is_file():
        raise SystemExit(
            f"Missing BYOB input '{key}': {path} (set {entry['env']} or place in {blobs_dir / entry['file']}). "
            "See docs/guides/byob.md."
        )
    actual = sha256_file(path)
    if actual != entry["sha256"]:
        raise SystemExit(
            f"SHA-256 mismatch for '{key}' ({path}): expected {entry['sha256']}, got {actual}"
        )
    return path


def verify_elf_aarch64(path: Path, label: str) -> None:
    if not path.is_file():
        raise SystemExit(f"Missing native binary {label}: {path}")
    header = path.read_bytes()[:20]
    if len(header) < 20 or header[:4] != b"\x7fELF" or header[4] != 2 or int.from_bytes(header[18:20], "little") != 183:
        raise SystemExit(f"Invalid ELF64 aarch64 binary for {label}: {path}")


def verify_zip_entries(path: Path, label: str, required_entries: tuple[str, ...]) -> None:
    if not path.is_file():
        raise SystemExit(f"Missing archive {label}: {path}")
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        missing = [req for req in required_entries if req not in names]
        if missing:
            raise SystemExit(f"Archive {label} ({path}) missing required entries: {missing}")


def write_zip_bytes(zf: zipfile.ZipFile, arcname: str, data: bytes, mode: int, compress: bool = True) -> None:
    info = zipfile.ZipInfo(arcname, date_time=FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    info.create_system = 3  # Unix
    info.external_attr = (0o100000 | mode) << 16
    zf.writestr(info, data)


def build_share_guide(zip_name: str, zip_sha: str, mishare_sha: str, compat_sha: str) -> str:
    return f"""# Owly-XOS 小米 14 原生互传高速 AirDrop 模块安装说明（{VERSION}）

## 1. 模块文件校验

- 文件名：`{zip_name}`
- SHA-256：`{zip_sha}`
- 内置组件：
  - `owly-xos` 高速后端运行时（`owly-awdl` VHT80 2SS MCS9 + 三星提取 `ezif` 多对端硬件 TX A-MPDU 固件 + 经 567 符号 CRC 审计的内核模块 + `XosBridge` AIDL 服务）
  - 小米 18 Pro Max 提取原厂平台签名 `MIShare.apk`（`5.4.2_20260923_1315`，SHA-256 `{mishare_sha[:16]}...`，无需核心破解即可直接升级系统互传）
  - `Owly-XOS 小米互传兼容` LSPosed 模块（`org.houji.xoscompat`，SHA-256 `{compat_sha[:16]}...`，已声明默认推荐作用域）

## 2. 严格设备、系统与内核要求（刷入时自动校验，不匹配会安全中止）

| 检查项 | 要求值 | 查看方式 / 说明 |
|---|---|---|
| 机型代号 | **小米 14（`houji`，如 `23127PN0CC`）** | 不支持 14 Pro（`shennong`）/ 14 Ultra（`aurora`） |
| 系统版本 | **澎湃 HyperOS 3（Android 16，SDK 36）** | 实测基准版本：**`OS3.0.307.0.WNCCNXM`** |
| 内核版本（`uname -r`） | **`{EXPECTED_KERNEL}`** | 设置 → 我的设备 → 全部参数与信息 → 内核版本（必须完全一致） |
| 原厂 WLAN 驱动 Build-ID | `{EXPECTED_STOCK_BUILD_ID_SHA}` | `/sys/module/qca_cld3_kiwi_v2/notes/.note.gnu.build-id` SHA-256 |
| 原厂 WLAN 固件哈希 | `{EXPECTED_STOCK_AMSS_SHA}` | `/vendor/firmware_mnt/image/kiwi/amss20.bin` SHA-256 |
| Root 与框架要求 | **KernelSU**（或 APatch / Magisk）+ **Zygisk + LSPosed** | 模块使用 `skip_mount`，**不需要** OverlayFS / `magic_mount` 元模块，也**不需要**核心破解（CorePatch） |

> 安全保护：若日后系统 OTA 升级导致内核版本或原厂 WLAN 驱动发生变化，开机守护 `service.sh` 会自动检测到版本不匹配并**安全停用**（不会加载 `.ko` 模块，无卡开机风险）。

## 3. 三步安装流程

1. **刷入模块**：保持手机普通 Wi-Fi 开启（且未处于互传状态），打开 **KernelSU（或 APatch / Magisk）管理器** → 模块 → 从本地安装 `{zip_name}`（模块会自动校验内核与固件、升级原厂签名 `MIShare.apk`、安装 `Owly-XOS-Compat.apk` 并部署后台守护）。
2. **启用 LSPosed 模块**：打开 **LSPosed 管理器** → 模块 → 开启 **「Owly-XOS 小米互传兼容」**。
   - 模块已内置默认推荐作用域，首次开启会自动勾选 **`小米互传`（`com.miui.mishare.connectivity`）** 与 **`Intent Resolver / 系统分享`（`com.android.intentresolver`）**，保持这两个勾选即可（切勿勾选 `系统框架 android`）。
3. **重启手机**：重启一次使 LSPosed Hook 对系统分享与小米互传完整生效。

## 4. 使用说明与注意事项

- **接收 Apple 设备文件**：下拉控制中心开启「小米互传」（并在互传设置中允许「所有人」发现），Apple 设备隔空投送即可在约 1.5–6 秒内搜到小米 14 并高速投送（小米超级岛弹窗接收）。
- **向 Apple 设备发送文件**：在系统相册或文件管理点击分享 → 选择顶栏「小米互传」或应用列表中的「隔空投送 / 小米互传」，搜索到 Apple 设备头像后点击发送。
- **无线电独占与自动重连**：在互传收发会话开启期间，`owly-xos` 会暂时接管无线电运行于 5 GHz 149 信道 VHT80 2SS 高速模式；关闭互传或收发结束后约 3–5 秒会自动恢复原厂驱动并重连原 Wi-Fi。
- **卸载方法**：在 KernelSU / Magisk 中直接移除模块并重启，并在桌面/应用管理中卸载 `Owly-XOS 小米互传兼容` 即可（如需回退系统自带旧版小米互传，可在系统应用管理中对「小米互传」点击「卸载更新」）。
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mishare-apk",
        default=os.environ.get("HOUJI_MISHARE_APK"),
        help="Path to stock platform-signed MIShare.apk (5.4.2_20260923_1315) or set HOUJI_MISHARE_APK",
    )
    parser.add_argument(
        "--compat-apk",
        default=str(DIST_DIR / "Owly-XOS-Compat.apk"),
        help="Path to built Owly-XOS-Compat.apk (default: runtime/owly-xos/dist/Owly-XOS-Compat.apk)",
    )
    parser.add_argument(
        "--blobs-dir",
        default=os.environ.get("HOUJI_BLOBS_DIR", str(REPO / "blobs/local")),
        help="Directory containing audited BYOB kernel/firmware blobs (default: blobs/local)",
    )
    parser.add_argument(
        "--output",
        default=str(DIST_DIR / DEFAULT_ZIP_NAME),
        help=f"Output flashable ZIP path (default: runtime/owly-xos/dist/{DEFAULT_ZIP_NAME})",
    )
    args = parser.parse_args()

    blobs_dir = Path(args.blobs_dir).expanduser().resolve()
    mishare_raw = args.mishare_apk or str(blobs_dir / "MIShare.apk")
    mishare_apk = Path(mishare_raw).expanduser().resolve()
    if not mishare_apk.is_file():
        raise SystemExit(
            f"Missing stock MIShare.apk: {mishare_apk}. "
            "Pass --mishare-apk /path/to/MIShare.apk or set HOUJI_MISHARE_APK."
        )

    compat_apk = Path(args.compat_apk).expanduser().resolve()
    out_zip = Path(args.output).expanduser().resolve()

    manifest = load_manifest()
    qca_ko = resolve_blob(manifest, blobs_dir, "qca_cld3_kiwi_v2")
    ezif_bin = resolve_blob(manifest, blobs_dir, "ezif")
    bridge_ko = resolve_blob(manifest, blobs_dir, "houji_wonder_bridge")
    wonder_ko = resolve_blob(manifest, blobs_dir, "wonder")

    owly_bin = OWLY_DIR / "dist/houji-owly-app"
    wonder_init_bin = OWLY_DIR / "dist/wonder-init"
    xos_bridge_zip = DIST_DIR / "xos-bridge.zip"

    verify_elf_aarch64(owly_bin, "houji-owly-app")
    verify_elf_aarch64(wonder_init_bin, "wonder-init")
    verify_zip_entries(xos_bridge_zip, "xos-bridge.zip", ("classes.dex",))
    verify_zip_entries(
        compat_apk,
        "Owly-XOS-Compat.apk",
        ("AndroidManifest.xml", "classes.dex", "assets/xposed_init"),
    )
    verify_zip_entries(mishare_apk, "MIShare.apk", ("AndroidManifest.xml", "classes.dex"))

    payload_sources: dict[str, tuple[Path, int, bool]] = {
        "xos-bridge.zip": (xos_bridge_zip, 0o644, False),
        "bridge-supervisor.sh": (HERE / "bridge-supervisor.sh", 0o755, True),
        "radio.sh": (HERE / "radio.sh", 0o755, True),
        "houji-owly-app": (owly_bin, 0o755, True),
        "wonder-init": (wonder_init_bin, 0o755, True),
        "qca_cld3_kiwi_v2-three-findings.ko": (qca_ko, 0o644, True),
        "firmware/ezif.bin": (ezif_bin, 0o644, True),
        "radio/houji_wonder_bridge.ko": (bridge_ko, 0o644, True),
        "radio/wonder.ko": (wonder_ko, 0o644, True),
    }

    template_sources: dict[str, tuple[Path, int]] = {
        "META-INF/com/google/android/update-binary": (
            KSU_DIR / "META-INF/com/google/android/update-binary",
            0o755,
        ),
        "META-INF/com/google/android/updater-script": (
            KSU_DIR / "META-INF/com/google/android/updater-script",
            0o644,
        ),
        "module.prop": (KSU_DIR / "module.prop", 0o644),
        "customize.sh": (KSU_DIR / "customize.sh", 0o755),
        "service.sh": (KSU_DIR / "service.sh", 0o755),
        "uninstall.sh": (KSU_DIR / "uninstall.sh", 0o755),
    }
    for arc, (src, _) in template_sources.items():
        if not src.is_file():
            raise SystemExit(f"Missing KSU template {arc}: {src}")

    payload_hashes = {name: sha256_file(src) for name, (src, _, _) in payload_sources.items()}
    installation_sha_text = "".join(f"{sha}  {name}\n" for name, sha in sorted(payload_hashes.items()))

    mishare_sha = sha256_file(mishare_apk)
    compat_sha = sha256_file(compat_apk)
    apks_sha_text = (
        f"{mishare_sha}  MIShare.apk\n"
        f"{compat_sha}  Owly-XOS-Compat.apk\n"
    )

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip = out_zip.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp_zip, "w") as zf:
        for arc, (src, mode) in template_sources.items():
            write_zip_bytes(zf, arc, src.read_bytes(), mode, compress=True)
        for name, (src, mode, compress) in sorted(payload_sources.items()):
            write_zip_bytes(zf, f"payload/{name}", src.read_bytes(), mode, compress=compress)
        write_zip_bytes(zf, "payload/installation.sha256", installation_sha_text.encode(), 0o644, compress=True)
        write_zip_bytes(zf, "apks/MIShare.apk", mishare_apk.read_bytes(), 0o644, compress=False)
        write_zip_bytes(zf, "apks/Owly-XOS-Compat.apk", compat_apk.read_bytes(), 0o644, compress=False)
        write_zip_bytes(zf, "apks/APKS.sha256", apks_sha_text.encode(), 0o644, compress=True)

    tmp_zip.replace(out_zip)
    zip_sha = sha256_file(out_zip)
    sha_file = out_zip.with_name(out_zip.name + ".sha256")
    sha_file.write_text(f"{zip_sha}  {out_zip.name}\n")

    guide_file = out_zip.parent / "SHARE-GUIDE.zh-CN.md"
    guide_file.write_text(build_share_guide(out_zip.name, zip_sha, mishare_sha, compat_sha))

    print(f"Created module ZIP: {out_zip} ({out_zip.stat().st_size:,} bytes)")
    print(f"ZIP SHA-256:        {zip_sha}")
    print(f"Share guide:        {guide_file}")


if __name__ == "__main__":
    main()
