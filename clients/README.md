# 客户端（`clients/`）

[owly-app](owly-app/README.zh-CN.md) 是暂缓维护的独立 Android 测试 App（`app/` 为 Java 界面，`native/` 为独立 App 专属上层协议封装，底层共享 [`../../native/houji-runner/`](../native/houji-runner/) 与 [`../../native/owly/`](../native/owly/)）。

当前主线为 [`runtime/owly-xos/`](../runtime/owly-xos/README.zh-CN.md)（直接赋能系统原版小米互传 `MIShare.apk`），其构建与打包流程完全不依赖本目录。
