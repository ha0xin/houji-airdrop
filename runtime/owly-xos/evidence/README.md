# `owly-xos` 匿名化实测日志与遥测归档（2026-11-15）

本目录保存 `runtime/owly-xos` 在小米 14（`houji`，`OS3.0.307.0.WNCCNXM` / Android 16，内核 `6.1.138`）上搭配官方 `ezif-amss20.bin`、免预绑定多对端硬件 TX A-MPDU（`pt_remote_multi=1`、`pt_tx_ampdu=1`、`pt_remote_reo=2`、`pt_remote_rx_window=256`）与冷启动/快速发现优化后的实机遥测及端到端收发时间线。

## 隐私与匿名化边界

遵循 [`docs/PRIVACY.md`](../../../docs/PRIVACY.md)，本目录所有文件已对个人与周边设备标识执行确定性匿名化替换，**时间戳、字节数、分块进度与内核/用户态性能计数均未改动**：

- 个人 Apple 设备名称替换为 `<ApplePeer>`；
- AWDL 临时 MAC 地址映射为本地管理测试地址 `02:00:00:00:00:11`、`02:00:00:00:00:12`、`02:00:00:00:00:13`，本机 `xos0` 随机接口地址映射为 `fe80::200:ff:fe00:101%xos0` / `fe80::200:ff:fe00:102%xos0`，对端 link-local 映射为 `fe80::200:ff:fe00:13%xos0`；
- 随机 AirDrop 服务实例名、会话 UUID、TLS 证书指纹与临时缓冲文件名替换为固定占位符（`<service-tx>`、`<service-rx>`、`<request-uuid>`、`< redacted-sha256 >`）。

## 文件清单

| 文件 | 说明 |
|---|---|
| [`summary-20261115.json`](summary-20261115.json) | 2026-11-15 全部实测指标结构化汇总：冷启动耗时、16 MiB TCP 裸链路吞吐（Mac `awdl0` ↔ 小米 `xos0`），以及原版 `MIShare.apk` 双向收发 `157,799,892` 字节 MP4 视频的发现与吞吐统计 |
| [`send-session-stats.txt`](send-session-stats.txt) | 原版 `MIShare.apk` 发送 `157.80 MB` 视频至 Apple 手机会话退出前由 `radio.sh` 自动保存的内核 sysfs 快照（`prev-session-stats.txt`） |
| [`receive-session-stats.txt`](receive-session-stats.txt) | 原版 `MIShare.apk` 接收 Apple 手机发来同一个 `157.80 MB` 视频会话退出前的内核 sysfs 快照（`last-session-stats.txt`） |
| [`receive-runner-telemetry.txt`](receive-runner-telemetry.txt) | 接收 `157.80 MB` 视频会话期间 `houji-owly-app` 的用户态运行遥测（动态多对端增删、每 10 秒 `STATS`、`OWLY_RX_GAPS`、`monitor/tap totals`） |
| [`mishare-e2e-timeline.txt`](mishare-e2e-timeline.txt) | 原版 `MIShare.apk` 连续完成 **发送 + 接收** `157.80 MB` 视频的毫秒级匿名化持久日志节选（含冷启动、mDNS/NSD 发现、`/Discover`、`/Ask`、`/Upload` 每 `4.00 MiB` 分块进度与退出恢复 5 GHz Wi-Fi） |

详细技术分析与对照说明见 [`docs/airdrop/34-owly-xos-validation.md`](../../../docs/airdrop/34-owly-xos-validation.md)。
