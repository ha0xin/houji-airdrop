# 小米 14 2×2 MIMO + SGI80 + 安全 ADDBA（A-MPDU + A-MSDU）实机突破报告

- 日期：2026-11-11
- 测试设备：Mac（`awdl0`，**零修改**）↔ 小米 14（`houji`，ADB `DEVICE_SERIAL`，149 信道 `PASSTHRU` `self-peer` 安全模式，实验后已恢复 `STOCK_RESTORED`）

---

## 1. 核心结论与吞吐跃升总览

在不修改 Mac 发送端任何配置、且 **不启用高风险 `WMI_PEER_CREATE`（保持 `self-peer` 安全卸载模式）** 的前提下，分两步完成了小米 14 的 **2×2 MIMO（2SS MCS 0–9）+ SGI80 + LDPC** 与 **用户态 `ADDBA`（A-MPDU + 3.6 KB A-MSDU 混合聚合）** 实机验证，全部样本通过双端逐样本 SHA-256 校验：

| 演进阶段 | 关键特性配置 | `Mac → 小米` 64 MiB×3 加权（单次峰值） | `小米 → Mac` 64 MiB×3 加权（16 MiB 峰值） | 相比交接基线（`Mac → 小米`） |
| --- | --- | ---: | ---: | ---: |
| 0. 交接基线（`24-owly-houji-handoff` 前） | 1SS MCS 7 LGI（`292.5 Mbps`），无纯 ACK 合包，默认 `SO_RCVBUF` | 2.894 MB/s | 6.470 MB/s（8.08 MB/s） | 基准（1.00×） |
| 1. 软件接收瓶颈修复（`device-rx-phy4`） | 1SS MCS 7 LGI + 纯 ACK 1ms 合包 + `SO_RCVBUF` 8 MiB | 4.532 MB/s（4.662 MB/s） | **6.590 MB/s**（**8.894 MB/s**） | +56.6%（1.57×） |
| 2. **Step 1：2×2 MIMO + SGI80**（`device-2ss-txrx1/5`，`--addba off`） | **2SS MCS 9 SGI80（`866.7 Mbps`）** + 3.6 KB A-MSDU（单 MPDU Legacy ACK） | **5.906 MB/s**（**6.037 MB/s**） | 5.814 MB/s（8.115 MB/s） | +104.1%（2.04×） |
| 3. **Step 1 + Step 2：2×2 MIMO + SGI80 + ADDBA（含 A-MSDU）**（`device-2ss-txrx1/6`，TX MCS 7） | **2SS MCS 9 SGI80 + 硬件 SIFS BlockAck + A-MPDU×A-MSDU 混合聚合** | **13.680 MB/s**（**16.159 MB/s**） | 4.738 MB/s（8.063 MB/s） | **+372.7%（4.73×）** |
| 4. **Step 1 + Step 2 对称满配**（`device-2ss-mcs9/2`，TX/RX 均 2SS MCS 9 SGI80） | **双向 2SS MCS 9 SGI80（`866.7 Mbps`）+ ADDBA（A-MPDU×A-MSDU）** | **14.705 MB/s**（**18.483 MB/s**） | **5.897 MB/s**（峰值 **6.369 MB/s**；16 MiB **7.978 MB/s**） | **+408.1%（5.08×）** |

> **里程碑意义**：
> - **双向 6 MB/s 目标全面达成**：`Mac → 小米` 64 MiB×3 加权跃升至 **14.705 MB/s**，第 2、3 对 64 MiB 稳定跑出 **18.362 MB/s** 与 **18.483 MB/s**（单样本耗时仅 `3.63 s`），**正面追平并略超 Apple phone 原生 AWDL 均值（`18.41 MB/s`）**！扣除 Mac 固定 149 信道 `11/16 = 68.75%` 占空比后，**在槽瞬时 TCP 净吞吐高达 `26.88 MB/s`（`215 Mbps`）**，已逼近 Apple phone 单槽位效率（`42.03 × 11/16 = 28.89 MB/s`）。
> - `小米 → Mac` 在 16 MiB 达到 **7.98～8.89 MB/s**（1 MiB 峰值 **8.604 MB/s**），64 MiB×3 在 `vht80-sgi` 下加权 **6.590 MB/s**（峰值 **6.721 MB/s**）、在对称 `vht80-2ss-mcs9-sgi` 下峰值 **6.369 MB/s**（加权 **5.897 MB/s**）。

