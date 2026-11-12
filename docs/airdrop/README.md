# 小米 14 AirDrop 移植文档

> 最新交接入口：[当前交接（2026-11-11）](28-three-findings-handover.md)。本文保留历史阶段信息；设备状态、用户要求和待办以最新交接为准。

更新：2026-11-11。本文组整理当前实现；旧 HANDOFF 保留排查历史，遇到阶段性结论冲突时以本组的状态与证据边界为准。手机状态描述是最后一次验证结果，不代表每次阅读时的实时状态。

## 阅读顺序

1. [架构与两条路线](01-architecture.md)：共用驱动、不同 AWDL 引擎和上层协议。
2. [Mosey 实现](02-mosey.md)：早期双向传输与 Live Photo 接收。
3. [小米 XOS 实现](03-xos.md)：独立测试应用、原版互传与系统分享集成。
4. [运行、Wi-Fi 与恢复](04-operations.md)：会话生命周期、排障和切换边界。
5. [验收与性能](05-validation-performance.md)：成功样本、吞吐和未解决问题。
6. [驱动重建与下一步](06-driver-build.md)：源码、ABI、服务器与优化顺序。

7. [公开资料与原因对照](07-public-research.md)：窗口、重试阻塞与适配边界。

8. [WLAN 固件资料](08-firmware-resources.md)：精确样本、公开固件、接口与逆向工具。

2026-11-10：build-host 已完成三个模块基线重建及实机加载；[产物与验收边界](06-driver-build.md)。149/HT20 原生小米→iPhone 约1.01MB/s，反向接收约2.16MB/s，退出恢复通过；MCS7 的小米→Mac 两轮为1.92–2.22MB/s。用户取消后续原生人工对照，改为先排查大于10MB/s的链路能力。

9. [2026-11-10 实机与149信道](09-20261110-device-validation.md)：基线加载、队列观测、约1MB/s实测与临时原生验证路径。

10. [吞吐限制与宽带实验](10-throughput-limits.md)：已确认的HT20/NSS1与单包聚合限制、公开资料及VHT80实验边界。

11. [Mosey JADX核查](11-mosey-inspection.md)：APK、Binder与native核心的证据边界。

12. [OWL、XOS、Mosey逻辑对照](12-engine-comparison.md)：XOS队列丢包定位与同条件对照要求。

13. [Owly Android 设计](13-owly-feasibility.md)记录接口选择和独立 Wonder 初始化。

14. [Owly 149 信道实机记录](14-owly-houji-device.md)记录小米与 Mac 的双向 SHA-256、吞吐和退出恢复。

15. [聚合接口与 BA 路线](15-aggregation-roadmap.md)区分软件 A-MSDU、固件 TX 聚合和 RX BA 所需证据。

16. [PASSTHRU RX 聚合实机观测](16-ba-observer-device.md)记录观察器开关对照、计数审计和恢复。

17. [单对端 RX peer 与 REO 观测方案](17-rx-peer-reo-observer-plan.md)记录下一步需要验证的前置条件。

18. [Apple phone 原生 AWDL 对照](18-iphone-radio-reference.md)记录 USB 只读调查及独立抓包能力的验证范围。

19. [PASSTHRU 远端 peer 能力探测](19-passthru-remote-peer-probe-plan.md)记录单次实机创建、固件回执、映射和清理闭环。

20. [VHT A-MSDU 上限实机对照](20-vht-amsdu-device.md)记录能力解析、软件大帧的实测退化和恢复。

21. [Mac TCP 连接遥测](21-owly-mac-tcp-telemetry.md)记录原配置的双向重传、乱序和 RTT。

22. [PASSTHRU RX route 与 REO 队列诊断](22-passthru-rx-reo-probe.md)分开验收队列准备、绑定和释放。

23. [149 信道 VHT 接收容量审计](23-vht-rx-capacity.md)记录单缓冲契约和独立出站广告的验证方案。

