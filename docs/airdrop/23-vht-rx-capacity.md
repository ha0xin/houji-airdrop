# 149 信道 VHT 接收容量审计

远端 peer 活流先冻结。仅 create/map 的对照保留了 self AST，115 次真实 enqueue 仍没有 TQM 完成，而同 worker 和暂停重开的 self-only 对照完成了 31 次。源码中，WMA 使用同一 PASSTHRU vdev 发送 `WMI_PEER_TYPE_DEFAULT` 和远端 MAC，宿主对象类型为 `WLAN_PEER_STA`，未在该入口更改 vdev 或安全模式。HTT map 同时保留远端自己的槽和 `bss_peer` 标志，旧 PASSTHRU 分支还会覆盖 vdev TX AST 缓存。保护缓存后，raw TX 仍用原 vdev 引用和 `HTT_INVALID_PEER`，没有指定远端 peer。现有回执不能区分固件建 peer 的副作用、HTT map 副作用或它们与 PASSTHRU 的交互。 [实机对照与恢复](22-passthru-rx-reo-probe.md#拆分暂停与-peer-setup) · [远端对象差分](../../experiments/remote-peer-probe/remote-peer-wma-stage1.patch) · [raw TX 入口](../../third_party/upstream/kernel/wlan-overlay/qca-wifi-host-cmn/dp/wifi3.0/dp_tx.c#L3210)

## 单缓冲尺寸

开始审计前搜索了公开 Qualcomm HTT ring buffer 大小、Kiwi RX DMA 和 Owly 能力广告。未找到同一 Kiwi 固件在 PASSTHRU 中超过 4096 字节单缓冲的公开实测。公开 ath12k 的普通 RX buffer 是 2048 字节，8192 字节定义对应另一条 TX monitor 路径。ath11k 有根据 continuation 重建帧的实现，亦区分首尾 TLV。当前 PASSTHRU 的未知 peer 路径可能到 REO no-peer 或 invalid-peer，二者均会拒绝分链。因此先验证单缓冲，比同时改链组装、所有权和广告更容易归因。 [Linux ath12k 尺寸](https://kernel.googlesource.com/pub/scm/linux/kernel/git/netdev/net-next.git/+/9f69d05b5a85c417c73fa2d5c7a2d507ac81cf4b/drivers/net/wireless/ath/ath12k/dp.h) · [Linux ath11k RX](https://github.com/torvalds/linux/blob/v6.1/drivers/net/wireless/ath/ath11k/dp_rx.c) · [Owly 协议](https://github.com/t4t5/owly/blob/main/docs/SPEC.md)

- **配置与 HTT。** 4096 是 `dp_rx_buffer_size` 的 INI schema 上限。BE 以同一配置值传入 `htt_h2t_rx_ring_cfg`，其 RX ring selection 消息中的 buffer size 是 16-bit 字节字段。字段能表示 4224，固件实际接受情况未知。 [配置和 RXDMA 源码](../../experiments/remote-peer-probe/vht-rx-next/kiwi-rx-capacity-source.txt) · [稳定源码 HTT SET 与掩码](../../experiments/remote-peer-probe/vht-rx-next/kiwi-rx-dma-allocation-source.txt)
- **对齐与分配。** `dp_rx.h` 直接包含定义 `RXDMA_OPTIMIZATION` 的 `hal_rx.h`，普通数据池用 128 字节对齐和 0 reserve。稳定构建的普通 QDF allocator 调用 `alloc_skb(size + align - 1)`，再调整数据指针。当前配置没有 `FEATURE_SKB_PRE_ALLOC`，模块和 allocator 对象均未导入 `wcnss_skb_prealloc_get`，因此该 fallback 返回 NULL。DP 自有 refill/emergency cache 是另一条路径，用同一 pool 规格分配与复用。 [allocator 与缓存](../../experiments/remote-peer-probe/vht-rx-next/kiwi-rx-allocator-recycle-source.txt) · [对齐与 fallback 核验](../../experiments/remote-peer-probe/vht-rx-next/kiwi-rx-fallback-alignment-source.txt)
- **DMA 与边界。** 普通 RX pool 记录同一配置值，DMA map/unmap 使用 pool 字节长度，硬件写入范围不能按 allocator 额外 tailroom 扩大。BE 和 REO no-peer 检查 `TLV + pad + packet_len` 对实际 skb 容量及配置上限。当前 Kiwi TLV 预算 272，pad 是两位、最大 3。最保守的 VHT Max MPDU 为 3895，其算术预算为 4170 字节，保守按 128 字节取整为 **4224 字节**。现有 DMA direction 差异和 monitor 独立缓冲留在原路径。 [DMA 源码](../../experiments/remote-peer-probe/vht-rx-next/kiwi-rx-dma-allocation-source.txt) · [实际 map 入口](../../experiments/remote-peer-probe/vht-rx-next/kiwi-rx-fallback-alignment-source.txt)

## 候选方案

1. 从实测稳定 wideband-ring 的对应源码派生默认关闭的装载参数，只为可恢复测试在 DP 建池前选固定 4224。保持原 INI schema 和上层广告。先在 149 确认普通 RX pool 分配、映射与实际 HTT 下发取到相同长度，并记录已交付 raw 帧的有效容量与链拒绝，随后跑现有双向 SHA 基线。首轮只验收容量改动与原数据路径，不能据此宣称 3895 字节空口帧已通过。
2. 在同一已通过的容量构建上，再独立切换出站 VHT capability。先限 VHT80、1SS MCS 0–7、最保守的 Max MPDU 3895，不宣告额外宽度、SGI 或未经验证的特性。继续关闭 BA，旧 `vht_amsdu` 保持 false。旧补丁读取 Mac 广告并改变小米发送组包上限，与本轮出站广告相互独立。 [旧 VHT A-MSDU 对照](20-vht-amsdu-device.md)
3. 对比广告 off/on/off，保持信道、发送参数与载荷固定。接收频宽与 MCS 必须来自有明确有效标志的硬件观测。若证据只证明数据继续可达，则记录哈希、吞吐和接收拒绝计数，并保留实际 RX PHY 未知。

## 容量烟测与持续基线

同一容量构建在保留 HT 广告时通过 1 MiB 双向烟测，并通过 64 MiB 双向各 3 次 SHA-256。两轮的配置、pool 与两个 HTT host submit 都是 4224。DMA 映射 4224 时观测到 7680 和 7808 字节的线性空间，表明分配舍入有额外内存成本。当前没有每个 skb 的 truesize 或全池内存实测，不能把该空间算作 DMA 容量。唯一 raw OSIF 采样是 130 字节 QoS Data，TLV 272、pad 0，证明原数据路径可用，较大空口 MPDU 尚无样本。两轮普通恢复成功，Wi-Fi 和原生待命指纹一致。 [烟测 JSON](../../experiments/remote-peer-probe/vht-rx-next/device-rx-capacity1/result.json) · [持续基线 JSON](../../experiments/remote-peer-probe/vht-rx-next/device-rx-capacity64/result.json) · [模块 ABI](../../experiments/remote-peer-probe/vht-rx-next/audit-rx-capacity.json)

| 方向 | 三个 64 MiB 样本 MB/s | 加权 MB/s | 峰值 MB/s | 失败 |
| --- | --- | ---: | ---: | ---: |
| Mac→小米 | 2.065 / 2.476 / 2.202 | 2.235 | 2.476 | 0 |
| 小米→Mac | 6.291 / 6.029 / 6.089 | 6.134 | 6.291 | 0 |

## 独立出站能力广告

开始实现前查阅了 Wireshark 的 AWDL TLV 17 解析、Linux 6.1 的 VHT capabilities/MCS 编码和 Owly 协议。历史自有 Mac 抓包有 24 条完整能力元素，都在 MIF subtype 3 的 HT TLV 后，容器仅有 IE 191。这只描述现有样本。候选只在 MIF 的 HT 后加同结构，保持 PSF 字节不变。 [Wireshark AWDL](https://github.com/wireshark/wireshark/blob/master/epan/dissectors/packet-awdl.c#L1010) · [Linux VHT MCS](https://github.com/torvalds/linux/blob/v6.1/include/linux/ieee80211.h#L1643-L1704) · [Linux VHT 能力位](https://github.com/torvalds/linux/blob/v6.1/include/linux/ieee80211.h#L1957-L1996) · [历史抓包脱敏统计](../../experiments/remote-peer-probe/vht-rx-next/outgoing-advert/wire-reference.json)

- TLV 17 的 value 14 字节，IE 191 的 body 12 字节。
- 32 位 capability 值为 0。Max MPDU 字段是 3895 档，宽度扩展位清零表示基准 VHT80 范围，160、80+80、SGI、LDPC、STBC、beamforming、TXOP PS 等可选位保持关闭。A-MPDU 指数保持最低编码，独立的 BA 响应路径仍关闭。
- RX/TX MCS map 均为 `0xfffc`，NSS1 的两位 0 对应 MCS 0–7，NSS2–8 两位 3 表示不支持。两个 highest rate 字段用 0 表示未指定。
- off/on 用同一 Android 二进制。源码默认 off，on 需要读回试验容量开关。不会引入旧发送侧大帧补丁。先做小载荷发现和回退核验，再决定是否进入 64 MiB 对照。没有独立空口记录时，本机序列化证据与 Mac 是否采用该能力必须分开。 [Owly 差分](../../experiments/remote-peer-probe/vht-rx-next/outgoing-advert/owly-outgoing-vht.patch) · [Android 差分](../../experiments/remote-peer-probe/vht-rx-next/outgoing-advert/runner-outgoing-vht.patch)

同一远端构建的用户态应用先做 1 MiB off/on/off。三个会话均通过双向 SHA、同 boot 容量校验和普通恢复。第一条成功写入 AF_PACKET 的 MIF 容器依次为缺席、`bf0c00000000fcff0000fcff0000`、缺席。这验证主机提交的序列化字节。手机会话重启和末轮缺席不能直接证明 Mac 已丢弃上一轮缓存的 peer capability。 [构建测试](../../experiments/remote-peer-probe/vht-rx-next/outgoing-advert/build-outgoing-vht-final.log) · [短测摘要](../../experiments/remote-peer-probe/vht-rx-next/outgoing-advert/small-summary.json)

## 64 MiB 广告开关对照

四轮使用同一用户态 SHA `ab84ddba…`、容量模块 SHA `bbb6d59c…` 和 149 信道。各轮双向交替，各方向 3 次 64 MiB。共 24 次传输通过两端 SHA-256，四轮均普通恢复，Wi-Fi、原生待命和 stock 指纹前后一致。加权速度按每方向总字节除以总秒数。 [样本、调度与恢复摘要](../../experiments/remote-peer-probe/vht-rx-next/outgoing-advert/long-summary.json)

| MIF 广告 | Mac→小米加权 / 峰值 MB/s | 小米→Mac 加权 / 峰值 MB/s | 整轮验收 |
| --- | ---: | ---: | --- |
| off1 | 1.761 / 2.236 | 4.318 / 6.051 | 通过 |
| on1 | 2.947 / 2.976 | 5.762 / 6.206 | SHA 和恢复通过，原容量日志覆盖未通过 |
| off2 | 2.316 / 2.469 | 5.114 / 5.751 | 通过 |
| on2 | 2.894 / 2.909 | 5.943 / 6.504 | 通过 |

on1 的 dmesg 环在运行中覆盖了本轮起点以前的旧记录。原守卫要求旧匹配行全部保留，原结果继续标为未通过。该轮未落盘完整前后快照，无法补验当时的重叠区间。后两轮完整保存前后匹配快照，守卫核对同 boot、时间单调和重叠区间内旧行的多重集，仅以起点后的新行验收容量。 [原始 on1](../../experiments/remote-peer-probe/vht-rx-next/outgoing-advert/device-64m-on1/result.json) · [on2 完整验收](../../experiments/remote-peer-probe/vht-rx-next/outgoing-advert/device-64m-on2/result.json) · [守卫差分](../../experiments/remote-peer-probe/vht-rx-next/outgoing-advert/rx-capacity-dmesg-coverage.patch)

off1 的样本 5 从 11 个 149 槽的 Data 快照转为 7 个槽，样本 6 持续记录 7 个槽。这两个样本也分别是该轮各方向最慢值，广告和调度的贡献无法拆分。on1、off2、on2 的 Data 快照均显示 11 个 149 槽。两次开启轮的 Mac→小米加权速度约 2.9 MB/s，分别比 off2 高约 27% 和 25%。快照稀疏，且同一 peer 地址的 Mac 能力缓存过期时间未知。当前没有有效的接收 PHY 或 BA 字段，不能据此确认 Mac 使用了哪种 PPDU，也不能确认 3895 字节 MPDU 通过。小米→Mac 的历史最高三次 64 MiB 加权成绩仍为 6.470 MB/s。 [既有最高发送结果](14-owly-houji-device.md)

同一 VHT IE 增加到 PSF 的独立候选，先按公开 iOS 26.x 的 subtype 覆盖证据验证 wire，Mac 侧是否因此采用不同 PHY 仍待观测。 [PSF 候选、公开检索和验证方法](../../experiments/remote-peer-probe/vht-rx-next/outgoing-psf/README.zh-CN.md)

## 同一能力字节加到 PSF

PSF 候选在 build-host 完成 68 项 Rust 测试和 Android release。实际 Node 产帧测试确认模式 2 的 PSF 只增加 17 字节，模式 1 与模式 2 的 MIF 整帧相同。实机同应用 SHA `93bb62c5…` 做 MIF → PSF+MIF → MIF。1 MiB 三轮均通过完整验收。两类日志来自成功的本机 AF_PACKET 提交，MIF 三轮均精确匹配，PSF 依次缺席、精确匹配、缺席。 [构建与测试](../../experiments/remote-peer-probe/vht-rx-next/outgoing-psf/build-outgoing-psf.log) · [短测摘要](../../experiments/remote-peer-probe/vht-rx-next/outgoing-psf/small-summary.json)

| 广告范围 | Mac→小米加权 / 峰值 MB/s | 小米→Mac 加权 / 峰值 MB/s | 整轮验收 |
| --- | ---: | ---: | --- |
| MIF 1 | 2.810 / 2.904 | 6.342 / 6.421 | 通过 |
| PSF+MIF | 2.830 / 2.977 | 6.256 / 6.350 | 通过 |
| MIF 2 | 2.441 / 2.904 | 4.883 / 6.435 | SHA 和恢复通过，末次容量采集失败 |

三轮各方向 3 次 64 MiB，共 18 次通过两端 SHA，stock、Wi-Fi 和原生待命均恢复。末轮 dmesg 采集命令退出 2，没有保存原始后快照，原整轮标记保留失败。之后同 boot 的有界只读检查在相邻两条非容量记录发现 1 μs 倒序。该现象与全局单调守卫拒绝输出相符，事后检查不能替代原后快照。 [长测摘要及原结果 SHA](../../experiments/remote-peer-probe/vht-rx-next/outgoing-psf/long-summary.json) · [脱敏时钟检查](../../experiments/remote-peer-probe/vht-rx-next/outgoing-psf/device-64m-mif2/post-run-dmesg-clock-audit.txt)

首轮 MIF 与前一应用的 on2 接收约 2.894 MB/s 相近。PSF+MIF 相对首轮 MIF 的接收加权差约 0.7%，前三个同窗 RX/TX 对照没有显示稳定提速。首轮 MIF 和 PSF+MIF 都只在第一个 RX 样本看见部分 7 槽的 Data 快照，随后都是 11 槽。末轮 MIF 在第 4 样本从 11 转向 7 槽，第 5、6 样本仅见 7 槽且速度降至 1.885、3.560 MB/s。快照只反映 Mac 记录的调度，不能量化实际空口占比。保留 MIF-only 基线，PSF 扩展未达到接收 6 MB/s 目标。

已从独立 Kiwi RX 观察器核对 Mac→小米的正常接收帧。MIF 广告在同一会话关闭和恢复时，目标帧均报告 VHT80、MCS 7、1SS，不能把剩余速度差归给 HT 回退。分方向时序显示 Mac→小米 16 MiB 的 5144 个目标 QoS 帧中，4888 个带 A-MSDU 位；其传输前段有 TCP 恢复，后段发送栈仍约 3.6 MB/s。 [真实 RX PHY、时序和当前限制](../../experiments/remote-peer-probe/vht-rx-next/rx-phy-next/README.zh-CN.md)
