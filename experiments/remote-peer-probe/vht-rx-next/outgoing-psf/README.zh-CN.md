# VHT 广告覆盖 PSF

本轮实现前检索 GitHub 的 AWDL PSF/MIF、VHT TLV 17 和吞吐实测。锁定 Owly 提交 `6f2e8ea…` 的公开语料支持覆盖两种 subtype 的假设。可归属 iOS 26.x 的 705 个 PSF 和 757 个 MIF 都在 HT TLV 7 后紧接 TLV 17，容器只含 VHT IE 191。小米方案复用已测的保守 1SS 能力字节，仅借鉴位置和覆盖范围。 [公开原始抓包](https://github.com/t4t5/owly/tree/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/tests/captures) · [按角色的解析证据](../../../iphone-radio-research/public-ios26-awdl-mif.json) · [Wireshark 容器实现](https://github.com/wireshark/wireshark/blob/master/epan/dissectors/packet-awdl.c#L1010)

Owly PR 62 在 MT7921/信道 44 报告 71.3 MB 传输约 11–14 MB/s。其实验同时改变 MIF 广告、HT 字段、80 MHz 和 2SS，并包含其它补丁，未测试 149 的该模式。Issue 38 把 HT TLV 放进 PSF 后改善了与 Galaxy 的接收兼容性，这与 VHT TLV 的独立速度效果是不同问题。PulseIt 的 4–6 MB/s 报告同时加入 80 MHz、VHT、BA 重排和 TCP ACK 调整。未找到单独把 VHT 加到 PSF 的公开吞吐对照。 [Owly PR 62](https://github.com/t4t5/owly/pull/62) · [Owly issue 38](https://github.com/t4t5/owly/issues/38) · [PulseIt 公开实验记录](https://github.com/gsl0001/windows-airdrop#how-fast-is-it)

同一独立应用按 `HOUJI_OWLY_OUTGOING_VHT` 选择 0（默认关）、1（只 MIF）或 2（PSF 和 MIF）。两个开启模式都要求 4224 接收容量和 VHT80 模式读回为真。容器字节 `bf0c00000000fcff0000fcff0000` 沿用，BA 保持关闭。只有模式 2 的 PSF 增加 17 字节 TLV；相对模式 1，MIF 保持逐字不变。应用对两个 subtype 分别保存第一条成功 AF_PACKET 提交的容器值。该记录能验收本机输出，无法确认 Mac 对广告的采用。

构建入口 `build-outgoing-psf.sh` 在 `build-host:/work/houji-build/tasks/outgoing-psf` 使用锁定的 Linux NDK/Rust。两层差分应用于独立的源码副本，保留已有 `ab84ddba…` 应用与原 clone。主机入口为 `run-remote-peer.py --rx-capacity --outgoing-psf mif|both|off`。短测用同一新 SHA，按 MIF → PSF+MIF → MIF 验证双向 1 MiB SHA、两条容器日志、容量和 stock 恢复。若进入长测，追加 `--capacity-64`，同一配置各方向 3 次，继续记录 Mac 调度和 peer 能力缓存边界。
