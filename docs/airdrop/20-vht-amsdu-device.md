# 小米 14 到 Mac 的 VHT A-MSDU 上限对照

原 Owly 只用对端 HT TLV 决定软件 A-MSDU 上限。历史 149 抓包中，当前 Mac 的 24 个 AWDL Action 都带完整的 802.11 容器（TLV 17）及 VHT Capabilities IE 191。HT 值 `0x006f` 表示 A-MSDU 上限 3839 字节，VHT `0x03800036` 的低两位为 2，表示 MPDU 上限 11454 字节。抓包的 snaplen 截断发生在完整能力元素之后。历史抓包只作设计参考。 [脱敏抓包摘要](../../experiments/owly-evaluation/houji-runner/results/vhtcap-reference.json) · [Wireshark 容器解析](https://github.com/wireshark/wireshark/blob/master/epan/dissectors/packet-awdl.c#L1010)

Linux iwlwifi 在 VHT 连接下优先使用 VHT MPDU 尺寸。Owly PR #62 同时调整了 HT/VHT 宣告、频宽、速率及接收 BA，MT7921 在 44 信道接收 iPad 达 9–14 MB/s。这是其他平台和方向的多变量结果。 [Linux 6.1 iwlwifi](https://github.com/torvalds/linux/blob/v6.1/drivers/net/wireless/intel/iwlwifi/mvm/rs-fw.c#L349-L386) · [Owly PR #62](https://github.com/t4t5/owly/pull/62)

## 实现和试验条件

补丁在现有 1 ms coalesce 之上默认关闭。开启时只用当前目标 Mac 的完整广告，过期、无效、重复或截断均回退 HT。VHT 限额扣除 24 字节三地址头、2 字节 QoS 及 4 字节 FCS，适用于当前未使用 802.11 加密或四地址的 AWDL 数据帧。每个子帧的头、LLC/AWDL 封装与对齐也计入长度。本地整帧仍限制为 7935 字节，不含 FCS，monitor MTU 为 8192。 [Owly 差分](../../experiments/owly-evaluation/houji-runner/owly-vht-amsdu.patch) · [Android 差分](../../experiments/owly-evaluation/houji-runner/runner-vht-amsdu.patch)

同一个 `build-host` 构建的 Android 二进制按 off/on/off 冷启动，每轮双向各传 16 MiB。驱动、149/VHT80 SGI、正常 ACK、MSS 1160、qlen 500 和关闭 RX BA 均保持一致。三轮的运行日志都重新看到当前 Mac 的 11454 宣告。TQM 有效完成全部报告 VHT、MCS7 和 SGI。开启轮有 2 项 40 MHz，其余 11,422 项为 80 MHz。关闭轮的有效完成都是 80 MHz。 脚本在软件 monitor 未报告 149 时调用 `iw … set channel 149 HT20`，Wonder 的 mac80211 `.config` 只记录变化。下层由独立的 vendor 缓存传入 `bw=2 gi=1 nss=1 mcs=7`，再提交 VHT preamble 与 channel width。`iw` 显示的 20 MHz 属于软件 monitor。 [monitor 设置](../../experiments/owly-evaluation/houji-runner/radio-owly.sh#L279) · [Wonder 配置回调](../../driver/wonder/mac80211.c#L824) · [WMI 速率配置](../../experiments/wideband/control.patch#L98) [构建日志](../../experiments/owly-evaluation/houji-runner/results/vht-amsdu-build.log) · [逐轮数据](../../experiments/owly-evaluation/houji-runner/results/vhtcap-summary.json)

## 结果

| 轮次 | Mac→小米 MB/s | 小米→Mac MB/s | A-MSDU 平均子帧 | 最大 raw 帧 | 超出原 HT 载荷的帧 |
| --- | ---: | ---: | ---: | ---: | ---: |
| off1 | 2.106 | 7.932 | 2.967 | 3780 B | 0 |
| on1 | 1.987 | 0.473 | 4.660 | 7620 B | 2767 |
| off2 | 2.091 | 7.940 | 2.989 | 3780 B | 0 |

6 次传输全部通过两端 SHA-256，三轮退出码均为 0，库存无线状态均恢复。开启后的大包进入了 AF_PACKET 提交路径，Air fatal 为 0。TQM 按整个会话汇总，缺少帧长与确认结果的配对。当前无法区分发射、重试或 Mac 解聚合造成的退化，`REM_CMD_TX` 也不能直接当作空口失败。 [汇总 JSON](../../experiments/owly-evaluation/houji-runner/results/vhtcap-summary.json) · [最终恢复复查](../../experiments/owly-evaluation/houji-runner/results/vhtcap-final-stock.txt)

候选保留关闭默认值。全量放宽显著降低小米→Mac 的速度，关闭后复测回到此前 16 MiB 区间。本轮到此停止。7.94 MB/s 是短测，既有 64 MiB 持续速度纪录仍为小米→Mac 6.470 MB/s。 [此前持续测试](14-owly-houji-device.md#吞吐结果)

## 计数能定位到哪一层

| 轮次 | 已提交 A-MSDU | 其中 HT 载荷上限内 | 超 HT 载荷上限 | Air 背压退还 | 区间 TQM 次数/完成 | owly0 TX 丢弃 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| off1 | 4941 | 4941 | 0 | 19,426 | 1.106 | 85 |
| on1 | 3209 | 442 | 2767 | 7,625 | 1.122 | 0 |
| off2 | 4898 | 4898 | 0 | 16,628 | 1.096 | 0 |

超过 3839 的判断针对 A-MSDU 载荷，最大 raw 帧包含 26 字节 MAC/QoS 头。当前只有两个长度桶和最大值，无法定位 4–7.6 kB 中的具体转折点。Air 背压是套接字暂未接受、退回后可重试的次数。开启轮背压更少，`owly0` TX 丢弃为零。关闭轮即使有 85 项 TX 丢弃也达到 7.932 MB/s。TQM 尝试/完成的区间平均变化较小，且不与帧长或 TCP 方向关联。以上结果不足以解释单次发射是否收到 ACK、MAC 是否解聚合，或判断 TCP 重传量。

若以后补证据，先在已有 TX 描述符回收前按原始 MPDU 长度分桶，同时统计提交、完成状态与尝试数。前提是核对读取的长度属于同一个已持有描述符，且包含哪些头部。AF_PACKET 记录只能验证提交侧，Mac 侧 TCP 序号或第三台设备独立被动空口采样才能检验实际交付。现有结果不继续扫长度阈值。 [TQM 现有代码](../../experiments/wideband/dp_pt_rate_observer.h#L15)
