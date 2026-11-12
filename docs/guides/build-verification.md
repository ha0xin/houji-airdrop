# 独立构建验证（2026-11-12）

本轮验证对象是新构建入口，不是手机无线行为。未运行 ADB、装载模块或安装 APK。

## 环境与隔离方式

- 将仓库源文件复制到 Linux 主机的独立目录 `/work/houji-airdrop-check/`，没有复制旧构建输出、模块、`dist/` 或 `.git/`。
- BYOB 输入使用已有的 MiCode 原始 kernel/WLAN tar.gz 和 Android Clang；NDK、Rust 工具链通过环境变量提供。输入缓存可以放在任意目录，不是脚本内置路径。
- 驱动入口自行解包，重新 modules_prepare、构建 bridge/WLAN/wonder；没有读取旧的 kernel out、参考模块或任务 Git 索引。
- App APK 在本机使用显式指定的 SDK/JDK 构建，已完成签名验证。

## 修复的隐藏依赖

1. App 构建脚本原先硬编码临时 SDK 和远端主机，现改为显式环境变量、本机编译。
2. `wonder-init-2ss.c` 原先引用远端目录布局下的 `probe.c`，现直接引用仓库内正式位置。
3. 两个必需的 FW API 头文件原先被忽略，现允许入库。
4. 首次全新驱动构建暴露 RX 容量定义缺失。与原实验源码树对照后，补齐以下五个遗漏的前置文件，放入 `driver/wlan/`；后续构建不再读取那棵实验树：
   - `qca-wifi-host-cmn/dp/wifi3.0/be/dp_be.c`
   - `qca-wifi-host-cmn/dp/wifi3.0/dp_pt_rx_phy.h`
   - `qca-wifi-host-cmn/dp/wifi3.0/dp_rx_buffer_pool.c`
   - `qca-wifi-host-cmn/wlan_cfg/wlan_cfg.c`
   - `qca-wifi-host-cmn/wlan_cfg/wlan_cfg.h`

这些文件来自整理前使用的 `/work/houji-build/src/three-findings/wlan` 源码基线。现有覆盖文件没有被旧树覆盖；补齐后重新从原始归档验证。没有新增锁文件或 SHA 清单。

## 结果

- Android APK：构建与签名验证通过（Java 8 目标及 deprecated API 警告保留）。
- Rust App 后端：release / aarch64-linux-android 构建通过。
- wonder-init：使用 NDK 独立编译通过。
- radio.sh：生成与 shell 语法检查通过。
- 驱动三个模块：从原始归档全新构建通过，产物为 `houji_wonder_bridge.ko`、`qca_cld3_kiwi_v2.ko`、`wonder.ko`，入口返回 `DRIVER_BUILD_PASS`。
- 仓库源码中的旧路径兼容链接：0；当前导航链接、Python 和 shell 语法检查通过。

编译成功与目标设备 ABI/实机验收分开。本轮没有把新构建产物接入旧安装器，也没有修改手机。

## 输出与保留的警告

远端成功构建的输出位于 `/work/houji-airdrop-check/dist/driver/` 和 `clients/owly-app/dist/`。本机 APK 位于 `clients/owly-app/dist/Owly-AirDrop.apk`。原始构建日志及本地构建产物随后已按隐私清理要求移除；本页保留清理前的验证结论，路径均为匿名化示例。

WLAN 链接保留上游两个函数的栈帧超限警告；wonder 的 BTF 因没有 vmlinux 跳过生成。这些没有导致编译失败，也未在本轮修改驱动逻辑处理。首轮失败的临时构建目录已清理，成功构建工作区保留。