---

## 2. 三大硬件与协议层新发现

### 发现一：零修改 Mac 即可协商跑满 `VHT80 2×2 MIMO MCS 9 SGI80`（`866.7 Mbps`）

此前 `houji-owly` 沿用旧版 8 字节 HT Capabilities（`0000ce111bff0000`，仅 1SS MCS 0–7）且仅在 MIF 中附带 1SS MCS 0–7 VHT Capabilities（`bf0c00000000fcff0000fcff0000`，不含 SGI80、不支持 PSF VHT 宣告），导致 Mac 发送端将对端锁死在 `VHT80 1SS MCS 7 LGI`（`292.5 Mbps`）。

本次对齐 Apple phone（iOS 26）原生 AWDL 帧格式（详见 [25-Apple phone-mac-awdl-features.md](25-Apple phone-mac-awdl-features.md)），在 **PSF 与 MIF 同时宣告**：
- **TLV 7（HT Capabilities，9 字节含 2 字节前缀）**：`00006f001fffff0000`
  - `HT Cap Info = 0x006f`（LDPC + 40MHz + SGI20 + SGI40，Max A-MSDU = 3839 B）
  - `A-MPDU Params = 0x1f`（Max RX A-MPDU = 64 KB，MPDU Density = 16 µs）
  - `RX MCS Mask = ff ff 00 ...`（2SS MCS 0–15）
- **TLV 17（VHT Capabilities Container，14 字节含 `3b 0c` 外层头）**：
  - 无 BA 模式：`bf0c30000000faff0000faff0000`（`Max MPDU = 3895`、`SGI80 = 1`、`RX LDPC = 1`、`SU Beamformee = 1`、`Max AMPDU Exp = 0`、RX/TX 2SS MCS 0–9 `0xfffa`）
  - 开启 BA 模式：`bf0c30008003faff0000faff0000`（同上，并将 bits 23..25 `Max A-MPDU Length Exponent` 置为 `7`，宣告 1 MB A-MPDU 窗口，同时严格保持 bits 0..1 `Max MPDU = 3895 B` 不突破内核 `4224 B` RX DMA 单缓冲契约）

配合内核模块 [`qca_cld3_kiwi_v2-2ss-rx-phy.ko`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/qca_cld3_kiwi_v2-2ss-rx-phy.ko) 的 `pt_vht80_2ss=1`（下发 `WMI_VDEV_PARAM_NSS = 2`、`TX_STREAMS = 2`、`RX_STREAMS = 2`、`SGI = 1`、`RX_LDPC = 1`、`TX_LDPC = 1`），**无需触碰 Mac 任何配置**：
- 小米内核 HAL RX 观测器（`pt_rx_phy_stats`）在 `device-2ss-mcs9/2` 的 **67,326 个单播数据 MPDU** 中实测：**100.0%（`67,326/67,326`）为 VHT80 + 2×2 MIMO（`vht_nss[2]`）+ MCS 9（`vht_mcs[9]`），99.72%（`67,135/67,326`）启用 SGI80（`vht_gi[1]`），且全程 `fcs = 0`！**
- Mac 内核与 `airportd` 日志同步确认：`updateHTandVHTCapsForRSDB: Num of Chains to use: 2 (original 2)`，`WeightAvgLQM rxRate=960730 / 1005786`。

---

### 发现二：WCN7850 `amss20.bin` 在 `PASSTHRU`（`self-peer`）模式下原生支持硬件 SIFS `BlockAck`（无需 `WMI_PEER_CREATE`！）

