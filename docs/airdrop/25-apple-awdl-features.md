# Apple phone 与 Apple phone 原生 AWDL 内核/驱动/固件特性解析及 houji 提速评估

2026-11-11。在**不修改 Mac 发送端任何代码或系统参数**的前提下，完成 **Mac ↔ Apple phone (`iPhone18,3`, iOS 26.5.2)** 的 `awdl0` 实测，并与 **Mac ↔ Apple phone (`iPhone8,2`, iOS 15.7.1)** 及修复后的 **Mac ↔ 小米 14 (`houji`, `device-rx-phy4`)** 的 Mac 端内核（`wlan0:com.apple.p2p.awdl0`）、Broadcom 4377 驱动（`AppleBCMWLANCore`）和固件遥测（`airportd` `LQM-WiFi`）日志进行逐项比对，确认苹果原生 AWDL 达到 `18~48 MB/s` 所开启的全部底层特性，同时给出小米 14 推进 **2×2 MIMO + SGI80 + ADDBA BlockAck** 的工程难度与风险评估。 [完整日志摘录](../../experiments/iphone-radio-research/Apple phone-vs-6sp-vs-houji-mac-logs.txt) · [结构化计数与速率摘要](../../experiments/iphone-radio-research/Apple phone-awdl-bench-summary.json)

---

## 一、 三机实测速率对照

1. **小米 14 (`houji`, `02:00:00:00:00:06`)**：经 [`ack-coalesce-rcvbuf-next`](../../experiments/remote-peer-probe/vht-rx-next/ack-coalesce-rcvbuf-next) 修复反向纯 TCP ACK 争用与 `AF_PACKET` `SO_RCVBUF` 突发丢包后，64 MiB × 3 轮加权速率从 `2.894 MB/s` 提升至 **`4.532 MB/s`**（单样本最高 **`4.662 MB/s`**，全程 0 丢包、第 2/3 对 0 重传），`小米 → Mac` 加权 **`6.590 MB/s`**（16 MiB 最高 **`8.894 MB/s`**）。实验结束后已恢复 `STOCK_RESTORED`。详见 [Owly 149 信道交接](24-owly-houji-handoff.md)。
2. **Apple phone (`02:00:00:00:00:05`)**：原生 `awdl0` 双向 TCP 16 MiB 加权 `Mac → iPhone` **`18.41 MB/s`**、`iPhone → Mac` **`8.77 MB/s`**；64 MiB `Mac → iPhone` 整段 **`24.76–26.19 MB/s`**（200 ms 窗口峰值 **`41.99 MB/s`**）。详见 [Apple phone 原生 AWDL 对照](18-iphone-radio-reference.md)。
3. **Apple phone (`02:00:00:00:00:04`, `iPhone18,3`, iOS 26.5.2)**：
   - **纯 `awdl0` 链路满载测试（`Mac → Apple phone`）**：使用 `Network.framework`（`nw_parameters_set_include_peer_to_peer(params, true)`）直连 `2001:db8::4e%awdl0:65255`，由 `awdl0` 接口 `ifi_obytes` 与内核 `Cached:<02:00:00:00:00:04>` 硬件计数双重校验：
     - 冷启动单轮（含从 `Infra Priority` 切入 `Data` 态的 ~0.62 s 爬坡）：`16.14 MiB / 1.448 s` = **`11.15 MB/s`**（切入 `Data` 态后稳态段约 `16.04 MB/s`）。
     - `Data` 态热身完成后连续 3 轮 × ~16 MiB：
       - Round 1：`16.88 MiB / 0.399 s` = **`42.27 MB/s`**
       - Round 2：`15.57 MiB / 0.324 s` = **`48.05 MB/s`**
       - Round 3：`15.25 MiB / 0.412 s` = **`37.03 MB/s`**
       - **三轮总计（`47.71 MiB / 1.135 s` 加权平均）**：**`42.03 MB/s` (`336.3 Mbps`)**。
   - **`CoreDevice` 双向 16 MiB × 3 轮文件传输（`xcrun devicectl device copy`，走 `en0` ch36 HE80 `nss=2` 加密隧道，测后已清理临时文件）**：`Mac → Apple phone` 热态 **`16.53–17.96 MB/s`**，`Apple phone → Mac` **`5.28–6.48 MB/s`**（受限于 `CoreDevice` 用户态文件服务封包；因 Apple phone 未越狱且当前 Mac 钥匙串无 Apple Development 签名证书，未在 Apple phone 本地部署自定义 `awdl-hashprobe` 发送端）。

