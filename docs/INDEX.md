# 审阅导航

下表路径均以 `../experiments/` 为基础。

| 目录 | 用途 |
|---|---|
| [owly-xos](../runtime/owly-xos/README.zh-CN.md) | 原版小米互传 `MIShare.apk` 的 AIDL `XosBridge` 守护、`owly-xosd` L2 守护、`ezif.bin` 免预绑定多对端硬件 TX A-MPDU `xos0` 后端及 `evidence/` 匿名化实测日志 |
| [driver](../driver/README.md) | 合并去重后的单一 `wlan/` 覆盖层、`bridge/`、`wonder/`、Kbuild 配置与 `sources.json` 基线清单 |
| [native](../native/README.md) | 公共 `owly/` Rust 协议栈、`houji-runner/`（`owly-xosd`）与 `wonder-init/` 无线初始化工具 |
| [owly-app](../clients/owly-app/README.zh-CN.md) | `app/` 是 Java 界面；`native/` 是独立 App 上层协议封装；`runtime/owly-app/` 管理 root 无线会话；`evidence/` 记录安装和收发检查（当前暂缓，聚焦 `owly-xos`） |
| [three-findings-validation](../experiments/three-findings-validation) | 当前改动覆盖层已移至 `driver/wlan/`，非完整 WLAN 树；`src/userspace` 为实验后端；根目录存补丁、ABI 审计、构建和实机脚本；`runs/` 为每轮结果 |
| [sta-coexist](../experiments/sta-coexist/README.zh-CN.md) | 普通 WLAN 与 PASSTHRU 共存，按时间记录失败、修复、成功与恢复问题；`artifacts/` 按产物哈希隔离 |
| [owly-evaluation](../experiments/owly-evaluation) | Owly 源码和 `houji-runner/bench.py` 等公共测试、无线生命周期工具 |
| [remote-peer-probe](../experiments/remote-peer-probe) | 远端 peer 建立、REO、接收 PHY、VHT/2SS/SGI 与 BA 验证；部分脚本是当前测试工具依赖 |
| [tx-ampdu-investigation](../experiments/tx-ampdu-investigation) | TX A-MPDU、三星版本差异和固件来源核对；`firmware-acquisition` 保留获取配方及来源记录 |
| [peer-tx-investigation](../experiments/peer-tx-investigation) | AST 覆写、远端 peer 与速率对照实验 |
| [build-host](../archive/legacy-build/README.md) | 远端工具链、源码准备、ABI 与环境审计 |
| duplex-optimization | 双向同时传输的实验分支，已暂停，不是当前 App 默认后端 |
| amsdu、wideband | 软件 A-MSDU、带宽及 ring 容量早期实验 |
| ba-observer、ba-peer-observer、tx-observer | BA/peer/TX 统计观测补丁 |
| iphone-radio-research | iPhone 空口特征、Mac TCP 时间线与测量工具 |
| implementation-comparison | 三星/社区实现对照记录 |
| device-tests、artifacts | 早期实机证据和构建来源记录 |
| xos-fifo | 早期 XOS 桥接研究；独立 Owly App 不走该分享框架 |

## 优先查看的实测证据

- [原生小米互传 `owly-xos` 实测日志与遥测归档](../runtime/owly-xos/evidence/README.md)、[第 34 篇专题报告](airdrop/34-owly-xos-validation.md)、[第 35 篇高 `NO_ACK` 与延迟审计专题报告](airdrop/35-rx-no-ack-and-latency-audit.md)、[第 36 篇修复与 `1 GiB` RX 回归验证报告](airdrop/36-rx-no-ack-fix-and-verification.md) 及 [持续优化待办（OWLY-XOS-TODO）](OWLY-XOS-TODO.md)。
- [双 5 GHz 两轮 16 MiB](../experiments/three-findings-validation/runs/coexist-5g40-16m-20261111)：`1/result.json`、`2/result.json`、`rx-stats-final.txt`、`manual-recovery.log`、`stock-after.txt`。
- [2.4 GHz + 5 GHz 两轮 16 MiB](../experiments/three-findings-validation/runs/coexist-marked-16m-20261111)。
- [App 安装产物身份](../clients/owly-app/evidence/installed-artifacts.json)。
- [驱动实测交接](airdrop/28-three-findings-handover.md)、[EZIF TX 聚合验证](airdrop/32-ezif-tx-ampdu-validation.md)。

部分历史文档链接指向原仓库的其他下载目录；未复制内容可通过来源清单回到原路径查找。历史失败记录保留，不能单独将其当成最新状态。