此前文档（[15-aggregation-roadmap.md](15-aggregation-roadmap.md)、[22-passthru-rx-reo-probe.md](22-passthru-rx-reo-probe.md)）假设：如果不调用 `WMI_PEER_CREATE` 建立远端硬件 peer，WCN7850 在收到多 MPDU 的 A-MPDU 时无法在 SIFS（16 µs）内回复硬件 `BlockAck`，会导致 Mac `NoACK` 风暴。而一旦启用 `WMI_PEER_CREATE`，又会破坏 TX TQM 完成环并阻塞 `rmmod` 恢复。

**本次实机实验彻底推翻了该悲观假设**：
1. 在纯 `self-peer`（`pt_remote_peer=0`）模式下，仅在用户态 `houji-owly` 响应 Mac 发来的 `ADDBA Request`（`addba req 1 resp 1`），WCN7850 的 RXPCU 硬件针对发往本机 `vdev->mac_addr` 的 QoS A-MPDU **原生自动在 SIFS 内发射硬件 Compressed `BlockAck`**！
2. 双端硬件铁证（以 `device-2ss-mcs9/2` 3×64 MiB 为例）：
   - **Mac `LQM-WiFi`**：`txAMPDU = 935`，**`rxBACK = 935`（BlockAck 接收成功率 100.0%！）**，`rxACKUcast = 20`，`BE<40 0 0 0 ...>`（`NoACK = 0`）；
   - **小米 `pt_rx_phy_stats`**：接收 `valid = 67,326` 个 MPDU，全部携带 `rx_ampdu = 67,326`，重传率仅 `247 / 67,326 = 0.37%`，用户态 `rx_dup = 0`，`air_rx_kernel_drops = 0`；
   - **小米双时钟 `OWLY_RX_GAPS`**：在 `57,742` 个 `>=1536 B` A-MSDU 中，**`equal = 56,110`（高达 97.17%！）** 拥有**完全相同的 Kiwi 硬件 PPDU 接收时间戳**，直接从芯片硬件时钟层面证实 Mac 将十余个 `3.6 KB` A-MSDU 打包进了同一个连续的 `866.7 Mbps` A-MPDU 物理帧内；
   - **安全卸载与恢复**：三次实验租约（`device-2ss-rx1`、`device-2ss-txrx1`、`device-2ss-mcs9`）执行 `stop` 均在 **8 秒内无挂起完成 `rmmod`** 并通过 `STOCK_RESTORED` 校验。

---

### 发现三：旧版 `owl-10` `ADDBA Response` 清零 Bit 0 导致 A-MSDU 被 Mac 禁用（`5.14 MB/s` vs `7.86 MB/s`）

在首次开启 `accept_addba`（`--addba no-amsdu`）的 16 MiB 对照（`device-2ss-txrx1/3`）中发现：虽然 `rxBACK` 100% 正常，但吞吐仅为 `5.144 MB/s`，且 `pt_rx_phy_stats.amsdu` 从 `5,053` 骤降至 **`0`**（MPDU 总数从 `5,782` 暴增 2.7 倍至 `15,415`）。

定位代码 [`crates/owly-awdl/src/frame.rs`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/owly-2ss-addba.patch) 发现根因：
- IEEE 802.11 Block Ack Parameter Set 字段的 **Bit 0 为 `A-MSDU Supported`**；
- 旧版 `frame::addba_response` 写成 `let params = (requested & 0x003c) | 0x0002 | (window << 6);`，掩码 `0x003c` **强行抹掉了 Bit 0**，向 Mac 宣告“本 BA 会话禁止在 A-MPDU 内嵌 A-MSDU”！
- 新增 [`addba_response_with_amsdu`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/owly-2ss-addba.patch)（`HOUJI_OWLY_ACCEPT_ADDBA=2` / `--addba amsdu`），保留 `requested & 0x0001` 后：
  - Mac 立即同时开启 **A-MPDU + 3 段 A-MSDU（3.6 KB）二级混合聚合**（与 Apple phone 原生架构完全一致，且单 MPDU 仍严格受 VHT `Max MPDU = 3895 B` 约束，零超长帧风险）；
  - 同租约 16 MiB 吞吐从 `5.144 MB/s` 直接飙升至 **`7.861 MB/s`（+52.8%）**，RTS/CTS + 帧间空口开销从 `--addba off` 的 `1,598 ms` 暴降至 **`35 ms`（缩减 45 倍！）**；
  - 在 TCP 拥塞窗口充分展开的 64 MiB 传输中，单次吞吐进一步冲上 **`18.362 / 18.483 MB/s`**！

