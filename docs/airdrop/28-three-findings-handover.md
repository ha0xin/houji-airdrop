> 2026-11-12 目录整理后的接手入口见[当前交接](../HANDOVER.md)。下文保留无线验证背景；旧目录兼容链接已删除，历史路径仅作记录。

# houji-airdrop 当前交接（2026-11-11）

本文件是接手入口，替代此前三项发现阶段的执行计划。旧文档保存在[历史交接](28-three-findings-handover-historical.md)，其中“禁止看汇编”“TX 聚合未通过”“完成后提交”等要求或结论已经过时，不得直接沿用。

## 1. 当前任务与用户要求

- 独立仓库：`<repo>`。已复制整理相关文件并初始化 Git，供用户审阅。2026-11-12 检查发现 README 已有暂存内容；保留用户索引，本轮不暂存、不提交。
- 原工作区 `<original-workspace>` 保留。新仓库是独立文件副本，后续编辑以新仓库为准，不能假设两边自动同步。
- 当前请求是分层整理仓库；暂不增加版本锁定和 SHA，不启动新一轮上机测试或部署。
- 用户已明确允许查三星/小米源码和反汇编；旧禁止汇编指令已被后续授权替代。
- 用户暂不优化双向同时传输；先前通用单向调参也因流媒体干扰而暂停。后续另行授权了 WLAN 共存实验，该实验已完成短时验证。
- 当前已实现产品为独立 Owly App，不走 XOS/系统分享框架；统一守护进程和小米互传 Hook 是后续目标，尚未实现。不要将实验驱动自动升级到已安装 App。

## 2. 最后确认的设备状态

最后一轮 `coexist-5g40-16m-20261111` 已结束，经 WLAN 关闭/开启与显式 recover 完成恢复：

- `STOCK_RESTORED WIFI_CONNECTED NO_EXPERIMENT_INTERFACE_OR_RULES`。
- 普通 WLAN 重新连接 TEST_WIFI，5200 MHz；恢复后网关 Ping 10/10，平均 6.599 ms。
- 原厂驱动、固件哈希及原后台服务检查通过，没有重启。没有遗留本轮实验任务。
- 用户关闭了双 WLAN 加速，并关闭 2.4 GHz TEST_WIFI 自动连接，以免驱动切换后选回 2.4 GHz。不要擅自恢复这些设置。

这是测试结束时的回执，不是未来设备状态保证。再次操作前重新核对 ADB、无线状态、活动会话及租约；文档整理期间没有访问手机。

| 项目 | 身份 |
|---|---|
| 手机 | 小米 14 / houji，ADB `DEVICE_SERIAL`，HyperOS `OS3.0.307.0.WNCCNXM` / Android 16 |
| 运行内核 | `6.1.138-android14-11-g0c3d559bcd85-ab14529422` |
| 原厂模块文件 SHA-256 | `d3fbe4f1b62f8c3533609945b617508e8a585660f5640aeeda08f8508416137b` |
| 原厂已加载模块 build-id note SHA-256 | `a3e9bd62646444e1ecbe0f421c95ec46e6caf69a78323efb2f6ebe00e6b2bd8b` |
| 原厂 amss20.bin SHA-256 | `4db7b0e1aa7e4ec071708359c9d0db8d3d7872390cd8e88ffa0bbb30d560bcc8` |
| Mac（文档更新时补采） | Apple silicon / Apple-computer，macOS 27.2 / 26B123；测试使用原生 awdl0 |

模块文件哈希与 sysfs build-id note 哈希不是同一对象，不可混用。Mac 的 AWDL 地址会变化，不沿用旧交接中的固定地址。

## 3. 已完成与证据

### Peer、TX 聚合及 RX 适配

远端 peer 的创建、HTT map、REO setup、关联确认、删除与 unmap 已完成实测闭环。官方 EZIF 固件与宿主适配后，硬件 completion 分组确认多 MPDU/PPDU，64 MiB×3 双向 SHA 全通过，小米→Mac 加权 20.978 MB/s，Mac→小米 14.636 MB/s；详见[聚合报告](32-ezif-tx-ampdu-validation.md)。

