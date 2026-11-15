# `owly-xos` 优化待办与能力核查（OWLY-XOS-TODO）

> 本文档跟踪 [`runtime/owly-xos`](../runtime/owly-xos/README.zh-CN.md) 的待实现优化项与演进状态，随开发进度持续更新；阶段性实机验证归档见 [`docs/airdrop/34-owly-xos-validation.md`](airdrop/34-owly-xos-validation.md)。

## 当前实测基线（2026-11-15）

已完成 KernelSU 单包干净刷入闭环验证（含 `NETWORK_FACTORY` 自动授权与独占无线电期间 `MIShare` UI/服务状态机保护）：

- **16 MiB 裸 TCP（小米 `xos0` ↔ Mac `awdl0`，信道 149）**：发送 **`64.08 MB/s`**（`max_ppdu_mpdus=31`），接收 **`9.55 MB/s`**（受 macOS Infrastructure Wi-Fi 下 `awdl0` ~10% 驻留占空比限制）。
- **原生 `MIShare 5.4.2` 双向传 `157.80 MB` 实拍视频（小米 14 ↔ Apple 手机，干净刷入后实测）**：
  - **发送（小米 14 → Apple 手机）**：正文吐出 **`4.23 s`（`37.31 MB/s` / `298.5 Mbps`）**，含对端落盘返回 `HTTP 200` 共 **`5.35 s`（`29.47 MB/s`）**；`99.95%` VHT80 2SS MCS9 SGI80 LDPC，`94.2%` 硬件 TX A-MPDU（`3,271 / 3,471` PPDU），多对端并发就绪（`active=3 tx_ready=3`）。
  - **接收（Apple 手机 → 小米 14）**：空口传输与 CPIO 解包 **`4.60 s`（`34.34 MB/s` / `274.7 Mbps`）**，含末尾 `2.00 s` 流收尾等待共 **`6.71 s`（`23.52 MB/s`）**；`57,247` 帧 `OWLY_RX_GAPS missing=0`、`dropped=0`。
  - **接收单张照片（`481 KB`）**：空口传输 **`89 ms`**，退出会话后自动恢复原厂 5 GHz Wi-Fi（`STOCK_RESTORED`）。

---

## 一、候选项实现状态核对

| 项目 | 当前状态 | 代码位置与核查结论 |
|---|---|---|
| **`dp_rx_tid.c` 初始 RX REO 窗口 `64 → 256`** | ✅ **已完成** | [`dp_rx_tid.c`](../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_rx_tid.c) 新增模块参数 `pt_remote_rx_window`（`1 / 64 / 256`），在 `wlan_op_mode_passthru` + `pt_remote_reo=2` 下将 TID 0 初始硬件 REO 窗口设为 `dp_pt_initial_rx_window(soc)`；[`radio.sh`](../runtime/owly-xos/radio.sh) `insmod` 已显式传入并校验 `pt_remote_rx_window=256`。 |
| **独占无线电期间保持 `MIShare` 发现态与热重入兼容** | ✅ **已完成** | [`Hook.java`](../runtime/owly-xos/compat/src/org/houji/xoscompat/Hook.java) 在 `sys.xos.exclusive=1` 期间保持 `isWifiEnabled()=true` / `WIFI_STATE_ENABLED`，拦截反向 `setWifiEnabled(true)` 与桥接切频产生的 `WIFI_STATE_CHANGED` 广播，修复分享面板掉回 `state=1` 丢失设备列表问题。 |
| **出向 `HOUJI_OWLY_TX_ADDBA` 窗口 `64 → 256`** | ⚠️ **代码已支持，运行时尚未开启** | [`android.rs`](../clients/owly-app/native/houji-runner/src/android.rs) 的 `tx_addba_mode()` 已支持 `HOUJI_OWLY_TX_ADDBA=2`（`initiate_addba_window = 256`），但 [`radio.sh`](../runtime/owly-xos/radio.sh) `start_xos()` 当前仍配置为 `HOUJI_OWLY_TX_ADDBA=1`（窗口 `64`）。 |
| **反向纯 TCP ACK 跳过 `OWLY_COALESCE_US` 立即发送** | ⬜ **待实现（当前逻辑相反）** | [`node.rs`](../clients/owly-app/native/owly/crates/owly-awdl/src/node.rs) 的 `should_coalesce()` 在 `packet.pure_tcp_ack().is_some()` 且距 EAW 关窗大于 `1000 µs` 时返回 **`true`**，强制纯 TCP ACK 在队列中等待 `1 ms` 合包窗口（沿用自早期无硬件 TX A-MPDU 的 `dzf2` 阶段）。 |
| **`xos0` TAP 启用 `IFF_VNET_HDR` / GRO 或批量 I/O** | ⬜ **待实现** | [`android.rs`](../clients/owly-app/native/houji-runner/src/android.rs) 仅以 `IFF_TAP \| IFF_NO_PI \| IFF_TUN_EXCL` 打开 `xos0`，未启用 `IFF_VNET_HDR` / `TUNSETOFFLOAD`；TAP 与 `wonder0` `AF_PACKET` 两侧均为逐包单次 `read()`/`write()`/`recv()` 系统调用及每包 `Vec<u8>` 堆分配。 |

