# 构建说明

先阅读 [BYOB 输入说明](byob.md)。编译器、SDK、NDK 和原始源码归档可以位于自选路径，通过环境变量传入。

## 1. 驱动三个内核模块（Linux x86_64）

```sh
export HOUJI_KERNEL_ARCHIVE=/path/to/kernel.tar.gz
export HOUJI_WLAN_ARCHIVE=/path/to/wlan.tar.gz
export HOUJI_CLANG_DIR=/path/to/clang-r487747c

bash scripts/build-driver.sh --check
bash scripts/build-driver.sh
```

脚本直接从 [`driver/`](../../driver/) 读取合并后的单一 `wlan/` 覆盖层、`bridge/`、`wonder/`、`configs/` 与 `sources.json`，在 `build/driver/` 全新解包校验并依次构建 `houji_wonder_bridge.ko`、`qca_cld3_kiwi_v2.ko`、`wonder.ko`，经 `llvm-strip --strip-debug` 后输出到 `dist/driver/`（无需 `third_party/` 或反向打补丁）。

重复全新构建前请自行移走或删除 `build/driver/`；脚本遇到已有目录会停止，不自动清理用户文件。预留至少 15 GiB 可用空间。

## 2. 主线 `Owly-XOS`（小米原生互传高速后端，零独立 App 依赖）

`Owly-XOS` 构建链路完全独立于 `clients/owly-app/`：原生守护 `owly-xosd` 仅编译链接 L2 `owly-awdl`（不编译 `owly-airdrop`、`rustls`/`ring` 或独立 App 业务代码），全部产物直接输出到 `runtime/owly-xos/dist/`。

```sh
export ANDROID_NDK_HOME=/path/to/android-ndk
export JAVA_HOME=/path/to/jdk-21
export ANDROID_HOME=/path/to/android-sdk
export BUILD_TOOLS="$ANDROID_HOME/build-tools/35.0.0"

rustup target add aarch64-linux-android

# 一键构建 owly-xosd、wonder-init、xos-bridge.zip、Owly-XOS-Compat.apk
bash scripts/build-xos.sh

# 若已准备好 blobs/local/ 与原厂签名 MIShare.apk，直接打包单文件 KernelSU/APatch/Magisk 刷机包
python3 runtime/owly-xos/package-ksu.py --mishare-apk /path/to/MIShare.apk
```

也可按需分步执行：

- 仅构建原生守护（`owly-xosd` + `wonder-init`）：`bash scripts/build-xos.sh --native-only`（或 `bash runtime/owly-xos/build-native.sh`）
- 仅构建桥接 DEX 与 LSPosed 兼容 APK：`bash scripts/build-xos.sh --java-only`
- 一步完成构建 + KSU 刷机包打包：`bash scripts/build-xos.sh --mishare-apk /path/to/MIShare.apk`

## 3. （可选/暂缓）独立 `Owly AirDrop` App

仅在需要构建暂缓的独立客户端 `Owly-AirDrop.apk`（`org.houji.owly`）时执行：

```sh
export ANDROID_NDK_HOME=/path/to/android-ndk
export JAVA_HOME=/path/to/jdk-21
export ANDROID_HOME=/path/to/android-sdk
export BUILD_TOOLS="$ANDROID_HOME/build-tools/35.0.0"

bash clients/owly-app/build-native.sh
bash scripts/build-app.sh
python3 runtime/owly-app/build-radio.py
```

独立 App 的原生程序（`houji-owly-app`、`wonder-init`）和 APK 写入 `clients/owly-app/dist/`，Rust 编译缓存分别隔离在 `build/rust-xos/` 与 `build/rust-app/`。APK 使用本地生成的测试签名，密钥不入库；若与手机现有安装签名不同需先卸载旧版。

## 4. 远端构建与安装边界

将本仓库同步到任意 Linux `x86_64` 主机目录，设置上述环境变量即可运行相同构建入口；`archive/legacy-build/` 仅作历史参考。部署到真机前请核对内核版本与符号 CRC 审计要求，参见 [`runtime/owly-xos/README.zh-CN.md`](../../runtime/owly-xos/README.zh-CN.md) 与 [实机验证导航](../../tests/device/README.md)。