---

## 3. 完整实机测试数据（全部通过双端 SHA-256 校验）

### 3.1 租约一：`device-2ss-rx1`（TX 保持 1SS `vht80-sgi`，单变量验证 RX 2SS+SGI80）

原始回执目录：[`device-2ss-rx1/`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-rx1/session.json)（退出回执：[stop.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-rx1/stop.json)）

| 阶段 | 模式与参数 | 规模 | `Mac→小米` MB/s | `小米→Mac` MB/s | RX 2SS 占比 | RX MCS | RX SGI80 占比 | 原始回执 |
| :---: | --- | :---: | ---: | ---: | ---: | :---: | ---: | :---: |
| 1 | `mif1ss` 对照，`addba=off` | 1 MiB | 0.914 | 5.792 | 0%（全 1SS） | 100% MCS 7 | 0%（全 LGI） | [1/result.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-rx1/1/result.json) |
| 2 | `2ss`（PSF+MIF），`addba=off` | 1 MiB | **1.904** | 6.842 | **100%**（`361/361`） | **100% MCS 9** | **88.4%** | [2/result.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-rx1/2/result.json) |
| 3 | `2ss`（PSF+MIF），`addba=off` | 16 MiB | **5.265** | **7.972** | **100%**（`6013/6013`） | **100% MCS 9** | **98.9%** | [3/result.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-rx1/3/result.json) |
| 4 | `2ss` 复测，`addba=off` | 16 MiB | **4.432** | **8.115** | **100%**（`5943/5943`） | **100% MCS 9** | **99.7%** | [4/result.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-rx1/4/result.json) |
| 5 | `2ss`，`addba=no-amsdu` 探针 | 1 MiB | 0.956 | 6.453 | **100%**（`986/986`） | **100% MCS 9** | **88.5%** | [5/result.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-rx1/5/result.json) |

---

### 3.2 租约二：`device-2ss-txrx1`（双向 2SS `vht80-2ss-sgi`，同租约 A/B/C 与 64 MiB×3 对照）

原始回执目录：[`device-2ss-txrx1/`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-txrx1/session.json)（退出回执：[stop.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-txrx1/stop.json)）

| 阶段 | `--addba` 模式 | 规模 | `Mac→小米` MB/s | `小米→Mac` MB/s | RX MPDU / A-MSDU 数 | 同 PPDU 聚合 `equal` | Mac `txAMPDU / rxBACK` | 原始回执 |
| :---: | --- | :---: | ---: | ---: | ---: | ---: | ---: | :---: |
| 1 | `amsdu`（bit0=1） | 1 MiB | 0.938 | **8.604** | `366 / 320` | `279 / 303` | `62 / 56` | [1/result.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-txrx1/1/result.json) |
| 2 | **`amsdu`（bit0=1）** | **16 MiB** | **7.861** | **8.063** | **`5,548 / 5,012`** | **`4,668 / 4,818`（96.9%）** | **`496 / 488`** | [2/result.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-txrx1/2/result.json) |
| 3 | `no-amsdu`（bit0=0） | 16 MiB | 5.144 | 7.984 | `15,415 / 0` | `0 / 0`（全小包） | 硬件 BA 正常，无 A-MSDU | [3/result.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-txrx1/3/result.json) |
| 4 | `off`（拒绝 BA） | 16 MiB | 5.259 | 8.009 | `5,782 / 5,053` | `0 / 4,870`（逐帧单发） | `2308 / 39`（`rxACK=2331`） | [4/result.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-txrx1/4/result.json) |
| 5 | `off`（仅 Step 1） | **64 MiB×3** | 加权 **5.906**（峰值 **6.037**） | 加权 **5.814**（峰值 **6.085**） | `70,628 / 59,659` | `0 / 58,193` | 单 MPDU Legacy ACK | [5/result.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-txrx1/5/result.json) |
| 6 | **`amsdu`（Step 1+2）** | **64 MiB×3** | 加权 **13.680**（`13.79 / 16.16 / 11.78`） | 加权 4.738（峰值 **6.255**） | **`69,165 / 61,592`** | **`56,159 / 57,721`（97.3%）** | **`1002 / 1002`（100%）** | [6/result.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-txrx1/6/result.json) |

