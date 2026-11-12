# 普通 Wi-Fi 与 Owly 共存验证（2026-11-11）

**最新结果：补充 EZIF 标记包接收分支后，STA 2.4 GHz + Owly 5 GHz 的双向收发基本验证通过。** 两轮双向各 16 MiB 全部 SHA 校验成功，网关 Ping 42/42，正常释放无需重启。详见文末；前面的失败记录为历史过程。独立 App 尚未接入共存选项。

目标是普通 Wi-Fi STA 联网时，Owly 仍通过 PASSTHRU 与 Apple 设备收发文件。此目录独立保存参考源码、移植工具、构建记录和候选模块；不覆盖手机上已安装 App 的稳定运行时。

## 当前范围

三星公开源码的移动平台策略表对 PASSTHRU 采用跨频段并发：STA 在 2.4 GHz 时 PASSTHRU 可用 5 GHz，STA 在 5 GHz 时 PASSTHRU 可用 2.4 GHz。即使 DBS/SBS 表也没有为 PASSTHRU 开放两个 5 GHz 信道。这与芯片支持一般 STA/P2P 的同频并发不是同一承诺。

当前 Owly 固定 149，因此先验证 **STA 2.4 GHz + Owly 5 GHz**。不支持的组合应在创建 PASSTHRU vdev 前明确返回错误。本轮最多允许一个 STA 加一个 PASSTHRU，不开放三连接、运行中换信道或漫游。用户已切换到 2.4 GHz；后续实测触发接收异常恢复，详见末节，尚未验证跨频段收发成功。

## 代码

- 按三星源码补充 PASSTHRU 策略枚举、连接计数、模式映射和 PCL/硬件模式表项；保留小米既有策略。
- 在创建 vdev 前检查并发准入，在提交信道请求时再次检查并切换硬件模式。
- 固件启动确认后检查实际 TX 空间流数量，登记活动 PASSTHRU 会话。
- 暂停 STA 漫游、关闭 STA 省电；清理时注销会话并恢复用户态请求的省电设置和漫游请求位。
- 区分接收异常环中已知普通 peer 与未匹配 peer。非 PASSTHRU 包不改变描述符所有权，仍交给原接收路径；共存监控保留这些计数，遇到 PASSTHRU 描述符或投递失败仍停止。
- 独占模式保持默认；共存仍需显式 `--sta-coexist 1`。

源文件位于相邻 `three-findings-validation/src/wlan/`。`port-policy.py` 从本目录保留的小米参考表中生成候选表，只导入三星移动平台 PASSTHRU 项，排除 MDM/AUTO 分支。`verify-policy.py` 独立比对原有表项和三星新增表项，防止重复初始化及错误分支混入。

## 已有证据及失败

- 历史 DZF2：STA 5180 MHz、Owly 5745 MHz，网关 Ping 172 发 0 收，Owly 无接收；参见 `docs/airdrop/28-three-findings-device-validation.md`。
- `coexist-ezif-baseline-20261111`、`coexist-ezif-counter-20261111`：在连接启动期间被原接收观察器的 `peer_miss` 条件提前终止，均恢复原厂。它们不能证明 EZIF 的物理无线并发失败。
- `coexist-policy-5g-reject-20261111`：策略正确拒绝 5745 MHz，但拒绝发生在 vdev 创建后，触发原运行时永久错误锁定，broker 返回 `-EBUSY`，不能正常卸载。保存日志后进行了一次受控重启，原厂驱动、固件和普通 Wi-Fi 已恢复。没有强制卸载或清除引用计数。
- 修正：把同一准入检查前移到创建 vdev 之前，让正常策略拒绝保持运行时 IDLE，不触发固件错误锁定。后置检查仍保留，以防状态在两次检查之间变化。

上述运行目录位于相邻 `three-findings-validation/runs/`。本目录 `evidence/restored-after-reboot.txt` 保存重启后的原厂恢复审计。

## 最终候选与本轮验收

