# 原生小米互传 `owly-xos` 多对端硬件 TX A-MPDU、快速发现与实机双向验证（2026-11-15）

## 摘要与阶段目标

在不修改或重签名系统原版 `MIShare.apk`（`com.miui.mishare.connectivity`）的前提下，[`runtime/owly-xos`](../../runtime/owly-xos/README.zh-CN.md) 用 `XosBridge`（`com.domestic.xoscomm.IDomesticXOsComm/default`）+ `owly-awdl` VHT80 2SS MCS9 SGI80 替代原厂低速 `libxos.so`（HT20 MCS3，`0.21–0.48 MB/s`）。

本轮将开发重心集中到 `owly-xos` 路线（独立 App `Owly AirDrop` 在完成 STA 共存合入后暂缓），依次打通并实机验证了四项关键能力：

1. **免预绑定多对端硬件 TX A-MPDU（`pt_remote_multi=1`）**：解决原版 `MIShare` 全程只调用一次无对端 MAC 的 `startDomestXOsComm(149)`、无法按单对端重载驱动绑定 `pt_remote_mac` 的限制；内核开放 `pt_remote_peer_ctl` 动态 8 槽位管理，用户态 `KernelPeerSync` 随 AWDL 节点上下线异步执行 `add/del/clear`，在单一持久化 `xos0` 会话中解锁官方 `ezif-amss20.bin`（`wire506=1`）硬件 TX A-MPDU 与 REO MSDU-buffer RX。
2. **驱动/固件冷启动加速（`~6.17–9.19 s` → `3.16–3.62 s`）**：`install.py` 预置 `$RADIO/amss20.bin`、合并 manifest 单遍校验，并用 `50–100 ms` 就绪轮询替代 `radio.sh enter` 中的固定 `sleep` 等待。
3. **AWDL 与 mDNS 双向快速发现**：`owly-awdl` 新增 `Config::fast_discovery`（零延迟发射首个 PSF+MIF，并在 `20 ms` 冷却内对未知/未确认对端即时回应 MIF）；`houji-runner` 在 `Tap::peer_up` 时向对端立即发送（及 `+250 ms` 补发）`_airdrop._tcp.local.` `PTR QU` 查询，同时在 `+50 ms` / `+900 ms` 向 `xos0` 注入入向 `QM` 提示，唤醒系统 `MdnsAdvertiser.xos0` 立即向新对端广播服务。
4. **RX REO 初始窗口扩展（`64 → 256`）与双会话遥测归档**：将 `pt_remote_rx_window` 调至 `256`，并在 `capture_session_stats()` 中保留 `prev-session-stats.txt`，完整捕获连续收发两轮会话的硬件计数。

匿名化实测日志与原始计数快照归档于 [`runtime/owly-xos/evidence/`](../../runtime/owly-xos/evidence/README.md)。

---

## 一、实现要点

### 1. 内核多对端 PASSTHRU（`driver/wlan/`）

- 在 `wma_dev_if.c` 与 `wlan_hdd_passthru_runtime.c` / `wlan_hdd_passthru_control.c` 中新增模块参数 `pt_remote_multi=1` 与 sysfs 控制节点 `/sys/kernel/kiwi_v2/pt_remote_peer_ctl`（权限 `0600`）：
  - 支持 `add <mac>`、`del <mac>`、`clear` 命令，最多维护 **8 个并发远端对端槽位**（每个槽位独立执行 `WMI_PEER_CREATE` → `cdp_peer_setup` / `reo_setup` → `WMI_PEER_ASSOC` → AST/hash 映射，并在删除或 `pt_release` 时逆序清理）。
  - 数据面发送按以太网/802.11 目的 MAC 匹配已 `tx_ready=1` 的远端槽位覆写 AST index/hash，未注册组播/管理帧保持原自节点路径，互不干扰。
- 目标内核编译通过 567 个导入符号 CRC、vermagic 与 ABI 审计（模块 SHA-256：`e81c4a90fd2f5d6e40cef5f2e282b3cad095c79a3e9a03dad1788f92306f4262`）。

### 2. 用户态 L2 TAP、协议兼容与异步对端同步（`owly-awdl` + `houji-runner`）