| 对照组合 | `Mac → 设备` (16 MiB) | `Mac → 设备` (持续 / 峰值) | `设备 → Mac` (16 MiB) | Mac 侧改动 |
|---|---:|---:|---:|---|
| **Mac ↔ 小米 14（修复前）** | `2.45 MB/s` | `2.89 MB/s` | `8.08 MB/s` | 无 |
| **Mac ↔ 小米 14（`device-rx-phy4`）** | **`4.31 MB/s`** | **`4.53 / 4.66 MB/s`** | **`8.00–8.89 MB/s`** | 无 |
| **Mac ↔ Apple phone (`awdl0`)** | `18.41 MB/s` | `26.19 / 41.99 MB/s` | `8.77 MB/s` | 无 |
| **Mac ↔ Apple phone (`awdl0`)** | **`42.03 MB/s`** (`11.15` 冷启) | **`42.03 / 48.05 MB/s`** | `6.48 MB/s` (`devicectl` `en0`) | 无 |

---

## 二、 Mac 内核、驱动与固件日志揭示的五大关键特性

Mac 端无需做任何修改——`IO80211AWDLPeerManager`、`AppleBCMWLANCore` 驱动与 Broadcom 4377 微码固件会根据对端设备宣告的 AWDL TLV 与 802.11 握手结果，自动开启或关闭以下 **5 项硬件/驱动/固件特性**：

### 1. 固件级 802.11 BlockAck (`ADDBA` / `rxBACK` / `txBACK(Ucode)`) 与高密度 A-MPDU 聚合（41~53 MPDU/PPDU vs 单帧 RTS/CTS + Legacy ACK）

这是决定 `Mac → 设备` 吞吐上限的**第一核心分水岭**：

- **Mac → 小米 14 (`02:00:00:00:00:06`) 固件日志**：
  ```text
  LQM-WiFi: (HighBand) txRTSFrm=5916 txRTSFail=0 rxCTSUcast=5712 rxRTSUcast=0 txCTSFrm=0 txAMPDU=5777 rxBACK=38 txPhyError=0 txAllFrm=13250 txMPDU=0 txUcast=5807 rxACKUcast=5766
  LQM-WiFi: RX AMPDU (HighBand) rxAmpdu=33 txBACK(Ucode)=33 rxMpduInAmpdu=34 ... rxaddbareq=0 txaddbaresp=0 rxbar=0
  ```
  - 因小米端未完成 `ADDBA` 协商（`rxaddbareq=0 txaddbaresp=0`），Mac 固件退化为 **1 个 MPDU 占 1 个独立 PPDU**（`txUcast=5,807` vs `txAMPDU=5,777`，聚合比 **`1.005`**），且**每个单帧前强制插入 `RTS/CTS` 保护（`txRTSFrm=5,916`、`rxCTSUcast=5,712`），帧后等待单播 Legacy ACK（`rxACKUcast=5,766`）**。
  - 每个 3-MSDU A-MSDU（约 `3,780 B`）除约 `104 µs` 物理层载荷时间外，还要额外承担 `RTS + SIFS + CTS + SIFS + PLCP + SIFS + Legacy ACK + DIFS/Backoff`（约 `140~180 µs`）固定空口开销，直接把 149 信道在槽吞吐锁死在 `~6.8 MB/s`（折合 `11/16` 占空比后即实测的 **`4.53~4.66 MB/s`**）。

