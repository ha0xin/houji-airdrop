# 当前状态（2026-11-15 整理快照）

## 已安装 App、共存合入与驱动状态

- 独立 App `org.houji.owly` 已合入 `2.4 GHz` 与非 DFS `5 GHz`（`5180–5240` / `5745–5825 MHz`）普通 WLAN STA 共存，并在退出时通过有界 `cmd wifi start-scan` + 必要时开关 WLAN 重试机制修复了双 5 GHz 退出后自动重连超时的问题。
- 共存模式下（如在 `owly0` 上共存期间）mDNS socket 已改用 `SO_BINDTOIFINDEX` 绑定到接口索引，解决多宿主 `:5353` 单播应答被系统/其他服务分走的问题，并已完成实机双向收发验证。
- `clients/owly-app/runtime-manifest.json` 现已绑定经过 567 符号 ABI 审计、支持 `pt_remote_multi` 多对端动态注册与硬件 TX A-MPDU 的候选驱动（`e81c4a90fd2f5d6e40cef5f2e282b3cad095c79a3e9a03dad1788f92306f4262`）；`install.py` 通过清单哈希校验从 `blobs/local/`（或环境变量）解析外部二进制与固件。
- 本地 `driver/wlan` 为增量覆盖层。实验补丁包括单/多 peer 生命周期（最多 8 个动态槽位）、聚合、EZIF RX 标记契约和受限的 5 GHz 准入放行；不是所有版本的固件通用接口。

## 原生小米互传（`runtime/owly-xos` 高速 `xos0` 后端）

- 新增 [`runtime/owly-xos`](../runtime/owly-xos/README.zh-CN.md)，通过 `XosBridge`（`com.domestic.xoscomm.IDomesticXOsComm/default`）+ `bridge-supervisor.sh`（保持 SELinux `Enforcing`）+ `houji-owly-app xos0 amsdu ack` 将系统原版 `MIShare.apk` 切换到 `owly-awdl` VHT80 2SS MCS9 SGI80 后端；独立 App `org.houji.owly` 路线当前暂缓，聚焦 `owly-xos` 开发。
- 现已切换至官方 `ezif-amss20.bin`（`pt_exclusive=1`、`pt_sta_coexist=0`）并启用免预绑定多对端硬件 TX A-MPDU（`pt_remote_multi=1`、`pt_tx_ampdu=1`、`pt_rx_msdu_buf=1`、`pt_rx_ezif_marked=1`、`pt_remote_reo=2`、`pt_remote_rx_window=256`、`HOUJI_OWLY_TX_ADDBA=1`）：用户态 `KernelPeerSync` 经专用线程异步向 `pt_remote_peer_ctl` 下发 `add/del/clear`（自动并发注册实测 `active=2 tx_ready=2`、`ast_valid=1`），无需预先绑定对端 MAC。
- 在冷启动与发现延迟上已完成两项端到端优化：`radio.sh enter` 通过预部署 `$RADIO/amss20.bin`、单遍 manifest 校验、50–100 ms 事件轮询将冷启动耗时从 `~6.2–9.2s` 缩短至 **3.16s**（`3,161 ms`）；同时在 `owly-awdl` 启用 `fast_discovery`（启动即发 PSF+MIF，并在 20 ms 冷却内对未知/未确认对端立即触发 MIF），在 `houji-runner` `Tap::peer_up` 时双向触发 `_airdrop._tcp.local.` PTR mDNS 查询与入向广播提示。
- 在信道 149 下与 Mac `awdl0` 实测单向 16 MiB（`16,777,216` 字节，SHA-256 全通过）TCP 传输：
  - 小米 `xos0` → Mac `awdl0`：**64.08 MB/s**（`0.262s`，Mac 4 MiB RX 缓冲，`agg_ppdus +307`、`agg_mpdus +6103`、`max_ppdu_mpdus=31`；前期 `win=64` 23.14 MB/s、旧 `dzf2.bin` 6.85 MB/s、原版 `libxos.so` 0.21–0.29 MB/s）；
  - Mac `awdl0` → 小米 `xos0`：**9.55 MB/s**（`9.11 MiB/s`，TCP 建连 `7.2 ms`；前期 `win=64` 基准 `8.09 MB/s`；`pt_rx_msdu_stats` 全程 `peer_miss=0`、`bad_desc=0`、`dropped=0`，`OWLY_RX_GAPS` `matched=24298 missing=0`）。
