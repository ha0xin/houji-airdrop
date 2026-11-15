# 构建说明

先阅读 [BYOB 输入说明](byob.md)。编译器、SDK、NDK 和原始源码归档可以位于自选路径，通过环境变量传入。

## 驱动（Linux x86_64）

```sh
export HOUJI_KERNEL_ARCHIVE=/path/to/kernel.tar.gz
export HOUJI_WLAN_ARCHIVE=/path/to/wlan.tar.gz
export HOUJI_CLANG_DIR=/path/to/clang-r487747c
bash scripts/build-driver.sh --check
bash scripts/build-driver.sh
```

脚本在 `build/driver/` 全新解包源码，应用上游原有覆盖层、独占模式转换和 `driver/wlan/` 当前覆盖层；独立生成内核准备输出、构建 bridge、WLAN、wonder。产物位于 `dist/driver/`。不会复制历史 `2ss-rx-phy-wlan` 输出，也不要求工作树已有 Git 索引。

重复全新构建前请自行移走或删除 `build/driver/`；脚本遇到已有目录会停止，不自动清理用户文件。预留至少 15 GiB 可用空间；实际需求随工具链与源码而异。

`--check` 只检查依赖是否可用；真正的源码检查和编译发生在不带 `--check` 时。模块配置仍来自已有移植配方。

## App 原生后端与 APK

```sh
export ANDROID_NDK_HOME=/path/to/android-ndk
rustup target add aarch64-linux-android
bash scripts/build-userspace.sh

export JAVA_HOME=/path/to/jdk-21
export ANDROID_HOME=/path/to/android-sdk
export BUILD_TOOLS="$ANDROID_HOME/build-tools/35.0.0"
bash scripts/build-app.sh
python3 runtime/owly-app/build-radio.py
```

原生程序和 APK 写入 `clients/owly-app/dist/`，Rust 缓存写入 `build/rust-app/`。原生构建同时编译仓库里的 `wonder-init-2ss.c`，不读取旧远端任务产物。无线脚本生成器使用仓库内的已保存 harness 模板，不访问设备。

APK 使用本地生成的测试签名，密钥不入库。其签名可能与手机现有安装不同，若无法覆盖升级需先卸载旧版。

## 远端构建

将本仓库复制到自行选择的 Linux 主机目录，在该目录设置上述环境变量并运行同样命令。只需准备 BYOB 输入和系统依赖，无需复制旧任务目录。`archive/legacy-build/` 保存早期主机配方与记录，属于历史参考，不是新入口的运行依赖。

## 安装与验证边界

本节只定义构建，不执行安装。`clients/owly-app/install.py` 仍使用旧实验审计、实验产物及已有手机运行环境；它不是当前独立构建结果的一键安装器。部署前需要单独完成 ABI 审计、运行时配套和恢复验证。参见[实机验证导航](../../tests/device/README.md)。

构建主机与目标手机是不同概念：入口只依赖所列 OS/架构及工具链，不绑定任何个人开发机；`houji` / `kiwi_v2` 仍表示本项目产物适配的 Android 硬件，不能当作主机构建限制删除。