- **Mac → Apple phone (`02:00:00:00:00:04`) 与 Apple phone (`02:00:00:00:00:05`) 固件日志**：
  ```text
  # Apple phone (47.71 MiB / 35,187 MPDUs):
  Cached:<02:00:00:00:00:04> C: txQ[35187][BE:35177,BK:10,VI:0,VO:0] ... txNoAck[0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0]
  LQM-WiFi: (HighBand) [00:15:26 + 00:15:31 合计] txRTSFrm=648 txAMPDU=863 rxBACK=759 txUcast=928 rxACKUcast=118
  LQM-WiFi: RX AMPDU (HighBand) rxAmpdu=845 txBACK(Ucode)=487 rxMpduInAmpdu=1740 ... rxaddbareq=1 txaddbaresp=1
  ```
  ```text
  # Apple phone (64,037 MPDUs):
  Cached:<02:00:00:00:00:05> txQ[64037][BE:64037,BK:0,VI:0,VO:0]
  LQM-WiFi: (HighBand) txRTSFrm=389 txRTSFail=5 rxCTSUcast=286 txAMPDU=1186 rxBACK=1146 txUcast=1227 rxACKUcast=43
  LQM-WiFi: RX AMPDU (HighBand) rxAmpdu=314 txBACK(Ucode)=304 rxMpduInAmpdu=1396 ... rxaddbareq=2 txaddbaresp=2 rxbar=2
  ```
  - iPhone 在建连瞬间即完成 `ADDBA` 双向握手（`tid=0, window=64, immediate=true, amsdu=true`）。
  - Mac 固件随即启用硬件高密度 A-MPDU 发送与微码 BlockAck（`txBACK(Ucode)`）：向 Apple phone 发送 **`35,187` 个 MPDU 仅耗时 `863` 个 `txAMPDU` PPDU（平均每帧聚合 `40.8` 个 MPDU ≈ 60 KB；6s Plus 达 `53.4` 个 MPDU/PPDU）**，`RTS/CTS` 与 `BlockAck` 开销被均摊稀释超过 **40 倍**，且全部 16 个槽位 `txNoAck` 均为 **`0`**。

---

### 2. 全 16-Slot 跨信道满占空比数据通路（`I:36` 基础网信道共享 + `ch6` 并发数据）vs 小米 `11/16` 槽位空窗

对比内核 `wlan0:com.apple.p2p.awdl0` 记录的 `Caching peer` 与 16 个 EAW Slot 的成功发包分布 `txOk[Slot0..Slot15]`：

- **小米 14 (`02:00:00:00:00:06`)**：
  ```text
  Caching peer: (02:00:00:00:00:06) (I:0) (V:34) (CC:149).
  AWDL ON:[infra(36) 24%] [sdb:0] (6/149/149) [149 149 149 149 149 149 36 36 6 149 149 149 149 149 36 36] Data + Infra Idle
  Cached():<02:00:00:00:00:06> (RSSI: -39|-44) (Non-SDB:infra on 0) (infraRT:No)
  Cached:<02:00:00:00:00:06> txQ[16739] txOk[1742 1490 1587 1518 1414 1413 3 0 0 1748 1722 1888 1922 1689 12 3]
  ```
  - 小米固定驻留 149 信道且在 Data Path State TLV (`TLV 12`) 上报 `I:0`（未关联同频基础网 AP）。
  - Mac 在 `Data + Infra Idle` 调度的 Slot `6, 7`（`ch36`，32 ms）、Slot `8`（`ch6`，16 ms）和 Slot `14, 15`（`ch36`，32 ms）切离 149 信道，这 5 个槽位对小米几乎零发包（`3, 0, 0` 与 `12, 3`），每 256 ms 周期存在 **80 ms（31.25%）空窗**，并在 Mac 内核触发 `fq_detect_dequeue_stall: iface: awdl0` 队列停滞记录。