必须保留的解释边界：

- `338` 为相关版本的主机枚举下标，不能当作固件 wire ID；DZF2 原始 wire506=0，EZIF=1。
- wire506 已确认关联 RX REO 路径；不能把整套固件差异和提速独占归因到这个位。
- `remote_reo=2` 同时改变路由和初始接收窗口，不是隔离变量的性能证明。
- `pt_tx_ampdu=0` 在 EZIF 下仍见聚合，不是有效的纯聚合关闭对照。
- 共存 RX 中 metadata `0x40000000` 的低 peer ID 为 0，不能当成普通未知 peer：EZIF 二进制存在优先检查 bit30 的路径。新增 `pt_rx_ezif_marked` 在有效元数据及安全描述符检查后寻找唯一 PASSTHRU vdev，保持单次 DMA unmap、回收与引用管理；未标记未知 peer 不泛化放行。

### 独立 App

包名 `org.houji.owly`，源码在 `downloads/airdrop-driver-work/owly-app/`。已完成协议烟测，用户报告 iPhone 成功发送。接收需 App 确认，保存到私有目录后手动导出。App 当前仍暂停普通 WLAN；没有系统分享入口，也没有共存开关。

安装记录中的驱动 SHA：`6914d32ece387172706e740cf63284daf9e9cd72b2f6f91dc2a3f678fc5dc6bb`。**安装脚本读取当前实验构建路径，不固定到此版本，直接重跑可能替换为实验候选。** 先将 App 发布清单和实验产物分离，再考虑更新安装。

### WLAN 共存

| STA / Owly 频率 | 轮次 | 小米→Mac MB/s | Mac→小米 MB/s | 网关 Ping |
|---|---:|---:|---:|---|
| 2442 / 5745 MHz | 1 | 37.887 | 10.217 | 16/16，平均 5.358 ms |
| 2442 / 5745 MHz | 2 | 45.419 | 4.492 | 26/26，平均 4.208 ms |
| 5200 / 5745 MHz | 1 | 16.037 | 2.339 | 52/52，平均 26.557 ms |
| 5200 / 5745 MHz | 2 | 16.594 | 4.169 | 36/36，平均 30.179 ms |

各轮依次双向各 16 MiB，全部 SHA-256 通过。不是双向同时满载，也未同时压满普通 WLAN。两组信号和时段不同，不作频段因果比较。

- 跨频段候选 SHA：`6336dfe17e69cf3e07b95621005737c6d2e1c9144301a077315756858559b0c9`。
- 双 5 GHz 候选 SHA：`dc6b21e494bda8df9f920e4b2d74f74aa077ae1c6fba41c13e1dd361151e8ec2`，567 符号/CRC 及默认值、只读参数审计通过；构建期间用户态 79 项测试通过。
- 双 5 GHz 仅对一个 STA（5180–5240 或 5745–5825 MHz）和固定 5745 MHz PASSTHRU，且原策略拒绝时，以默认关闭、只读的 `pt_coex_5g_override` 受控放行。
- STA 准入记录 MAC 0，PASSTHRU 固件应答 MAC 1 / 2SS。这不是两路 RF 始终独立工作的证明，也不能据此认定只能单 MAC 跳信道。
- 双 5 GHz 最终 RX `seen=reaped=delivered=397`，miss/bad/drop 为零。聚合 observer 混入 STA 事件，尚不能对 Owly 聚合单独归因。

证据路径（均在 `downloads/airdrop-driver-work/`）：

- `three-findings-validation/runs/coexist-marked-16m-20261111/`
- `three-findings-validation/runs/coexist-5g40-smoke-v2-20261111/`
- `three-findings-validation/runs/coexist-5g40-16m-20261111/`
- `sta-coexist/README.zh-CN.md`、`sta-coexist/evidence/5g40-16m-summary.json`

首次 `coexist-5g40-smoke-20261111` 自动连回 2442 MHz，被频率守卫终止，不能当成双 5 GHz 成功样本。

