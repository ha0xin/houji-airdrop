# 小米接收方向高 `NO_ACK` 率根因与调试/兼容代码延迟吞吐审计

- 日期：`2026-11-15`（`2026-11-16` 按连锁审计复核修正指标口径并补齐用户态调度/控制面修复）
- 设备与栈：`houji`（小米 14，Android 16 / HyperOS `OS3.0.307.0.WNCCNXM`，内核 `6.1.138`）+ 官方 `S928BXXU6EZIF` `amss20.bin` + `runtime/owly-xos` + 原版 `MIShare`
- 后续修复与回归验证：已按本文 **A1 + A2 + A3 + A4 + A5 + A7**（及 `2026-11-16` 补充的 `DELBA`/per-peer TX 队列/非阻塞 Netlink & `peer_ctl`/TX 冲刷窗口重检/`writev`/过滤让渡/`XosBridge` 等修复）推进用户态改造；早期第一批改动在远端重编装机实测中同载荷 `1 GiB` RX 均速从 `5.71 MB/s` 升至 **`24.64 MB/s`**、总 `NO_ACK` 从 `43.11%` 降至 **`1.46%`**（详情见 **[第 36 篇：高 `NO_ACK` 修复实施与 `1 GiB` RX 回归验证报告](36-rx-no-ack-fix-and-verification.md)**）。
- 触发问题：同环境下连续实测 `1 GiB`（`1,073,741,824 B`）传输时，**小米发送（TX）均速 `20.42 MB/s`（峰值 `35.55 MB/s`），而小米接收（RX）仅 `5.71 MB/s`（P50 `6.20 MB/s`）**；已观测计数显示小米侧已知丢包/TSFT 缺失统计均为 `0`，但小米反向发送链路的硬件 **`NO_ACK`（`status[2]`）高达 `38.22%–43.11%`**。

---

## 1. 核心结论摘要

1. **瓶颈集中于小米侧反向 TX 触发的高 `NO_ACK`（`status[2]`）**：
   - 两轮 `1 GiB` RX 中，WCN7850 内核 REO RX（`pt_rx_msdu_stats` `dropped=0`）、用户态 `AF_PACKET` 接收（`air_rx_kernel_drops=0`）、`OWLY_RX_GAPS`（`missing=0`，代表连续 TSFT 且无零/缺失时间戳跳变、`host_delayed=0` 覆盖 `radio<2ms && host>=16ms` 特定场景）及 TUN 回注（`tap_delivery_drops=0`）**在各自观测点均计为 `0`**（注：这些计数器不覆盖空口未知丢失或所有下层队列拒绝）。
   - 与此相反，小米发送端硬件完成计数 `pt_rate_stats` 中 `status[2]`（`HAL_TX_TQM_RR_REM_CMD_TX` ← `HTT_TX_FW2WBM_TX_STATUS_TTL`，即 `QDF_TX_RX_STATUS_NO_ACK`）从发送方向的 **`1.28%`**（`2,068 / 161,766`）升至接收方向的 **`38.22%`**（Round 1 `4,699 / 12,296`）和 **`43.11%`**（Round 2 `6,157 / 14,282`），平均每帧尝试次数（`tries / completed`）升至 **`1.92`**。
   - 同期 `air_tx_submitted=12,298` 中包含不需要单播 ACK 确认的广播 AWDL Action 帧（`tx_action=4,537`）与少量多播帧；由于硬件统计未按帧类型×目的地址×完成状态交叉分桶，且 sysfs 快照早于 `stop_runner` 退出前打印的 `totals`，本文仅直接使用总体 `status[2]` 比例 `38.22%–43.11%`，不以总减法估算精确单播失败率。
2. **`OWLY_RX_GAPS` 区间分布显示大量 `4–32 ms` 间隔与 `128–256+ ms` 长间隔并存**：
   - Round 1 `1 GiB` RX 的 `387,611` 次可观测 RX PPDU 间隔中，`128–256+ ms` 长间隔（`240` 次）总时长 **`44.62 s`**，**`4–32 ms` 区间共 `4,421` 次，总时长 `60.15 s`**（注：区间总时长同时包含正常协议时隙/对端窗口编排与拥塞停顿，不能直接视作可 100% 消除的纯开销）。反向 ACK 迟到与高 `NO_ACK` 会耦合恶化对端 TCP 发送窗口停等与重传。
