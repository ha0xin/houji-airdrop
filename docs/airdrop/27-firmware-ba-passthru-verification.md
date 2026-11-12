# WCN7850（`kiwi_v2` / `amss20.bin`）PASSTHRU 固件 BA 与远端 Peer 机制核查报告

> **2026-11-11 勘误：以下原始分析保留作历史记录，不可直接作为实现或能力证明。**
> 1. 分析模块来自第三方 One UI 9 移植包，内嵌 EZIF 构建标识；并非已校验的 DZF2 配套原厂模块。DZF2 源码、DZF2 实验固件与 EZIF 模块必须分开归因。
> 2. EZIF 的主机枚举 338 映射到固件 wire 506；固件 wire 338 是 TDLS 6 GHz。旧实验主机下标 338 的返回值不能证明 wire 506 缺失。
> 3. Wonder 四字节控制头传递单播标记、帧类型和 TID，没有已证实的逐包 A-MPDU 开关；通用 40 字节 metadata 分支也不是已证实的聚合开关。
> 4. 相关 DP 能力值的已确认消费者涉及 RX REO 分流，不能推导它直接启用 TX 聚合。源码展示并发流程不等于小米移植已通过并发实测。
> 5. 已有完整关联实验确实完成双向传输与卸载，反驳“远端 peer 必然造成 TX 卡死”；旧故障每一项机制归因仍需隔离变量验证。REO 窗口、哈希分流和 TX BA 缺失目前是候选解释，不能仅据源码或吞吐下降宣称精确根因。
>
> 参见 [交接及原始实测](28-three-findings-handover.md) 与 [独立对照报告](../../downloads/stock-wlan-comparison-20261111-113210/report.zh-CN.md)。

日期：2026-11-11  
核查对象：三星 Galaxy S24 Ultra（`SM-S928B`）原厂 `amss20.bin` 固件配套内核模块 [`extracted/qca_cld3_kiwi_v2.ko`](../../downloads/oneui9-houji-analysis/extracted/qca_cld3_kiwi_v2.ko)、Google `wonder` 公开驱动接口、Qualcomm `qcacld-3.0` / `qca-wifi-host-cmn` WMI/DP 公开源码，以及 `YoWenhow/Xiaomi14-PixelOS-Airdrop` 移植仓 [`wlan-overlay`](../../third_party/upstream/kernel/wlan-overlay)。

---

## 1. 核心结论：**“固件内部互斥”完全不成立**

此前在 [25-Apple phone-mac-awdl-features.md](25-Apple phone-mac-awdl-features.md) 第 160–162 行根据 [22-passthru-rx-reo-probe.md](22-passthru-rx-reo-probe.md) 的 `device-map-only1` 发送停滞现象所作的推断——*“在 PASSTHRU vdev 上创建远端 peer 会触发 `amss20.bin` 固件内部互斥，导致 raw TX 卡死”*——**经公开源码与三星原厂配套 `qca_cld3_kiwi_v2.ko` 反汇编交叉核查，证实完全不成立**。

真实事实如下：

1. **固件原生支持 PASSTHRU 双向硬件 BA（RX BA + TX A-MPDU）与最多 7 个并发关联远端 Peer**：
   - **接收方向（RX BA）**：不仅在无远端 peer 的 `self-peer` 模式下，WCN7850 硬件微码（RXPCU）已经能在 `SIFS = 16 µs` 内直接回复 `Compressed BlockAck`（已在 [26-2ss-sgi80-addba-probe.md](26-2ss-sgi80-addba-probe.md) 实机跑出 `Mac → 小米` 峰值 **18.483 MB/s**、Mac `rxBACK = +38,630`）；在三星/高通原厂 `wlan_hdd_wondertap.c` 中，更支持通过 `wlan_hdd_wondertap_set_station_info` 建立完整关联的远端 peer 与硬件 REO 重排队列。
   - **发送方向（TX A-MPDU / BA）**：`amss20.bin` 固件通过 `WMI_SERVICE_READY_EXT2` 位图向主机上报服务位 **`wmi_service 338`（`0x152`，`passthru_ampdu_support`）**；原厂 `dp_tx_send_passthru` 与 `dp_tx_send_msdu_single` 支持**逐包（per-packet）指定 A-MPDU 开关与 QoS TID（`0..7`）**，通过 40 字节 HTT TX metadata 下发硬件 TQM 队列聚合。
