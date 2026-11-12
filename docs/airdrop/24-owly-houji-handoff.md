# 小米 14 Owly 149 信道交接

> 最新交接入口：[当前交接（2026-11-11）](28-three-findings-handover.md)。本文保留历史阶段信息；设备状态、用户要求和待办以最新交接为准。

2026-11-10～2026-11-11 完成 `Mac → 小米` 接收慢速根因定位、软件瓶颈修复，以及 **2×2 MIMO（2SS MCS 0–9）+ SGI80 + 安全 ADDBA（A-MPDU + A-MSDU 混合聚合）** 16 MiB / 64 MiB（3 对）实机验证。实验使用 Mac（零修改）与小米 14 的独立 AWDL 探针，载荷为生成数据，两端逐样本核对 SHA-256。**实验已正常退出并完成 `STOCK_RESTORED` 恢复校验（普通 Wi-Fi 与原生服务已恢复）。** Owly 的改动尚未回迁到原生 MIShare/XOS。

## 已达到的速度

| 方向 | 完整通过的 64 MiB 重复组（3 对） | 16 MiB 短测 | 相比交接前提升 |
| --- | ---: | ---: | ---: |
| 小米→Mac | `vht80-sgi` 加权 **6.590 MB/s**（峰值 **6.721 MB/s**）；`vht80-2ss-mcs9-sgi` 加权 **5.897 MB/s**（峰值 **6.369 MB/s**） | 最高 **8.894 MB/s**（2SS 下 **8.063 MB/s**） | +1.9%（64 MiB）/ +10.1%（16 MiB） |
| Mac→小米（软件修复，1SS） | 三次加权 **4.532 MB/s**，最快完整样本 **4.662 MB/s**（交接前 2.894 MB/s） | 最高 **4.338 MB/s** | +56.6%（64 MiB） |
| **Mac→小米（2SS + SGI80 + ADDBA/AMSDU）** | 三次加权 **14.705 MB/s**，最快完整样本 **18.483 MB/s**（次快 **18.362 MB/s**） | 最高 **7.861 MB/s** | **+408.1%（5.08×，单样本峰值 6.39×）** |

速率为十进制，重复组按每方向载荷总字节（3 × 67,108,864 B = 201,326,592 B）除以总时间。2026-11-11 新增的 **2×2 MIMO + SGI80 + ADDBA（保留 Bit 0 `A-MSDU Supported`）** 在纯 `self-peer` 安全模式下触发 WCN7850 硬件 SIFS `BlockAck`（Mac `txAMPDU=935, rxBACK=935`），全程 `fcs = 0`、`air_rx_kernel_drops = 0`，单样本峰值追平 Apple phone 原生 AWDL（`18.41 MB/s`）。 [2SS+SGI80+ADDBA 完整报告](26-2ss-sgi80-addba-probe.md) · [2SS 64 MiB×3 原始结果](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-mcs9/2/result.json) · [1SS 64 MiB×3 原始结果](../../experiments/remote-peer-probe/vht-rx-next/rx-phy-next/device-rx-phy4/3/result.json) · [容量、广告和日志验收](23-vht-rx-capacity.md)

## 同一次模块加载的九阶段

| 阶段 | 样本与变量 | Mac→小米 MB/s | 小米→Mac MB/s |
| --- | --- | ---: | ---: |
| 1 | 1 MiB，MIF VHT，首个 PHY 观测 | 0.816 | 2.140 |
| 2 | 16 MiB，撤销 MIF VHT | 2.427 | 7.979 |
| 3 | 16 MiB，恢复 MIF VHT | 2.660 | 8.063 |
| 4 | 16 MiB，分方向 PHY 和 Mac TCP 时序 | 2.825 | 7.941 |
| 5 | 16 MiB，原调度加双时钟间隔 | 2.474 | 8.076 |
| 6 | 16 MiB，公平轮询候选 | 2.264 | 7.954 |
| 7 | 16 MiB，回原轮询对照 | 2.443 | 7.248 |
| 8 | 16 MiB，`owly0` 表 10075 `quickack 1` | 1.753 | 8.020 |
| 9 | 16 MiB，回无 `quickack` 对照 | 2.461 | 8.037 |