24. [Owly 149 信道当前交接](24-owly-houji-handoff.md)记录接收慢速根因修复（`4.532 MB/s` 加权 / `4.662 MB/s` 峰值，0 丢包 0 重传）、真实 RX PHY 及 `STOCK_RESTORED` 恢复状态。

25. [Apple phone 与 6s Plus 原生 AWDL 特性解析及提速评估](25-Apple phone-mac-awdl-features.md)记录 `Mac → Apple phone` 实测 `42.03 MB/s`（峰值 `48.05 MB/s`）、Mac 内核/驱动/固件开启的五大硬件特性对照，以及 `2×2 MIMO + SGI80 + ADDBA BlockAck` 的工程难度评估。

26. [2×2 MIMO + SGI80 + 安全 ADDBA 实机突破报告](26-2ss-sgi80-addba-probe.md)记录在不修改 Mac、不启用 `WMI_PEER_CREATE` 的纯 `self-peer` 模式下跑通 `VHT80 2SS MCS 9 SGI80`（`866.7 Mbps`）+ 硬件 SIFS `BlockAck` + A-MPDU×A-MSDU 混合聚合，将 `Mac → 小米` 64 MiB×3 加权速度推进至 **14.705 MB/s**（单样本峰值 **18.483 MB/s**），实现双向 `>6 MB/s` 及接收 `>10 MB/s` 突破。

27. [WCN7850 PASSTHRU 固件 BA 与远端 Peer 机制核查](27-firmware-ba-passthru-verification.md)交叉核对公开 Qualcomm WMI/DP 规范与三星模块，推翻“固件内部互斥”旧推断并定位 [第 22 篇](22-passthru-rx-reo-probe.md) TX 卡死的主机侧根因（注：涉及服务号 `506` vs `338`、`wonder_txd` 与 DP REO 消费者的四项最新源码对照勘误及实测进度见第 28 篇）。

28. [三项技术发现实机验证进度与交接文档](28-three-findings-handover.md)记录发现一实机闭环通过回执（远端 Peer `ASSOC_CONF` 零 TX 卡死、2s 无重启恢复）、发现二首轮实测与 C 源码根因、三星公开源码 + `EZIF` 四项关键勘误，以及下一会话完成发现二补完与发现三（`wlan0` STA + `PASSTHRU` 并发）的接手执行清单。

## 当前成果

| 项目 | Mosey 路线 | 小米 XOS 路线 |
|---|---|---|
| AWDL 引擎 | One UI 提取的 mosey_server / FFI | madrid 提取的 libxos.so，基于 OWL 改造 |
| 虚拟接口 | mosey0 | xos0 |
| iPhone 原生双向传输 | 已验证 | 测试应用、原版互传分别验证 |
| Live Photo 接收 | .pvt 完整包已验证 | 测试应用 .pvt 完整包已验证 |
| 小米系统相册分享 | 未集成 | 原版互传已验证 |
| 超级岛 | 未集成 | 原版接收已由用户确认 |
| 普通 Wi-Fi 并发 | 未实现 | 未实现 |
| 开机自动恢复运行 | 未完成 | 未完成 |

当前日常验证主线是 **XOS + 原版 MIShare 5.4.2 + Binder 桥接 + LSPosed 兼容**。Mosey 保留为另一套已验证方案，不与 XOS 同时启动。原运行包保留上游预编译模块；独立 Owly 宽带实验路径已率先突破 **>10 MB/s** 目标（`Mac → 小米` 64 MiB×3 加权 **14.705 MB/s**、峰值 **18.483 MB/s**），待按需回迁至日常主线。

