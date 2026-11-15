# 交接入口

## 2026-11-15：App 共存合入与小米互传 `xos0` 高速后端

本轮推进了两条路线并完成了实机匿名化验收：

- **独立 App（`clients/owly-app/` + `runtime/owly-app/`）**：
  - 将 `2.4 GHz` 与非 DFS `5 GHz`（`5180–5240` / `5745–5825 MHz`）STA 共存合入 `radio.sh` / `control.sh` 与 `MainActivity.java`，并改用 `dc6b21e4...` 567 符号 ABI 审计驱动模块；DFS/受限频段自动回落 `pt_exclusive=1`。
  - 修复双 5 GHz 退出后 Wi-Fi 自动重连超时：增加有界 `cmd wifi start-scan` 与必要时的 WLAN 开关恢复循环；验证恢复后连接稳定、零丢包。
  - 在 `owly-airdrop/src/driver/mdns.rs` 使用 `SO_BINDTOIFINDEX` 将 IPv6 `:5353` mDNS socket 绑定到对应接口索引，避免共存多宿主下被其他服务截走单播应答；App 收发双向冒烟校验通过。
  - 解耦部署清单：`clients/owly-app/runtime-manifest.json` 定义外部内核模块与固件 SHA-256，由 `install.py` 从 `blobs/local/`（或环境变量）解析并校验；`runtime/owly-app/build-radio.py` 完成脚本校验通过。
- **小米原生互传（`runtime/owly-xos/` + `driver/wlan/` + `houji-runner` / `owly-awdl`，当前主线）**：
  - 实现 AIDL `XosBridge`（`com.domestic.xoscomm.IDomesticXOsComm/default`）与 `bridge-supervisor.sh`，在保持 SELinux `Enforcing` 下通过 `ksud sepolicy patch` 与每次启动首次 `servicemanager` `avc_cache` 驱逐让原版 `MIShare.apk` 直接绑定守护服务。
  - 在内核新增 `pt_remote_multi=1` 与 `pt_remote_peer_ctl`（最多 8 个动态远端 peer 槽位），配合用户态 `KernelPeerSync` 异步线程切换至官方 `ezif-amss20.bin` 免预绑定硬件 TX A-MPDU + REO MSDU-buffer RX（`pt_remote_rx_window=256`、`HOUJI_OWLY_TX_ADDBA=1`），并补齐 RFC 2464 组播 MAC、用户态 NDP 与单播 `:5353` mDNS 响应转投 `ff02::fb`。
  - 完成冷启动与双向发现优化：`radio.sh enter` 预置固件与事件轮询将冷启动从 `~6.2–9.2s` 压至 **`3.16–3.62s`**；`owly-awdl` `fast_discovery` 与 `Tap::peer_up` 双向 PTR 查询/提示使接收侧 NSD 就绪后 **`1.46s`** 即收到 Apple `/Discover`。
  - 实机验收（详见 [`34-owly-xos-validation.md`](airdrop/34-owly-xos-validation.md) 与 [`runtime/owly-xos/evidence/`](../runtime/owly-xos/evidence/README.md)）：
    - 16 MiB TCP 裸链路（`xos0` ↔ Mac `awdl0`，SHA-256 通过）：小米→Mac **`64.08 MB/s`**（`max_ppdu_mpdus=31`），Mac→小米 **`9.55 MB/s`**；
    - 原版 `MIShare.apk` ↔ Apple 手机双向收发 `157.80 MB` MP4 视频：发送正文 **`8.40s`（`18.79 MB/s`）**、含对端落盘 **`9.42s`（`16.75 MB/s`，`98.4%` MPDU 硬件聚合）**；接收单对端 `win=64` **`11.97s`（`13.18 MB/s`，稳态 `20.35 MB/s`）**，多对端 `win=256` 全程零丢包、`host_delayed=0`、A-MSDU 阶段 **`13.00 MB/s`（峰值 `29.54 MB/s`）**，退出均干净恢复 `STOCK_RESTORED`。

## 2026-11-12：目录整理

用户要求大致按分层 Monorepo 方向整理，**暂不增加任何版本锁定和 SHA**。本轮进行了独立构建验证，没有上机、部署、暂存或提交；既有校验逻辑和历史 SHA 记录保留。检查时发现 README 已有暂存内容，本轮保留该索引状态，没有覆盖或撤销用户暂存。

- 独立 App：`clients/owly-app/`。
- 自包含驱动目录：`driver/`（单一合并 `wlan/`、`bridge/`、`wonder/`、`configs/`、`sources.json`）。
- 公共原生协议栈与守护：`native/`（`owly/`、`houji-runner/`、`wonder-init/`）。
- App root 运行脚本：`runtime/owly-app/`；主线互传后端：`runtime/owly-xos/`。
- 构建入口：`scripts/`（已彻底脱离 `third_party/` 与独立 App 交叉依赖）。
- 实验工具与记录：`experiments/`；实机测试导航：`tests/device/`。
- 首次导入证据：`archive/import/`；旧 `review/` 与 `downloads/airdrop-driver-work` 及所有兼容链接已删除。

源码实体已移动，引用改用正式路径。独立构建流程见[构建指南](guides/building.md)和[BYOB 输入清单](guides/byob.md)。本轮实际编译验证另见[验证记录](guides/build-verification.md)。暂未统一守护进程、实现互传 Hook、整理有序驱动补丁集或改变安装产物选择。安装器仍使用当前实验驱动路径；不要将其视为重装手机现有版本。

下一步可继续收敛测试依赖和明确共享后端设计。没有用户进一步指示时，不新增锁定清单、不自动部署。用户现已授权公开仓库、提交和推送。

无线实现、实测数据、恢复限制与最后设备回执见[详细交接](airdrop/28-three-findings-handover.md)。设备状态属于历史回执，后续上机必须重新检查。[当前状态](STATUS.md)与[构建说明](guides/building.md)为常用入口。

## 隐私清理

已完成[隐私清理](PRIVACY.md)。原始日志和本地构建产物已移除，旧原始证据/哈希完整性描述不再适用于匿名化副本。既有构建成功记录是清理前的验证结果。再次实测需自行设置设备参数；不得恢复个人标识到可发布文件中。

## 构建平台解耦

正式入口为 `scripts/build-driver.sh`（Linux x86_64）、`scripts/build-userspace.sh` 与 `scripts/build-app.sh`（macOS/Linux）。入口不使用固定 SSH 主机或个人路径；旧主机配方移至 `archive/legacy-build/`。历史实验若依赖旧布局，必须显式设置 `HOUJI_LEGACY_ROOT`，不属于正式构建依赖。本轮通过 Python/shell 语法检查、生成器检查与已知个人标识扫描（含 Git 对象）；没有重新编译或上机。

## 首次公开发布审查

用户已授权创建公开 GitHub 仓库并提交推送。仓库保留组件原有许可，根目录说明许可边界；未新建版本锁或 SHA 清单。首次发布只包含源码、文档及匿名化汇总，不包含固件、APK、内核模块、签名密钥或原始设备日志。