---

## 二、待办改进清单（按优先级排序）

### P0：低改动、高确定性收益（吞吐与响应延迟）

- [ ] **1. 反向纯 TCP ACK 零等待直发（保留队列积压时的原地折叠）**
  - **现状问题**：在 `ezif` 硬件 TX A-MPDU（`pt_tx_ampdu=1`）路径下，硬件自身可零额外开销聚合多帧；接收大文件时，将首个反向纯 TCP ACK 强行截留 `OWLY_COALESCE_US=1000` 微秒会直接抬高双向 RTT，在空口突发末尾只剩单个尾随 ACK 时白等 `1 ms`，延缓对端拥塞窗口爬升与双包 A-MSDU 激活。
  - **方案**：在 [`node.rs`](../clients/owly-app/native/owly/crates/owly-awdl/src/node.rs) 的 `should_coalesce()` 中移除 `packet.pure_tcp_ack().is_some()` 分支，仅对 `packet.data.len() >= AMSDU_COALESCE_MIN_BYTES` 执行软件 A-MSDU 凑包等待；保留入队路径 `PureTcpAck::supersedes()` 原地覆盖，使非活跃 EAW 槽位或队列积压期间的旧 ACK 仍自动折叠。

- [ ] **2. 切换出向 `HOUJI_OWLY_TX_ADDBA=2`（发送侧 256 窗口）**
  - **现状问题**：对 Mac 4 MiB 套接字窗口实测 `max_ppdu_mpdus=31`（`64.08 MB/s`），而对手机发送视频时 PPDU 聚合深度主要集中在 `[9..=16]`。
  - **方案**：在 [`radio.sh`](../runtime/owly-xos/radio.sh) 中将 `HOUJI_OWLY_TX_ADDBA=1` 升至 `2`（向支持 256 缓冲区的 Apple 对端协商 `win=256`；对仅回 `win=64` 的对端，`node.rs` 已按 `window <= initiate_addba_window.clamp(1, 256)` 兼容接受），给固件硬件聚合器留出更充裕的未确认窗口。

- [ ] **3. 调整 `XosBridge` 空闲释放宽限期（当前仅 `2 s`）**
  - **现状问题**：[`XosBridge.java`](../runtime/owly-xos/src/XosBridge.java) 在收到 `stopDomestXOsComm` 后仅延迟 **`2 秒`**即触发 `radio.sh restore` 卸载驱动并重连原厂 Wi-Fi。用户刚发完一张照片、退出面板重选第二张照片时极易超过 2 秒，被迫再次等待 `~3.2 s` 冷启动切频。
  - **方案**：将空闲宽限期适度上调至 **`8–12 秒`**（新会话进入时 `begin()` 会立即取消待执行的 `pending` 恢复任务，直接零延迟复用热态 `xos0`），兼顾连续多次分享的秒开体验与退出后的 Wi-Fi 自动恢复速度。

---

### P1：数据面系统调用与协议栈卸载（降低大文件 CPU 开销、推高单流极限）

- [ ] **4. `xos0` TAP 启用 `IFF_VNET_HDR` + `TUNSETOFFLOAD`（TSO6 / CSUM / GRO）**
  - **现状问题**：157.80 MB 视频收发在 `xos0` 与 `wonder0` 两侧累计触发超过 **20 万次**单包系统调用和临时 `Vec<u8>` 分配。
  - **方案**：
    - **发送路径（内核 → `houji-runner`）**：开启 `IFF_VNET_HDR` 与 `TUNSETOFFLOAD(TUN_F_CSUM | TUN_F_TSO6)`，让内核 TCP 栈单次 `read()` 直接下发最高 64 KB 的 IPv6/TCP 超大段，由用户态在构造 802.11 帧时原地分段并补齐传输层校验和、直接填满 7,935 字节多包 A-MSDU——无需再依赖 `OWLY_COALESCE_US=1000` 定时器等待凑包；
    - **接收路径（`houji-runner` → 内核）**：携带 `VIRTIO_NET_HDR_F_DATA_VALID` 写入 `xos0`，省去内核协议栈重复校验和计算，并利用 TAP GRO 在上送 TCP 栈前合并同流分片。