3. **软件层“历史补丁 + 单线程调度 + 同步系统调用”显著抬高了延迟敏感性**：
   - `should_coalesce` 中由早期 `dzf2` 调试补丁引入的 `|| (packet.pure_tcp_ack().is_some() && ...)`（在存在即将到来发射窗口条件时将纯 TCP ACK 纳入 `OWLY_COALESCE_US=1000` 等待），叠加 Tokio `biased;` 在持续 RX 突发时饿死 `Netif` (`Tap`) 收包与 `1 ms` 定时器轮唤醒偏移（`timer late` 仅反映事件循环唤醒迟到，非端到端空中 ACK 时延），增大了纯 TCP ACK 在窄接收窗尾端错窗的概率（同场还存在 `active=5` 对端 + Apple 开启 Infra Wi-Fi 并发）。
   - 同时审计出了 **7 处历史遗留/调试/兼容性高开销代码**（包括 `BURST_PAUSE_US=500`、保守的 `HOUJI_OWLY_TX_ADDBA=1`、单线程内 `fork+exec /system/bin/ip`、`Monitor::send` 对 TX A-MSDU 二次解析、RX 每包 `3× Vec<u8>` 拷贝、`AsyncFd` 逐包唤醒与 `takes_outbound()` 全局阈值），以及 **4 项必须保留的真实协议/硬件兼容项**。

---

## 2. 硬件 `NO_ACK` 定义与四轮实测证据对照

### 2.1 内核 `status[2]` 的真实硬件语义

在驱动 TX 完成路径中，WCN7850 固件通过 WBM 返回 `HTT_TX_FW2WBM_TX_STATUS_TTL`，经 `be` 转换与统计观察器记录如下：