- 实机已完成原版 `MIShare.apk` 与 Apple 手机双向收发同一 `157,799,892` 字节（`157.80 MB`）MP4 视频端到端验证（匿名化遥测与日志归档见 [`runtime/owly-xos/evidence/`](../runtime/owly-xos/evidence/README.md)，专题分析见 [`34-owly-xos-validation.md`](airdrop/34-owly-xos-validation.md)）：
  - **发现延迟**：`MIShare` 冷切驱动 `startDomestXOsComm` 发送 **`3.62s`**、接收 **`3.44s`**；接收侧 NSD 注册完成后 **`1.46s`**（自 BLE 唤醒起共 **`6.18s`**）即收到 Apple `POST /Discover`；发送侧 `XOS_READY` 后 **`2.43s`** 发现对端 mDNS，点击分享至出头像共 **`9.03s`**。
  - **发送视频（小米 `xos0` → Apple 手机）**：`/Upload` 正文 **`8.40s`（`18.79 MB/s`）**，含对端落盘返回 `HTTP 200` 共 **`9.42s`（`16.75 MB/s`）**，`99.97%` VHT80 MCS9 SGI LDPC，`89.9%` 聚合 PPDU / `98.4%` 聚合 MPDU，`dropped=0`。
  - **接收视频（Apple 手机 → 小米 `xos0`）**：单对端 `win=64` 全程 **`11.97s`（`13.18 MB/s`）**、后半程稳态 **`20.35 MB/s`**（单块峰值 **`41.12 MB/s`**）；3 个 AWDL 节点在场 `win=256` 下开启双包 A-MSDU 阶段 **`13.00 MB/s`**（双块窗口 **`23.56 MB/s`**、单块峰值 **`29.54 MB/s`**），全程交付 `114,598` 个 IPv6 包，`OWLY_RX_GAPS` `matched=37093 missing=0 host_delayed=0`、全链路零丢包。
- 退出 `restore()` 前自动保存 `$STATE/last-session-stats.txt`（并保留 `prev-session-stats.txt` 备份），经 `op=clear` 与 `pt_release` 卸载并恢复原厂模块与 5 GHz Wi-Fi（`STOCK_RESTORED`）。

## App 路线共存测量

每轮分别传输双向各 16 MiB，SHA-256 全通过；单位十进制 MB/s，非整次 AirDrop UI 用时。

| STA / Owly | 轮次 | 小米→Mac | Mac→小米 | STA 网关 |
|---|---:|---:|---:|---|
| 2442 / 5745 MHz | 1 | 37.89 | 10.22 | 16/16 |
| 2442 / 5745 MHz | 2 | 45.42 | 4.49 | 26/26 |
| 5200 / 5745 MHz | 1 | 16.04 | 2.34 | 52/52，平均 26.557 ms |
| 5200 / 5745 MHz | 2 | 16.59 | 4.17 | 36/36，平均 30.179 ms |

双 5 GHz RX 描述符 397/397 交付，peer miss、bad descriptor、drop 为零。STA 映射 MAC 0，PASSTHRU 映射 MAC 1；这不能排除固件分时调度，也不能证明两路 RF 一直独立工作。聚合 observer 混有 STA 事件，不能把所有聚合计数归于 Owly。以上测试条件不完全相同，不是隔离变量的频段性能对比。

## 未完成项

1. 原生 `MIShare` 的 `xos0` 后端已在 `pt_exclusive=1` + `ezif.bin` 下完成免预绑定多对端硬件 TX A-MPDU、冷启动/发现提速与 `157.80 MB` 视频双向端到端验收；后续可继续挖潜 `peer_up` EUI-64 NDP 邻居预热（消除发送侧首连 `~1.76s` 等待）、反向纯 TCP ACK 跳过 `1000 μs` 合包等待直发，或评估开启 `pt_sta_coexist=1` 普通 Wi-Fi 共存。
2. 动态多信道调度、仅限联系人身份证书校验，以及 `iPhone` 长时间锁屏、漫游或高并发 STA 流量下的稳定性长测仍待补全。
3. Mac→小米在部分共存场景下存在速率波动；继续沿用当前稳定 RX 优先策略。
4. 独立构建与部署入口见[构建验证记录](guides/build-verification.md)；编译验收与手机运行验收分开。

最后一轮验证结束后已恢复原厂模块/固件、普通 5 GHz 连接及 `IDomesticXOsComm/default` 守护待命状态；无残留实验接口或路由。
