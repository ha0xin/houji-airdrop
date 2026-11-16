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
| **出向 `HOUJI_OWLY_TX_ADDBA=2` 与软件 BA 生命周期完善** | ✅ **已完成（待实机观察 `rx_resp`）** | [`android.rs`](../native/houji-runner/src/android.rs) 与 [`radio.sh`](../runtime/owly-xos/radio.sh#L321) 已启用 `HOUJI_OWLY_TX_ADDBA=2`（`initiate_addba_window = 256`）；[`node.rs`](../native/owly/crates/owly-awdl/src/node.rs)、[`frame.rs`](../native/owly/crates/owly-awdl/src/frame.rs)、[`peer.rs`](../native/owly/crates/owly-awdl/src/peer.rs) 已补齐 `DELBA` 解析与重连、`ADDBA Response` 的 `amsdu`/`window` 约束落地及 4 次超限后 `10 s` 恢复重试。 |
| **反向纯 TCP ACK 跳过 `OWLY_COALESCE_US` + `driver` 调度与时槽加固** | ✅ **已完成** | 已移除 [`node.rs`](../native/owly/crates/owly-awdl/src/node.rs) `should_coalesce()` 的 `pure_tcp_ack()` 等待分支并去除 `BURST_PAUSE_US=500`；随后进一步完成每对端独立 `BTreeMap` 轮询 TX 队列、[`driver.rs`](../native/owly/crates/owly-awdl/src/driver.rs) 逐帧重检 `self.now()`/64 帧或 `2 ms` 冲刷预算/`Input::Frame`/`Outbound` 到期迟到记录，以及 [`net_util.rs`](../native/houji-runner/src/net_util.rs) 连续过滤包 `SkipBudget` 让渡。 |
| **非阻塞批抽包、`NeighWorker`、`KernelPeerSync` 与 TAP `writev`** | ⚠️ **用户态已完成（`IFF_VNET_HDR`/池化 RX 待做）** | 已引入 `Air`/`Netif::try_recv`（`DRAIN_BATCH=32`）、将 `RtnlNeigh` 迁出到带超时与退避重试的后台 `NeighWorker`、[`peer_ctl.rs`](../native/houji-runner/src/peer_ctl.rs) 期望态合并与 5 次指数退避重试、[`android.rs`](../native/houji-runner/src/android.rs) `Tap::deliver` `writev` 零分配投递，以及 [`XosBridge.java`](../runtime/owly-xos/src/XosBridge.java) / [`radio.sh`](../runtime/owly-xos/radio.sh) `10 s` 宽限与幂等路由修复；`xos0` 仍未开启 `IFF_VNET_HDR` / `recvmmsg` / `sendmmsg`。 |

---

## 二、待办改进清单（按优先级排序）

### P0：低改动、高确定性收益（吞吐与响应延迟）

- [x] **1. 反向纯 TCP ACK 零等待直发（保留队列积压时的原地折叠）**（已在 [`36-rx-no-ack-fix-and-verification.md`](airdrop/36-rx-no-ack-fix-and-verification.md) 验证落地）
  - **现状问题**：在 `ezif` 硬件 TX A-MPDU（`pt_tx_ampdu=1`）路径下，硬件自身可零额外开销聚合多帧；接收大文件时，将首个反向纯 TCP ACK 强行截留 `OWLY_COALESCE_US=1000` 微秒会直接抬高双向 RTT，在空口突发末尾只剩单个尾随 ACK 时白等 `1 ms`，延缓对端拥塞窗口爬升与双包 A-MSDU 激活。
  - **方案与结果**：在 [`node.rs`](../native/owly/crates/owly-awdl/src/node.rs) 的 `should_coalesce()` 中移除 `packet.pure_tcp_ack().is_some()` 分支，仅对 `packet.data.len() >= AMSDU_COALESCE_MIN_BYTES` 执行软件 A-MSDU 凑包等待；保留入队路径 `PureTcpAck::supersedes()` 原地覆盖。`1 GiB` RX 回归显示硬件 `status[2] NO_ACK` 由 `38.22%–43.11%` 降至 **`1.46%`**、`tries/completed` 由 `1.24–1.92` 降至 **`1.028`**。

- [x] **2. 切换出向 `HOUJI_OWLY_TX_ADDBA=2` 并完善软件 BA 协商与退避恢复**（已扩展至 [`frame.rs`](../native/owly/crates/owly-awdl/src/frame.rs)、[`peer.rs`](../native/owly/crates/owly-awdl/src/peer.rs)、[`node.rs`](../native/owly/crates/owly-awdl/src/node.rs)）
  - **现状问题**：此前仅有单次 `ADDBA Req` 上浮与 `win=64` 回退假设，缺少 `DELBA` 处理、协商字段 `amsdu`/`window` 落地，以及 4 次未响应后永久锁死问题；且真机日志曾见 `rx_resp=0`，需要结合对端应答确认硬件 vs 软件路径实际生效情况。
  - **方案与结果**：支持接收 `DELBA` 自动重置 TX BA 并触发冷却后重协商、将对端 `ADDBA Resp` 返回的 `tx_ba_amsdu=false` 与 `tx_ba_window` 约束接入出站路径、4 次无应答后进入 `10 s` 冷却再恢复尝试；RX 端保持 `win=64` / `128` 序号去重安全边界不变。

- [x] **3. 调整 `XosBridge` 空闲释放宽限期（`2 s → 10 s`）、拆分锁作用域并优化 `radio.sh` 热重入**
  - **现状问题**：[`XosBridge.java`](../runtime/owly-xos/src/XosBridge.java) 此前在 `stopDomestXOsComm` 后仅延迟 `2 s` 执行 `restore`，且 `begin`/`release` 共用同一把 `lock` 长时间持有 `radio()` 子进程执行；[`radio.sh`](../runtime/owly-xos/radio.sh) 热重入也无条件清删重建规则并三次校验 `amss20.bin`。
  - **方案与结果**：将默认空闲宽限期上调至 `10 s`（支持 `XOS_IDLE_GRACE_SEC` 在 `1..=60` 间配置），拆分毫秒级状态锁 `stateLock` 与串行壳锁 `shellLock` 并在持 `shellLock` 后二次核对 `generation`，保持 `code == 3` 状态查询不阻塞；在 [`radio.sh`](../runtime/owly-xos/radio.sh) 用 `ensure_rule` + `ip -6 route replace` 实现幂等无黑洞路由刷新（保留退出清理 `clean_routes`），并跳过有效缓存 `amss20.bin` 的第三次重复哈希。

---

### P1：数据面系统调用与协议栈卸载（降低大文件 CPU 开销、推高单流极限）

- [ ] **4. `xos0` TAP 启用 `IFF_VNET_HDR` + `TUNSETOFFLOAD`（TSO6 / CSUM / GRO）**
  - **现状问题**：157.80 MB 视频收发在 `xos0` 与 `wonder0` 两侧累计触发超过 **20 万次**单包系统调用和临时 `Vec<u8>` 分配。
  - **方案**：
    - **发送路径（内核 → `houji-runner`）**：开启 `IFF_VNET_HDR` 与 `TUNSETOFFLOAD(TUN_F_CSUM | TUN_F_TSO6)`，让内核 TCP 栈单次 `read()` 直接下发最高 64 KB 的 IPv6/TCP 超大段，由用户态在构造 802.11 帧时原地分段并补齐传输层校验和、直接填满 7,935 字节多包 A-MSDU——无需再依赖 `OWLY_COALESCE_US=1000` 定时器等待凑包；
    - **接收路径（`houji-runner` → 内核）**：携带 `VIRTIO_NET_HDR_F_DATA_VALID` 写入 `xos0`，省去内核协议栈重复校验和计算，并利用 TAP GRO 在上送 TCP 栈前合并同流分片。

- [ ] **5. `wonder0` `AF_PACKET` 批量收发与 RX 固定缓冲池**（已落地：`try_recv` 批抽包、过滤包让渡与 `Tap::deliver` `writev` 零分配；`recvmmsg` / `sendmmsg` / RX 池化待做）
  - **已落地**：在 [`android.rs`](../native/houji-runner/src/android.rs)、[`net_util.rs`](../native/houji-runner/src/net_util.rs) 与 [`driver.rs`](../native/owly/crates/owly-awdl/src/driver.rs) 为 `Monitor`、`Tap`、`Tun`、`HostNetif` 引入非阻塞 `try_recv`（`DRAIN_BATCH=32`）、异步 `SkipBudget(32)` 过滤让渡、`driver` 逐帧重检 `self.now()` 与 TX 冲刷预算（`64` 帧 / `2 ms`），并用 `Ipv6Packet::ethernet_header()` + `libc::writev` 消除了 `Tap::deliver` 热路径上的包拼接堆分配。
  - **待完成**：用 `recvmmsg` / `sendmmsg` / `TPACKET_V2` 环形缓冲区以及预分配帧池/切片复用，消除 `Monitor::recv()` 与 `Node::rx_data` 中的上行堆分配。

---

### P2：建连首包时延、内核/APK 链路加固与多信道体验

- [x] **6a. 消除热路径同步 `/system/bin/ip` 与事件循环上的阻塞 Netlink / sysfs 同步**
  - **已落地**：将 `RtnlNeigh` 移入后台期望态 [`NeighWorker`](../native/houji-runner/src/net_util.rs)（并加上 `SO_SNDTIMEO`/`SO_RCVTIMEO`、`2 s` 总截止期与退避重试），并将 [`peer_ctl.rs`](../native/houji-runner/src/peer_ctl.rs) 升级为带就绪查询与 5 次指数退避的期望态同步，避免对端上下线在单线程 Tokio 循环上造成任何 `fork+exec` 或阻塞停顿；`Tap::open` 启动期 `/system/bin/ip` 迁移（6b）仍待评估。

- [ ] **6b. 控制面冷启动瘦身：迁移启动期 `Tap::open`、`wonder-init` 与策略路由到原生 Netlink**
  - **方案**：在继续收拢 `Tap::open` 初始化的同时，将 [`radio.sh`](../runtime/owly-xos/radio.sh) 中 `wonder-init`（`nl80211` 建口设频）、`ip -6 addr/route/rule` 策略路由表 `10074` 及多次 `sleep 0.05` sysfs 轮询逐步并入 `owly-xosd`（原生 `rtnetlink`/`nl80211` + 事件等待），保留 [`radio.sh`](../runtime/owly-xos/radio.sh) 专注做 HAL 启停、固件挂载、`.ko` 装卸与异常回滚事务，预计可再压缩冷启动切换耗时约 `0.5–1.0 s`，并端到端实测首次 `/Discover` 建立时间。

- [x] **7a. 驱动与用户态 F2 / F6 / F10 深度治理项（已完成真机调试、570 符号 CRC 审计并合入 `runtime/owly-xos` 默认配置）**
  - **Audit F2（真实 TX 状态、计数与背压）**：`qca_cld3` 新增 `enum cdp_pt_tx_status` 与无锁 per-CPU `pt_tx_stats` / `pt_hdd_tx_stats`、完成与释放账本 `pt_tx_observe`（`insmod` 可选），为 `wondertap0` 接入 `.ndo_get_stats64` 并以 `NETDEV_TX_BUSY` + `250 us` `hrtimer` 唤醒实现 `pt_tx_backpressure=1` 队列背压（四线程 60k 帧洪泛实测触发 `busy_desc=23472`/`requeue=23472` 且队列自愈，无丢包黑洞与崩溃）；`wonder.ko` 改为先快照 `tx_info` 再按 `dev_queue_xmit()` 实际返回值报告 `wonder_txs_direct_report_info` 并计入 `tx_dropped`。
  - **Audit F6（per-peer 动态速率能力）**：`owly-awdl` 解析 TLV 17 中的 IE 191 `VhtCapabilities` 并通过 `Action::PeerCaps` 同步至 [`peer_ctl.rs`](../native/houji-runner/src/peer_ctl.rs) 写入 `pt_remote_peer_ctl`；WMA 层修复 `is_2ss=false` 时错误写入 `0xfffc` 的缺陷并默认开启 `pt_dynamic_rate=1`（`0444`），按真实对端 `ht/vht/nss/bw/mcs/sgi/ldpc` 协商能力设置 WMI `assoc` 参数。
  - **Audit F10（热点锁消除、多环与轻量遥测）**：[`dp_pt_rate_observer.h`](../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_pt_rate_observer.h) 改用 per-CPU 缓冲消除全局 `irqsave` 自旋锁竞争，`wma_dev_if.c` 将活动 peer 查找与 AST 查表改为 RCU/seq-lock 无锁快路径（消除嵌套持锁）；[`android.rs`](../native/houji-runner/src/android.rs) 将 `RxGaps` 迁至 `HOUJI_OWLY_RX_GAPS=1` 可选开关；[`radio.sh`](../runtime/owly-xos/radio.sh) 默认切为 `pt_single_ring=0`、`pt_dynamic_rate=1`、`pt_tx_backpressure=1`、`pt_rate_observe=0`。真机 `xos0 ↔ awdl0` 对比测试显示 `64 MiB` TX 由 `37.430 MB/s` 升至 `46.000 MB/s`（`+22.9%`），`RX` 由 `14.855 MB/s` 升至 `32.774 MB/s`，`16 MiB` 连续 3 轮 TX 中位数由 `24.584 MB/s` 升至 `33.348 MB/s`（注：AWDL 驻留调度波动大，单轮预热前后方差明显）。

- [ ] **7b. `MIShare.apk` 闭源层治理项（Audit F11–F13，可通过 [`Owly-XOS-Compat`](../runtime/owly-xos/compat/src/org/houji/xoscompat/Hook.java) LSPosed Hook 精准切入，待真机独立评估）**：
    - 压缩分支逐块构造 `StringBuilder` / `String.format` 并无条件投递 `Handler.post` 日志，以及逐块分配 `132,096 B` 解压缓冲（Audit F11）；
    - 接收 spool 文件生产者/消费者共用一把锁，完成时 `getFD().sync()` 持锁刷盘及末端收尾延迟（Audit F12）；
    - 发送 HTTP chunk framing 逐段 `write` 长度/CRLF/payload（Audit F13）。

- [ ] **8. 退出会话时的原厂 5 GHz Wi-Fi 定向频点快连与社交信道 `44 / 149` 协同**
  - **退场快连**：在 [`radio.sh`](../runtime/owly-xos/radio.sh) `snapshot()` 阶段记录原连接频点（如 `iw dev wlan0 link` 的频率），恢复时优先触发单频点定向扫描，缩短全频段盲扫重连时间。
  - **多信道协同**：当对端 Mac 连接在 `36–48` 信道 5 GHz AP 上时，其在信道 `149` 的 AWDL 驻留占空比仅约 `10%`；后续可评估按对端 PSF 主信道或 `XosBridge` 请求支持信道 `44`（`5220 MHz`）切换。

- [ ] **9. 协议栈跨对端隔离、内核时间戳与 RX BA `win=256` / 首轮预热打通（Audit F4 / F8 / F9 / §5）**
  - **per-peer 背压与控制流优先（F8 剩余项）**：[`driver.rs`](../native/owly/crates/owly-awdl/src/driver.rs) 的 `takes_outbound()` 仍按全节点总积压（`self.node.backlog() + self.unicast.len()`）关闭 `xos0` 读取；单个离线或闭窗对端堆满队列时仍会拖滞发往其他活跃对端的数据与 ACK，需改为按对端独立背压限额并为控制/纯 ACK 保留通行通道。
  - **RX 同步时钟对齐内核收包时间戳（F9 剩余项）**：当前 `Driver` 在从 `AF_PACKET` 读出帧后的用户态时刻生成 `rx_time` 并推进 AWDL 同步时钟，主机 RX 堆积时会将排队时延带入时隙估算；后续结合 `TPACKET` / `SO_TIMESTAMPING` 内核时间戳与 radiotap TSFT 校准时隙估计误差。
  - **打通真实空口 RX BA `win=256` 并排查 Run-0 首轮预热慢（F4 / §5）**：虽然驱动侧已配置 `pt_remote_rx_window=256`，但 [`frame.rs`](../native/owly/crates/owly-awdl/src/frame.rs) 回复对端 `ADDBA Request` 仍硬性限制窗口为 `64`、[`peer.rs`](../native/owly/crates/owly-awdl/src/peer.rs) 软件去重窗口为 `128` 序号；且真机仍观察到 `HOUJI_OWLY_TX_ADDBA=2` 下 `rx_resp=0` 及新会话 Run-0 TX 首轮速率明显低于稳态（`6.3–13.2 MB/s` vs `33–46 MB/s`）。需结合空口抓包明确 `ezif` 固件与用户态的 BA 所有权，协同 REO/去重边界放开真实 `256` 协商窗口并加速首秒速率爬升。

- [ ] **10. 大密度环境（`9+` 台 Apple 设备）：解耦用户态发现表与内核 `WMA_PT_MAX_PEERS=8` 硬件槽位**
  - **现状问题**：当前用户态 [`node.rs`](../native/owly/crates/owly-awdl/src/node.rs) `max_peers=8` 与内核 [`WMA_PT_MAX_PEERS=8`](../driver/wlan/qcacld-3.0/core/wma/src/wma_dev_if.c) 一比一绑定。教室/办公室同时存在 ≥9 台 Apple 设备时，第 9 台的 MIF 会触发 `evict_stalest()` 把前 8 台之一踢出用户态 peer 表并发起 `del <mac>`，在持续广播环境下易引发邻近对端来回挤出与反复拆建 WMI peer。
  - **参考对标**：小米原厂 `libxos.so` 与三星 One UI 9 闭源驱动 `wlan_hdd_wondertap_set_station_info`（见 [`27-firmware-ba-passthru-verification.md`](airdrop/27-firmware-ba-passthru-verification.md)）均只给 `wondertap` 远端站保留 `7` 槽上限（因芯片全局 `cfg->num_peers=17` 需与并发 STA/P2P 等接口共享），其中小米仅对解析出 AirDrop 服务记录的对端下发驱动注册，满 7 台后直接抑制新注册而不淘汰用户态表。
  - **方案**：将 `owly-awdl` 用户态发现表上限放宽至 `32–64`，把“用户态发现状态”与“内核硬件 8 槽位”解耦——仅优先为已建立 mDNS/TCP 或存在排队数据包的活跃交互对端下发 `pt_remote_peer_ctl` 占据硬件槽位，其余旁观对端留在用户态表与基础回退发送路径（`HTT_INVALID_PEER`），防止 9+ 设备震荡淘汰。

---

## 三、建议实施批次

1. **第一批（纯用户态/桥接加固，✅ 已全部合入源码，已过本地单测及远端隔离交叉编译）**：
   - [x] `node.rs` / `frame.rs` / `peer.rs`：纯 ACK 直发、每对端轮询队列、`DELBA`/`ADDBA Resp` 协商约束、4 次无应答 `10 s` 退避恢复；
   - [x] `driver.rs`：每帧重检 `self.now()`、关窗即止与 `64` 帧/`2 ms` 冲刷预算、补齐 `Frame`/`Outbound` 迟到统计；
   - [x] `android.rs` / `net_util.rs` / `peer_ctl.rs`：后台期望态 `NeighWorker` & `KernelPeerSync` 退避重试、`SkipBudget(32)` 过滤让渡、`Tap::deliver` `writev` 零分配；
   - [x] `XosBridge.java` / `radio.sh`：空闲宽限 `2 s → 10 s` + 状态锁/壳锁分离、路由幂等刷新与 `amss20.bin` 跳过第三次重复校验。
2. **第二批（数据面高阶零拷贝与内核/驱动联动，待后续专项真机排障）**：
   - [ ] `xos0` `IFF_VNET_HDR` + RX 缓冲池 / `recvmmsg` / `sendmmsg`（P1 #4/#5）；
   - [x] 驱动 `.ko` 真机联合修改（F2 真实 TX 完成与背压、F6 动态速率、F10 观察器无锁化与 `pt_single_ring=0`）。
3. **第三批（上层兼容性、跨信道体验与协议栈深水区，待真机验证）**：
   - [ ] `MIShare` spool 锁/`fsync`、chunk framing、压缩日志/大缓冲优化（F11–F13，`Owly-XOS-Compat` Hook 评估）；
   - [ ] 信道 `44 / 149` 协同、退场定向频点重连，以及 `Tap::open`/`wonder-init`/策略路由收拢至 `owly-xosd` Netlink（P2 #6b/#8）；
   - [ ] `takes_outbound()` per-peer 背压隔离、`AF_PACKET` 内核时间戳校准，以及 Run-0 预热与真实 RX BA `win=256` 打通（P2 #9）；
   - [ ] 用户态发现表放宽（`32–64`）并与内核 8 槽硬件准入解耦，解决 9+ 设备教室/办公室震荡淘汰（P2 #10）。