- [dp_be_tx.c (`L449-L458`)](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/be/dp_be_tx.c#L449-L458)：将 `HTT_TX_FW2WBM_TX_STATUS_TTL` 映射为 `ts.status = HAL_TX_TQM_RR_REM_CMD_TX`（枚举值 `2`）。
- [dp_tx.h (`L956-L970`)](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_tx.h#L956-L970)：`HAL_TX_TQM_RR_FRAME_ACKED`（`0`）对应 `QDF_TX_RX_STATUS_OK`，`HAL_TX_TQM_RR_REM_CMD_TX`（`2`）对应 `QDF_TX_RX_STATUS_NO_ACK`（TQM 按最大重传次数/TTL 尝试发送后仍未收到对端 MAC ACK/BlockAck 而移除描述符）。
- [`dp_pt_rate_observer.h` (`L68-L125`)](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_pt_rate_observer.h#L68-L125)：独立统计速率元数据有效性（`ts->valid`，无效记 `invalid`）与完成状态桶 `pt_rates.status[ts->status < 16 ? ts->status : 16]++`。注意 **`valid`（速率元数据有效计数）并不等价于 `status[0]`（MAC ACK 成功）**，某些轮次数值接近/重合纯属样本统计，不应混用定义。

### 2.2 匿名化实测汇总对照表（`157.80 MB` 基线 vs `1 GiB` RX/TX）

> 所有日志与内核快照均已按 [`docs/PRIVACY.md`](../PRIVACY.md) 脱敏，时间戳、字节数与硬件计数器保持原值。

| 实测轮次 | 方向与载荷 | 端到端耗时 / 均速 | 并发对端 (`active`) | `completed` | `valid`（速率有效） | `NO_ACK` (`status[2]`) | `NO_ACK` 占比 | `tries` (`tries/compl`) | `agg_mpdus` | 匿名化证据链接 |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 157.80 MB `win64` 基线 | RX (Apple → 小米) | `11.97 s` / **`13.18 MB/s`**（稳态 `20.35 MB/s`，单块峰值 `41.12 MB/s`） | `1`（单对端干净态） | — | — | — | — | — | — | [summary-20261115.json](../../runtime/owly-xos/evidence/summary-20261115.json#L74-L81) |
| 157.80 MB TX 基线 | TX (小米 → Apple) | `8.398 s` body / **`18.79 MB/s`**（HTTP 200 `16.75 MB/s`） | `2` | `24,043` | `23,308` | `735` | **`3.06%`** | `27,099` (`1.13`) | `22,933` | [send-session-stats.txt](../../runtime/owly-xos/evidence/send-session-stats.txt#L11-L18) |
| 157.80 MB RX `win256` | RX (Apple → 小米) | `23.861 s` / **`6.61 MB/s`**（前半 `3.42` → 后半 `13.00 MB/s`） | `2`–`3` | `8,147` | `6,898` | `1,249` | **`15.33%`** | `9,531` (`1.17`) | `2,509` | [receive-session-stats.txt](../../runtime/owly-xos/evidence/receive-session-stats.txt#L11-L18)、[receive-runner-telemetry.txt](../../runtime/owly-xos/evidence/receive-runner-telemetry.txt) |
| **1 GiB RX Round 1 (`21:49`)** | RX (`1 GiB`) | 未录 MiShare 毫秒值（同类慢速） | 峰值 `3` / 稳态 `2` | `12,296` | `7,596` | **`4,699`** | **`38.22%`** | `15,217` (`1.24`) | `741` | [1gb-rx-round1-telemetry.txt](../../runtime/owly-xos/evidence/1gb-rx-round1-telemetry.txt) |
| **1 GiB RX Round 2 (`21:53`)** | RX (`1,073,741,824 B`) | `188.01 s` `/Upload` / **`5.71 MB/s`**（P50 `6.20`，峰值 `16.13`，最低 `2.25 MB/s`） | **`5`** (`seq=1..14`) | `14,282` | `8,124` | **`6,157`** | **`43.11%`** | **`27,487` (`1.92`)** | `560` | [1gb-rx-session-stats.txt](../../runtime/owly-xos/evidence/1gb-rx-session-stats.txt)、[1gb-mishare-timeline.txt](../../runtime/owly-xos/evidence/1gb-mishare-timeline.txt#L6-L58) |
| **1 GiB TX Round 3 (`21:57`)** | TX (`1,073,741,824 B`) | `52.58 s` `PERF_TRANSFER` / **`20.42 MB/s`**（P50 `23.65`，P90 `33.04`，峰值 `35.55 MB/s`） | `3`–`5`（频繁增删） | `161,766` | `159,698` | **`2,068`** | **`1.28%`** | `173,997` (`1.08`) | `155,578` | [1gb-tx-session-stats.txt](../../runtime/owly-xos/evidence/1gb-tx-session-stats.txt)、[1gb-tx-runner-telemetry.txt](../../runtime/owly-xos/evidence/1gb-tx-runner-telemetry.txt)、[1gb-mishare-timeline.txt](../../runtime/owly-xos/evidence/1gb-mishare-timeline.txt#L59-L95) |

### 2.3 为什么 `status[2]` 在 RX 中高度可疑且不可用总减法外推

1. 在 Round 1 `1 GiB` RX（[1gb-rx-round1-telemetry.txt#L59-L62](../../runtime/owly-xos/evidence/1gb-rx-round1-telemetry.txt#L59-L62)）中：
   - 用户态退出前打印 `air_tx_submitted=12,298`（含无单播 ACK 确认需求的广播 AWDL Action 帧 `tx_action=4,537` 及少量多播帧），内核快照 `completed=12,296` 中 `status[2]=4,699`（**`38.22%`**）。因为两者统计采集点存在先后时间差，且内核统计没有按「帧类型 × 目的地址 × 完成状态」交叉分桶，所以只能直接认定总体 `status[2]` 极高，而**不能**用总数直接相减当作单播纯失败率公式。
2. 在 Round 2 `1 GiB` RX（[1gb-rx-session-stats.txt#L11-L32](../../runtime/owly-xos/evidence/1gb-rx-session-stats.txt#L11-L32)）中，`active=5` 对端（`seq=1,2,3,13,14`）同时存在且发生过反复建链与掉线，`tries=27,487` 相对 `completed=14,282` 达 **`1.92`**（`status[2]` 达 `43.11%`）。其余 `8,090` 个有效单播速率样本全跑在 **`80 MHz VHT80 MCS9 SGI LDPC`**（`bw=34,0,8090`, `mcs=...,9=8090`），说明成功帧物理信道 SNR 与调制速率良好；但失败帧本身并未附带空口/队列上下文，不能仅据此断定 `100%` 均由“对端离信道”引起，竞争、队列超时与软件延迟耦合亦可能共同贡献了 TTL 淘汰。
3. **RX 已观测计数未出现主机/核内丢包记录**：Round 1 `air_rx_kernel_packets=398,997`、`air_rx_kernel_drops=0`、`pt_rx_msdu_stats dropped=0`、`OWLY_RX_GAPS missing=0`、`tap_delivery_drops=0`（注：仅覆盖各统计埋点，不等价于整条空口与所有下层 TX/RX 队列零丢帧）。

---

## 3. 高 `NO_ACK` 与卡顿耦合的根因链条

### 3.1 `OWLY_RX_GAPS` 揭示的微停顿与长断流双峰分布

查看 Round 1 `1 GiB` RX 的 [OWLY_RX_GAPS 统计 (`L60`)](../../runtime/owly-xos/evidence/1gb-rx-round1-telemetry.txt#L60)（共匹配 `387,611` 帧，同 PPDU 内 `equal=363,884` 帧，相当于平均每 PPDU 约 16 帧 A-MPDU）：

| RX PPDU 到达间隔区间（`radio_us`） | 事件次数 (`radio_count`) | 累计耗时 (`radio_us`) | 物理/协议成因 |
|---|---:|---:|---|
| `0 – 250 µs`（及同 PPDU） | `9`（+`363,884 equal`） | `0.002 s` | 单次 A-MPDU 突发内部连续帧 |
| `250 µs – 1 ms` | `16,138` | `12.19 s` | 正常相邻 PPDU 间隔 / SIFS+退避 |
| `1 – 4 ms` | `2,843` | `5.73 s` | 轻度 MAC 竞争 / 协议调度周转 |
| **`4 – 16 ms`** | **`2,802`** | **`27.37 s`** | **中短停顿：反向 TCP ACK 迟到/NO_ACK 后等待重传窗口** |
| **`16 – 32 ms`** | **`1,619`** | **`32.78 s`** | **短AWDL窗口错过导致等下一个 AW（16 TU ≈ 16.38 ms）** |
| `32 – 128 ms` | `75` | `2.95 s` | 跨多个 AW 重建 |
| **`128 – 256+ ms`** | **`240`** (`234 + 6`) | **`44.62 s`** (`42.90 + 1.72`) | **iPhone 未断普通 Wi-Fi / 切信标扫描 / 多对端离线导致离信道长休眠** |

其中仅 **`4–32 ms` 区间累计出现 `60.15 s`**，几乎与 `128–256+ ms` 长间隔（`44.62 s`）相当——需要说明的是，桶累计时长混合了正常协议时隙间隔、对端发送节奏与异常停顿，不可直接视作“完全可回收净损”，但仍侧面印证中短窗耦合停顿对有效传输占空比的拖累。

### 3.2 `should_coalesce` 中的纯 TCP ACK 延迟等待增大窄接收窗错窗风险

在 AWDL `self` ↔ Apple 的交互中，苹果端作为发送方在 AWDL 的可及窗口（AW）内突发倾泻一个 A-MPDU burst 后，尾端可用接收窗口较短（若苹果开启普通 Wi-Fi 并发或周边有多对端竞争 AWDL 时隙，停留时间更碎片化）。

- 本地调试补丁 [`owly-ack-coalesce.patch`](../../experiments/remote-peer-probe/vht-rx-next/ack-coalesce-next/owly-ack-coalesce.patch)（`2026-11-10` 针对旧款 `dzf2` 无硬件 TX A-MPDU 固件编写，上游原版 `t4t5/owly` 并无此改动）在 [`node.rs`](../../native/owly/crates/owly-awdl/src/node.rs) 将 `should_coalesce` 扩展为对 `>= 512 B` 数据包和 `pure_tcp_ack()` 触发等包（`pure_tcp_ack` 分支实际还叠加“存在未来邻近发包窗口”的判定条件，而非无条件一律截留）。
- 配合 [`radio.sh`](../../runtime/owly-xos/radio.sh) 的 `OWLY_COALESCE_US=1000`：
  1. **TX 场景影响较小**：小米发 `1 GiB` 大包时队列持续有大包（`>= 512 B`）快速凑齐批处理（`air_tx_amsdu_mpdu=158,343` 承载 `780,591 MSDUs`，均约 `4.93` 合 1），很少长期空等。
  2. **RX 场景副作用显著**：小米接收数据时上行几乎全为几十字节纯 TCP ACK，难以迅速凑满大批；触发该等待条件时纯 ACK 会被人为延迟 `~1000 µs` 发包定时器。
  3. **不可简单把 `timer late` 相加为单包端到端耗时**：[`1gb-rx-round1-telemetry.txt#L39-L59`](../../runtime/owly-xos/evidence/1gb-rx-round1-telemetry.txt#L39-L59) 中的 `timer late avg 1,408–1,500 µs`（`max 9,445 µs`）仅衡量 `service_timer()` 被调度触发相对于到期 deadline 的迟到，并不能等同于每一个纯 ACK 在空口前的实际端到端排队时间；但两者叠加后确会抬高尾段纯 ACK 错窗概率。

### 3.3 多对端干扰 + Apple 自身双模切频对不对称方向的影响

- `pt_remote_peer_ctl` 显示 `1 GiB` RX Round 2 并发激活 `active=5`（`seq=1,2,3,13,14`），timeline 中亦观测到 `<ApplePeer>`（iPhone）与 `<ApplePeer-2>`（MacBook Air）同时在线且周边另有 AWDL 对端（[1gb-mishare-timeline.txt#L13-L16](../../runtime/owly-xos/evidence/1gb-mishare-timeline.txt#L13-L16)）。
- 即使多对端同时存在且日志里出现对端增删（如 TX Round 3 亦有 `peer 5` 反复 add/del 见 [1gb-tx-runner-telemetry.txt#L58-L133](../../runtime/owly-xos/evidence/1gb-tx-runner-telemetry.txt#L58-L133)），**小米发大包（TX `20.42 MB/s`）受影响远小于小米收大包（RX `5.71 MB/s`）**。原因是：
  - 小米做 TX 时，小米主导排布 A-MPDU/A-MSDU 突发节奏，对单次 ACK 时序微偏移相对迟钝（`NO_ACK` 仅 `1.28%`）；
  - 小米做 RX 时，流量节奏完全由苹果控制：苹果突发结束若贴近离开信道 149 边界，小米端反向 ACK 人为滞留更易使苹果收不到 ACK 进而触发拥塞退避与重传。
- 此外，[1gb-mishare-timeline.txt#L39,L43](../../runtime/owly-xos/evidence/1gb-mishare-timeline.txt#L39-L43) 还记录了在 `/Upload` 传输中途（`21:55:42`、`21:56:12`）MiShare 仍触发了周期性 `/Discover OK` 请求，占用了控制面通道。

### 3.4 单线程 Tokio `biased;` 饥饿与同步定时器指标边界

- [`driver.rs`](../../native/owly/crates/owly-awdl/src/driver.rs) 采用 `tokio::runtime::Builder::new_current_thread()` 单线程运行 `owly-awdl`、`Monitor` 与 `Tap`。
- 审计时 [`driver.rs`](../../native/owly/crates/owly-awdl/src/driver.rs) 使用 **`tokio::select! { biased; ... }`**：`d.air.recv()` 排在 `d.netif.recv()` 和 `tokio::time::sleep_until(wake)` 之前。
  - **准确机制说明**：`Node::handle` 对每个输入（包括 `Frame`、`Outbound` 与 `Timer`）都会推进状态机并触发发送，因此**并非“只有 timer 分支才能发包”**；`biased;` 真正的主要风险是高密度 `Air` RX 突发持续就绪时，单包 `.readable().await` 会在 `select!` 中长时间抢占 `Netif`（`Tap`，如 ACK 读取）以及推迟纯 `Timer` 分支到期唤醒。
  - 加之 Tokio 1 ms 时间轮槽粒度与内核/子进程阻塞叠加，导致日志 `timer late avg` 长期位于 `1.4–1.5 ms` 并出现 `9.4–20.0 ms` 尖峰（注：此前 `Input::Frame`/`Outbound` 内先推进并重置 `node` timer 时未预先记录迟到样本，因此旧日志 `timer late` 仅反映一部分纯 timer 分支唤醒样本）。

---

## 4. 调试 / 兼容 / 历史遗留代码对吞吐与延迟的系统审计

除上述直接诱发 RX `NO_ACK` 的核心原因外，代码库中还残留多处为早期硬件 (`dzf2`)、早期调试验证或工程便利编写的兼容与防御性逻辑。以下逐一对照给出定位与结论，并明确划分**可删改项**与**不可误删的真兼容项**。

### 4.1 影响吞吐/延迟的可删除或可重构项清单

| 编号 | 位置与符号 | 历史由来与当前机制 | 性能代价与日志实证 | 处置建议 |
|---|---|---|---|---|
| **A1** | [`node.rs#L948-L952`](../../native/owly/crates/owly-awdl/src/node.rs#L948-L952) `should_coalesce` + [`radio.sh#L322`](../../runtime/owly-xos/radio.sh#L322) `OWLY_COALESCE_US=1000` | 源于 `2026-11-10` [`owly-ack-coalesce.patch`](../../experiments/remote-peer-probe/vht-rx-next/ack-coalesce-next/owly-ack-coalesce.patch)，当时 `dzf2` 无硬件 TX A-MPDU，为减少发包数把满足未来窗口条件的纯 TCP ACK 也拖入 `1000 µs` 等待。**上游 `t4t5/owly` 无此逻辑**。 | TX 端影响极小；RX 端使满足等待条件的队头纯 ACK 人为延迟 `1000 µs`（叠加调度迟到增大错窗概率），与总体 `43.11% NO_ACK` 及大量 `4–32 ms` RX 间隔并存。 | **删除 `pure_tcp_ack()` 等待分支**（纯 ACK 允许立即入队即发），**但保留软件 A-MSDU 本身**及 [`PureTcpAck::supersedes()` (`node.rs#L668-L686`)](../../native/owly/crates/owly-awdl/src/node.rs#L668-L686) 入队就地去重覆盖。注：TX `1 GiB` 已证实软件 A-MSDU 仍带来 `780,591 / 158,343 ≈ 4.93×` 两级聚合密度。 |
| **A2** | [`android.rs#L589-L595, L621-L628, L678-L690`](../../native/houji-runner/src/android.rs#L589-L690) `Tap::peer_up` / `peer_down` 同步调用 `/system/bin/ip` | 为早期省事在 `Tap` 生命周期里直接 `Command::new("/system/bin/ip")` 增删 `ip -6 neigh` / 路由。该回调从 `Driver::handle()` 同步执行，落在单线程 Tokio 事件循环中！ | [1gb-tx-runner-telemetry.txt#L102-L105, L132-L136](../../runtime/owly-xos/evidence/1gb-tx-runner-telemetry.txt#L102-L136) 中：`add peer` 伴随 **`timer late max 19,963 µs`**，`remove peer` 伴随 **`max 13,034 µs`**。一次进程 `fork+exec` 卡死收发/定时器达 13–20 ms。 | **移除子进程调用**，直接复用仓库内已现成实现的纯 netlink 套接字模块 [`owly-radio/src/rtnl.rs#L14-L108`](../../native/owly/crates/owly-radio/src/rtnl.rs#L14-L108)（参见 [`owly-radio/src/native.rs#L193-L199`](../../native/owly/crates/owly-radio/src/native.rs#L193-L199) 调用 `RTM_NEWNEIGH` / `RTM_DELNEIGH`）。 |
| **A3** | [`node.rs#L237-L238, L741-L743`](../../native/owly/crates/owly-awdl/src/node.rs#L237-L743) 与 [`android.rs#L836-L839`](../../native/houji-runner/src/android.rs#L836-L839) `BURST=64` / `burst_limit=256` & `BURST_PAUSE_US=500` | 上游及早期防发包打满网卡缓冲的软件节流开关：连发满一个突发配额就硬插 `500 µs` 强制休眠。 | 现已启用 `ezif` 硬件 TX A-MPDU 与 `8 MiB` `SO_SNDBUF`，所有实测中 `air_tx_backpressure=0`；每次触及上限即白停 `500 µs`（叠加 Tokio 轮实际 `~1–2 ms`），且在 RX 高重传时可能无故阻滞队列排空。 | **关闭或大幅弱化纯定时休眠**（改为仅在底层 `send` 真正返回 `WouldBlock`/`ENOBUFS` 时才短退避，并保留发送预算防饥饿让渡）。 |
| **A4** | [`driver.rs#L417-L454`](../../native/owly/crates/owly-awdl/src/driver.rs#L417-L454) `tokio::select! { biased; ... }` 与 [`android.rs#L318-L331, L418-L425`](../../native/houji-runner/src/android.rs#L318-L425) 单包 `AsyncFd` | 早期单线程阻塞原型：逐包 `.readable().await` 一次 `libc::recv`/`libc::read`，再用 `biased;` 优先抽 I/O，把 `sleep_until(wake)` 放到后面。 | 高并发 RX 下定时器长期饥饿；`1 GiB` 数据单次系统调用+事件循环轮转近百万次（`air_rx_kernel_packets=398,997`、`delivered=781,023`）。 | **移除 `biased;`**（或提升关键 TX/ACK 调度优先级），并改为每次唤醒非阻塞有界批量抽包（drain up to `N` frames 或 `recvmmsg`），单次循环处理完一批包后及时检查并触发到期发送。 |
| **A5** | [`android.rs#L377-L387`](../../native/houji-runner/src/android.rs#L377-L387) `Monitor::send` 二次解析 TX A-MSDU | 调试监控逻辑：为了打印末尾汇总的 `air_tx_amsdu_mpdu` / `air_tx_amsdu_msdu`，`Monitor::send` 对每个已拼装好的 TX 帧再调用 `frame::parse` + `frame::msdus`（含堆 `Vec` 分配）。 | `1 GiB` TX 期间凭空多解析了 **`158,343` 次完整 802.11 + AWDL + A-MSDU 子帧**（见 [1gb-tx-runner-telemetry.txt#L138](../../runtime/owly-xos/evidence/1gb-tx-runner-telemetry.txt#L138)）。 | **从热路径移除二次解析**：改由 `node.rs` 拼装 A-MSDU 时把 `msdu_count` 直接随 `Action::Transmit` 透传给统计模块，或仅在显式 debug 变量开启时解析。 |
| **A6** | [`android.rs#L356`](../../native/houji-runner/src/android.rs#L356)、[`node.rs#L607`](../../native/owly/crates/owly-awdl/src/node.rs#L607)、[`android.rs#L583`](../../native/houji-runner/src/android.rs#L583) + [`ip.rs#L38-L54`](../../native/owly/crates/owly-awdl/src/ip.rs#L38-L54) RX 3× 堆拷贝 | 跨层切片均以 `to_vec()` + `Vec::with_capacity()` 传递所有权：`Monitor::recv` 拷贝 1 次 → `Node::rx_data` `m.payload.to_vec()` 拷贝 1 次 → `Tap::deliver` `to_ethernet()` 再分配拷贝 1 次。 | `1 GiB` RX 共 `delivered=781,023` 包 → 产生约 **`234 万` 次短命堆分配与 `memcpy`**，增加 CPU 抖动和单线程调度时延，后续冲高 `>40 MB/s` 时将成为明显瓶颈。 | **使用池化缓冲区 / 固定栈缓冲就地解包与写 TUN**（在 `Monitor::recv` 的 `[u8; 9216]` 栈缓冲内直接解出 slice，用预留了 14 字节 headroom 的复用缓冲或 `writev` 写入 TUN）。 |
| **A7** | [`runtime/owly-xos/radio.sh#L321`](../../runtime/owly-xos/radio.sh#L321) `HOUJI_OWLY_TX_ADDBA=1` + [`driver.rs#L358-L360`](../../native/owly/crates/owly-awdl/src/driver.rs#L358-L360) `takes_outbound()` 全局上限 | ① `HOUJI_OWLY_TX_ADDBA=1` 把小米发出帧内的软件 ADDBA 窗口声明限制在 `win=64`（历史兼容旧端保守值），而驱动层 `pt_remote_rx_window` 已调高到 **`256`**（另：实测 `STATS` 显示 `tx_req 33–37` 而 `rx_resp 0`，表明部分对端未回软件 ADDBA Resp，硬件聚合实际依赖固件/BE 接管，但声明 `win=64` 已毫无意义）。<br>② `takes_outbound()` 以全局 `node.backlog() + self.unicast.len() < unicast_queue` 控制 TUN 拉包，没有给低字节 ACK / 邻近控制流预留优先通道。 | ① `win=64` 人为限制软件 ADDBA 声明与 `pt_remote_rx_window=256` 错配。<br>② 若某条流大包堆积接近队列上限，反向小包与 TCP ACK 会与大包同池竞争甚至被 `takes_outbound()` 连带停拉。 | ① 将默认配置切至 `HOUJI_OWLY_TX_ADDBA=2`（`win=256`，对应 [`android.rs#L710-L718`](../../native/houji-runner/src/android.rs#L710-L718)）。<br>② 优化出站队列优先级：纯 TCP ACK / 小控制包独立优先队列或不与大包共享 `coalesce`/背压门限。 |

> **补充边界说明（哪些“能省 CPU”vs“立刻降 `NO_ACK`”）**：A1（取消纯 ACK `1000 µs` 等待）、A2（消除 `/system/bin/ip` `13–20 ms` 尖峰）、A4（消除 `biased;` 饿死）与环境清理直接降低 TX 侧调度迟到和 RX `NO_ACK`；A3/A5/A6/A7 则是消除固定人工停顿、释放单核 CPU 与突破高吞吐天花板（如向 `30–64 MB/s` 迈进）的关键工程债。

### 4.2 明确不能误删的真协议与硬件兼容项（Keep List）

以下逻辑虽然表面像“兜底/定时代码”，但在实机上承担真实的 Apple / WCN7850 互操作职责，**切勿移除**：

1. **`neighbour_echo`（`node.rs` [`L137`](../../native/owly/crates/owly-awdl/src/node.rs#L137)、[`L214`](../../native/owly/crates/owly-awdl/src/node.rs#L214)、[`L717`](../../native/owly/crates/owly-awdl/src/node.rs#L717)）**：每 `3 s` 向对端发 NDP 探测保活，防止 iOS 发送端长期静默后邻居表过期重新发起昂贵的 NS/NA 握手。
2. **`Tap` `MTU = 1450`（`android.rs` [`L447`](../../native/houji-runner/src/android.rs#L447)、[`L520`](../../native/houji-runner/src/android.rs#L520) / `owly-radio` [`native.rs#L27`](../../native/owly/crates/owly-radio/src/native.rs#L27)）**：对齐 iPhone `awdl0` MTU，避免大于 1450 字节的 IPv6 包被对端或中间层分片丢弃。
3. **`UNICAST_GUARD_US`（[`sync.rs#L23`](../../native/owly/crates/owly-awdl/src/sync.rs#L23) `3 * TU`）**：避开 AWDL 时隙边界切频保护时间发送单播，避免切信道瞬间的必然丢帧。
4. **`hold_syn`（[`node.rs#L619-L628`](../../native/owly/crates/owly-awdl/src/node.rs#L619-L628)）**：在完成对端首个 PSF/MIF 能力发现前暂存首个 TCP SYN，防止首次冷启动建连阶段 SYN 在空口“放空炮”。

---

## 5. 分步对照验证建议（Clean A/B Matrix）

为了将 **“外部环境因素（多对端 / iPhone 普通 Wi-Fi 切频）”** 与 **“内部代码与延迟因素（`should_coalesce` / `timer late` / `fork+exec`）”** 完全解耦，建议按如下顺序做最小变量回归实测（每次复测复用 `1 GiB` `airdrop-test-1GB.zip` 并采集同口径 `pt_rate_stats` + `OWLY_RX_GAPS`）：

1. **Step 1（零改代码纯环境对照组）**：关闭周边非测试 Mac/Apple 设备的隔空投送（确保 `active=1`），测试机 iPhone **从控制中心或设置彻底断开普通 Wi-Fi（仅保留蓝牙 + Wi-Fi 开关开着供 AWDL 使用）**，在现有参数下重跑一轮 `1 GiB` RX。
   - **预期观测**：`OWLY_RX_GAPS` 中 `128–256+ ms` 区间（原 `44.62 s`）应显著收缩，验证环境干扰占比。
2. **Step 2（仅移除纯 TCP ACK 等待：修改 A1 + `HOUJI_OWLY_TX_ADDBA=2`）**：去掉 `node.rs#L948-L952` 的 `pure_tcp_ack()` 等待分支（保留 `supersedes()` 覆盖），重新编入 `owly-xosd` 后保持 Step 1 干净环境复测 `1 GiB` RX，并重跑一轮 `1 GiB` TX 确认发送侧两级 A-MSDU 未回退。
   - **关键验收指标**：
     - RX `pt_rate_stats` 总体 `status[2]` (`NO_ACK`) 是否显著回落、`tries / completed` 是否从 `1.92` 降至接近 `1.0x`；
     - `OWLY_RX_GAPS` `4–32 ms` 区间事件占比与归一化停顿时长是否同步下降；
     - RX `PERF_TRANSFER` 均速向 `157.80 MB win64` 基线（`13.18 MB/s` 整体 / `20+ MB/s` 稳态）靠拢，TX 维持或高于 `20+ MB/s`。
3. **Step 3（调度与零拷贝收口：A2 + A4 + A3/A5）**：用 `owly-radio/src/rtnl.rs` 替换 `/system/bin/ip` 子进程（消除 `13–20 ms` 尖峰），去掉 `driver.rs` `biased;` 与 `Monitor::send` 二次 A-MSDU 解析，并把 `AsyncFd` 改为有界批量抽包后，重验 `timer late avg / max` 是否收敛到亚毫秒至 1 ms 水平。

---

## 6. 关联证据与源码索引

- **匿名化日志与 sysfs 归档（`runtime/owly-xos/evidence/`）**：
  - [evidence 索引与隐私说明](../../runtime/owly-xos/evidence/README.md)
  - [21:49 1 GiB RX Round 1 telemetry + `OWLY_RX_GAPS`](../../runtime/owly-xos/evidence/1gb-rx-round1-telemetry.txt)
  - [21:53 1 GiB RX Round 2 内核 sysfs 快照 (`NO_ACK=6,157/14,282`)](../../runtime/owly-xos/evidence/1gb-rx-session-stats.txt)
  - [21:57 1 GiB TX Round 3 内核 sysfs 快照 (`NO_ACK=2,068/161,766`)](../../runtime/owly-xos/evidence/1gb-tx-session-stats.txt)
  - [21:57 1 GiB TX Round 3 `owly-xosd` 运行态日志（`timer late max 19,963 µs`）](../../runtime/owly-xos/evidence/1gb-tx-runner-telemetry.txt)
  - [21:53–21:58 1 GiB 双向 MiShare logcat 里程碑与进度分位时间线](../../runtime/owly-xos/evidence/1gb-mishare-timeline.txt)
  - [157.80 MB 基线对照 (`summary-20261115.json` / `receive-session-stats.txt` / `send-session-stats.txt`)](../../runtime/owly-xos/evidence/summary-20261115.json)
- **源码与历史补丁**：
  - [`node.rs`](../../native/owly/crates/owly-awdl/src/node.rs#L668-L952)（`should_coalesce`、`PureTcpAck::supersedes`、`BURST_PAUSE_US`、`hold_syn`、`neighbour_echo`）
  - [`driver.rs`](../../native/owly/crates/owly-awdl/src/driver.rs#L111-L454)（`new_current_thread`、`takes_outbound`、`select! { biased; ... }`）
  - [`android.rs`](../../native/houji-runner/src/android.rs#L318-L839)（`Monitor::recv`、`Monitor::send`、`Tap::peer_up/down` `/system/bin/ip`、`tx_addba_mode`、`burst_limit`）
  - [`rtnl.rs`](../../native/owly/crates/owly-radio/src/rtnl.rs#L14-L108)（可复用的纯 netlink 邻居表操作实现）
  - [`owly-ack-coalesce.patch`](../../experiments/remote-peer-probe/vht-rx-next/ack-coalesce-next/owly-ack-coalesce.patch)（`pure_tcp_ack()` 等待条件历史补丁出处）
  - [`radio.sh`](../../runtime/owly-xos/radio.sh#L319-L323)（`HOUJI_OWLY_TX_ADDBA=1` 与 `OWLY_COALESCE_US=1000` 运行参数）
  - [`dp_tx.h`](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_tx.h#L956-L970) & [`dp_be_tx.c`](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/be/dp_be_tx.c#L449-L458)（`NO_ACK` / `TTL` 状态码映射）