- 九阶段、18 个样本均通过两端 SHA。阶段 2 撤广告后仍收到 VHT80/MCS7/1SS，与开启相同。Mac 可能保留同一对端能力，不把关广告直接等同 HT。阶段 4 的 Mac→小米 5144 个目标 QoS 帧中，4888 个置了 A-MSDU 位，4610 个 raw 长度至少 3072 B。结构验证通过的描述符报告 VHT80/MCS7/1SS/GI raw 0/SU。固件实际 SIFS ACK、A-MPDU 和 Block Ack 保持未知，已交付 Retry 不能估计所有空口丢失。 [各阶段原始结果和模块](../../experiments/remote-peer-probe/vht-rx-next/rx-phy-next/README.zh-CN.md)
- 阶段 4 的 200 ms TCP 快照显示早段窗口收缩及 loss recovery。前约 1.405 s 发送栈增量约 0.175 MB/s，之后到末采样为 3.593 MB/s。阶段 5 的 4817 个大帧全有 Kiwi RX 时钟。radio 与 host 的相邻跨度分别 6.771792 s 和 6.769082 s，两侧都观察到 12 个 ≥128 ms 的间隔，合计约 2.8 s。11 个落在 128–256 ms，与 Mac 调度里约 131/197 ms 的非 149 窗口相容。时间线不区分对端未发与接收不到，也没有发现明显的 userspace 读帧滞后。即使假定能消除全部 ≥128 ms 的间隔，阶段 5 也只对应约 4.21 MB/s。 [双时钟与 TCP 时序](../../experiments/remote-peer-probe/vht-rx-next/rx-phy-next/device-rx-phy1/5/result.json)
- 只删除 Tokio `biased;` 的候选未改善整轮接收，已回到原应用。阶段 5、6、7 从约 1.6 s 起的 Mac 发送栈速率分别 2.821、2.903、2.852 MB/s，公平轮询阶段的启动及重传更差。这三轮不足以把退化归因于轮询顺序，现有结果不支持采纳。 [公平轮询对照](../../experiments/remote-peer-probe/vht-rx-next/fair-poll-next/README.zh-CN.md)
- 阶段 8 在新 socket 前对 `owly0` 的表 `10075` 应用并校验 `quickack 1`（`metric 1024`），阶段 9 恢复默认路由并校验无 `quickack`。阶段 8 的 Mac 重传由 111,084 / 91,568 B 降到 14,652 B，证明接收端快速 TCP ACK 已生效；但由于 `<512 B` 的 TCP ACK 不进入 1 ms A-MSDU 合包，逐段回 ACK 使本机额外提交约 6,100 个独立出站 802.11 帧（`air_tx_submitted` 由 13,870 / 13,263 增至 19,709），半双工争用使 4–16 ms 接收间隔由 335 / 295 个（1.985 / 1.649 s）增至 561 个（3.535 s），1.6 s 后发送栈速率降至 2.031 MB/s，整轮接收降至 1.753 MB/s。阶段 9 撤回后恢复至 2.461 MB/s。 [阶段 8](../../experiments/remote-peer-probe/vht-rx-next/rx-phy-next/device-rx-phy1/8/result.json) · [阶段 9](../../experiments/remote-peer-probe/vht-rx-next/rx-phy-next/device-rx-phy1/9/result.json)

## 根因定位与修复验证（`device-rx-phy2` ～ `device-rx-phy4`）

| 租约 / 阶段 | 变量与候选 | 规模 | Mac→小米 MB/s | 小米→Mac MB/s | 内核 `>=1536B` / 用户态 `matched` | `AF_PACKET` 丢包 | Mac TCP 重传包数 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `phy2/1` | 基线（无纯 ACK 合包，默认 `SO_RCVBUF`） | 16 MiB | 2.678 | 6.433 | `4,862 / 4,858` | 4 | 13 |
| `phy2/2` | `ack-coalesce-next`（纯 ACK 1 ms 合包+覆盖） | 16 MiB | **4.338** | **8.894** | `4,797 / 4,791` | 6 | 19 |
| `phy2/3` | `ack-coalesce-next` 复测 | 16 MiB | **4.084** | **8.125** | `4,854 / 4,808` | 46 | 120 |
| `phy2/4` | 回基线对照 | 16 MiB | 2.377 | 8.029 | `4,819 / 4,806` | 13 | 41 |
| `phy3/1` | 8320 模块 + `ack-coalesce-next`（VHT 3895） | 16 MiB | 3.406 | 8.210 | `4,985 / 4,860` | **125** | **385** |
| `phy3/2` | 8320 模块 + VHT `7991` 广告 | 16 MiB | 2.418 | 8.332 | `4,884 / 4,868` | 16 | 43 |
| `phy3/3` | 8320 模块 + VHT `7991` + HT `7935` 广告 | 16 MiB | 2.840 | 7.989 | `4,893 / 4,864` | 29 | 96 |
| `phy4/1` | 4224 模块 + `ack-coalesce-rcvbuf-next`（`SO_RCVBUF` 8 MiB） | 16 MiB | 3.822 | 8.031 | **`4,840 / 4,840`** | **0** | 3（正文段 **0**） |
| `phy4/2` | 4224 模块 + `ack-coalesce-rcvbuf-next` 复测 | 16 MiB | **4.307** | **8.004** | **`4,778 / 4,778`** | **0** | **1**（正文段 **0**） |
| `phy4/3` | 4224 模块 + `ack-coalesce-rcvbuf-next`（3 对） | **64 MiB×3** | 加权 **4.532**（峰值 **4.662**） | 加权 **6.590**（峰值 **6.721**） | **`57,407 / 57,407`** | **0** | 样本 1：`3`；**样本 3/5：`0`** |