候选模块 SHA-256：`849ddabb3be6b79a6fbad1def0dc2380f237fd6a4e884120d34a3383eba7edc6`，保存在 `artifacts/849ddabb3be6/`。此前 `308a174e...` 是拒绝检查位置过晚的失败版本，不应部署。

- `evidence/build-admission.log`：最终版本远端构建与 ABI 审计通过，567 个导入符号及 CRC 匹配；79 项用户态测试通过。这些测试不是驱动并发行为的证明。
- `evidence/policy-table-verification.json`：移动平台相关表中，3,502 个已有叶子表项保持原值，535 个新增叶子表项与三星源码一致。第三方平台的替代分支没有混入；第四/第五连接表未移植，本实现拒绝第三个连接。
- `coexist-policy-early-reject-v2-20261111`：实机日志确认 `PT coexist admission rejected before vdev creation freq 5745`；peer 未创建，释放后普通 Wi-Fi 仍连接，最终 `STOCK_RESTORED`。测试程序因预期拒绝返回非零，但恢复回执为 `restored=true`，**没有再次重启**。
- `coexist-policy-exclusive-regression-20261111`：新模块在原独占模式下，Mac→小米、小米→Mac 各 1 MiB 的 SHA-256 均通过；RX 适配路径 `reaped=231 delivered=231 peer_miss=0 bad_desc=0 dropped=0`，正常卸载并恢复原厂。此轮仅验证功能，不用 1 MiB 样本评价吞吐或 A-MPDU 协商。
- `coexist-policy-early-reject-20261111` 是重启后遗留测试文件导致的 staging 拒绝，没有启动无线测试。之后按旧 session 的哈希逐项核对并清理这些临时文件，再运行了上述 v2。

跨频段测试使用以下参数。当前已发现接收异常，解决末节问题前不要原样重复上机：

```sh
python3 downloads/airdrop-driver-work/three-findings-validation/run-ezif-rx-adapter.py \
  --run-name coexist-policy-24g-validation-NEW \
  --sta-coexist 1 --remote-reo 2 --tx-addba win64 --mib 16 --rx-window 64
```

必须先检查 `cmd wifi status` 的 Frequency 为 2.4 GHz；相同 SSID 不代表相同频段。确认跨频段成功后，才把共存选项接入独立 App。

## 尚需验证

2.4 GHz STA 下的硬件 MAC 分配、普通 Wi-Fi 网关丢包/联网情况、Owly 双向文件 SHA、A-MPDU 实际使用、释放后的普通 Wi-Fi 连通性。还未验收锁屏长期运行、STA 断线重连、运行中漫游或带宽退让。跨频段通过前，不替换 App 的稳定独占驱动。

## 2.4 GHz 实测更新（17:03 启动）

用户已切换；实测前确认 TEST_WIFI 为 2442 MHz、信道 7。运行目录：`../three-findings-validation/runs/coexist-policy-24g-validation-20261111/`。

- 固件成功配置 PASSTHRU：`vdev=3 mac=1 freq=5745 tx_streams=2`，并发策略会话登记完成。
- 远端 peer 23 的创建、HTT map、REO 设置与关联确认均成功；STA 在启动回执中仍显示连接。
- 文件传输未开始，`samples=[]`、`verified=false`。用户态报 `No such device` 是 WLAN 恢复期间接口消失的结果，不能据此认定为单纯的用户态接口选择错误。
- RX 适配统计为 `seen=22 peer_miss=22 other_peer=0 reaped=0 delivered=0 bad_desc=0 dropped=0`。内核明确记录 `reo_err_msdu_buf_rcved 21` 达到异常阈值，随后 `cds_trigger_recovery_handler` 以 reason 25 启动 WLAN 恢复。
- 因运行时进入恢复冻结状态，broker release 返回 `-EAGAIN`；保存日志后进行了本轮一次受控重启并核验原厂恢复。没有强制卸载。此轮没有有效网关 Ping 汇总、吞吐、SHA 或空口聚合验收结果。

