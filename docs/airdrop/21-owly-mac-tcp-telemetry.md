# Owly 149 信道的 Mac TCP 连接遥测

在原 ring、VHT80 SGI、正常 ACK、软件 A-MSDU、MSS 1160 和 qlen 500 下做两轮。VHT 大帧开关与 RX BA 关闭。同一 Mac 客户端对原 BSD TCP fd 读取 `TCP_CONNECTION_INFO`，采样在计时开始前和结束后。原 payload、SHA 收据、绑定接口和 TCP 探针均沿用。四次 64 MiB 的两端 SHA-256 均通过，两轮均退出 0 并恢复。 [第一轮](../../experiments/owly-evaluation/houji-runner/results/tcp-info-best1-64m/results.json) · [第二轮](../../experiments/owly-evaluation/houji-runner/results/tcp-info-best2-64m/results.json) · [恢复检查](../../experiments/owly-evaluation/houji-runner/results/tcp-info-best2-64m/restored.txt)

| 轮次 | 方向 | MB/s | Mac MSS | 结束 SRTT | Mac 发送重传 | Mac 接收乱序 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | Mac→小米 | 2.036 | 1148 B | 28 ms | 349 计数，400380 B | 0 B |
| 2 | Mac→小米 | 2.156 | 1148 B | 27 ms | 369 计数，415576 B | 0 B |
| 1 | 小米→Mac | 6.539 | 1148 B | 12 ms | 0 B | 7038388 B |
| 2 | 小米→Mac | 6.426 | 1148 B | 19 ms | 0 B | 6491940 B |

Mac→小米的 TCP 重传字节分别约为应用负载的 0.60% 和 0.62%，无法定位短时停顿是接收窗口、拥塞恢复还是 AWDL 可用窗口。小米→Mac 的乱序字节约为应用负载的 10.49% 和 9.67%，是接收内核的字节计数。它不代表丢失比例。Mac 的发送重传计数无法说明小米发送侧的重传。内核 TCP 包计数也不能换算为空口 MPDU 或 BA 聚合密度。

helper ABI 为 `awdl_tcp_info_sample/10`。实测 dylib SHA-256 `a3cd67d04ac1d9e5134e86167d753bc84bd341af63d9fb6fba0a4d3afe4c0652`。C 源码版本 SHA-256 `2b2d16ba83dcd41d6d295bb5963620b66d24fb817b9fd5d61670a2fe8766ec41`。macOS 27.0 SDK 的 `netinet/tcp.h` 说明 RTT 和 SRTT 单位为毫秒，XNU 将相关连接累计值导出为包或字节计数。MSS 和 RTT 保留采样原值，累计计数只计算前后差值。采样 errno 和计数倒退单独记录。 [Apple XNU TCP 头](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp.h) · [XNU 导出实现](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp_usrreq.c) · [本机 IPv6 回环验证](../../experiments/owly-evaluation/houji-runner/results/tcp-info-loopback.json)

## 每 200 ms 采样及紧邻对照

在同一配置做时间轴开启、关闭、再开启三轮。关闭轮仍在计时外记录 v1 前后两点。每轮两端各传 64 MiB，全部通过 SHA-256 并恢复。时间轴的零点与传输计时相同，计入启动采样线程的短开销，停止并 join 的耗时位于计时之外。原样本保留同 fd 的本地拥塞窗口、对端宣告窗口、发送缓冲字节和恢复标志。 [机器汇总](../../experiments/owly-evaluation/houji-runner/results/tcp-timeline-summary.json) · [回环验收](../../experiments/owly-evaluation/houji-runner/results/tcp-timeline-loopback.json)

| 轮次 | Mac→小米 MB/s | 小米→Mac MB/s |
| --- | ---: | ---: |
| 时间轴 on1 | 1.295 | 3.758 |
| 时间轴 off1 | 2.136 | 6.309 |
| 时间轴 on2 | 2.133 | 6.450 |

首轮低速在第二次开启时未复现。第一轮 Mac→小米 260 个样本中有 37 个标为恢复中，SRTT 峰值 1000 ms。对端宣告窗口中位 6,292,992 B，拥塞窗口中位 84,378 B。发送字节累计值最长约 397 ms 不增，位置为 1.805–2.202 s。此段的 cwnd 为 2,276,484 B，窗口 5,094,400→5,987,840 B，发送缓冲仍有 3,393,000 B，恢复标志为 0。发送缓冲含排队和飞行字节，不能从这四个快照字段计算可立即发送的字节数。TCP 发送累计值也不代表对端已接收。200 ms 的样本无法排除更短的窗口变化。现有 runner 日志只证明 peer 保持可用，没有记录对端信道序列，无法将该间隔归因于 AWDL 时隙。 [XNU 字段来源](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp_usrreq.c) · [公开的窗口与时隙反例](https://github.com/jedbillyb/airdrop-mt7921/blob/main/docs/FINDINGS.md#18-2026-07-31-the-receive-window-was-a-real-pathology-but-not-the-limit)

全部轮次保留原配置，未用此时间轴重定义持续吞吐纪录。 [on1 原始样本](../../experiments/owly-evaluation/houji-runner/results/tcp-timeline-best1-64m/results.json) · [off1 原始样本](../../experiments/owly-evaluation/houji-runner/results/tcp-timeline-off1-64m/results.json) · [on2 原始样本](../../experiments/owly-evaluation/houji-runner/results/tcp-timeline-best2-64m/results.json)