## 4. 当前主要缺陷：退出恢复的自动重连

双 5 GHz 两轮实验均完成 peer delete / HTT unmap、`teardown_rc=0`，原厂模块/固件校验通过，但 WLAN 自动重连等待超时。16 MiB 轮最终需等待自动任务结束、关闭 WLAN 5 秒再开启、显式 recover，才完成审计。

- `lifecycle-end.log` 保留自动恢复失败；`manual-recovery.log`、`stock-after.txt`、`recovery-receipt.json` 记录辅助恢复成功。
- 不将该结果描述为“自动恢复通过”或“无缝共存”。加载/卸载实验驱动本身会中断 STA。
- Python `root()` 默认 20 秒超时，小于 harness 的 30 秒 Wi-Fi 等待，可能造成外层提前退出；不能仅延长超时就认定底层自动重连问题已经修复。
- 先前普通 Wi-Fi 频繁断开时曾见主、副管理器争用 wlan0，用户关闭双 WLAN 后稳定；它与当前退出重连问题是否同因尚未证实。

后续若继续共存实装，应先复现和修复有界恢复流程，再验证 App 集成、iPhone、锁屏、长时运行、漫游、其他信道及普通 WLAN 大流量。不要以吞掉错误、强卸载或无限重试替代恢复验收。

## 5. 构建与运行入口

本地工作目录为独立仓库根。`src/wlan` 是修改覆盖层，完整构建工作树在 `build-host:/work/houji-build/src/three-findings/wlan`，用户态在相邻 `userspace`。工具链与基线输出仍是远端依赖，见[构建边界](../guides/building.md)。

- 构建：`downloads/airdrop-driver-work/three-findings-validation/build-driver.sh`。
- RX/共存编排：同目录 `run-ezif-rx-adapter.py`，依赖 `run-three-findings.py`、`remote-peer-probe`、`owly-evaluation`，不要只移动单个脚本。
- App：`owly-app/build-native.sh`、`build-app.sh`、`runtime/build-radio.py`；SDK 默认临时路径需配置，原签名私钥未复制。
- 官方 EZIF 固件 SHA：`37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f`，来源在 `tx-ampdu-investigation/firmware-acquisition/`。
- DZF2 SHA：`13f01cb46efbe498c854352bfc253fa3665c14a14c37a0a6ed6a609a48a8709c`；原 `downloads/oneui9-houji-analysis/reference-runtime/` 不在此次复制范围。

以下是已测参数记录，不是本次文档任务的执行步骤：

```sh
python3 downloads/airdrop-driver-work/three-findings-validation/run-ezif-rx-adapter.py \
  --run-name UNIQUE-NEW-RUN --sta-coexist 1 --remote-reo 2 \
  --tx-addba win64 --rx-window 64 --rx-ezif-marked \
  --coex-5g-override --mib 16 --rounds 2
```

运行前必须确认新目录名、产物哈希/ABI、实际 STA 频率及无其他会话。守卫检查启动前后频率，发现切回 2.4 GHz 就终止。手机上 harness 位于 `/data/local/tmp/houji-buildtest-20261110/owly/`，已清理的临时脚本不能假设仍存在。

## 6. 继续工作时的约束

- 先核对真实运行状态；不靠旧日志推断设备仍然空闲或仍加载相同模块。
- 正常 stop/recover，保留锁和引用检查；禁止强制 rmmod、清引用或删除活动 lease。
- 清理暂存文件前核对本轮 token 和实际 harness/module/app 哈希，不能拿原脚本哈希清理动态生成的 harness。
- 只操作本轮实验，不调整 Mac 网络设置，不覆盖已安装 App 的稳定运行时。
- 记录加载参数、模块/固件身份、双端 SHA、方向计时、STA 频率/Ping、RX 统计和最终恢复回执；失败结果也保留。
- 导入清单是整理时的历史快照。后续正常修改源码/文档不应倒改原始来源哈希；独立 Git 的首次提交继续等待用户审阅。