修正此前判断：未匹配 peer 的包虽然没有被 PASSTHRU 钩子接管所有权，但进入原异常路径后仍会累积恢复阈值，不能当作无害旁路流量。测试观察器已恢复对 `peer_miss` 的严格停止条件；该用户态保护不能保证在高流量下抢先于内核阈值，因此不再用同一候选反复上机。

下一步需要先区分 **EZIF 下普通 STA 单独收包**和 **加入 PASSTHRU 后收包**，核对 REO 异常类型、原始 peer metadata、PN 检查条件及 vdev/MAC 映射。现有日志不足以确定这些未知包来自普通 STA 还是 PASSTHRU；不能直接放宽 peer 匹配、吞掉异常包或提高恢复阈值。

结论：跨频段启动和 peer 关联已有实证，**跨频段数据传输未通过，候选尚不能作为共存功能交付**。独立 App 的稳定独占运行时未替换。

## 接收契约诊断（本轮继续）

`coexist-rx-diag-startup-20261111` 在首个未知 peer 后停止并干净恢复；QDF WARN 被过滤，未得到原始字段。`coexist-rx-diag-v2-20261111` 改用最多 8 条 printk，记录 6 个 metadata=`0x40000000`、peer=0、sw_pn=0 的 MSDU-buffer 描述符，保护停止后正常释放；两轮均无需重启，释放后 STA 仍连接。原始证据见 `evidence/rx-marked-evidence.txt`。完整 dmesg 含非 UTF-8 字节，已修复采集器保存原始字节并以替换字符生成文本。

与 EZIF 汇编交叉核对：`dp_rx_err_process` 的 `0x3d95d0..0x3d95f4` 在已解析 MPDU 结构第 11 字节检查 bit 6，即 peer metadata bit 30；该条件优先于 peer lookup。现场包符合这条分支，排除了此次由软件 PN 分支留下零 metadata 的假说。不能将低位解出的 peer=0 当作真实关联身份。

正在验证的补丁增加默认关闭、只读的 `pt_rx_ezif_marked`：只在元数据有效且 bit 30 置位时，通过验证的描述符 pool 查 pdev，在锁内寻找唯一 PASSTHRU vdev 并取得引用。仍保持 DMA 恰好解除一次、描述符回收、完整 RAW 帧与长度检查。无标志的未知 peer 不做泛化映射，不修改恢复阈值。测试工具只有显式 `--rx-ezif-marked` 且固件为已校验 EZIF 时才启用。这是二进制验证出的版本契约，不是公开源码中的具名协议定义。

## 标记包补丁实测结果

候选 SHA-256：`6336dfe17e69cf3e07b95621005737c6d2e1c9144301a077315756858559b0c9`；模块、补丁、审计与验证脚本保存在 `artifacts/6336dfe17e69/`。567 符号/CRC 等 ABI 检查通过，新开关只读且默认关闭；79 项用户态测试通过。没有替换 App 稳定运行时。

- `coexist-marked-smoke-20261111`：双向各 1 MiB SHA 全通过，151 个 MSDU-buffer 全部交付，错误为 0。STA 网关 Ping 16/16、平均 6.058 ms。正常删除 peer、卸载、恢复原厂，无重启。
- `coexist-marked-16m-20261111`：同一无线会话内两轮双向各 16 MiB，四次 SHA 均为 `341aacac661ccb210720bedaa9ead5d668fe5ea41a73532fc147c71e34040df1`。STA 始终为 TEST_WIFI 2442 MHz，PASSTHRU 为 5745 MHz、硬件 MAC 1、2SS。

| 轮次 | Mac→小米 | 小米→Mac | STA 网关 Ping | 平均延迟 |
|---|---:|---:|---:|---:|
| 1 | 10.22 MB/s | 37.89 MB/s | 16/16 | 5.358 ms |
| 2 | 4.49 MB/s | 45.42 MB/s | 26/26 | 4.208 ms |

单位为十进制 MB/s，来自测试流的传输计时，不代表完整 AirDrop UI 耗时或长期稳定速率。Mac→小米存在轮次波动，目前未作进一步性能归因。