- **`KernelPeerSync` 异步控制线程**（`houji-runner/src/peer_ctl.rs`）：独立后台线程通过有界队列串行向 `pt_remote_peer_ctl` 写入 `add/del/clear`，避免 sysfs 同步 WMI 等待阻塞 `owly-awdl` 16 TU 定时器主循环；退出时确保 `op=clear active=0`。
- **Android `xos0` TAP 栈三项兼容修正**：
  1. `owly-awdl/src/ip.rs`：Apple AWDL 组播数据帧的 802.11 `addr1` 为广播 `ff:ff:ff:ff:ff:ff`，直接转成以太网帧会被 Linux `ip6_rcv_core` 判定 `PACKET_BROADCAST` 丢弃；现按 RFC 2464 统一映射为 `33:33:<low32>`。
  2. `houji-runner/src/ndp.rs`：用户态直接为邻居请求生成 NDP Neighbor Advertisement，并把对端发往本机 link-local `:5353` 的单播 mDNS 响应（`QR=1`）重写目的地址为 `ff02::fb` 后重算 UDPv6 校验和，绕开 `system_server` `NsdService` 在 `xos0` 上 `SO_BINDTODEVICE` 抢占单播 `:5353` 的问题。
  3. `TapInject` 注入通道：`Tap::peer_up` 时双向触发出向空口 `QU` 查询与入向 TAP `QM` 提示，消除被动等待 Apple 周期性 mDNS 广播的空窗。

---

## 二、冷启动与发现时延实测

对照优化前基线与 2026-11-15 实机连续收发日志（见 [`mishare-e2e-timeline.txt`](../../runtime/owly-xos/evidence/mishare-e2e-timeline.txt)）：

| 指标 | 优化前基线 | 优化后实测 | 说明 |
|---|---:|---:|---|
| CLI `radio.sh enter` 冷启动 | `6.17 – 9.19 s` | **`3.161 s`** | 含停原厂 WLAN、`rmmod`、装载 3 个 `.ko`、固件激活与 `xos0` 就绪 |
| `MIShare` 发送 `startDomestXOsComm(149)` | `6.17 – 9.19 s` | **`3.616 s`** (`14:29:22.952 → 26.568`) | 提速约 **41%–61%** |
| `MIShare` 接收 `startDomestXOsComm(149)` | `6.17 – 9.19 s` | **`3.444 s`** (`14:30:07.539 → 10.983`) | BLE 扫到 Apple 广播后仅 `49 ms` 即触发拉起 |
| 发送：`XOS_READY` → `RawMdnsBrowser` 发现对端 | `1.2 – 5.0 s` | **`2.425 s`** (`26.597 → 29.022`) | 含 `26.608` 发出 BLE 广播唤醒对端手机 AWDL 的时间 |
| 发送：点击分享 → `/Discover 200 OK` 出头像 | `~8 – 12+ s` | **`9.027 s`** (`22.412 → 31.439`) | 其中首次 TCP 建连占 `1.761 s`（见第四节待优化项） |
| 接收：`NSD` 注册完成 → Apple 连入 `POST /Discover` | — | **`1.462 s`** (`11.929 → 13.391`) | 从 `XOS_READY` (`11.061`) 起算共 **`2.330 s`** |
| 接收：BLE 触发激活 → Apple `/Discover 200 OK` | `~8 – 12+ s` | **`6.176 s`** (`07.219 → 13.395`) | 随后 `+1.58 s` 第二台附近 Apple 设备也自动完成 `/Discover` |

---

## 三、吞吐与硬件遥测实测

### 1. 裸 TCP 16 MiB 双向基准（小米 `xos0` ↔ Mac `awdl0`，信道 149）

单向各传输 `16,777,216` 字节随机载荷，两端 SHA-256 严格一致：

| 方向 | 原厂 `libxos.so`（HT20 MCS3） | `dzf2.bin` 软件 A-MSDU | `ezif.bin` 多对端 A-MPDU（`win=64`） | `ezif.bin` 多对端 A-MPDU（`win=256`） |
|---|---:|---:|---:|---:|
| 小米 `xos0` → Mac `awdl0` | `0.21 – 0.29 MB/s` | `6.85 MB/s` | `23.14 MB/s` | **`64.08 MB/s`**（`0.262 s`，Mac 4 MiB RX 缓冲） |
| Mac `awdl0` → 小米 `xos0` | `0.48 MB/s` | `9.95 MB/s` | `8.09 MB/s` | **`9.55 MB/s`**（`1.757 s`，TCP 建连 `7.2 ms`） |

