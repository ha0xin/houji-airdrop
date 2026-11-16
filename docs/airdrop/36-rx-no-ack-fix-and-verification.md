# 小米接收方向高 `NO_ACK` 修复实施与 `1 GiB` RX 实机回归验证报告

- 日期：`2026-11-15`（`2026-11-16` 补充修正第 7 节证据口径并记录后续用户态加固）
- 设备与栈：`houji`（小米 14，Android 16 / HyperOS `OS3.0.307.0.WNCCNXM`，内核 `6.1.138`）+ 官方 `S928BXXU6EZIF` `amss20.bin` + `runtime/owly-xos` + 原版 `MIShare`
- 承接报告：[第 35 篇：小米接收方向高 `NO_ACK` 率根因与调试/兼容代码延迟吞吐审计](35-rx-no-ack-and-latency-audit.md)
- 实测结论：**首轮 A1+A2+A3+A4+A5+A7 修复落地并经 `1 GiB`（`1,073,741,824 B`）AirDrop RX 实机闭环验证后，端到端均速从 `5.71 MB/s`（`188.01 s`）飙升至 `24.64 MB/s`（`43.569 s`，HTTP Body EOF `43.286 s / 24.81 MB/s`，后段采样 P50 `27.59 MB/s` / P90 `41.53 MB/s` / 峰值 `50.53 MB/s`），硬件总体 `NO_ACK`（`status[2]`）由 `43.11%` 断崖降至 `1.46%`（`2,023 / 138,894`），各观测点丢失计数均为 `0`。**（`2026-11-16` 后续已继续补齐 `NeighWorker` 非阻塞 Netlink、`KernelPeerSync` 重试、`SkipBudget` 过滤让渡、`writev`、`DELBA`/per-peer TX 队列、`driver` 时槽重检与 `XosBridge` 10s 宽限期，尚未二次重刷真机实测。）

---

## 1. 本次实施的代码与配置修复清单

针对第 35 篇审计报告定位的延迟与开销瓶颈，本次按 **A1 + A2 + A3 + A4 + A5 + A7** 在用户态 Rust 协议栈与启动参数中完成 6 项精准修复（当前工作区改动暂未提交，构建于远端 Linux 构建机）：