最终 RX 统计 `seen=350 reaped=350 delivered=350 peer_miss=0 bad_desc=0 dropped=0 other_peer=0`。正常 peer delete/HTT unmap、`teardown_rc=0`，释放后 STA 仍连接；最终审计 `STOCK_RESTORED WIFI_CONNECTED NO_EXPERIMENT_INTERFACE_OR_RULES`，没有重启。额外公网 ICMP 5/5 成功，但该探测未独立保存无线会话边界，不用它代替共存期间联网验收。两轮原始数据、Ping、接收日志、恢复回执均在运行目录；汇总 `evidence/marked-16m-summary.json`。

聚合配置启用且聚合计数增长，但现有 rate observer 混合统计 STA 与 PASSTHRU 完成事件，不能把共存时的全部 agg_ppdus 归于 Owly。后续若需严格验收共存下的 A-MPDU，需要按 peer/MAC 区分统计或空口抓包。本轮证明的是跨频段基本收发与网关连通；尚未覆盖锁屏长时运行、漫游、重连、普通 Wi-Fi 大流量并发、App/iPhone 共存体验。

## 用户要求记录的速度差异与后续方向

用户要求先记录，暂停进一步性能测试。原始日志复核：16 MiB 小米→Mac 两轮为 0.442823/0.369391 秒，手机侧为 0.442869/0.369257 秒；Mac→小米为 1.642071/3.735096 秒，手机侧为 1.633804/3.722688 秒。两端计时相符，不是已发现的单端计时错误，但短样本不可视为持续速度。

第二轮 RX gap 统计有 3 个超过 256 ms 的无线接收空档，合计 3.122301 秒，主机侧对应档合计 3.115502 秒；接收 PHY 记录为 VHT80/2SS/MCS9，未观察到 socket 丢包。这提示应调查发送或无线调度等待，但统计覆盖整轮，未逐包对齐文件传输，不能断言全部空档就是文件传输瓶颈。固定先 Mac→小米、后小米→Mac 也有预热顺序偏差。待办：交换顺序、预热、较长单向样本、对齐 TCP/AWDL 时间线；目前不执行。

5 GHz 共存线索：用户提供一加 15 PLK110 的 Owly Android 帖子，实验选项加载 `owly_coex.ko` 保持 WLAN。已通过酷安只读搜索定位作者 happybear、帖子 https://www.coolapk.com/feed/74878377 。接口仅返回截断正文，评论获取失败；本轮公开搜索未找到该模块实现仓库。用户提供的完整正文未说明 STA/AWDL 频率、同信道/异信道条件或吞吐，不能把它视为双 5 GHz 已验证证据，也不能据此认定模块具体使用了哪种 hook。

本项目当前仅验证 STA 2.4 GHz + PASSTHRU 5 GHz；移植策略拒绝 STA 5 GHz + PASSTHRU 5 GHz 是宿主策略事实，不构成硬件绝对不支持的证明。后续可按同信道（例如均为 149）、不同信道的独立 MAC 或分时调度分别调查；须验证固件对 PASSTHRU 的支持，不能仅解除策略拒绝就宣告成功。未修改准入策略、未切换手机频段、未部署该第三方模块。

## 双 5 GHz 受控准入实验（准备中）

用户授权尝试，并要求验证调度和实际效果。参考 APK 的返回值覆盖思路，在 `policy_mgr_allow_concurrency` 外增加默认关闭、只读的 `pt_coex_5g_override`，原策略先运行；仅在原策略拒绝、请求为固定 5745 MHz PASSTHRU、且只有一个 STA（5180–5240 或 5745–5825 MHz）时放行。不为普通模式或第三个连接放行，不修改 RX、聚合及恢复阈值。实际调度仍由原硬件模式选择路径和固件决定。

构建候选 `dc6b21e494bda8df9f920e4b2d74f74aa077ae1c6fba41c13e1dd361151e8ec2` 已通过 ABI/默认值/只读参数审计，存于 `artifacts/dc6b21e494bd/`；日志 `evidence/build-5g-override.log`。测试脚本需显式 `--coex-5g-override --sta-coexist 1 --rx-ezif-marked`，会检查启动前后 STA 频率。尚未部署此候选时扫描到 TEST_WIFI 5200 MHz，但手机未连接，已请用户完成连接；不将扫描结果当作实际关联。