- **Apple phone (`02:00:00:00:00:04`)**：
  ```text
  Caching peer: (02:00:00:00:00:04) (I:36) (V:a0) (CC:149) SDB.
  Cached():<02:00:00:00:00:04> C: (RSSI: -23|-22) (SDB:infra on 36) (infraRT:No)
  Cached:<02:00:00:00:00:04> C: txQ[35187] txOk[1630 1513 2725 2777 2519 2095 1164 1953 342 2192 3046 3332 3785 2251 1702 2151]
  Cached:<02:00:00:00:00:04> C: RxPkts[207 218 331 314 305 283 147 281 32 267 400 422 497 262 227 276]
  ```
  - Apple phone 上报 `(I:36)`（与 Mac `en0` 同处 `ch36`），并在 Channel Sequence (`TLV 18`) 中包含 `149`、`36` 与 `6`。
  - **全部 16 个 Slot（`16/16 = 100%`）连续高速收发**：不仅在 `ch149` 槽位每槽收发 `1,513~3,785` 帧，切回 `ch36`（Slot `6, 7, 14, 15`）时每槽仍发送 `1,164~2,151` 帧，甚至在 `2.4 GHz ch6`（Slot `8`）也发送了 `342` 帧，彻底消除了跨信道周期性断流。

---

### 3. Apple phone 新一代射频特性：`SDB` (Simultaneous Dual Band)、`V:a0` 与 `ProMode` (6 GHz) 能力检查

对比三台设备在 Mac 内核缓存时的能力签名：
- **小米 14 (`Houji-Owly`)**：`(I:0) (V:34) (CC:149)` → `(Non-SDB:infra on 0)`（伪装 AWDL 2.2 `0x22=34`）
- **Apple phone (iOS 15.7.1)**：`(I:36) (V:65) (CC:149)` → `(Non-SDB:infra on 36)`（AWDL `0x41=65`）
- **Apple phone (iOS 26.5.2)**：
  ```text
  wlan0:com.apple.p2p.awdl0: Caching peer: (02:00:00:00:00:04) (I:36) (V:a0) (CC:149) SDB.
  wlan0:com.apple.p2p.awdl0: Cached():<02:00:00:00:00:04> (RSSI: -36|-41) (SDB:infra on 36) (infraRT:No)
  wlan0:com.apple.p2p.awdl0: monitorProModeState[40121]: cannot use ProMode (mode 1). allowed 0, AirDrop count (legacy 0 6G 0, sender legacy 0 6G 0, cached: 1) has common 6G channel 0 migration 0
  ```
  1. **`SDB`（硬件双频并发 RSDB）**：Apple phone 在 Data Path State TLV (`TLV 12`) 中宣告 `SDB` 能力（见 [`public-ios26-awdl-mif.json`](../../experiments/iphone-radio-research/public-ios26-awdl-mif.json) `flags=0x9f7c`），支持 2.4 GHz 社交信道与 5 GHz 高速数据信道硬件并发，免去单射频切频死区。
  2. **AWDL 协议版本 `V:a0` (`160`)**：iOS 26 原生协议栈版本标识，选主度量 `RMet:522`（高于 Mac 的 `SMet:510`），自动接管 AWDL Master 时钟。
  3. **`ProMode` 6 GHz 高速迁移探测**：Mac 缓存 Apple phone 后立即调用 `monitorProModeState` 与 `updatePref6GChannel` 评估 AirDrop ProMode（6 GHz 宽频迁移），当前仅因国家码 `country:CN`（`allowed 0`）留在 5 GHz 80 MHz。

---

### 4. 2×2 MIMO (`nss=2`, VHT MCS 0–9, SGI80, LDPC) vs 小米 1×1 单流 (`nss=1`, MCS 0–7, LGI)

- Mac 内核日志确认 AWDL 链路激活双空间流射频链：
  ```text
  wlan0:com.apple.p2p.awdl0: updateHTandVHTCapsForRSDB[5413]: Num of Chains to use: 2 (original 2) number of core 2 dynSdbState 0
  ```
- 原生 iOS 设备在 **PSF 与 MIF 两种动作帧中均携带** 完整双流能力（见 [`public-ios26-awdl-mif.json`](../../experiments/iphone-radio-research/public-ios26-awdl-mif.json)）：
  - `TLV 7` (HT Capabilities): `capabilities = 0x016f`（含 `RX STBC`、20/40 MHz SGI、40 MHz），`ampdu_params = 0x17`，`rx_mcs_mask = ffff`（2SS）。
  - `TLV 17` (VHT Capabilities): `0x03b199fa`（`Max MPDU = 7991`、`RX LDPC = true`、`SGI80 = true`、`max_ampdu_exponent = 7` 即 1,048,575 B），`rx_mcs_map = 0xfffa`、`tx_mcs_map = 0xfffa`（**2SS MCS 0–9**，最高 PHY 速率 **`866.7 Mbps`**）。
