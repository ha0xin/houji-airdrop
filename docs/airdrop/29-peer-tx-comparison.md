# 同模块 self-peer / remote-peer 发送对照（2026-11-11）

目的：检验发送低速是否与远端 peer 路径有关，避免将历史不同版本成绩直接比较。未改驱动、固件、用户态二进制或 Mac 网络设置；继续使用第 28 篇审计通过的模块 `7c647a0bb3c570f1bd2a0527c92298476ab26d96d90cc3de6a837a0c2125e10c`。固定 vht80-2ss-mcs9-sgi、remote_reo=0、fw_addba=0、tx_ampdu=0、dynamic_rate=0、sta_coexist=0，仅切换 remote_assoc。每次双向 16 MiB，SHA-256 校验。

先执行 self→remote→remote→self 四轮，再执行 self→remote 两轮补采。补采使用项目已有 Darwin TCP_CONNECTION_INFO 包装器，只读测试 socket；按方向前后读取手机 pt_rate_stats。200 ms TCP 快照不是最终连接计数，最后一帧可能早于传输结束。

## 数据

| 组别 | Mac→小米 MB/s | 小米→Mac MB/s | TX完成 | 尝试计数 | status=2 | 聚合观测 |
|---|---:|---:|---:|---:|---:|---:|
| peer-abba-20261111-self1 | 14.754 | 3.195 | 5520 | 6767 | 238 | 0 |
| peer-abba-20261111-remote1 | 4.347 | 0.571 | 6629 | 10322 | 1079 | 0 |
| peer-abba-20261111-remote2 | 4.128 | 2.215 | 5825 | 6769 | 376 | 0 |
| peer-abba-20261111-self2 | 9.232 | 8.402 | 5271 | 5769 | 156 | 0 |
| peer-tcp-20261111-self | 14.260 | 8.913 | 5329 | 5668 | 148 | 0 |
| peer-tcp-20261111-remote | 7.279 | 2.232 | 5572 | 6336 | 316 | 0 |

全部 12 次单向传输 SHA-256 校验通过。上述 TX 计数覆盖双向测试阶段，包含接收方向的 ACK 等流量，不等同于单独小米发送文件阶段。

补采小米→Mac 阶段的独立发送计数及 Mac 接收快照：
- peer-tcp-20261111-self：TX完成 5001，transmit_cnt 总和 5188，status=2 共 58；MCS直方图 [0, 0, 0, 0, 0, 0, 0, 0, 0, 4943, 0, 0, 0, 0, 0, 0]。Mac 10 个 TCP 快照，最后接收字节 15810256，累计乱序字节 3846948，最大采样平滑 RTT 7 ms。
- peer-tcp-20261111-remote：TX完成 5366，transmit_cnt 总和 6114，status=2 共 221；MCS直方图 [0, 0, 0, 1, 0, 0, 0, 0, 0, 5144, 0, 0, 0, 0, 0, 0]。Mac 38 个 TCP 快照，最后接收字节 16392292，累计乱序字节 2871148，最大采样平滑 RTT 7 ms。

## 结论与证据边界

补采结果：self-peer 8.913 MB/s 对比 remote-peer 2.232 MB/s，后者约为前者的 25%。独立小米发送阶段，status=2 比例分别为 58/5001=1.16% 与 221/5366=4.12%；尝试计数/完成数分别约 1.037 与 1.139。两组采样平滑 RTT 最大值均 7 ms，remote 的累计乱序字节没有高于 self。只能说明额外发送代价与降速同时出现，不能据此推导 4 倍差距的完整机制。

- self-peer 也未观察到多 MPDU 聚合，但能超过 8 MB/s，因此缺少 A-MPDU 不是约 2.3 MB/s 的硬上限，不能单凭聚合为零解释全部降速。
- 本轮 remote-peer 发送样本均低于 self-peer 样本，支持远端 peer 路径存在额外代价；但 self-peer 本身变化明显，无线环境和时间因素未完全受控，不能据此定位单一字段。
- 有效速率完成记录主要是 MCS9，不能用“配置写着 MCS9”直接保证全部失败包也采用同样速率。现有观测没有 NSS 字段。
- `status=2` 在目标 HAL 头文件中是 `HAL_TX_TQM_RR_REM_CMD_TX`（Remove_transmitted_mpdus），并不直接编码“重试超限”。`tries` 是 transmit_cnt 累加，不能直接当作精确丢包率。
- 聚合统计通过相邻完成记录的 ppdu_id 分组计算，是驱动侧观测，不是独立空口抓包；Mac 全局 HighBand 统计没有 peer 隔离，不作为小米聚合的单独证明。
- Mac 收到小米数据时，其 snd_cwnd、snd_sbbytes 和 TX 重传属于 Mac 发送侧，不等于小米拥塞窗口或发送队列。本轮没有硬件队列深度和逐包完成延迟，不能断言队列不足或串行等待就是根因。
- 未启动监控模式空口抓包，因为需要改变无线接口状态，与保持 Mac 网络设置不变的约束冲突。本轮采用按测试 socket 隔离的只读 TCP 统计。

下一层定位应对远端 peer 的 descriptor/AST 选择、发送调度与完成路径做隔离变量实验；在得出原因前，不把这份对照当作已经修复性能问题。

所有六轮 stop 回执均恢复成功，最终审计为 `STOCK_RESTORED WIFI_CONNECTED NO_EXPERIMENT_INTERFACE_OR_RULES`，无本轮后台测试残留。原始 JSON 位于 `three-findings-validation/runs/peer-abba-20261111-*` 与 `peer-tcp-20261111-*`，汇总为 `peer-comparison-20261111-summary.json`。

补采脚本：`three-findings-validation/peer-diagnostics/run-peer-tcp.py`。所需只读采样库通过 `cc -dynamiclib -O2 -Wall -Wextra -Werror downloads/airdrop-driver-work/iphone-radio-research/mac-tcp-info.c -o downloads/airdrop-driver-work/three-findings-validation/peer-diagnostics/mac-tcp-info.dylib` 构建；运行前应使用新的输出目录名以保留历史回执。