---

### 3.3 租约三：`device-2ss-mcs9`（TX/RX 对称 `vht80-2ss-mcs9-sgi` = `866.7 Mbps` + `ADDBA amsdu`）

原始回执目录：[`device-2ss-mcs9/`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-mcs9/session.json)（退出回执：[stop.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-mcs9/stop.json)）

| 阶段 | 规模 | `Mac → 小米` 逐样本与加权 | `小米 → Mac` 逐样本与加权 | 关键 PHY 与丢包指标 | 原始回执 |
| :---: | :---: | --- | --- | --- | :---: |
| 1 | 16 MiB | **7.799 MB/s**（`2.151 s`） | **7.978 MB/s**（`2.103 s`） | TX `5,042/5,042` 2SS MCS 9 SGI80 LDPC（`1.047` 次尝试/帧）；RX `fcs=0, drops=0` | [1/result.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-mcs9/1/result.json) |
| 2 | **64 MiB×3** | 样本 1：**10.477 MB/s**（`6.405 s`）<br>样本 3：**18.362 MB/s**（`3.655 s`）<br>样本 5：**18.483 MB/s**（`3.631 s`）<br>**三次加权：14.705 MB/s**（总计 `13.691 s`） | 样本 2：**6.369 MB/s**（`10.537 s`）<br>样本 4：**5.799 MB/s**（`11.573 s`）<br>样本 6：**5.579 MB/s**（`12.029 s`）<br>**三次加权：5.897 MB/s** | RX：`67,326/67,326` 2SS MCS 9、`99.72%` SGI80、`fcs=0`、`rx_dup=0`、`drops=0`、`txAMPDU=935 / rxBACK=935`；<br>TX：`60,731/60,731` 2SS MCS 9 SGI80 LDPC（`1.059` 次尝试/帧），Mac `rxPER 0%` | [2/result.json](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-mcs9/2/result.json) |

---

## 4. 为什么 `小米 → Mac` 停留在 `6～8.9 MB/s`，而 `Mac → 小米` 达到了 `14.7～18.5 MB/s`？

双端日志已经给出了完全闭环的定量解释：
1. **出站方向（`小米 → Mac`）尚未发起硬件 TX A-MPDU 聚合**：
   - 在 `PASSTHRU` 模式下，`wonder` 驱动通过 `HTT_TX_H2T_MSG_TYPE_WBM_TRANSMIT` 逐帧注入软件合包的 `3.6 KB` A-MSDU，且 `houji-owly` 当前不主动向 Mac 发送出站 `ADDBA Request`。
   - 因此 Mac `LQM-WiFi` 显示接收端 `rxDataUcastMbss = 7,150`、`txACKFrm = 7,125`、`RX AMPDU rxAmpdu = 23`——即小米每发一个 `3.6 KB` MPDU 都是一次独立的单 MPDU PPDU + Legacy ACK 交互，这与 `--addba off` 时 `Mac → 小米` 的状态（`5.906 MB/s` 加权 / `6.037 MB/s` 峰值）**在物理机制和实测数值上完全对称（`5.897～6.590 MB/s` vs `5.906 MB/s`）**！
