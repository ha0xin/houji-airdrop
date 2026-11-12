# 现有 149 PASSTHRU tap 的第三方可见性

现有备份脚本可以独占初始化 `wonder0`，跳过 Owly 和 TCP 子进程，只用另一个仅接收的 AF_PACKET 进程读取。`wonder-init.c:7–28` 设置 5745 和 AWDL BSSID。物理驱动仍启动 PASSTHRU，主机不提交帧不等于固件或射频绝对静默。`pt_rate_stats` 前后值可以核对主机 raw TX 是否有提交尝试。这个测试尚未进行。

稳定 `be/dp_be.c:1788–1803` 的普通 RXDMA 选择启用 FP、关闭 MD/MO，显式控制位仅 `FILTER_CTRL_BA_REQ`。`cdp_txrx_cmn_struct.h:166–167` 定义独立的 `FILTER_CTRL_BA_REQ` 与 `FILTER_CTRL_BA`。`dp_htt.c:1465–1469` 分别写入两个 subtype。即使看到 BAR，也无法用当前 tap 的 BA 缺席否定 Apple BA。帧目的地址都属于 Mac/iPhone 时，第三方 QoS 和定向 BA action 同样可能在送到 host 前被过滤。

`qdf_nbuf_update_radiotap_passthru` 写 FLAGS、CHANNEL 和可选 TSFT/RSSI。当前没有 VHT、MCS 或 A-MPDU present 位。CHANNEL 来自 vdev 设定，不能重建对端跳频。接收到的 QoS 帧可读 QoS Control 的 TID/A-MSDU 位和重试位。该位属于单帧头，不能代替空口 A-MPDU 状态。`dp_rx_deliver_raw_passthru` 和 REO no-peer 路径会丢弃分链，帧长样本有可见性限制。

如果安排有限可见性诊断，固定 149，不改变现有 RXDMA 过滤。只保留两台已明确的自有 Apple 设备，对自有 TA 的 AWDL PSF/MIF 允许广播 RA，对 BAR/BA、BA action 和 QoS Data 同时核对两个地址。只输出角色、单调时间、相关头字段与有效性标志，不记录载荷、PCAP 或地址。先用至少一种已知自有帧验证观察链能看到该类别，空结果只表示本路径未见。现有 Owly parser 将控制帧分类为 Other，BAR/BA 需要独立边界检查。
