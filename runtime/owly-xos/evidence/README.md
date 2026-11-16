# `owly-xos` 匿名化实测日志与遥测归档（2026-11-15）

本目录保存 `runtime/owly-xos` 在小米 14（`houji`，`OS3.0.307.0.WNCCNXM` / Android 16，内核 `6.1.138`）上搭配官方 `ezif-amss20.bin`、免预绑定多对端硬件 TX A-MPDU（`pt_remote_multi=1`、`pt_tx_ampdu=1`、`pt_remote_reo=2`、`pt_remote_rx_window=256`）与冷启动/快速发现优化后的实机遥测及端到端收发时间线。

## 隐私与匿名化边界

遵循 [`docs/PRIVACY.md`](../../../docs/PRIVACY.md)，本目录所有文件已对个人与周边设备标识执行确定性匿名化替换，**时间戳、字节数、分块进度与内核/用户态性能计数均未改动**：

- 个人 Apple 设备与本机显示名称替换为 `<ApplePeer>`、`<ApplePeer-2>`、`<LocalDevice>`；
- AWDL 临时 MAC 地址映射为本地管理测试地址 `02:00:00:00:00:11`..`02:00:00:00:00:15`（对应 UUID `<peer-uuid-1>`..`<peer-uuid-5>`），本机 `xos0` 随机接口地址映射为 `fe80::200:ff:fe00:101%xos0` / `fe80::200:ff:fe00:102%xos0` / `fe80::200:ff:fe00:103%xos0`，对端 link-local 映射为 `fe80::200:ff:fe00:11%xos0`..`fe80::200:ff:fe00:13%xos0`；
- 随机 AirDrop 服务实例名、会话/连接 UUID、TLS 证书指纹与临时缓冲文件名替换为固定占位符（`<service-tx>`、`<service-rx>`、`<service-peer-1..3>`、`<conn-1..3>`、`<request-uuid>`、`<redacted-sha256>`、`airdrop-upload-<id>.spool`）。

## 文件清单

| 文件 | 说明 |
|---|---|
| [`summary-20261115.json`](summary-20261115.json) | 2026-11-15 全部实测指标结构化汇总：冷启动耗时、16 MiB TCP 裸链路吞吐（Mac `awdl0` ↔ 小米 `xos0`），以及原版 `MIShare.apk` 双向收发 `157,799,892` 字节 MP4 视频的发现与吞吐统计 |
| [`send-session-stats.txt`](send-session-stats.txt) | 原版 `MIShare.apk` 发送 `157.80 MB` 视频至 Apple 手机会话退出前由 `radio.sh` 自动保存的内核 sysfs 快照（`prev-session-stats.txt`） |
| [`receive-session-stats.txt`](receive-session-stats.txt) | 原版 `MIShare.apk` 接收 Apple 手机发来同一个 `157.80 MB` 视频会话退出前的内核 sysfs 快照（`last-session-stats.txt`） |
| [`receive-runner-telemetry.txt`](receive-runner-telemetry.txt) | 接收 `157.80 MB` 视频会话期间 `houji-owly-app` 的用户态运行遥测（动态多对端增删、每 10 秒 `STATS`、`OWLY_RX_GAPS`、`monitor/tap totals`） |
| [`mishare-e2e-timeline.txt`](mishare-e2e-timeline.txt) | 原版 `MIShare.apk` 连续完成 **发送 + 接收** `157.80 MB` 视频的毫秒级匿名化持久日志节选（含冷启动、mDNS/NSD 发现、`/Discover`、`/Ask`、`/Upload` 每 `4.00 MiB` 分块进度与退出恢复 5 GHz Wi-Fi） |
| [`1gb-rx-round1-telemetry.txt`](1gb-rx-round1-telemetry.txt) | 2026-11-15 晚间 `21:49` 第一轮 `1 GiB` AirDrop 接收实测的内核 sysfs 快照与 `owly-xosd` 终态遥测（`NO_ACK status[2]=4,699 / 12,296 = 38.22%`、`OWLY_RX_GAPS 387,611`、`timer late avg 1408–1500 µs`；在第二轮覆盖 `runner.log` 前抓取） |
| [`1gb-rx-session-stats.txt`](1gb-rx-session-stats.txt) | 2026-11-15 `21:53:27–21:56:35` 第二轮 `1 GiB`（`1,073,741,824 B`）AirDrop 接收（`5.71 MB/s`）退出前保存的内核 sysfs 快照（`NO_ACK status[2]=6,157 / 14,282 = 43.11%`、`tries=27,487`、`active=5`） |
| [`1gb-tx-session-stats.txt`](1gb-tx-session-stats.txt) | 2026-11-15 `21:57:41–21:58:31` 同环境 `1 GiB` AirDrop 发送（`20.42 MB/s`）退出前保存的内核 sysfs 快照（`NO_ACK status[2]=2,068 / 161,766 = 1.28%`、`agg_mpdus=155,578`） |
| [`1gb-tx-runner-telemetry.txt`](1gb-tx-runner-telemetry.txt) | 与 `1gb-tx-session-stats.txt` 同轮的 `owly-xosd` 用户态全量遥测（含对端频繁增删、`air_tx_amsdu_msdu=780,591 / mpdu=158,343` 及 `timer late max 19,963 µs / 13,034 µs` 尖峰） |
| [`1gb-mishare-timeline.txt`](1gb-mishare-timeline.txt) | 第二轮 `1 GiB` 接收（`188,011 ms` HTTP `/Upload`、`5.71 MB/s`）与第三轮 `1 GiB` 发送（`52,580 ms` `PERF_TRANSFER`、`20.42 MB/s`）的匿名化 `MIShare` logcat 关键里程碑与分位采样时间线 |
| [`1gb-rx-fixed-runner-telemetry.txt`](1gb-rx-fixed-runner-telemetry.txt) | **修复后**（2026-11-15 `22:52:43–22:53:26`）`1 GiB` AirDrop 接收实测（`24.64 MB/s`）的 `owly-xosd` 匿名化全量遥测（`OWLY_TX_ADDBA=2`、`OWLY_RX_GAPS 387,706`、`4–32 ms` 停顿降至 `17.55 s`） |
| [`1gb-rx-fixed-session-stats.txt`](1gb-rx-fixed-session-stats.txt) | **修复后**同轮 `1 GiB` AirDrop 接收退出前保存的内核 sysfs 快照（`NO_ACK status[2]=2,023 / 138,894 = 1.46%`、`tries=142,805`、`agg_mpdus=136,005`、`max_ppdu_mpdus=64`、`dropped=0`） |
| [`1gb-rx-fixed-mishare-timeline.txt`](1gb-rx-fixed-mishare-timeline.txt) | **修复后**同轮 `1 GiB` 接收的匿名化 `MIShare` logcat 时间线（`/Upload` EOF `43,286 ms`、`PERF_TRANSFER` `duration=43,569 ms`、`speedBps=24,644,628 = 24.64 MB/s`） |

详细技术分析与对照说明见 [`docs/airdrop/34-owly-xos-validation.md`](../../../docs/airdrop/34-owly-xos-validation.md)、审计报告 [`docs/airdrop/35-rx-no-ack-and-latency-audit.md`](../../../docs/airdrop/35-rx-no-ack-and-latency-audit.md) 与回归报告 [`docs/airdrop/36-rx-no-ack-fix-and-verification.md`](../../../docs/airdrop/36-rx-no-ack-fix-and-verification.md)。