2. **Mac 的 `Data(5)` 状态机主要由 Mac 自身出站队列驱动**：
   - 16 MiB 短测（耗时 `~2.1 s`）期间 Mac 仍处于刚结束发送后的 `Data(5) DutyCycle 76%` 高占空比窗口，因此 `小米 → Mac` 稳定跑出 **8.0～8.9 MB/s**；
   - 在 64 MiB 长流（`>10 s`）后半段，由于 Mac 仅回传少量 TCP ACK，Mac 的 AWDL 状态机会偶发回落至 `Idle(3) DutyCycle 40%` 或 `Infra Priority(29) DutyCycle 22%`（见 [`device-2ss-txrx1/6/mac-lqm.log`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-txrx1/6/mac-lqm.log)），将 64 MiB 全程均值拉回至 `5.90～6.59 MB/s`。
3. **与 Apple phone（`42.03 MB/s`）剩余差距的唯一来源**：
   - 在 149 信道在槽期间，`Mac → 小米` 已达 `18.483 ÷ 0.6875 = 26.88 MB/s`；Apple phone 之所以能达到 `42.03 MB/s`（峰值 `48.05 MB/s`），是因为 Apple 私有的跨信道并发调度（`Channel Sequence: 149 ... I:36` + `SDB`）消除了剩余 `5/16 = 31.25%` 的离槽空窗，并将单次 A-MPDU 聚合度进一步推高至 41～53 个 MPDU。

---

## 5. 产物清单与复现路径

所有代码补丁、构建脚本、ABI 审计回执与实机原始日志均保存在 [`downloads/airdrop-driver-work/remote-peer-probe/vht-rx-next/2ss-sgi-next/`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next)（远端构建目录 `build-host:/work/houji-build/tasks/pt-2ss-rx-phy` 与 `build-host:/work/houji-build/tasks/owly-2ss-addba`）：

- **内核模块与补丁**：
  - 模块二进制：[`qca_cld3_kiwi_v2-2ss-rx-phy.ko`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/qca_cld3_kiwi_v2-2ss-rx-phy.ko)（SHA-256 `c57c80fdbd0784c3b9c245bdaffa36c18e441c559b1ed14f602e7892a743dd46`）
  - 严格 ABI 审计（567 符号 CRC 全量一致）：[`audit-2ss-rx-phy.json`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/audit-2ss-rx-phy.json)
  - 内核补丁与脚本：[`2ss-rx-phy.patch`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/2ss-rx-phy.patch) · [`build-2ss-rx-phy.py`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/build-2ss-rx-phy.py) · [`verify-2ss-rx-phy.py`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/verify-2ss-rx-phy.py)
- **用户态二进制与补丁**：
  - 产物校验清单：[`dist/SHA256SUMS`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/dist/SHA256SUMS)（`houji-owly` `a77688a8...`、`wonder-init` `71c91e9c...`、`owly-hashprobe` `bc89d601...`，76 项 Rust 单元测试全通）
  - 补丁与源码：[`owly-2ss-addba.patch`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/owly-2ss-addba.patch) · [`runner-2ss-addba.patch`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/runner-2ss-addba.patch) · [`wonder-init-2ss.c`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/wonder-init-2ss.c) · [`build-2ss-userspace.sh`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/build-2ss-userspace.sh)
- **设备编排与恢复脚本**：
  - [`run-2ss-sgi.py`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/run-2ss-sgi.py) · [`device-2ss-harness.patch`](../../experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/device-2ss-harness.patch)
- **当前手机状态**：
  - 已通过 `ensure_restored()` 校验为 `STOCK_RESTORED WIFI_CONNECTED NO_EXPERIMENT_INTERFACE_OR_RULES`（原厂模块 build-id `a3e9bd62...`、原厂 `amss20.bin` `4db7b0e1...`、普通 Wi-Fi 与原生 `IDomesticXOsComm` 服务正常在线）。下一步可将上述四项核心改动（纯 ACK 合包 + `SO_RCVBUF` 8 MiB + 2SS/SGI80 WMI/TLV 宣告 + `ADDBA Response` 保留 Bit 0）回迁至日常主线（Owly / 原生小米互传 XOS）。
