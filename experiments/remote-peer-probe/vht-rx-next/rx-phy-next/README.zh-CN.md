# Kiwi 普通 RX descriptor 的 PHY 观测

本轮开始前检索了 Qualcomm Kiwi、ath11k/ath12k 普通 data descriptor、radiotap 和 Owly 硬件 BA 的公开源码和实测。Linux ath11k 和 ath12k 都从普通 RX descriptor 解码 packet type、BW、MCS、NSS、GI；这证明同类架构有普通数据路径的观测先例，具体字段仍以本机 Kiwi-v2 源码为准。Owly PR 58 在 MT7612U/149 上的 30 MB 传输由 47 秒降到 14 秒，前提是实际接收 A-MPDU 并由硬件发 BA。该结果不提供 Kiwi PASSTHRU 的能力证据。 [ath11k data RX](https://github.com/torvalds/linux/blob/master/drivers/net/wireless/ath/ath11k/dp_rx.c) · [ath12k data RX](https://github.com/torvalds/linux/blob/master/drivers/net/wireless/ath/ath12k/dp_rx.c) · [Owly PR 58](https://github.com/t4t5/owly/pull/58)

## 已定位

- 4224 构建的 `dp_rx_deliver_raw_passthru` 在持有效 vdev 引用、确认线性完整 raw、TLV size 和 headroom 后取 descriptor。该位置还没加 radiotap，也没把 nbuf 交给 OSIF，适合纯观察。REO no-peer 先按配置检查真实长度，再 pull 同一前缀并调用此函数。 [当前构建路径与位置](kiwi-rx-phy-path-source.txt)
- 真实编译 Kiwi-v2 非 compact layout。Kiwi HAL 已给普通 DP 绑定 packet type、BW、MCS、GI、NSS 和 reception type。RX_MSDU_END body +0x58 的位依次是 packet type 11:8、GI 13:12、MCS 17:14、BW 20:18、reception 23:21、SS bitmap 30:24、MSDU_DONE_COPY 31。NSS getter 返回 bitmap 的 popcount，MCS getter 返回 raw index。普通统计对 HT NSS2 另加 8，这个归一索引不能覆盖原读数。 [确切 getter、位域、编译 flags](kiwi-rx-phy-source.txt) · [TLV 布局和 data HTT](kiwi-rx-phy-layout-source.txt)
- 原 272 B clock/RSSI 辅助路径交叉检查 RX_MSDU_END PPDU ID 与 RX_MPDU_START PPDU ID。其时间戳/RSSI 校验只适用于那两项。PHY 候选需另校验 target/尺寸、完成位、两处 PPDU 一致和字段组合，允许 PPDU ID 正常回绕到 0。没有独立 PHY validity bit 的源码依据时，只称通过结构与范围校验的 descriptor 读数。
- 普通 DP 统计对 Kiwi A-MPDU 位留有 `ring_desc` TODO。当前观察位置没有各 REO 来源都可靠传入的 ring 记录，因此新 PHY 观察保持 A-MPDU 未知。QoS Control 的 A-MSDU present、ACK policy 以及 Frame Control Retry 是同一 raw 帧的独立字段，均不能证明实际回 ACK 或 BA。

## 最小旁路

以已通过的 4224 树为唯一模块基线，默认关闭。可选 load-only allowlist 为本轮 Mac TA，同时要求 RA 与临时 vdev 本机 MAC 相等、AWDL BSSID、完整的 3-address QoS Data 与非错误 FCS。命中后只读原 descriptor，分开记录 valid、layout/done/PPDU/field 不成立原因以及 raw PHY 位域的分桶。记录 packet type×BW 联合桶，并给有效 HT/VHT 的原始 MCS、NSS、GI 及 reception。普通接收函数所有返回、skb 所有权、radiotap、HTT filter、固件参数和 BA 路径保持现行行为。计数从模块只读快照读取，恢复前后由既有 token 生命周期绑定。

先以已通过的 MIF-only 配置跑一小轮双向 SHA，检查目标 QoS 数据确实命中且 invalid 原因可解释。实际读到 VHT80 时才能开始用它解释速度差异；如果快照全部无效或只见窄带，分别记录。后续才决定是否对照广告 off/on。当前的应用 `ack` 参数只设置小米出站 no-ACK 选择，不能用于证明小米接收后发送的 SIFS ACK。PASSTHRU vdev type、self MAC 和 UP/BSSID 是固件下发的前置配置；目前没有找到单独控制本机接收普通 ACK 的 PASSTHRU 宿主参数。现有吞吐与 retry 只能形成假说。 [既有 peer/ACK 约束](../../../../../docs/airdrop/15-aggregation-roadmap.md)

## 149 实测

远端构建通过，模块 `d966a7d4…` 的 567 个导入、CRC、模块布局和依赖与参考相符。观察的 TA 默认全零，三个参数权限均为 0400。实际实验固定 4224、149、VHT80 TX 请求、原 HT 限制的 A-MSDU、MSS 1160、接收 BA off。同一 lease 启动一次，阶段间只停启 app/probe 和专用 TUN。 [ABI](audit-rx-phy.json) · [同一租约](device-rx-phy1/session.json)

| 阶段 | Mac→小米 MB/s | 小米→Mac MB/s | Mac 源匹配 / 有效 QoS 帧 | A-MSDU present | Retry |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 MiB MIF | 0.816 | 2.140 | 445 / 445 | 374 | 3 |
| 16 MiB off | 2.427 | 7.979 | 5773 / 5773 | 5064 | 26 |
| 16 MiB MIF | 2.660 | 8.063 | 6224 / 6224 | 5058 | 35 |
| 16 MiB MIF，同向快照 | 2.825 | 7.941 | 5144 / 5144，仅 Mac→小米 | 4888 | 1 |

每个阶段双向 SHA 均通过。有效 descriptor 一致给出 VHT80、MCS 7、NSS 1、GI raw 0、SU。前 3 行合计两个方向，包含小米发送时从 Mac 回来的小包。阶段 4 单独读取样本边界，Mac→小米有 4610 帧 raw 长度至少 3072 字节，小米→Mac 时观察到 503 个 Mac 源帧，454 个小于 512 字节，没有达到 1536 字节的样本。关闭广告后同一 Mac 仍使用同一 PHY，能力缓存可能延续。不能再把之前广告关闭轮的低速解释成必然 HT 或窄带。QoS ACK policy 均为 raw 0，仅为帧头请求。Retry 仅计算已交付帧，A-MPDU 和实际发送的 ACK/BA 保持未知。 [阶段 1](device-rx-phy1/1/result.json) · [阶段 2](device-rx-phy1/2/result.json) · [阶段 3](device-rx-phy1/3/result.json) · [分方向和 TCP 时间线](device-rx-phy1/4/result.json)

阶段 4 的 Mac→小米有 30 个同 fd TCP 快照。前 1.405 秒的发送栈计数只增 245,400 B，约 0.175 MB/s，0.604–0.805 秒无进展。该段有四个 loss recovery 采样，拥塞窗一度缩到约一个 MSS，发送缓存到 1.205 秒已满 4 MiB。1.405–5.805 秒的发送栈计数增 15.807 MB，约 3.593 MB/s，后段仍低于 6 MB/s。全段 SRTT 中位 24.5 ms、最高 149 ms，接收窗中位 1.682 MB。采样范围内重传累计增加 22,688 B，不能用低总比例抹去启动恢复。Mac 的两条 Data 快照都是 11 个 149 槽。TCP_INFO 不提供此处需要的重复 ACK 计数，发送栈字节不能作对端收讫或空口测量。 [原始时序和调度](device-rx-phy1/4/result.json)

## 现有 TSFT 的下一切点

先检索了 radiotap TSFT、A-MPDU 状态、Owly 调度与公开性能实验。标准 TSFT 为 µs 单位，Kiwi 实验路径实际把自己的 960 MHz RX descriptor 时钟除以 960 放入该字段，只有现有 metadata 守卫通过的帧才有这个字段。设备启动已传 `pt_rx_metadata=1`，尚无本轮该字段覆盖率。用户态 radiotap parser 已做长度和对齐，但现在丢弃 TSFT 值。独立诊断应用可以读取该值，并在已有 TA/频道过滤后，针对同 RA/BSSID、长度至少 1536 的 QoS 帧，累积两种相邻间隔，分别来自 descriptor 时钟与用户态 `Instant`。缺失、同值和倒序分开，两个时钟不直接相减；仅比较各自相邻帧间隔。相同 timestamp 可能只是同一 PPDU 或时钟粒度，不判为 A-MPDU。没有逐帧有效的 A-MPDU 标志时，间隙也无法区分 Mac 未发和未捕获。 [radiotap TSFT](https://www.radiotap.org/fields/TSFT.html) · [radiotap A-MPDU status](https://www.radiotap.org/fields/A-MPDU%20status.html) · [Owly AWDL 窗口](https://github.com/t4t5/owly/blob/main/docs/SPEC.md#3-link-layer-awdl) · [当前用户态 parser](../../../owly-evaluation/owly/crates/owly-radio/src/radiotap.rs#L36)

阶段 5 继续同一租约，只在 idle 暂时装载 SHA `8b620c89…` 的诊断应用。两端 SHA 通过，收到 4817 个目标大帧且全有 clock 字段，无同值和倒序。radio 4816 个正增量共 6.771792 s，host 4816 个相邻差值共 6.769082 s。两条时钟各有 12 个 ≥128 ms 间隔，分别累计 2.794312 s 和 2.805107 s。radio 短于 2 ms 而 host 至少 16 ms 的间隔为零，反向赶上指标也为零。该轮的大间隔在用户态读取前已经出现，无法从样本区分 Mac 离信道、未发包或接收不到。Mac→小米 2.474 MB/s，小米→Mac 8.076 MB/s，后者的 Mac 源回包无 ≥1536 B 样本。阶段结束已原子换回原应用，WLAN 模块仍是同一次加载。 [双时钟原始结果](device-rx-phy1/5/result.json) · [时钟观测源码](../rx-gap-next/rx_gap.rs) · [下一公平轮询对照](../fair-poll-next/README.zh-CN.md)

## 路由表 10075 `quickack 1` 对照（阶段 7–9）

在同一模块租约和同一双时钟诊断应用（SHA `8b620c89…`）下，阶段 8 仅对 `owly0` 的临时表 `10075` 应用 `ip -6 route change fe80::/64 dev owly0 table 10075 metric 1024 quickack 1`，并在样本 1、2 建立新 socket 前核验 `ip -6 route show table 10075` 与 `ip -6 route get`（含 `oif owly0 uid 0` 与 `fibmatch`）均命中 `table 10075`、`metric 1024`、`quickack 1`。阶段 9 不改路由，在新建 socket 前核验 `table 10075` 与所选路由均无 `quickack`。

| 阶段 | 变量 | Mac→小米 MB/s | 小米→Mac MB/s | 1.6 s 后发送栈 MB/s | Mac 重传 B | 4–16 ms radio 间隔数 / 累计 s | 本机 `air_tx_submitted` |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 7 | 基线（无 `quickack`） | 2.443 | 7.248 | 2.852 | 111,084 | 335 / 1.985 | 13,870 |
| 8 | `table 10075 quickack 1` | 1.753 | 8.020 | 2.031 | 14,652 | 561 / 3.535 | 19,709 |
| 9 | 回基线（验证无 `quickack`） | 2.461 | 8.037 | 2.713 | 91,568 | 295 / 1.649 | 13,263 |

阶段 8 中 Mac 端记录的 TCP 重传字节由约 91–111 KB 降到 14,652 B（10 包），证明接收端 `quickack 1` 已生效；但因 `<512 B` 的短包在用户态不进入 1 ms A-MSDU 合包，逐段立即回 TCP ACK 使本机在 Mac→小米接收期间额外提交约 6,100 个独立出站 802.11 帧（`air_tx_submitted` 由 13,870 / 13,263 增至 19,709），半双工信道争用导致 Mac→小米 4–16 ms 接收间隔由 335 / 295 个（1.985 / 1.649 s）增至 561 个（3.535 s），整轮接收降至 1.753 MB/s。阶段 9 恢复默认延迟 ACK 后立即回到 2.461 MB/s。候选不采纳。 [阶段 8 结果](device-rx-phy1/8/result.json) · [阶段 9 结果](device-rx-phy1/9/result.json)

## 根因定位与修复（`device-rx-phy2` ～ `device-rx-phy4`）

### 1. 反向纯 TCP ACK 洪泛导致半双工空口争用（`ack-coalesce-next` / `device-rx-phy2`）

- **根因**：`Node::rx_data` 解包对端每个 3-MSDU A-MSDU 后逐个调用 `Tun::deliver` 写入 `owly0`，触发 Linux 内核每 2 个 TCP 段立即回传 1 个 `<512 B` 的纯 TCP ACK；原 `owly-coalesce.patch` 仅对 `>=512 B` 载荷启用 `1,000 µs` 合包，导致每个纯 TCP ACK 都立刻封装为独立 802.11 单播帧发往空口（16 MiB 接收期间小米反向发射 ~8,300 个独立 ACK 帧），持续打断 Mac 的 VHT80 发送突发。
- **修复**：在 [`ack-coalesce-next/owly-ack-coalesce.patch`](../ack-coalesce-next/owly-ack-coalesce.patch) 中识别无负载纯 TCP ACK（`Ipv6Packet::pure_tcp_ack`），纳入 `1,000 µs` 等待窗口；同一四元组、无 SACK 块且窗口非零的严格递增纯累计 ACK 在 `Node::enqueue` 中原位覆盖队列内旧纯 ACK（保留原 `at` 时间戳），重复 ACK 与带 SACK 块的 ACK 则保留并聚合成单个出站 A-MSDU。
- **实测（`device-rx-phy2` 16 MiB A/B/B/A）**：
  - 阶段 1（基线）：Mac→小米 `2.678 MB/s`，小米→Mac `6.433 MB/s`，`air_tx_submitted = 13,221`。
  - 阶段 2（`ack-coalesce-next`）：Mac→小米 **`4.338 MB/s`**（+62%），小米→Mac **`8.894 MB/s`**，`air_tx_submitted = 6,367`（反向 ACK 帧减少约 6,850 个）。
  - 阶段 3（`ack-coalesce-next` 复测）：Mac→小米 **`4.084 MB/s`**，小米→Mac **`8.125 MB/s`**，`air_tx_submitted = 6,620`。
  - 阶段 4（回基线）：Mac→小米 `2.377 MB/s`，小米→Mac `8.029 MB/s`，`air_tx_submitted = 13,575`。
  [阶段 1](device-rx-phy2/1/result.json) · [阶段 2](device-rx-phy2/2/result.json) · [阶段 3](device-rx-phy2/3/result.json) · [阶段 4](device-rx-phy2/4/result.json)

### 2. 8320 单缓冲 RX 容量与 VHT 7991 / HT 7935 广告验证（`device-rx-phy3`）

- 构建并通过 7 项 ABI/符号校验的 [`qca_cld3_kiwi_v2-rx-capacity-8320.ko`](../rx-capacity-8320-next/out/qca_cld3_kiwi_v2-rx-capacity-8320.ko)（SHA `89cc24dc…`），设备 `dmesg` 确认 `PT_RX_CAPACITY cfg_bytes=8320`、`pool_bytes=8320 align=128 entries=16384`、`map_bytes=8320 linear=16000`、`htt_bytes=8320 host_submit_status=0`、`raw_osif_bytes=500 tlv=272 pad=0 linear_span=15872`。
- 构建 [`vht-mpdu-7991-next`](../vht-mpdu-7991-next/build-vht-mpdu-7991.sh) 并在 `device-rx-phy3` 阶段 2、3 核验 `OWLY_MIF_SUBMITTED_VHT_CONTAINER=bf0c01000000fcff0000fcff0000`（VHT Max MPDU `7991`）及 `OWLY_MIF_SUBMITTED_HT_CAPABILITIES=0000ce191bff0000`（HT Max A-MSDU `7935`）。
- 内核 `pt_rx_phy_stats` 与用户态 `OWLY_RX_GAPS` 均显示 `gt3895=0`、`max_bytes=3780`：macOS `awdl0`（`AppleBCMWLANCore`）在 AWDL 路径将出站 A-MSDU 硬性限制为 3 个 TCP 段（3,780 B），不因对端宣告 7991/7935 而增大。 [8320 模块审计](../rx-capacity-8320-next/audit-rx-capacity-8320.json) · [阶段 1](device-rx-phy3/1/result.json) · [阶段 2](device-rx-phy3/2/result.json) · [阶段 3](device-rx-phy3/3/result.json)

### 3. 用户态 `wonder0` `AF_PACKET` 接收缓冲溢出丢包引发 Mac 端 3 倍 TCP 重传（`ack-coalesce-rcvbuf-next` / `device-rx-phy4`）

- **根因**：对比内核 `dp_rx_deliver_raw_passthru` 交付的 `>=1536 B` 帧数（`raw_len[2] + raw_len[3]`）与用户态 `Monitor::recv` 从 `wonder0` 读到的 `>=1536 B` 帧数（`OWLY_RX_GAPS matched`），发现差值 `DROPPED_IN_AF_PACKET` 与 Mac 端 `tx_retransmit_packets` 呈精确 **1:3** 关系（每个被 `AF_PACKET` 丢弃的 A-MSDU 含 3 个 TCP 段）：
  - `phy2/2`：内核 `4,797` vs 用户态 `4,791`（丢 6 个 A-MSDU）→ Mac 重传 `19` 包；
  - `phy2/3`：内核 `4,854` vs 用户态 `4,808`（丢 46 个 A-MSDU）→ Mac 重传 `120` 包；
  - `phy3/1`（8320 模块，`skb->truesize ≈ 17.1 KB`）：内核 `4,985` vs 用户态 `4,860`（丢 **125** 个 A-MSDU）→ Mac 重传 **385** 包！
  原因是 `Monitor::open` 未设置 `SO_RCVBUF`，使用 Linux 默认 `rmem_default = 212,992 B`，而 `AF_PACKET` 按 `skb->truesize`（4224 模块约 8.9 KB、8320 模块约 17.1 KB）计费，仅能容纳 12～23 个 A-MSDU（约 3.5～6 ms 突发），每当单线程事件循环向 `owly0` 写包触发内核软中断处理时即发生 `AF_PACKET` 队尾丢包，打断 Mac TCP 拥塞窗口。
- **修复**：在 [`ack-coalesce-rcvbuf-next`](../ack-coalesce-rcvbuf-next/) 中：
  1. 在 `Monitor::open` 绑定 `wonder0` 前设置 `SO_RCVBUFFORCE` / `SO_RCVBUF = 8 MiB`（内核实设 `rcvbuf=16777216`）与 `SO_SNDBUFFORCE` / `SO_SNDBUF = 4 MiB`（`sndbuf=8388608`），并在退出时读取 `PACKET_STATISTICS`（`air_rx_kernel_packets` / `air_rx_kernel_drops`）；
  2. 在 `Node::should_coalesce` 中，当时隙剩余可用时间不足 `1,000 µs`（`unicast_wait(peer, now + 1000 µs) > 0`）时不再推迟纯 TCP ACK，立即在离槽前刷出。
- **实测（`device-rx-phy4`，4224 模块 + `ack-coalesce-rcvbuf-next`）**：

| 阶段 | 规模 | Mac→小米 MB/s | 小米→Mac MB/s | 内核 `>=1536B` / 用户态 `matched` | `air_rx_kernel_drops` | Mac TCP 重传包数 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 16 MiB | 3.822 | 8.031 | `4,840 / 4,840` | **0** | 3（全在首轮 Infra Priority 窗口，1.0 s 后 **0 重传**） |
| 2 | 16 MiB | **4.307** | **8.004** | `4,778 / 4,778` | **0** | **1**（16 MiB 正文段 **0 重传**） |
| 3 | **64 MiB × 3 对** | 加权 **4.532**（样本 1/3/5：`4.352` / `4.592` / **`4.662`**） | 加权 **6.590**（样本 2/4/6：**`6.721`** / `6.681` / `6.378`） | **`57,407 / 57,407`** | **0**（共 77,901 帧） | 样本 1：`3`；**样本 3（64 MiB）：`0`；样本 5（64 MiB）：`0`** |

- **剩余物理与调度边界分解（`device-rx-phy4/3` 3×64 MiB = 44.43 s）**：
  - `OWLY_RX_GAPS` 显示在 384 MiB 双向测试中，`16–128 ms` 区间间隔仅 1 次（`0.046 s`），证明在信道 149 活跃槽内 Mac 发送端从未因 `cwnd` 或 `rwnd` 断流；
  - `128–256 ms` 区间共 82 次（累计 `13.98 s`，占总时间 `31.5%`），对应 Mac `awdl0` 在 `Data` 模式下固有的 16 槽 `[149×6, 36×2, 6×1, 149×5, 36×2]` 切回基础 AP 信道 36 与发现信道 6 的离槽窗口（在信道 149 槽位占比 `11/16 = 68.75%`）；
  - 扣除离槽窗口后，Mac 在 149 信道上的持续在槽 TCP 有效吞吐为 **`6.82 MB/s`**（受限于 Mac 对 1SS 对端封顶 VHT80 MCS 7、出站 A-MSDU 硬上限 3,780 B，以及无 Block Ack 下的逐 MPDU Legacy ACK 开销）。 [阶段 1](device-rx-phy4/1/result.json) · [阶段 2](device-rx-phy4/2/result.json) · [阶段 3（64 MiB×3）](device-rx-phy4/3/result.json) · [恢复证明](device-rx-phy4/stop.json)