- 发送单轮硬件计数增量：`agg_ppdus +307`、`agg_mpdus +6,103`、峰值 **`max_ppdu_mpdus=31`**（284 个 PPDU 落在 `[17..=32]` 档），`100%` 80 MHz VHT MCS9 SGI LDPC。
- 接收单轮：`pt_rx_msdu_stats` `peer_miss=0`、`bad_desc=0`、`dropped=0`；`OWLY_RX_GAPS` `matched=24,298 missing=0 host_delayed=0`。

> 说明：Mac `awdl0` → 小米 `xos0` 低于 Apple 手机 → 小米（稳态 `20–29 MB/s`），原因是 macOS 在存在普通 Infrastructure Wi-Fi 时对 `awdl0` 施加约 `10%` 信道驻留占空比（`600 ms / 6 s`，对应实测天花板约 `9–10 MB/s`）。

### 2. 原生 `MIShare.apk` 发送 `157.80 MB` 视频（小米 `xos0` → Apple 手机）

测试样本为同一实拍 MP4 视频（精确 `157,799,892` 字节，`dvzip` `storedOnly=true`、`payloadBytes=157,800,448`、`storedChunks=1,204`、`deflateMs=0`），内核快照见 [`send-session-stats.txt`](../../runtime/owly-xos/evidence/send-session-stats.txt)：

- **建连与确认**：点击头像后 TCP 建连 **`8 ms`**（`14:29:32.886 → 32.894`），TLSv1.3 握手 **`17 ms`**（`→ 32.911`），对端确认 `/Ask` 后于 `14:29:34.570` 开始 `/Upload`。
- **端到端吞吐**：
  - `/Upload: headers sent` → `/Upload: body sent`（`34.570 → 42.968`）：**`8.398 s`**，发送端吐出均速 **`18.79 MB/s`**；
  - 含对端落盘返回 `HTTP/1.1 200 OK`（`→ 43.992`）：总耗时 **`9.422 s`**，端到端落盘均速 **`16.75 MB/s`**（此前 `win=64` 同视频后半程稳态 **`22.73 MB/s`**、分块突发 `35–38 MB/s`）。
- **硬件 TX A-MPDU 与反向 RX 健康度**：
  - `pt_remote_peer_ctl`：`active=2 tx_ready=2 reo_mode=2`（并发维护周围 2 台 Apple 设备 `peer_id=24, 25`，`ast_valid=1`、`setup_rc=0`）；
  - `pt_rate_stats`：`valid=23,308` 帧中 **`23,300` 帧（`99.97%`）为 VHT80 MCS9 SGI LDPC**；共 `3,713` 个 PPDU，其中 **`agg_ppdus=3,338`（`89.9%`）**、**`agg_mpdus=22,933`（`98.4%`）**，`[9..=16]` 档达 `2,211` 个 PPDU（`max_ppdu_mpdus=9`）；
  - `pt_rx_msdu_stats`：`seen=372 reaped=372 delivered=372 dropped=0 peer_miss=0 bad_desc=0`。

### 3. 原生 `MIShare.apk` 接收同一个 `157.80 MB` 视频（Apple 手机 → 小米 `xos0`）

对比单对端 `pt_remote_rx_window=64` 基线与多对端在场 `pt_remote_rx_window=256` 实测（见 [`receive-session-stats.txt`](../../runtime/owly-xos/evidence/receive-session-stats.txt) 与 [`receive-runner-telemetry.txt`](../../runtime/owly-xos/evidence/receive-runner-telemetry.txt)）：

| 轮次与环境 | `rx_window` | `/Upload` 总耗时 | 全程均速 | 稳态 / A-MSDU 阶段均速 | 双分块窗口 / 单分块峰值 | 丢包与乱序等待 |
|---|---:|---:|---:|---:|---:|---|
| 轮次 A（单对端在场） | `64` | **`11.970 s`** | **`13.18 MB/s`** | 后半程 **`20.35 MB/s`** | `0.804 s` 窗口 **`26.08 MB/s`** / 单块 **`41.12 MB/s`** | `dropped=0` |
| 轮次 B（3 个 AWDL 节点在场） | `256` | `23.861 s` | `6.61 MB/s` | 阶段二（`102.87 MB / 7.911 s`）**`13.00 MB/s`** | 双块 **`23.56 MB/s`** / 单块 **`29.54 MB/s`** | `missing=0`、`host_delayed=0`、`dropped=0` |

