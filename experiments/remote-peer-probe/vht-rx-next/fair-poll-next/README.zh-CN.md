# Owly 用户态公平轮询对照

开始前检索了 Owly 的 AWDL 窗口、ACK 处理、Mac 基础设施冲突案例和 Tokio 的轮询语义。Owly 规定 3 TU guard，连续同信道槽免去内部边缘保护。Mac 本轮 11 个 149 槽的序列有连续 2 个和 3 个离开 149 的槽，按 64 TU 一槽约 131 和 197 ms。阶段 5 的 11 个 128–256 ms 间隔与约 6.8 秒内应出现的两组离信道窗口数量相当。以所有 ≥128 ms 间隔都可消除的乐观假设计算，也只把 16 MiB 的 2.474 MB/s 提到约 4.21 MB/s，不能把目标差距全归给 guard。 [Owly AWDL 窗口](https://github.com/t4t5/owly/blob/main/docs/SPEC.md#3-link-layer-awdl) · [其它硬件的基础设施冲突案例](https://github.com/netmojo-systems/omdrop-awdl/issues/18) · [阶段 5 原始结果](../rx-phy-next/device-rx-phy1/5/result.json)

候选只在 `owly-awdl/src/driver.rs` 的主循环删掉 `biased;`。原顺序优先轮询 Air RX，后面才是读 Android TUN 和执行窗口 timer。在 Air 持续可读时，TUN 里的 TCP ACK 有延后的可能。短 TCP ACK 已低于 512 B 的 1 ms 合包门槛，本轮保持合包、窗口、队列、PHY、固件和 BA 配置。该变化只影响用户态 poll 机会，和固件收到单个 MPDU 后的 SIFS ACK 分开。Tokio 的默认随机起点用于缓解一直可读分支造成的饥饿。PulseIt 公开提到 TCP ACK 优化与其它多项改动一起提升速度，没有同机公平轮询的独立收益数据。 [Tokio 1.54.1 select fairness](https://docs.rs/tokio/1.54.1/tokio/macro.select.html#fairness) · [PulseIt 原实验](https://github.com/gsl0001/windows-airdrop#how-fast-is-it)

从相同 gap 诊断应用源码独立构建，使用同一 149 物理会话，按原 gap 基线、候选、回到原 gap 基线比较。每轮双向 16 MiB、两端 SHA、同 fd TCP 时序、每方向 PHY 快照及相同 gap 数据。样本用来筛选是否值得做 64 MiB 重复，不单凭短轮启动差异采纳候选。

三轮共六次 SHA 全通过，Mac→小米为 2.474 / 2.264 / 2.443 MB/s，小米→Mac 为 8.076 / 7.954 / 7.248 MB/s。Mac 发送栈在约 1.6 秒之后的进展率为 2.821 / 2.903 / 2.852 MB/s，重传累计增加 162744 / 918128 / 111084 B。三轮的 Data 快照均为 11 个 149 槽。公平候选没有改善整轮接收，原应用已换回，未追加测试。 [基线](../rx-phy-next/device-rx-phy1/5/result.json) · [公平候选](../rx-phy-next/device-rx-phy1/6/result.json) · [回到基线](../rx-phy-next/device-rx-phy1/7/result.json)