- 相比之下，小米 [`houji-owly`](23-vht-rx-capacity.md) 当前仅在 MIF 中宣告保守的 **1SS MCS 0–7、无 SGI80、无 LDPC、`max_ampdu_exponent = 0`**（`bf0c00000000fcff0000fcff0000`，最高 PHY 速率仅 **`292.5 Mbps`**），单看物理层速率上限就相差 **2.96 倍**。

---

### 5. 固件级 `MITxOnDwellChannel`、`PSFDwell {110ms 110ms}` 与 `SUI` / `UMI` 单播动作帧

与 Apple phone / Apple phone 通信时，Mac 驱动向固件下发：
- `configureMITxOnDwellChannel awdl_config 128, success (ret 0)`
- `setPSFDwell::Setting PSFDwell(1) to {110ms 110ms}`（`MIoNDwell:760, dwelEvent:64`）
- 并实时收发 `IO80211AWDLPeer::actionFrameReport SUI from 02:00:00:00:00:04 ... ssth 0x20002201` 与 `UMI` 单播控制帧以维护窗口和缓存保活；而与小米通信时上述计数器均为 `0`。

---

## 三、 小米 14 推进 `2×2 MIMO + SGI80 + ADDBA BlockAck` 的工程难度评估

结合 `houji` 的硬件底座（Qualcomm `WCN7850` / `qca_cld3_kiwi_v2` + 三星 S24U `amss20.bin` PASSTHRU 固件）以及前期实验积累（[15-aggregation-roadmap.md](15-aggregation-roadmap.md)、[22-passthru-rx-reo-probe.md](22-passthru-rx-reo-probe.md)、[23-vht-rx-capacity.md](23-vht-rx-capacity.md)），这三项特性的实现难度**高度分化——前两项极易落地且低风险，第三项（BlockAck）则需拆分为“零风险快速证伪探测”与“高难度固件级改造”两步**：

