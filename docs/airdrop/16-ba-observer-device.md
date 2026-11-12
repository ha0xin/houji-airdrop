# 小米 14 的 PASSTHRU RX 聚合观测

2026-11-10。小米 14 与 Mac 只在 149 信道通信。Owly 使用 VHT80 SGI、软件 A-MSDU、正常 ACK、测试 TCP MSS 1160 和 TUN qlen 500。四轮依次以 off、on、on、off 冷启动同一个 WLAN 模块，每轮两个方向各发送 64 MiB 生成数据。Owly 的 RX BA 始终关闭。每轮读回参数和计数，结束后恢复普通 Wi-Fi 与原生 149/MCS3 待命服务。 [实机配置](14-owly-houji-device.md) · [切换脚本差分](../../experiments/ba-observer/device-harness.patch)

## 结果

| 轮次 | 观察器读回 | Mac→小米 MB/s | 小米→Mac MB/s | 通过 SHA-256 |
| --- | --- | ---: | ---: | ---: |
| off1 | N | 2.167 | 4.275 | 2/2 |
| on1 | Y | 2.201 | 6.419 | 2/2 |
| on2 | Y | 1.992 | 5.567 | 2/2 |
| off2 | N | 2.159 | 5.677 | 2/2 |
| off 加权 | | 2.163 | 4.877 | 4/4 |
| on 加权 | | 2.092 | 5.963 | 4/4 |

加权值为同方向总字节数除以实测总秒数。两种模式间没有观察到一致的性能下降。每种模式只有两轮，而且小米→Mac 的 off1 和 off2 自身相差约 1.4 MB/s，无法从本次结果分离观察器开销。 [逐样本 JSON 与计数审计](../../experiments/ba-observer/device-summary.json)

| 观察轮次 | 启动快照 raw | 结束快照 raw | 区间非 A-MPDU | 区间 A-MPDU | 区间未知 | 区间 ADDBA Request 头 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| on1 | 37 | 62,971 | 62,934 | 0 | 0 | 45 |
| on2 | 11 | 62,839 | 62,828 | 0 | 0 | 46 |

每份快照均满足 `raw = nonampdu + ampdu + unknown`，各 TID 的 A-MPDU 计数与未知 TID 之和等于 `ampdu`。off 两轮所有计数为零。on 两轮在 raw RX 路径均没有看到 ADDBA Response 或 DELBA 头。计数覆盖整个模块的 PASSTHRU raw RX，合计两个方向的传输阶段以及期间经过该路径的其他帧。Request 只匹配 Action 头，无法确认发送方、目标 TID 或握手结果。当前没有测到 A-MPDU，也没有测出硬件 BA、重排或空口控制帧能力。 [观察器范围](../../experiments/ba-observer/README.zh-CN.md)

实机装载的模块 SHA-256 为 `b9f0a77ec9132e57677f83e75e4e3185087b8c4a104878db532a894cb0c4637b`。模块的 vermagic release 为 6.1.68，与此前已测 ring 参照相同。实际运行内核为 6.1.138，四轮均沿用该设备已验收的普通 `insmod` 路径，静态审计的导入和导出 CRC 与参照一致。每轮都通过恢复检查，最终复查包含库存 WLAN build ID、固件和原生文件哈希，Wi-Fi 已连接、Binder 与 supervisor 已恢复，并且实验接口、规则和恢复状态文件已清除。 [交付模块 ABI](../../experiments/ba-observer/audit-stripped.json) · [最后一次恢复复查](../../experiments/ba-observer/device-final-stock.txt)

## 为什么优先补齐 RX peer 与 REO 观测

- MT7612U 在 149 信道以 HT40 接收 iPhone。作者使用硬件 BA 后，30 MB 从 47 秒降到 14 秒，后续在软件路径处理了较多乱序。该实验支持分别验证硬件确认、A-MPDU 和交付顺序。 [Owly PR #58](https://github.com/t4t5/owly/pull/58) · [Owly PR #59](https://github.com/t4t5/owly/pull/59)
- MT7921 接收 iPad 时已接受 Block Ack，并宣告 2SS/VHT80。作者在固定 44 信道记录 71.3 MB 的 11.0 和 14.4 MB/s，跟随跳频为 11.4 和 14.2 MB/s。网卡、固件和对端均不同，且没有独立改变 BA 的对照。结果说明固定信道值得继续验证。 [Owly issue #38](https://github.com/t4t5/owly/issues/38)
- Owly 维护者在 AX201 上单独将选主 metric 提到 550，没有复现吞吐收益。本机 TUN qlen 从 500 扩到 2048 后消除了已有掉包，也没有提升小米→Mac 的平均速度。两组结果支持先找清 Mac→小米为什么一直以非聚合 MPDU 进入 raw RX。 [Owly PR #81](https://github.com/t4t5/owly/pull/81) · [本机 qlen 对照](14-owly-houji-device.md)

下一步只记录既有路径中的 peer 与 REO 事件。先给 raw ADDBA 的地址和 TID 增加单对端归属，再查 HTT peer-map/unmap、RX_ADDBA，以及可归属到同一 peer 生命周期的 REO 状态。这样能区分 Mac 是否提出协商、固件是否为 PASSTHRU 提供了远端 peer ID、现有 RX 路径是否尝试配置重排。具体观测点与证伪条件另见方案。 [下一步方案](17-rx-peer-reo-observer-plan.md)