- [ ] **5. `wonder0` `AF_PACKET` 批量收发与固定缓冲池**
  - **方案**：
    - 在 [`android.rs`](../clients/owly-app/native/houji-runner/src/android.rs) `Monitor` 与 `Tap` 的每次 `AsyncFd` 可读事件中，使用非阻塞循环一次性排空当前就绪批次（或采用 `recvmmsg` / `sendmmsg` / `TPACKET_V2` 环形缓冲区），统一推进 `Node::handle` 后批量写出，减少 `tokio` 调度与用户态/内核态上下文切换次数；
    - 用预分配帧池或切片复用替代 `Monitor::recv()` 中的每包 `raw.to_vec()` 与 `Ipv6Packet::to_ethernet()` 分配。

---

### P2：建连首包时延、收尾延迟与多信道体验

- [ ] **6. 消除首次 `/Discover` 的 `~1.76 s` TCP `SYN` 重传等待**
  - **现状问题**：实测发现对端后首次 `/Discover` TCP 建连耗时 `1.761 s`，而 3 秒后正式 `/Upload` 建连仅 `8 ms`（详见 [`34-owly-xos-validation.md`](airdrop/34-owly-xos-validation.md)）。[`android.rs`](../clients/owly-app/native/houji-runner/src/android.rs) 在 `Tap::peer_up` 中通过派生外部进程 `/system/bin/ip -6 neigh replace` 写入邻居项，存在子进程启动开销及首包跨越 EAW 关窗触发内核 `1 s` `SYN` 退避的概率。
  - **方案**：用直接的 `NETLINK_ROUTE`（`RTM_NEWNEIGH` / `RTM_NEWADDR` / `RTM_NEWLINK`）套接字调用替代 `Tap::open` 与 `Tap::peer_up` 中的多次 `/system/bin/ip` 子进程派生，确保邻居项在注入 mDNS 报文前同步就绪。

- [ ] **7. 收敛接收端流结束后的 `~2.0 s` 尾部等待**
  - **现状问题**：Apple 手机 → 小米 14 接收 157.80 MB 视频时，空口接收 + CPIO 解包 `4.60 s` 即已完成，但返回 `HTTP 200` 前固定多等约 `2.00 s`（481 KB 小图空口 `89 ms` 同样多等约 `2 s`）。
  - **方案**：核查 `MIShare` `AirDropService` / CPIO 流读取器在分块流末尾的超时判定条件，若属保守轮询/静默等待，可通过 `OwlyXosCompat` 在流明确 EOF 时立即收尾，实现照片与小文件真正“秒弹出”。

- [ ] **8. 退出会话时的原厂 5 GHz Wi-Fi 定向频点快连与社交信道 `44 / 149` 协同**
  - **退场快连**：在 [`radio.sh`](../runtime/owly-xos/radio.sh) `snapshot()` 阶段记录原连接频点（如 `iw dev wlan0 link` 的频率），恢复时优先触发单频点定向扫描，缩短全频段盲扫重连时间。
  - **多信道协同**：当对端 Mac 连接在 `36–48` 信道 5 GHz AP 上时，其在信道 `149` 的 AWDL 驻留占空比仅约 `10%`；后续可评估按对端 PSF 主信道或 `XosBridge` 请求支持信道 `44`（`5220 MHz`）切换。

---

## 三、建议实施批次

1. **第一批（纯用户态/桥接小改，1 次构建即可实机 A/B 对照）**：
   - `node.rs` 反向纯 TCP ACK 零等待直发；
   - `radio.sh` 切换 `HOUJI_OWLY_TX_ADDBA=2`；
   - `XosBridge.java` 空闲释放延迟从 `2 s` 调至 `10 s`。
2. **第二批（用户态 I/O 架构升级）**：
   - `Tap::peer_up` / `Tap::open` 改纯 `rtnetlink` 同步注入；
   - `xos0` `IFF_VNET_HDR` + 批量非阻塞收发与缓冲复用。
3. **第三批（上层体验打磨）**：
   - 接收流末尾 `2.0 s` 等待收敛与退场单频点定向重连。