#### 轮次 B 的两阶段空口行为定性

轮次 B 的用户态 `STATS` 与分块时间戳呈现严格的数学对应关系：

1. **阶段一（`14:30:18.619 → 34.564`，`15.945 s`，前 `54.53 MB` = `3.42 MB/s`）**：
   - 现场同时有 **3 个 Apple AWDL 节点**入网（`active=1 → 2 → 3 → 2`），且 2 台 Apple 设备同时与 `AirDropServer` 保持 OPEN 连接；
   - `runner` 前 20 秒统计 `rx_data=29,468`、`delivered=29,460`（`delivered / rx_data = 1.000`），说明对端手机在此期间**仅发送单包非 A-MSDU MPDU**（约 `40,433` 帧），且 `OWLY_RX_GAPS` 的 `radio_us` 与 `host_us` 同步观测到 `14` 次 `128–256 ms` 空口静默间隔（合计 `2.436 s`），证实限速来自对端空口保守调度而非小米侧丢包。
2. **阶段二（`14:30:34.564 → 42.475`，`7.911 s`，后 `102.87 MB` = `13.00 MB/s`）**：
   - 对端切入**双包 A-MSDU + 高阶 A-MPDU**，全程共收到 `37,093` 个双包 A-MSDU 大帧（`40,433 × 1 + 37,082 × 2 ≈ 114,598 = delivered`）；
   - `OWLY_RX_GAPS` 显示 `matched=37,093` 中有 **`equal=34,790`（`93.8%`）** 与前帧共享同一 PPDU 空口时间戳，即平均每个 PPDU 聚合 **`16.1` 个 A-MSDU 大帧（约 32 个 IPv6 包）**，单块（`4.19 MB / 0.142 s`）瞬时峰值达 **`29.54 MB/s`**。
3. **`pt_remote_rx_window=256` 验证结论**：
   - 全程 `host_delayed=0`、`equal_host_delayed=0`、`host_caught_up=0`、`missing=0`、`air_rx_kernel_drops=0`、`tap_delivery_drops=0`、`pt_rx_msdu_stats` `seen=999 delivered=999 dropped=0`：硬件 REO 256 窗口在 32 包级突发下实现 **0 次主机侧乱序超时阻塞**。
   - 同时对照轮次 A/B 可确认：此前 `win=64` 时的接收瓶颈同样不在硬件 REO 窗口溢出，而取决于对端何时启用 A-MSDU、多节点 EAW 空口共享以及反向 TCP ACK 节奏；保留 `win=256` 可作为零副作用安全余量。

---

## 四、退出恢复与下一步优化方向

- **恢复闭环**：两轮会话结束后，`MIShare` 通过 `WifiConflictManager` 定向扫描信道 `5200 MHz` 并释放 `xos0`，`radio.sh restore` 执行 `op=clear`、`pt_release`、卸载实验模块并重连原厂 5 GHz Wi-Fi，状态回执为 `STOCK_RESTORED`。
- **后续高收益可选项**：
  1. **消除发送侧首次 `/Discover` 的 `1.76 s` TCP 建连等待**：首次发现对端后 `AirDropClient` 建连耗时 `1.761 s`（对比 3 秒后正式 `/Upload` 建连仅 `8 ms`），系 `xos0` 首次 IPv6 NDP 邻居解析跨越 AWDL 窗口触发首包 `SYN` 1 秒重传所致；后续可在 `peer_up` 时按对端 AWDL MAC 预热邻居缓存。
  2. **反向纯 TCP ACK 免等待直发**：接收 `157.80 MB` 期间本机共回发 `8,153` 个空口帧（仅 `101` 个触发合包）；让短纯 TCP ACK 跳过 `OWLY_COALESCE_US=1000` 微秒合包等待立即入队，有助于缩短 RTT、促使 Apple 发送端更快拉升拥塞窗口与开启 A-MSDU。