| 待验证特性 | 工程难度 | 涉及层级与改动量 | 内核/固件风险 | 预期独立收益 |
|---|---|---|---|---|
| **1. `SGI80`（收+发）** | **★☆☆☆☆（极低）** | 发送端 `wlan_hdd_passthru_control.c` 已支持 `wmi_vdev_param_sgi`（[10-throughput-limits.md](10-throughput-limits.md) 已验 `sgi=1`）；接收端仅改 `owly` `TLV 7/17` 能力位 | 零风险（不触碰 DMA/peer） | PHY 速率 `+11.1%` |
| **2. `2×2 MIMO (2SS MCS 0–9)`** | **★★☆☆☆（低）** | 用户态 `owly` 更新 `TLV 7` (`ffff`) + `TLV 17` (`0xfffa` + LDPC)；驱动 [`wideband/control.patch`](../../experiments/wideband/control.patch) 放开 `nss=1..2, mcs=0..9` 并修 `hdd_assemble_rate_code` 硬编码 `nss=0` 及 `wmi_vdev_param_nss`（约 15 行） | 低风险（原厂 `wiphy` 已确认 5 GHz 2SS MCS 0–9 866 Mbps） | PHY 上限 **`292.5 → 866.7 Mbps`（×2.96）**，即使无 BA 也可显著缩短每帧空口占用 |
| **3A. `ADDBA` 纯用户态应答探测（快速证伪）** | **★★☆☆☆（低）** | 保持现有驱动不变，仅在 `owly` 启用 `accept_addba=true` + VHT `max_ampdu_exponent=7` 跑 1 MiB 短测，检查 Mac `LQM-WiFi` `rxBACK` | 零内核崩溃风险（若固件不回硬件 BA，仅当轮 TCP 超时回退） | 10 分钟内定性确认 `amss20.bin` PASSTHRU 是否具备无状态硬件 RX BA 应答 |
| **3B. 固件级硬件 `ADDBA / BlockAck + REO`（若 3A 为否）** | **★★★★★（极高 / 研究级）** | 需硬件微码在 **`SIFS = 16 µs`** 内回发压缩 `BlockAck`（用户态 `AF_PACKET` 往返百微秒级，物理上不可能软件代回） | **高风险**：此前 [`device-map-only1`](22-passthru-rx-reo-probe.md#拆分暂停与-peer-setup) 已证实对 PASSTHRU vdev 执行 `WMI_PEER_CREATE` + `HTT map` 会导致 raw TX TQM 完成归零并需重启恢复 | 若打通则直接解锁 `18~40+ MB/s` 原生级吞吐 |

### 为什么 `2×2 MIMO + SGI80` 容易，而 `ADDBA BlockAck` 必须分两步走？

1. **`2×2 MIMO + SGI80` 的源码阻塞点完全清楚且非常浅**：
   - 接收方向（`Mac → 小米`）：[stock wiphy 预检](22-passthru-rx-reo-probe.md) 已证实小米 14 硬件在 5 GHz 149 信道原生支持 2×2 VHT MCS 0–9（866.7 Mbps）。此前 Mac 只发 `1SS MCS7 LGI`，纯粹是因为 [`owly-outgoing-vht.patch`](../../experiments/remote-peer-probe/vht-rx-next/outgoing-advert/owly-outgoing-vht.patch) 保守填了 `0xfffc`（1SS）与 `cap=0`。修改 `owly` 出站 `TLV 7/17`（必要时在 PASSTHRU vdev 补一行 `wmi_vdev_param_nss = 2`），即可用现有的 [`qca_cld3_kiwi_v2-rx-phy.ko`](24-owly-houji-handoff.md) 直接读取硬件描述符验收 Mac 是否切到 `2SS MCS 8/9 SGI80`。
   - 发送方向（`小米 → Mac`）：查阅 [`wlan_hdd_passthru_control.c`](../../driver/wlan/qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c#L329-L340) 与 [`wideband/control.patch`](../../experiments/wideband/control.patch#L37-L106) 可见，当前驱动有两处人工硬编码限制：
     - `hdd_pt_control_validate()` 限制了 `params->tx_rate.nss != 1 || params->tx_rate.mcs > 7`；
     - `hdd_pt_control_finish_start()` 调用 `hdd_assemble_rate_code(..., 0, params->tx_rate.mcs)` 把空间流索引硬编码成了 `0`（即 1SS），而 `wmi_vdev_param_sgi` 其实早已接通。
     - 将这两处改为按 `params->tx_rate.nss`（`1..2`）和 `mcs`（`0..9`）下发并同步设置 `wmi_vdev_param_nss`，不涉及任何 peer 表、REO 队列或 DMA 生命周期变更，编译与实机验证成本极低。

2. **`ADDBA BlockAck` 的物理硬约束与未知边界**：
   - **物理硬约束**：802.11 Immediate BlockAck 要求接收端在收到 A-MPDU / `BAR` 后的 **`SIFS = 16 µs`** 内由射频硬件微码立即发出 `BlockAck` 控制帧；Linux 用户态 `AF_PACKET` 接收再注入的延迟在 `200~1,000 µs` 量级，**绝无可能由 `owly` 在用户态软件伪造实时 `BlockAck`**。
   - **值得花 10 分钟先做 Stage 3A（纯用户态开启 `accept_addba`）的线索**：在 `Xiaomi14-PixelOS-Airdrop` 移植仓的 clean-room 适配文件 [`wlan_hdd_passthru_control.c:275-298`](../../driver/wlan/qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c#L275-L298) 中（注：后续核查证实该文件为第三方移植作者编写的最小胶水层，三星 S24 Ultra 原厂对应实现为高通官方 `wlan_hdd_wondertap.c`，详见 [27-firmware-ba-passthru-verification.md](27-firmware-ba-passthru-verification.md)），曾留下如下设置：
     ```c
     /* The documented maximum is one MPDU/MSDU. Leave RX BA limits untouched.
      * This prevents combining TX packets; firmware application has no ACK here. */
     WMI_VDEV_CUSTOM_RX_AGGR_SZ_DIS_SET(cmd->enable_bitmap, 1);
     ```
     之前我们在 `Houji-Owly` 上**从未在保持纯 `self-peer` 驱动不变的情况下单独测试过回复 `ADDBA Response (status=0)`**。虽然常规关联模式通过 peer AST/BA 记分板管理重排，但既然 Mac 端 `LQM-WiFi` 现在能直接看到 `rxBACK` 计数，只需在用户态放开 `accept_addba` 跑一次 1 MiB 烟测：
     - 若 Mac 日志出现 `rxBACK > 0` 且 SHA-256 校验通过（由 `owly` 按 802.11 序号做软件重排去重），则无需触碰高风险驱动即可直接吃到 A-MPDU 红利（**后续实测结果**：该假设在 [26-2ss-sgi80-addba-probe.md](26-2ss-sgi80-addba-probe.md) 中**完全成功**，仅需保留 `ADDBA Parameter Set` Bit 0 `A-MSDU Supported = 1`，WCN7850 在纯 `self-peer` 下即可硬件 SIFS 回复 `BlockAck`，将 `Mac → 小米` 推升至峰值 `18.483 MB/s`）；
     - 若 Mac 日志显示 `txAMPDU` 发出后 `rxBACK = 0`、`txNoAck` 暴增，则说明无远端 peer 时不会硬件回 `BlockAck`。
   - **关于 Stage 3B（固件级远端 Peer + 双向硬件 BA/REO）的修订说明（重要勘误）**：
     - 此前 [22-passthru-rx-reo-probe.md](22-passthru-rx-reo-probe.md) 观察到在 `PASSTHRU` vdev 上仅执行 `WMI_PEER_CREATE` + `HTT map` 会导致 raw TX 停滞（`115` 次入环、`0` 次 TQM 完成），当时曾初步怀疑存在“固件内部互斥”。
     - **2026-11-11 补充核查勘误（见 [27-firmware-ba-passthru-verification.md](27-firmware-ba-passthru-verification.md)）**：交叉核对公开 Qualcomm WMI/DP 规范与三星 S24 Ultra 原厂 `qca_cld3_kiwi_v2.ko` 反汇编后已证实，**`amss20.bin` 固件内部根本不存在所谓“PASSTHRU 与远端 Peer / BA 互斥”**！原厂 `wondertap` 不仅原生支持最多 7 个关联远端 peer，还通过 `wmi_service 338`（`passthru_ampdu_support`）原生支持逐包 TX A-MPDU 与 QoS TID 硬件聚合。此前 TX 卡死的真实根因是移植仓主机驱动：① 只发了 `WMI_PEER_CREATE_CMDID` 而漏发 `WMI_PEER_ASSOC_CMDID`（导致硬件 AST 命中了未关联暂停的固件 peer 队列）；② [`dp_peer.c`](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_peer.c#L2936-L2952) 将 `bss_peer` 的 MAC 比较条件写反了 180°；③ 缺失原厂 `dp_start_xmit_passthru` → `dp_tx_hw_enqueue_be` 的按关联状态覆写 TCL `search_index` 逻辑。

---

## 四、 建议执行顺序

1. **第一步（低难度、高确定性、零重启风险）**：先做 **`2×2 MIMO (2SS MCS 0–9) + SGI80 + RX LDPC`**（同时覆盖 `owly` 出站 `TLV 7/17` 与 `wideband` 驱动 `nss=2`），用 `device-rx-phy` 框架实测双向 16 MiB / 64 MiB 吞吐增益及真实 RX/TX PHY 描述符。
2. **第二步（10 分钟低风险定性探针）**：在已打通 2×2 MIMO 的基础上，保持内核驱动不变，仅在用户态做一次有界 1 MiB **`accept_addba` + 软件序号重排** 探测，读取 Mac `LQM-WiFi` 的 `rxBACK` 给出定论。
3. **第三步（信道占空比优化 / 视前两步收益决定是否攻坚 Stage 3B）**：若前两步已将双向稳定吞吐推升至 `10~15+ MB/s` 目标区间，可优先回迁至原生 MIShare/XOS；若仍需冲击 `25~40 MB/s`，再立项攻关 `amss20.bin` 的跨角色硬件 BA 或双信道（`ch149 + ch36`）跟随调度。