| 审计编号 | 修改文件与符号锚点 | 具体改动 | 预期作用 |
|---|---|---|---|
| **A1** | [`node.rs`](../../native/owly/crates/owly-awdl/src/node.rs#L946-L948) (`should_coalesce`)、[`node.rs`](../../native/owly/crates/owly-awdl/src/node.rs#L1055-L1065) | 删除 `|| packet.pure_tcp_ack().is_some()` 强制等待分支，仅对 `>= 512 B`（`AMSDU_COALESCE_MIN_BYTES`）大包执行 `OWLY_COALESCE_US=1000` 软件 A-MSDU 等包；若同批次混入纯 TCP ACK 则立即 flush。保留闭窗队列下的 [`PureTcpAck::supersedes`](../../native/owly/crates/owly-awdl/src/node.rs#L670-L686) 去重覆盖。 | 消除纯 TCP ACK `~2.4–2.5 ms` 人为滞留，使其紧跟空口接收窗尾立即回传。 |
| **A2** | [`android.rs`](../../native/houji-runner/src/android.rs#L570-L728) (`RtnlNeigh`)、[`Tap::peer_up` (`L878-L909`)](../../native/houji-runner/src/android.rs#L878-L909)、[`Tap::peer_down` (`L911-L920`)](../../native/houji-runner/src/android.rs#L911-L920) | 引入纯 `libc` 持久 `NETLINK_ROUTE` 套接字 `RtnlNeigh` 并缓存 `xos0` `ifindex`，通过零堆分配栈缓冲直接下发 `RTM_NEWNEIGH` / `RTM_DELNEIGH`，彻底移除事件循环热路径上 `/system/bin/ip` `fork+exec`（启动阶段 `Tun::open`/`Tap::open` 保留一次性初始化）。 | 消除对端上下线导致的 `13–20 ms` 单线程事件循环阻塞尖峰。 |
| **A3** | [`node.rs`](../../native/owly/crates/owly-awdl/src/node.rs#L236-L238)、[`node.rs`](../../native/owly/crates/owly-awdl/src/node.rs#L735-L742) | 删除 `BURST_PAUSE_US = 500` 强制定时器休眠，当单轮发送触及 `burst_limit` 时置 `self.resume = Some(now)` 直接让渡事件循环一轮，不再白等 `1 ms` 级别定时器轮周期。 | 消除高突发发送场景下的人工强制停顿。 |
| **A4** | [`driver.rs`](../../native/owly/crates/owly-awdl/src/driver.rs#L39-L83)、[`driver.rs`](../../native/owly/crates/owly-awdl/src/driver.rs#L422-L493)、[`android.rs`](../../native/houji-runner/src/android.rs#L400-L424) | ① 从 `tokio::select!` 中移除 `biased;`，避免持续 RX 突发饿死 `sleep_until(wake)` 定时分支；<br>② 为 `Air` / `Netif` 增加非阻塞 `try_recv()`（`DRAIN_BATCH = 32`，`Monitor`/`Tun`/`Tap`/`HostNetif` 均实现），每次唤醒有界批量排空就绪报文并在每轮循环结尾统一调用 `service_timer()` 与 `flush_retries()`。 | 消除单包 `AsyncFd` 反复调度与定时饥饿，让一批就绪报文及时聚合或覆写去重。 |
| **A5** | [`android.rs`](../../native/houji-runner/src/android.rs#L350-L368)、[`Monitor::send` (`L425-L445`)](../../native/houji-runner/src/android.rs#L425-L445) | 新增零堆分配切片扫描函数 `count_amsdu_subframes()` 直接读取 QoS A-MSDU 子帧长度步进计数，替换 `Monitor::send` 中对每个 TX A-MSDU 调用 `frame::parse` + `frame::msdus`（含 `Vec` 分配）的二次全量解析。 | 消除大文件发送热路径上十余万次冗余解析与临时堆分配。 |
| **A7** | [`radio.sh`](../../runtime/owly-xos/radio.sh#L319-L322) | 将 `HOUJI_OWLY_TX_ADDBA` 由 `1`（`win=64`）调整为 `2`（`win=256`，与驱动 `pt_remote_rx_window=256` 对齐）。 | 解除出向软件 ADDBA `win=64` 保守限制。 |

同步更新单元测试 [`tests/node.rs`](../../native/owly/crates/owly-awdl/tests/node.rs#L722-L796)，分别验证：
- 开窗态纯 TCP ACK 立即发送（`pure_tcp_ack_sends_immediately_when_slot_is_open`）；
- 闭窗等待态累积纯 TCP ACK 原地覆盖去重（`cumulative_pure_tcp_acks_coalesce_and_supersede_while_waiting_for_slot`）；
- 闭窗等待态 `DUP` / `SACK` ACK 保留并合并发送（`duplicate_and_sack_tcp_acks_are_kept_and_aggregated_while_waiting_for_slot`）；
- 临近闭窗纯 TCP ACK 立即冲刷不遗留（`pure_tcp_ack_near_slot_close_flushes_immediately_instead_of_stranding`）。

---

## 2. 构建验证与装机版本边界

1. **远端 Linux 构建机 Rust 测试全通过**：
   - `owly-awdl`（`--features driver`）：`src/lib.rs` `38 passed`、`tests/driver.rs` `5 passed`、`tests/node.rs` `28 passed`、`tests/simulation.rs` `5 passed`（全部 `0 failed`）。
   - `houji-runner`：`14 passed; 0 failed`。
2. **远端 Linux 构建机 Android `aarch64` Release 交叉编译产物与部署核验**：
   - [`build-native.sh`](../../runtime/owly-xos/build-native.sh) 编译产出并验证设备 `/data/local/tmp/xos/owly/` 运行副本 SHA-256 完全一致：
     - `owly-xosd`：`424fa3789076ed21bbe406d6dca9291a06980628c7e86a32fb02f2cae34edb6c`
     - `wonder-init`：`549eb29e2a3a977cd4a472bdde0cdadd836e4865f720608b8435cf18e820b742`
     - `radio.sh`：`2eb306ea4b27c8627eff412dffeb4ca2de71beec93dc0538dec8bfd5f20a0c3f`（含 `HOUJI_OWLY_TX_ADDBA=2`）
   - KernelSU 模块安装并整机重启完成（`sys.boot_completed=1`、`XosBridge` 与 `bridge-supervisor.sh` 正常常驻）后完成下方 `1 GiB` RX 实测。

---

## 3. `1 GiB` AirDrop RX 修复前后实机对比

> 测试载荷仍为同一 `1,073,741,824 B`（`1 GiB`）文件，方向均为 `Apple 手机 → 小米 14 (MIShare)`；注：本次实测现场周边仍有其它未完全离线 AWDL 设备（传输期 `active=2`，传输完第 3 台对端上线至 `active=3`），并非极限单对端理想环境，但干扰已显著低于修复前（`active=5` 频繁增删）。

| 对比维度 | 修复前 `1 GiB` RX Round 1 (`21:49`) | 修复前 `1 GiB` RX Round 2 (`21:53`) | **本次修复后 `1 GiB` RX (`22:52–22:53`)** | 对照：`1 GiB` TX Round 3 (`21:57`) |
|---|---:|---:|---:|---:|
| **端到端总耗时** | 未录完整毫秒（同类慢速） | `188.011 s` `/Upload` / `188.190 s` `PERF_TRANSFER` | **`43.286 s` `/Upload` / `43.569 s` `PERF_TRANSFER`** | `52.580 s` `PERF_TRANSFER` |
| **端到端均速** | — | `5.71 MB/s` (`5,705,626 B/s`) | **`24.64 MB/s`** (`24,644,628 B/s`，Body **`24.81 MB/s`**) | `20.42 MB/s` (`20,421,107 B/s`) |
| **分段速率** | — | P50 `6.20` / Peak `16.13` / Min `2.25 MB/s` | **P50 `27.59` / P90 `41.53` / Peak `50.53` / Min `12.83 MB/s`**（87 个采样） | P50 `23.65` / P90 `33.04` / Peak `35.55 MB/s` |
| **活跃对端数 (`active`)** | 峰值 `3` / 稳态 `2` | `5`（频繁切换） | **`2`（传输完升至 `3`）** | `3`–`5` |
| **硬件 `completed`** | `12,296` | `14,282` | **`138,894`** | `161,766` |
| **硬件 `valid`（速率元数据有效）** | `7,596` (`61.77%`) | `8,124` (`56.88%`) | **`136,871` (`98.54%`)** | `159,698` (`98.72%`) |
| **硬件 `NO_ACK (status[2])`** | `4,699` (**`38.22%`**) | `6,157` (**`43.11%`**) | **`2,023` (`1.46%`)** | `2,068` (**`1.28%`**) |
| **尝试比 (`tries/completed`)** | `1.24` (`15,217`) | `1.92` (`27,487`) | **`1.028` (`142,805`)** | `1.08` (`173,997`) |
| **硬件 TX A-MPDU 聚合** | `741` MPDUs | `560` MPDUs (`max=10`) | **`136,005 / 136,937` (`99.3%`, `6,589` PPDUs, `max=64`)** | `155,578` (`96.2%`) |
| **`tx_action` + `tx_multicast`** | `4,537` + `~20` | — | **`1,955` + `29` = `1,984`** | `1,254` + `24` |
| **用户态 `tx_amsdu` (MSDU)** | `257` (`1,582` MSDUs) | — | **`8` (`39` MSDUs)** | `158,343` (`780,591` MSDUs) |
| **`OWLY_RX_GAPS` `4–32 ms` 桶** | `60.15 s` (`4,421` 次) | — | **`17.55 s` (`1,727` 次：`11.91 + 5.64 s`)** | — |
| **`OWLY_RX_GAPS` `128–256+ ms` 桶** | `44.62 s` (`240` 次) | — | **`3.49 s` (`7` 次：`4 + 3`)** | — |
| **传输期 `timer late` (avg/max)** | `1,408–1,500 µs` / `9,445 µs` | — | **`1,052–1,319 µs` / `2,592–3,958 µs`** | `868–1,343 µs` / `19,963 µs` |
| **观测点错误/TSFT 缺失计数** | `0`（观测点计数） | `0`（观测点计数） | **`0`（`missing=0`/`dropped=0`/`backpressure=0`/`host_delayed=0`）** | `0` |

---

## 4. 关键物理与调度变化机制解析

1. **总体 `status[2] (NO_ACK)` 降至 `1.46%` ＆ 硬件 A-MPDU 深度显著回升**：
   - 在 [1gb-rx-fixed-runner-telemetry.txt#L109-L111](../../runtime/owly-xos/evidence/1gb-rx-fixed-runner-telemetry.txt#L109-L111) 与 [1gb-rx-fixed-session-stats.txt#L11-L16](../../runtime/owly-xos/evidence/1gb-rx-fixed-session-stats.txt#L11-L16) 中：内核总 `completed=138,894`、`status[2] (NO_ACK)=2,023`（总体 **`1.46%`**）、`tries / completed = 1.028`；用户态退出前统计 `tx_action=1,955` + `tx_multicast=29` = `1,984`。说明无需确认的广播/控制帧在总体 TX 中占有相当比重，但因驱动未按「帧类型 × 目的地址 × 完成状态」交叉统计，且 sysfs 快照取于 `stop_runner` 前、userspace totals 取于退出时（统计窗口不完全对齐），此处**不通过总减法外推单播具体 `< 0.05%` 失确认数**，分类型统计留待后续补齐观测后确认。
   - 同时，由于 `tx_unicast` 反向 ACK 无需再在 `should_coalesce` 被按 `1,000 µs` 规则等待合包，软件合包 `tx_amsdu` 从 `257` 降至 `8`，反向 ACK 能够及时落入 `ezif` 硬件 TX 队列并由固件打包为 `136,005` 个 `VHT80 MCS9 SGI LDPC` A-MPDU（`99.3%` 硬件聚合比例、`6,589` 个 PPDU、`max_ppdu_mpdus=64`）。
2. **`OWLY_RX_GAPS` 高区间时长随总时长同步收敛（`43.57 s` vs 原 `~188 s`）**：
   - 对比第 35 篇 Round 1 与 [1gb-rx-fixed-runner-telemetry.txt#L110](../../runtime/owly-xos/evidence/1gb-rx-fixed-runner-telemetry.txt#L110)：`4–32 ms` 间隔桶累计时长由 `60.15 s`（`4,421` 次）降至 **`17.55 s`**（`1,727` 次），`128–256+ ms` 桶由 `44.62 s`（`240` 次）降至 **`3.49 s`**（`7` 次）。注意前后两次传输总耗时和周边活跃设备数量（`active=2` vs 峰值 `3–5`）不同，桶内也包含正常协议周期与对端调度间隔，因而不能将原始差值当作单纯“代码净节省秒数”。
   - 已观测路径记为 `matched=387,706`、`equal=365,692`、`missing=0`（TSFT 无缺失/零值跳变）、`backwards=0`、`host_delayed=0`（`radio<2 ms && host>=16 ms` 无命中）、`air_rx_kernel_drops=0`、`pt_rx_msdu_stats dropped=0`、`tap_delivery_drops=0`。
3. **无 13–20 ms 事件循环尖峰 & `HOUJI_OWLY_TX_ADDBA=2` 控制帧发出**：
   - [1gb-rx-fixed-runner-telemetry.txt#L10,L17,L27-L38](../../runtime/owly-xos/evidence/1gb-rx-fixed-runner-telemetry.txt#L10-L38) 显示 `OWLY_TX_ADDBA=2` 且 ADDBA Req 已按 `params=0x4003`（`win=256`、`amsdu=1`）发出；不过同一日志中 `rx_resp=0`，尚不能直接归因这次 RX 均速提升归功于对端软件 ADDBA 响应生效（`HOUJI_OWLY_TX_ADDBA=2` 在此场景的作用需结合对端实际 BA 协商与固件行为继续核实）。
   - 核心传输窗内（`L39-L83`，`delivered 16 -> 781,009`）采样到的纯定时器唤醒 `timer late avg` 为 **`1,052–1,319 µs`**、`max` 为 **`2,592–3,958 µs`**（需注意本次前后 `select!` 与统计入口发生过调整，且旧路径在 `Input::Frame` 推进时钟时未记录 overdue 迟到样本，因此均值不宜直接横向等价为同口径抖动降幅）；传输结束后第 3 台对端上线时段（`L85-L107`）出现的 `max 9,141 µs` / `12,804 µs` 与 `pt_peer` 上线同步出现在相近时间窗，但 `peer_ctl` 已运行于独立线程，该峰值更可能来自并发调度/锁或驱动资源竞争，缺少直接同步阻塞因果证据。

---

## 5. 残余瓶颈与下一步建议

1. **残余间隙（仍有向 `30+ MB/s` 迈进空间）**：
   - 本次传输中周边仍存在未关 Wi-Fi 总开关的其余 Apple 设备（开场 `active=2`，传完上线第 3 台），导致 `OWLY_RX_GAPS` 中 `64–128 ms`（`37` 次，`2.82 s`）和 `128+ ms`（`7` 次，`3.49 s`）仍有合计 `6.31 s` 长间隔，`4–32 ms` 也有 `17.55 s`。若现场严格保持非测试 Apple 设备进入「设置 → Wi-Fi 总开关关闭」（仅留 `active=1`），预期均速可进一步接近后段 P50（`~27.6 MB/s`）甚至更高。
2. **Tokio ~1 ms 定时器轮基准量化延迟（`1,052–1,319 µs`）**：
   - 去掉 `biased;` 并启用 `DRAIN_BATCH=32` 后，单线程 `timer late avg` 从 `1.4–1.5 ms` 回落到 `~1.05–1.32 ms`，其余延迟主要来自 Tokio 时间轮 `~1 ms` 槽粒度（以及内核定时器唤醒抖动），后续可按第 35 篇 **A6**（栈缓冲/池化零拷贝 + `writev` TAP 投递）与 `OWLY-XOS-TODO` `P1`（`IFF_VNET_HDR` + 批量 `sendmmsg`/`recvmmsg`）继续压低 CPU 占用与单核延迟。
3. **补充交叉验证**：
   - 建议在当前同固件与同套改动下补跑一轮 **`1 GiB` 小米 → Apple 发送（TX）** 实测并确认 `HOUJI_OWLY_TX_ADDBA=2` 下发送端吞吐、A-MSDU 密度（`air_tx_amsdu_msdu / mpdu`）与 `NO_ACK` 无回退且继续提升，完成后即可统一梳理并提交本次源码与文档变更。

---

## 6. 关联匿名化证据与源码索引

- **本次修复后 `1 GiB` RX 匿名化证据（`runtime/owly-xos/evidence/`）**：
  - [`1gb-rx-fixed-runner-telemetry.txt`](../../runtime/owly-xos/evidence/1gb-rx-fixed-runner-telemetry.txt)（`22:52:43–22:53:26` `owly-xosd` 全量遥测、`OWLY_RX_GAPS` 与 `monitor/tap totals`）
  - [`1gb-rx-fixed-session-stats.txt`](../../runtime/owly-xos/evidence/1gb-rx-fixed-session-stats.txt)（退出前内核 sysfs 快照：`completed=138,894`、`NO_ACK=2,023 (1.46%)`、`agg_mpdus=136,005`、`max_ppdu_mpdus=64`）
  - [`1gb-rx-fixed-mishare-timeline.txt`](../../runtime/owly-xos/evidence/1gb-rx-fixed-mishare-timeline.txt)（匿名化 `MIShare` `/Upload` EOF `43,286 ms` 与 `PERF_TRANSFER` `duration=43,569 ms`、`speedBps=24,644,628` 日志时间线）
  - [evidence 总索引](../../runtime/owly-xos/evidence/README.md)
- **前置审计与本次改动源码**：
  - [第 35 篇：高 `NO_ACK` 率根因与调试/兼容代码延迟吞吐审计](35-rx-no-ack-and-latency-audit.md)
  - [`node.rs`](../../native/owly/crates/owly-awdl/src/node.rs#L236-L1065)（`should_coalesce`、去除 `BURST_PAUSE_US`）
  - [`driver.rs`](../../native/owly/crates/owly-awdl/src/driver.rs#L39-L493)（去除 `biased;`、`Air`/`Netif::try_recv`、`DRAIN_BATCH=32`、`service_timer`）
  - [`tests/node.rs`](../../native/owly/crates/owly-awdl/tests/node.rs#L722-L796)（pure TCP ACK 回归单测）
  - [`android.rs`](../../native/houji-runner/src/android.rs#L350-L975)（`count_amsdu_subframes`、`RtnlNeigh`、`try_recv`）
  - [`radio.sh`](../../runtime/owly-xos/radio.sh#L319-L322)（`HOUJI_OWLY_TX_ADDBA=2`）