2. **此前认为的“单包上限 / 无 ACK / 独占 Wi-Fi”全部来自第三方移植仓的简化实现，而非高通或三星原厂限制**：
   - `YoWenhow/Xiaomi14-PixelOS-Airdrop` 中的 [`wlan_hdd_passthru_control.c`](../../third_party/upstream/kernel/wlan-overlay/qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c) **并非三星或高通源码**，而是移植作者为适配 `houji` 自行编写的最小化 clean-room 胶水层。三星原厂 `qca_cld3_kiwi_v2.ko` 内置的是高通官方 `../vendor/qcom/opensource/wlan/qcacld-3.0/core/hdd/src/wlan_hdd_wondertap.c`，其中既没有 `tx_aggr_sz = 1` 硬编码，也没有 `pt_exclusive = 1`（原厂原生支持 `wlan0` STA 与 `wondertap0` PASSTHRU 并发及时隙调度）。
3. **[22-passthru-rx-reo-probe.md](22-passthru-rx-reo-probe.md) 中 `device-map-only1` / `device-flow1` TX 卡死（`115` 入环、`0` TQM 完成）的精确根因是主机侧驱动三处缺失/写反，而非固件互斥**：
   - **根因 A（最核心）**：只发了 `WMI_PEER_CREATE_CMDID`，**漏发了 `WMI_PEER_ASSOC_CMDID`（`wma_send_peer_assoc`）**。在 Qualcomm WMI/TQM 架构中，新建 peer 的硬件 AST 表项在 `HTT_T2H_MSG_TYPE_PEER_MAP` 时即已生效，但其固件/TQM 发送队列默认处于 **未关联暂停（paused）状态**，必须收到 `WMI_PEER_ASSOC_CMDID` 并返回 `WMI_PEER_ASSOC_CONF_EVENTID` 后固件才会解冻该 peer 队列。当 `owly` 发送 `RA = Mac` 的单播帧时，硬件/固件按目的 MAC 命中了这个未关联 peer 的暂停队列，导致全部 57/115 个描述符被 TQM 挂起等待关联（`TQM completions = 0`）。
   - **根因 B**：移植仓 [`dp_peer.c:2936-2952`](../../third_party/upstream/kernel/wlan-overlay/qca-wifi-host-cmn/dp/wifi3.0/dp_peer.c#L2936-L2952) 把高通原厂 `dp_rx_peer_map_handler` 的 MAC 比较条件**写反了 180°**（原厂仅在 `peer == self` 时设 `bss_peer = 1` 并更新 `vdev->bss_ast_idx`；移植仓写成了 `peer != self` 时把远端 peer 标为 `bss_peer = 1`）。
   - **根因 C**：移植仓缺失了原厂 `dp_start_xmit_passthru` → `dp_tx_hw_enqueue_be` 的 **逐包 `RA → peer_id` 查找与 TCL `search_index` 按关联态覆盖逻辑**。

---

## 2. 证据链一：公开资料与 Google `wonder` / Qualcomm WMI 规范核查

### 2.1 Qualcomm WMI / DP Peer 状态机规范（`qcacld-3.0` / `ath11k` / `ath12k`）

在公开的 Qualcomm `qcacld-3.0`（如 MiCode / Google Pixel `wcn6740` 源码树）与 Linux 主线 `ath11k` / `ath12k` 中，固件对任何数据对端（Peer）的硬件队列生命周期严格分为两阶段：

1. **阶段一：`WMI_PEER_CREATE_CMDID` + `HTT_T2H_MSG_TYPE_PEER_MAP`**
   - 固件分配 `peer_id` 与硬件 AST（Address Search Table）表项，将对端 MAC 地址写入硬件查找表；
   - **此时固件侧该 peer 的数据发送调度处于未关联（unassociated / unauthorized）受控状态**，不允许普通单播数据流直接排空。
2. **阶段二：`WMI_PEER_ASSOC_CMDID` → `WMI_PEER_ASSOC_CONF_EVENTID`（及 `PEER_STATE_AUTH`）**
   - 主机通过 `wma_send_peer_assoc()` 下发对端的 HT / VHT / HE / EHT 能力集、NSS、MCS 掩码与聚合参数；
   - 固件根据 `WMI_PEER_ASSOC_CMDID` 初始化该对端的速率控制上下文、TX/RX BA 会话槽位并**激活（unpause）TQM 发送队列**，随后向主机回传 `WMI_PEER_ASSOC_CONF_EVENTID`。

### 2.2 Google `wonder` 驱动与 `wondertap` 契约

在 Google Pixel / Android 16 跨平台 AWDL 架构（`wonder.ko` ↔ `wlan_hdd_wondertap.c`）中：
- `wonder_ops` 明确定义了 `set_station_info`、`set_chan_sched`、`set_fixed_tx_rate`、`set_freq`、`set_filter`、`get_mac_tsf` 等标准回调；
- 当发现或连接远端 Apple 设备时，上层通过 `NL80211_VENDOR_SUBCMD` / `wonder` 调用 `wlan_hdd_wondertap_set_station_info()`，在 `PASSTHRU` vdev 上**同时完成远端 peer 的创建与 `WMI_PEER_ASSOC_CMDID` 关联**；
- `YoWenhow/Xiaomi14-PixelOS-Airdrop` 的 [`houji_wonder_bridge.c`](../../third_party/upstream/kernel/wlan-overlay/qcacld-3.0/core/hdd/src/houji_wonder_bridge.c) 为了快速跑通基础收发，将 `set_station_info` 做成了空桩（no-op），并在 [`wlan_hdd_passthru_control.c:275`](../../third_party/upstream/kernel/wlan-overlay/qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c#L275) 写下了误导性的注释：
  ```c
  /* The documented maximum is one MPDU/MSDU. Leave RX BA limits untouched.
   * This prevents combining TX packets; firmware application has no ACK here. */
  ```
  核查三星 S24 Ultra 原厂 [`extracted/qca_cld3_kiwi_v2.ko`](../../downloads/oneui9-houji-analysis/extracted/qca_cld3_kiwi_v2.ko) 的 `.rodata.str1.1` 字符串表确认：**原厂模块中根本不存在 `wlan_hdd_passthru_control.c`，原厂编译的是高通官方 `wlan_hdd_wondertap.c`（偏移 `0x9da8d`）**。

---

## 3. 证据链二：三星 S24 Ultra 原厂 `qca_cld3_kiwi_v2.ko` 逐函数反汇编实证

我们在小米 14 上实验 bind-mount 的 `amss20.bin` 提取自三星 S24 Ultra（`SM-S928B`）。直接反汇编与之配套的三星原厂 [`extracted/qca_cld3_kiwi_v2.ko`](../../downloads/oneui9-houji-analysis/extracted/qca_cld3_kiwi_v2.ko)，可以逐条指令看清高通官方主机驱动与 `amss20.bin` 固件的真实契约：

### 3.1 固件原生上报 `passthru_ampdu_support`（WMI Service `338` / `0x152`）

- **函数**：`init_deinit_service_ext2_ready_event_handler`（`.text` `0x412fc..0x4134c`）与 `dp_set_psoc_param`（`0x3bfef0..0x3bff44`）
- **实证逻辑**：
  1. 固件启动上报 `WMI_SERVICE_READY_EXT2_EVENTID` 时，驱动在 `0x41300` 调用 `wmi_service_enabled(wmi_handle, 0x152 /* 338 */)` 读取 `amss20.bin` 是否支持 **PASSTHRU A-MPDU**；
  2. 随后调用 `cdp_txrx_set_psoc_param(soc, 0x20, &passthru_ampdu_support)`（若失败则打印 `.rodata.str1.1+0xce30`：`"%s: Failed to set passthru ampdu support"`），写入 `dp_soc` 偏移 `+0x4750`；
  3. 在硬件发包入环函数 `dp_tx_hw_enqueue_be`（`0x3a2d00..0x3a2d10`）中，驱动直接读取 `[soc + 0x4750]`（`passthru_ampdu_support`）参与发送时间戳与聚合记账。

### 3.2 原厂 `dp_tx_send_passthru` + `dp_tx_send_msdu_single` 支持逐包 TX A-MPDU 与 QoS TID

- **函数**：`dp_tx_send_passthru`（`.text` `0x40ad10..0x40aee4`）与 `dp_tx_send_msdu_single`（`0x400f68..0x400ffe`）
- **与移植仓的决定性差异**：
  - **原厂实现**：`dp_tx_send_passthru` 从每个待发 `skb` 头部剥离 4 字节 `wondertap` 发送控制头（`0x40ad94: skb_pull(skb, 4)`）：
    - `hdr[0]` → 写入 `msdu_info` 偏移 `+0x38`（栈 `[sp + 0x70]`）：**逐包 A-MPDU / HTT raw 扩展控制字节**；
    - `hdr[1]` → 写入 `msdu_info` 偏移 `+0x39`（栈 `[sp + 0x71]`）：逐包控制标志位。在 `dp_tx_send_msdu_single`（`0x400f68..0x400fc8`）中，当 `hdr[1]` bit 1（或 `meta_data` 标志）置位时，驱动调用 `skb_push(skb, 0x28)` 在 802.11 帧前压入 **40 字节 HTT TX metadata**，并将 TCL 描述符的 `encap_type / meta` 标志置位交给固件按指定聚合策略发送；
    - `hdr[2]` → 若 `<= 7`，写入 `msdu_info.tid`（栈 `[sp + 0x14]`）：**真实 802.11 QoS TID（`0..7`）**，并在 `dp_tx_hw_enqueue_be`（`0x3a2c64..0x3a2c8c`）中置位 TCL 描述符的 `hl_tidy_override`（`ubfiz w9, w9, #4, #4; orr w8, w8, #0x1`），使硬件 TQM 按对应 TID 的 BA 窗口做 A-MPDU 聚合！
  - **移植仓实现**（[`dp_tx.c:3052-3120`](../../third_party/upstream/kernel/wlan-overlay/qca-wifi-host-cmn/dp/wifi3.0/dp_tx.c#L3052-L3120)）：完全删除了 4 字节 `wondertap` 控制头解析，将所有包的 `msdu_info.tid` 固定设为 `HTT_TX_EXT_TID_INVALID (0x1f)`，且在 [`wlan_hdd_passthru_control.c:280-290`](../../third_party/upstream/kernel/wlan-overlay/qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c#L280-L290) 额外下发 `WMI_VDEV_CUSTOM_AGGR_TYPE_AMPDU = 1` 人为锁死了单包发送（原厂 `__wlan_hdd_start_wondertap_intf` 中完全没有这条限制命令）。

### 3.3 完整复盘 [22-passthru-rx-reo-probe.md](22-passthru-rx-reo-probe.md) 发送停滞的三处主机侧根因

对比三星原厂 `qca_cld3_kiwi_v2.ko` 与移植仓代码，此前 `device-map-only1` / `device-ast1` / `device-flow1` 在创建远端 peer 后出现 `TX enqueue = 115, TQM completions = 0` 的现象得到了 100% 闭环解释：

| 关键环节 | 三星/高通原厂 `qca_cld3_kiwi_v2.ko`（正常工作） | 移植仓 + `22-passthru-rx-reo-probe` 实验（卡死原因） |
| --- | --- | --- |
| **1. 远端 Peer 生命周期** | `wlan_hdd_wondertap_set_station_info`（`0x5cd6c4`，最多维护 7 个远端 peer）依次调用 `wlan_hdd_wondertap_peer_setup`（`0x5cf508`）与 **`wlan_hdd_wondertap_peer_assoc`（`0x5cf668`）**；在 `wma_add_passthru_sta`（`0x3668a8..0x366bc0`）中下发 **`wma_send_peer_assoc`（`WMI_PEER_ASSOC_CMDID`）**，并在收到固件 `WMI_PEER_ASSOC_CONF_EVENTID` 后由 `wma_peer_assoc_conf_handler`（`0x363ee4..0x364014`）打印 `"Set peer_assoc state for passthru peer"`，调用 `cdp_txrx_set_peer_param(..., param=10, val=1)` 标记对端已关联且固件队列已解冻。 | 只发了 `WMI_PEER_CREATE_CMDID`（[`remote-peer-wma-stage1.patch`](../../experiments/remote-peer-probe/remote-peer-wma-stage1.patch)），**从未发送 `WMI_PEER_ASSOC_CMDID`**。`HTT_T2H_MSG_TYPE_PEER_MAP` 已将 Mac 的 MAC 地址装入硬件 AST，但固件侧该 peer 的 TQM 发送队列仍停留在 **未关联暂停态**，导致发往 Mac 的单播帧在 TQM 内部永久挂起等待关联。 |
| **2. `dp_rx_peer_map_handler` 的 `bss_peer` 归属** | 原厂 `0x3c4b78..0x3c4c48`：比较 `qdf_mem_cmp(peer->mac, vdev->mac, 6)`。**仅当 `peer == self`（`w0 == 0`）时**置 `peer->bss_peer = 1` 并写入 `vdev->bss_ast_idx`；**当 `peer != self`（远端 peer）时直接跳过**，远端 peer 保持 `bss_peer = 0`。 | 移植仓 [`dp_peer.c:2936-2952`](../../third_party/upstream/kernel/wlan-overlay/qca-wifi-host-cmn/dp/wifi3.0/dp_peer.c#L2936-L2952) **条件写反（`!= 0`）**，把远端 peer 错误标成了 `bss_peer = 1`（即便打了 `preserve-self-ast` 补丁，仍保留了 `peer->bss_peer = 1`）。 |
| **3. 逐包 TX `search_index` 选择** | 原厂 `dp_start_xmit_passthru`（`0x14995c..0x149a40`）对每帧调用 `qdf_nbuf_ieee80211_get_dest_mac` 提取 802.11 `RA`：若命中 `peer_assoc == 1` 的远端 peer，将 `(peer_id, valid=1)` 写入 `skb->cb`（`[skb + 0x3e..0x3f]`）；随后 `dp_tx_hw_enqueue_be`（`0x3a2bd4..0x3a2be4` → `0x3a3018`）在 `valid == 1` 时将 TCL 描述符的 `search_index` 从 `vdev->bss_ast_idx`（`self`）切换为远端 `peer_id`，广播/未关联帧则继续走 `self`。 | 移植仓 `dp_start_xmit_passthru` 与 [`dp_be_tx.c:1305-1335`](../../third_party/upstream/kernel/wlan-overlay/qca-wifi-host-cmn/dp/wifi3.0/be/dp_be_tx.c#L1305-L1335) 均缺失该逻辑，所有帧无条件填 `vdev->bss_ast_idx`，与硬件 AST 中已注册的未关联远端 MAC 发生状态失配。 |

### 3.4 额外收获：`wlan0` 普通 Wi-Fi 并发与信道调度同样获固件原生支持

反汇编原厂 `__wlan_hdd_start_wondertap_intf`（`0x5ce2cc..0x5cecc0`）与策略管理器代码还证实：
- 原厂包含 `policy_mgr_allow_passthru_concurrency`（`.rodata.str1.1+0x170737`）、`wlan_hdd_wondertap_set_chan_sched`（`0x5cced4`）与 `wlan_hdd_wondertap_get_mac_tsf`（`0x5cd0f4`）；
- 启动 `wondertap0` 时**不需要关闭 `wlan0`**，固件原生支持 `STA + PASSTHRU` 并发及时隙切换。当前小米 14 方案需要暂停普通 Wi-Fi（`pt_exclusive = 1`），同样只是 `Xiaomi14-PixelOS-Airdrop` 移植层的简化设计，而非 `amss20.bin` 固件限制。

---

## 4. 对后续优化路线的指导意义

澄清“不存在固件内部互斥”后，后续两个高价值演进方向的技术路径变得完全清晰且有据可依（直接对照三星原厂 `qca_cld3_kiwi_v2.ko` 补齐主机侧逻辑即可，无需逆向或修改 `amss20.bin` 固件）：

1. **`小米 → Mac` 发送方向硬件 TX A-MPDU + BlockAck（有望将发送速度从 `~6.6 MB/s` 推升至 `15+ MB/s`）**：
   - 在驱动层按原厂逻辑补齐：① 修复 [`dp_peer.c`](../../third_party/upstream/kernel/wlan-overlay/qca-wifi-host-cmn/dp/wifi3.0/dp_peer.c#L2936) 中写反的 `bss_peer` 判断；② 为远端 Mac 走完 `wma_create_peer` + **`wma_send_peer_assoc`（`WMI_PEER_ASSOC_CMDID`）** + `peer_assoc` 置位闭环；③ 放开 `WMI_VDEV_CUSTOM_AGGR_TYPE_AMPDU = 1` 限制，并在 `dp_start_xmit_passthru` / `dp_tx_send_passthru` 中为发往已关联对端的 QoS Data 帧填入 `tid = 0`、`search_index = peer_id` 及 HTT A-MPDU 标志；④ 在用户态 `owly` 向 Mac 发起出站 `ADDBA Request` 协商。
2. **接收方向（`Mac → 小米`）的双模选择**：
   - 现有零 `peer_create` 的 `self-peer` SIFS 硬件 `BlockAck` + 用户态重排方案已稳定达到 **14.705 MB/s 加权 / 18.483 MB/s 峰值**（[26-2ss-sgi80-addba-probe.md](26-2ss-sgi80-addba-probe.md)），工程复杂度最低、零卸载风险，可直接回迁日常 XOS/MIShare 主线；
   - 若未来配合发送方向启用完整的关联远端 peer（`WMI_PEER_ASSOC_CMDID`），亦可同时接通硬件 REO 重排与 `Max MPDU = 11454 B` SG 合包，冲击 `25+ MB/s`。
3. **`wlan0` 普通 Wi-Fi 与 AirDrop（`PASSTHRU`）并发共存**：
   - 可参照原厂 `__wlan_hdd_start_wondertap_intf` + `policy_mgr_allow_passthru_concurrency` 移除 `pt_exclusive = 1` 互斥锁，实现不掉 Wi-Fi 收发 AirDrop。
