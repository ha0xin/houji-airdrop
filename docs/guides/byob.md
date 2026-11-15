# BYOB：自行提供外部输入

BYOB（Bring Your Own Blobs）表示仓库不附带完整闭源系统包和固件。构建另需自行准备公开源码归档与工具链。**编译不需要从手机拉取文件，也不需要原 android 工作区或旧远端实验输出。**

本轮不新增版本锁定或 SHA 清单，沿用上游原有来源说明及校验。

## 编译输入

| 构建目标 | 自备输入 | 配置 |
|---|---|---|
| 驱动三个模块 | Linux x86_64；Python 3.12+；make、flex、bison、perl、pahole、libelf/OpenSSL 开发包；Android Clang | `HOUJI_CLANG_DIR` 指向包含 `bin/clang`、`bin/ld.lld`、`bin/llvm-strip` 的目录；原配方使用 clang-r487747c |
| 驱动原始源码 | MiCode kernel 与 WLAN 原始 `tar.gz`，每个包内有一个顶层目录 | `HOUJI_KERNEL_ARCHIVE`、`HOUJI_WLAN_ARCHIVE`；来源记录于 [`driver/sources.json`](../../driver/sources.json) |
| `Owly-XOS` 原生守护 | Rust/Cargo、`aarch64-linux-android` target、Android NDK | `ANDROID_NDK_HOME`；原验证环境 NDK r30。仅依赖 L2 `owly-awdl`，无需独立 App 协议栈 |
| `Owly-XOS` 桥接与兼容 APK | JDK 21、Android SDK API 35、build-tools（`aapt2`/`d8`/`apksigner`）、`zip` | `JAVA_HOME`、`ANDROID_HOME`、`BUILD_TOOLS`；也可用 `SDK_STUB` 明确指定 `android.jar` |

源码版本与下载来源记录在 [`driver/sources.json`](../../driver/sources.json)（MiCode kernel commit `8c66ad18...` 与 MiCode WLAN commit `a4da9e3b...`）。[`scripts/build-driver.py`](../../scripts/build-driver.py) 直接在解压目录校验未修改基线并合入 [`driver/wlan/`](../../driver/wlan/) 单一完整覆盖层，不再依赖 `third_party/` 或反向补丁。

## 手机运行与模块打包输入（不属于源码编译依赖）

- **主线 `Owly-XOS`（[`runtime/owly-xos/runtime-manifest.json`](../../runtime/owly-xos/runtime-manifest.json)）**：
  - 三星 SM-S928B EZIF 的 `amss20.bin`：默认放在 `blobs/local/ezif-amss20.bin`，或设置 `HOUJI_EZIF_FIRMWARE`。
  - 经 567 符号 CRC 审计的三个内核模块：默认放在 `blobs/local/`（`qca_cld3_kiwi_v2-three-findings.ko`、`houji_wonder_bridge.ko`、`wonder.ko`）。
  - 打包 KernelSU 刷机包另需提供小米原厂平台签名 `MIShare.apk`（`5.4.2`）：默认 `blobs/local/MIShare.apk`，或传 `--mishare-apk` / `HOUJI_MISHARE_APK`。
- **暂缓的独立 App 路线（[`clients/owly-app/runtime-manifest.json`](../../clients/owly-app/runtime-manifest.json)）**：除上述模块与 EZIF 外，扫描阶段另使用 `blobs/local/dzf2-amss20.bin`（或 `HOUJI_DZF2_FIRMWARE`）。
- 原有固件提取材料见[获取目录](../../experiments/tx-ampdu-investigation/firmware-acquisition/)。默认不自动下载 ROM，也不运行手机操作。

`blobs/local/` 被 Git 忽略。固件与原厂系统 APK 不随源码发布；安装器与打包器的 SHA-256 硬校验保留，不能仅靠改文件名替换不同固件或未审计模块。