## 双 5 GHz 实测（2026-11-11，信道 40 + 149）

候选 `dc6b21e494bd…` 的受控放行已完成实测；普通 STA 连接 TEST_WIFI 5200 MHz（信道 40），Owly 使用 5745 MHz（149）。测试前关闭双 WLAN 后，原厂基线 Ping 20/20、平均 16.563 ms；最初一次试验因驱动重载后自动连回 2442 MHz，被频率守卫终止，没有当作双 5 GHz 成功。用户随后关闭 2.4 GHz 自动连接。

`coexist-5g40-smoke-v2-20261111`：双向各 1 MiB 哈希通过，Mac→小米 0.424 MB/s，小米→Mac 8.810 MB/s；网关 21/21，平均 27.947 ms。样本太短，不作为持续速率。

`coexist-5g40-16m-20261111`：同一无线会话内两轮双向各 16 MiB，四次 SHA-256 全通过，均为 `341aacac661ccb210720bedaa9ead5d668fe5ea41a73532fc147c71e34040df1`。两轮前后 STA 均连接同一 AP、频率 5200 MHz。

| 轮次 | Mac→小米 MB/s | 小米→Mac MB/s | 网关 Ping | RTT 平均 / 最大 ms |
|---|---:|---:|---:|---:|
| 1 | 2.339 | 16.037 | 52/52 | 26.557 / 68.540 |
| 2 | 4.169 | 16.594 | 36/36 | 30.179 / 71.720 |

最终 RX 统计 `seen=397 reaped=397 delivered=397 peer_miss=0 bad_desc=0 dropped=0 other_peer=0`。传输顺序为每轮先 Mac→小米，再小米→Mac；不是双向同时满载。普通 WLAN 仅以网关 Ping 检查，没有同时进行普通 WLAN 大流量下载。当前 5 GHz 样本比先前 2.4 GHz 共存样本慢，但测试时间、信号及链路条件不同，不构成受控因果对比。

准入日志 STA `sta_mac=0`，固件启动应答 PASSTHRU `vdev=2 mac=1 freq=5745 tx_streams=2`。这是不同 MAC 映射的证据；尚不足以证明两路 RF 始终独立同时收发，也不足以排除固件分时调度。不能将延迟或吞吐差异直接归因于同一个 MAC 跳信道。聚合 observer 混有普通 STA 事件，仍不能把所有聚合计数归于 Owly。

退出时 peer delete/HTT unmap 完成、`teardown_rc=0`，原厂模块/固件校验通过，但 WLAN 自动重连超时。1 MiB 轮经 WLAN 关闭再开启和显式 recover 后通过完整 STOCK_RESTORED 审计，已按 token/hash 清理暂存运行文件。16 MiB 轮恢复处理结果见后续补记。这个缺陷发生在驱动切换退出阶段，不能宣传为无缝共存；App 稳定运行时未替换。

原始目录：`../three-findings-validation/runs/coexist-5g40-smoke-v2-20261111/`、`../three-findings-validation/runs/coexist-5g40-16m-20261111/`；汇总 `evidence/5g40-16m-summary.json`。当前结论为信道 40 + 149 短时双向数据和网关连通验证通过，尚未覆盖其他信道、DFS、漫游、锁屏、长时、大流量 WLAN 并发或 iPhone/App 共存。

恢复补记：16 MiB 轮自动恢复仍超时，等待自动任务结束后关闭 WLAN 5 秒再开启，成功重连 TEST_WIFI 5200 MHz；显式 recover 随后完成。`stock-after.txt` 确认原厂模块/固件、原后台服务和无实验接口/路由；按 token 与五项 SHA 校验清理了暂存程序。无需重启，恢复后网关 Ping 10/10、平均 6.599 ms。恢复回执为 `recovery-receipt.json`，自动失败日志保留，未将人工辅助恢复伪装成自动恢复通过。实验现已停止。
