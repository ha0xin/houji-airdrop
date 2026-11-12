# 三项发现续测报告（2026-11-11）

本次按交接清单进行源码修改、远程构建和实机验证，没有运行反汇编工具、读取反汇编文本或修改 Mac 网络设置。原始数据在 `downloads/airdrop-driver-work/three-findings-validation/runs/resume-20261111-*`，各目录分别保存 session、启动回执、逐样本结果与恢复回执。

## 测试边界

- 手机：小米 14 houji，OS3.0.307.0.WNCCNXM；实验固件为项目已有 DZF2 amss20.bin。三星 EZIF 模块仅作为既有报告中的参考，未部署。
- 本轮模块 SHA-256：`7c647a0bb3c570f1bd2a0527c92298476ab26d96d90cc3de6a837a0c2125e10c`。远程 build-host 构建，去除调试信息；567 个导入、CRC、vermagic、PLT/GOT、参数权限与默认关闭状态审计通过，用户态原有测试通过。
- 固定速率测试配置为 vht80-2ss-mcs9-sgi、remote_assoc=1，用户态接收 ADDBA=amsdu、发送 ADDBA=off。fw-addba 组通过内核在关联确认后提交 WMI 命令；提交成功不等于空口协商成功。
- MB/s 按十进制字节计算，载荷大小使用 MiB。16 MiB 行每方向一个样本，不构成统计显著性证明；64 MiB 验收每方向三个样本。
- 每轮测试结束均要求恢复原厂状态。实验驱动的装载/卸载会切换普通 Wi-Fi，不能宣称整个部署和恢复过程零闪断。

## 已验证结论

1. DZF2 运行时 `wire_506_valid=1, ext2_len_words=8, ext2_w7=0x00000000, wire_506_passthru_ampdu=0`。这是原始位图读数，不是旧主机枚举 338 的查询结果。只证明本固件本轮未声明该服务，不证明所有 PASSTHRU 聚合方式均不可能。
2. 固定速率下远端 peer 关联、双向传输及删除闭环可工作。REO 模式 2 的组合修改没有超过关闭 REO 的模式 0；无法据此认定窗口大小或哈希配置是唯一根因。
3. 固件 ADDBA 固定速率组 `sent=Y, rc=0`，但有效流量阶段聚合 PPDU 仍为 0，最大 MPDU/PPDU 为 1。动态速率组在建立传输时双向 `No route to host`，没有可用吞吐，不能据其零计数推断聚合行为。
4. 并发首轮只等待关联，尚无 DHCP 地址及网关，因此该轮不足以验证联网并发。脚本补齐网络就绪等待后复测：启动时关联及 IPv4 正常，PASSTHRU 运行期间 Ping 172 发、0 收，100% 丢包；AWDL 无接收帧、等待就绪超时。故本配置下并发验证失败。Ping 在超时恢复前手动向该轮 ping 进程发送 SIGINT，保存约 36 秒统计，无 RTT 样本。
5. Mac LQM 原始日志已保留，但含普通 Wi-Fi 流量，未做 peer 级隔离，不能把其中的 rxAmpdu/txBACK 全部归给实验流量。

## 实测数据

| 本轮配置 | 每方向载荷 | Mac→小米 MB/s | 小米→Mac MB/s | TX完成 | 聚合PPDU | 结果 |
|---|---:|---:|---:|---:|---:|---|
| reo0 | 16 MiB × 1 | 15.920 | 2.214 | 5587 | 0 | SHA通过 |
| reo1 | 16 MiB × 1 | 7.957 | 2.289 | 5507 | 0 | SHA通过 |
| reo2 | 16 MiB × 1 | 9.481 | 2.294 | 5498 | 0 | SHA通过 |
| fwaddba-fixed | 16 MiB × 1 | 8.373 | 2.301 | 5506 | 0 | SHA通过 |
| fwaddba-dynamic | 16 MiB × 1 | — | — | 0 | 0 | advertisement or paired SHA verification failed |
| coexist-b | 16 MiB × 1 | — | — | — | — | device state deadline |
| coexist-dhcp | 16 MiB × 1 | — | — | — | — | device state deadline |
| acceptance64 | 64 MiB × 3 | 22.365 | 2.316 | 64797 | 0 | SHA通过 |

64 MiB 逐次实测（各行 SHA-256 均通过）：

| 方向 | 次序 | 秒 | MB/s |
|---|---:|---:|---:|
| Mac→小米 | 1 | 3.768260 | 17.809 |
| 小米→Mac | 2 | 28.872597 | 2.324 |
| Mac→小米 | 3 | 2.588059 | 25.930 |
| 小米→Mac | 4 | 29.270904 | 2.293 |
| Mac→小米 | 5 | 2.645614 | 25.366 |
| 小米→Mac | 6 | 28.775295 | 2.332 |

64 MiB 载荷 SHA-256：`281e519df3077b557c6b03f5da83c4e8d397219259615dd7c3308f89cae8f2a6`。

历史 self-peer 64 MiB×3 基线：接收 14.705 MB/s；同一 2SS 配置发送约 5.897 MB/s，另一个 1SS 配置发送最佳 6.590 MB/s。本轮远端 peer 接收 22.365 MB/s、发送 2.316 MB/s；接收更高而发送退化。历史测试时间、peer 与发送配置有差异，不能称为严格同条件 A/B，也不能用 6.590 作为同一 2SS 配置的基线。

## 对历史结论的修正

最终恢复审计：`STOCK_RESTORED WIFI_CONNECTED NO_EXPERIMENT_INTERFACE_OR_RULES`。普通 Wi-Fi 网关 Ping 10 发、10 收，0% 丢包，RTT min/avg/max/mdev 为 3.708/9.514/15.068/3.637 ms。无需重启手机；本轮编译和测试任务均已结束。独立对照目录保持只读。

验证工具：Python 语法、harness 补丁组装、shell 语法及 SHA 校验通过；源码 diff 空白检查通过。生成的 `.patch` 文件被外层 Git 空白检查标记的空行/空格加 Tab 来自 unified diff 上下文前缀，未为消除告警破坏补丁。构建日志共有 77 项测试通过（38+5+24+5+5）。

第 27 篇顶部已增加勘误。交接文档中的“源码一眼看清 TX 开关”“已找到两处精确接收根因”“并发官方源码实锤”均不能代替小米实测。本轮不将负结果包装为功能完成：发现一通过，发现二取得能力位与聚合的实测边界，发现三本配置失败，后续需要单独诊断并发调度和动态速率发送路径。