Owly 在 149 信道与 Mac（零修改）的独立探针已通过双向 SHA-256。经修复反向纯 TCP ACK 信道争用、`AF_PACKET` 接收缓冲丢包，并开启 **2×2 MIMO（2SS MCS 0–9）+ SGI80 + 安全 ADDBA（保留 Bit 0 `A-MSDU Supported`，触发 WCN7850 `self-peer` 硬件 SIFS `BlockAck`）** 后，完整验收的 64 MiB×3 重复组加权速度提升至 **Mac→小米 14.705 MB/s（峰值 18.483 MB/s，追平 Apple phone 原生 AWDL 均值 18.41 MB/s，全程 `fcs=0`、0 丢包）、小米→Mac 6.590 MB/s（16 MiB 峰值 8.894 MB/s；对称 2SS MCS9 下峰值 6.369 MB/s）**，实验结束后已恢复原厂 Wi-Fi（`STOCK_RESTORED`）。 [2SS+SGI80+ADDBA 突破报告](26-2ss-sgi80-addba-probe.md) · [最新结果与设备交接](24-owly-houji-handoff.md) · [Apple phone 特性对照](25-Apple phone-mac-awdl-features.md)

设备：小米 14 / houji，ADB `DEVICE_SERIAL`，目标内核 `6.1.138-android14-11-g0c3d559bcd85-ab14529422`。外部组件涉及三处来源：**三星 Galaxy S24 Ultra（`SM-S928B` / `e3q`）** 的 `S928BXXS6DZF2` `kiwi_v2` PASSTHRU 固件 `amss20.bin` 与 `S928BXXU6EZIF` One UI 9 镜像中的 Mosey 组件、**上游 `YoWenhow/Xiaomi14-PixelOS-Airdrop`** 为 `houji` 适配编译的三个内核模块（`qca_cld3_kiwi_v2.ko` / `houji_wonder_bridge.ko` / `wonder.ko`），以及 **小米 18 Pro Max（`madrid` / `OS4.0.29.0.XEOCNXM`）** 提取的 XOS 与原版互传组件；完整哈希与拆包对照见 [架构与两条路线](01-architecture.md)。不能将来源机型的原生驱动能力直接套用到 houji。

## 原始记录

- [Mosey 与共同底座交接](../../downloads/oneui9-houji-analysis/AIRDROP-HANDOFF.md)
- [madrid 提取及 XOS 交接](../../downloads/madrid-analysis/HANDOFF.md)
- [XOS 测试应用说明](../../downloads/madrid-analysis/port/README.zh-CN.md)
- [原版互传说明](../../downloads/madrid-analysis/native-integration/README.zh-CN.md)
- [驱动性能定位原始记录](../../downloads/madrid-analysis/native-integration/verification/DRIVER-PERFORMANCE-FINDINGS.zh-CN.md)

续测结果：[三项发现续测实测报告](28-three-findings-device-validation.md)（2026-11-11）：原始 wire 506 未置位；REO/固件 ADDBA 对照、64 MiB×3 验收及联网并发负结果。

29. [同模块 self-peer / remote-peer 发送对照](29-peer-tx-comparison.md)：四轮交叉对照及两轮按 socket 隔离的 TCP 采样，确认远端 peer 路径额外降速，尚未定位唯一根因。

30. [远端 AST 隔离与 TX A-MPDU 启用链调查](30-peer-ast-and-ampdu-investigation.md)：保留远端关联，只切换 AST 覆写，发送速度出现恢复→下降→恢复；并行核查聚合上限、重试、协商时机及固件 BA 状态同步缺口。

31. [EZIF 官方固件实机探测](31-ezif-firmware-probe.md)：官方 EZIF 实测 wire 506=1，关联成功；旧驱动 REO 接收异常触发恢复，尚未完成传输或 TX 聚合验证，手机已恢复原厂 Wi-Fi。

32. [EZIF TX A-MPDU 实装与吞吐验证](32-ezif-tx-ampdu-validation.md)：修复 BE cookie 转换与 PASSTHRU RX 接管，完成64 MiB×3双向校验；小米→Mac加权20.978 MB/s，硬件完成记录观测5,718个聚合PPDU、峰值18 MPDU/PPDU。独立实验已通过，日常分享组件尚未回迁。