### 已查明的三大根因

1. **反向纯 TCP ACK 洪泛引发半双工信道争用（已修复）**
   - `Node::rx_data` 解出每个 3-MSDU A-MSDU 后逐包写入 `owly0`，触发 Linux 内核每 2 个 TCP 段立即回传 1 个 `<512 B` 纯 TCP ACK；原 `owly-coalesce.patch` 仅对 `>=512 B` 数据包启用 `1,000 µs` 合包，导致每个纯 TCP ACK 被立即封装成独立 802.11 帧发往空口（16 MiB 接收期间小米发射 ~8,300 个反向 ACK 帧）。
   - [`ack-coalesce-rcvbuf-next/owly-ack-coalesce.patch`](../../experiments/remote-peer-probe/vht-rx-next/ack-coalesce-rcvbuf-next/owly-ack-coalesce.patch) 对无负载纯 TCP ACK 启用 `1,000 µs` 合包，并在 `Node::enqueue` 中用同一流严格递增的纯累计 ACK 原位替换队列中的旧纯 ACK（离槽前最后 `1,000 µs` 内则立即刷出，不跨槽滞留），使反向出站帧数减少约 **88%**，16 MiB `Mac → 小米` 由 `2.4–2.68 MB/s` 提升至 **`4.08–4.34 MB/s`**。

2. **用户态 `wonder0` `AF_PACKET` 默认接收缓冲过小导致突发丢包与 3 倍 TCP 重传（已修复）**
   - 对比内核 `dp_rx_deliver_raw_passthru` 交付的 `>=1536 B` 帧数（`raw_len[2] + raw_len[3]`）与用户态 `Monitor::recv` 收到的 `>=1536 B` 帧数（`OWLY_RX_GAPS matched`），差值与 Mac 端 `tx_retransmit_packets` 呈精确 **1:3** 比例（每个丢弃的 A-MSDU 含 3 个 TCP 段）。
   - 原因是 `Monitor::open` 未设置 `SO_RCVBUF`，沿用 Linux 默认 `rmem_default = 212,992 B`，而 `AF_PACKET` 按 `skb->truesize`（4224 模块约 8.9 KB、8320 模块约 17.1 KB）计费，仅能缓存 12～23 个 A-MSDU（约 3.5～6 ms 突发）。
   - [`ack-coalesce-rcvbuf-next/runner-rcvbuf.patch`](../../experiments/remote-peer-probe/vht-rx-next/ack-coalesce-rcvbuf-next/runner-rcvbuf.patch) 在 `Monitor::open` 绑定前设置 `SO_RCVBUFFORCE` / `SO_RCVBUF = 8 MiB`（内核实设 `rcvbuf=16777216`）并记录 `PACKET_STATISTICS`：在 `device-rx-phy4` 的 16 MiB 与 **384 MiB（6×64 MiB）** 全程测试中，`air_rx_kernel_drops = 0`（内核 `57,407` == 用户态 `57,407`），第 2、3 对 64 MiB `Mac → 小米` 实现 **全程 0 次 TCP 重传**，64 MiB 加权接收速率由 `2.894 MB/s` 提升至 **`4.532 MB/s`**（单样本最高 **`4.662 MB/s`**）。

3. **8320 单缓冲容量与 VHT `7991` / HT `7935` 广告验证及剩余物理边界（已定位）**
   - [`rx-capacity-8320-next`](../../experiments/remote-peer-probe/vht-rx-next/rx-capacity-8320-next) 与 [`vht-mpdu-7991-next`](../../experiments/remote-peer-probe/vht-rx-next/vht-mpdu-7991-next) 已实机验证 8320 单缓冲 RX 容量及 MIF 宣告 VHT Max MPDU `7991` + HT Max A-MSDU `7935`，但 macOS `awdl0`（`AppleBCMWLANCore`）在 AWDL 路径将出站 A-MSDU 硬性封顶为 3 个 TCP 段（`max_bytes=3780`，`gt3895=0`）。
   - 在 0 丢包、0 重传的 `device-rx-phy4/3`（3×64 MiB = `44.43 s`）中：
     - `16–128 ms` 间隔仅 1 次（`0.046 s`），证明在槽期间 Mac TCP 从未断流；
     - `128–256 ms` 离槽间隔 82 次（共 `13.98 s`，占总时间 `31.5%`），对应 Mac `awdl0` 在 `Data` 模式下固有的 `[149×6, 36×2, 6×1, 149×5, 36×2]` 切回 AP 信道 36 与发现信道 6 的窗口（149 信道占比 `11/16 = 68.75%`）；
     - 扣除离槽窗口后，Mac 在 149 信道上的持续在槽 TCP 有效吞吐已达 **`6.82 MB/s`**（`6.82 MB/s × 68.4% ≈ 4.66 MB/s`），与 `小米 → Mac`（`6.59 MB/s`）的剩余差距来自 Mac 对 1SS 对端封顶 VHT80 MCS 7、出站 A-MSDU 硬上限 3,780 B，以及无 Block Ack 下的逐 MPDU Legacy ACK 开销。

## 当前设备与构建状态

- **设备状态**：小米 14（`houji`，ADB `DEVICE_SERIAL`）已通过 [`run-2ss-sgi.py stop device-2ss-mcs9`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-mcs9/stop.json) 正常退出实验租约，并通过 `ensure_restored()`（`STOCK_RESTORED`）校验：实验模块与 bind mount 已卸载，普通 Wi-Fi 与原生服务已恢复。
- **远端与本地产物**：
  - **最新最佳模块与用户态组合（2SS + SGI80 + ADDBA/AMSDU，64 MiB×3 加权 `14.705 MB/s` / 峰值 `18.483 MB/s`）**：[`2ss-sgi-next/`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next)（内核模块 [`qca_cld3_kiwi_v2-2ss-rx-phy.ko`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/qca_cld3_kiwi_v2-2ss-rx-phy.ko) SHA `c57c80fdbd0784c3b9c245bdaffa36c18e441c559b1ed14f602e7892a743dd46`；远端 `build-host:/work/houji-build/tasks/pt-2ss-rx-phy` 与 `build-host:/work/houji-build/tasks/owly-2ss-addba`；完整报告见 [`26-2ss-sgi80-addba-probe.md`](26-2ss-sgi80-addba-probe.md)）。
  - 1SS 软件瓶颈修复基线：[`ack-coalesce-rcvbuf-next`](../../experiments/remote-peer-probe/vht-rx-next/ack-coalesce-rcvbuf-next)（远端 `build-host:/work/houji-build/tasks/ack-coalesce-rcvbuf`，`houji-owly` SHA `b3e65990cd7858d01ad438a532a329b50144b5bedda5e0b422a8cf10f2924d46`）。
  - 4224 RX PHY 观测模块：[`qca_cld3_kiwi_v2-rx-phy.ko`](../../experiments/remote-peer-probe/vht-rx-next/rx-phy-next/qca_cld3_kiwi_v2-rx-phy.ko)（SHA `d966a7d42a27e6e55cfffd1ee29877f63e2746e0e230a2771ffd8f732a8593e7`）。
  - 8320 单缓冲 RX 容量模块：[`qca_cld3_kiwi_v2-rx-capacity-8320.ko`](../../experiments/remote-peer-probe/vht-rx-next/rx-capacity-8320-next/out/qca_cld3_kiwi_v2-rx-capacity-8320.ko)（SHA `89cc24dccd930d5148e0bc50c1c6cf48a990cc3ccee03c7afa336558e861f042`）。
  - `WMI_PEER_CREATE` 远端 peer 路线已确认无需启用（`self-peer` 硬件原生支持 SIFS `BlockAck`，详见 [`26-2ss-sgi80-addba-probe.md`](26-2ss-sgi80-addba-probe.md) 与 [`22-passthru-rx-reo-probe.md`](22-passthru-rx-reo-probe.md)）。
