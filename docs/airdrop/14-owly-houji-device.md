# Owly 在小米 14 与 Mac 的 149 信道实测

日期 2026-11-10。小米 14 / houji，内核 `6.1.138-android14-11-g0c3d559bcd85-ab14529422`。无线固定 149 / 5745 MHz，只接受当前 Mac `awdl0` 的空口源地址。独立的 IPv6 TUN 为 `owly0`，MTU 1450。测量覆盖 Mac 与小米之间的 TCP 数据路径，MIShare UI、AirDrop 上层协议和 iPhone 仍按原生路径单独验收。

## 实现与构建

- 复用 `owly-awdl` 提交 `6f2e8ea99aed0687558d78a1dd49154ab3a5fa76`。Rust `Air` 使用绑定到 `wonder0` 的 AF_PACKET，沿用 Owly radiotap 解析并检查截断/FCS。发送遇到 `EAGAIN` 或 `ENOBUFS` 时返还完整帧，供原 driver 进行窗口内或后续窗口重试。其它错误独立计数。
- 独立的 nl80211 初始化器确认 Wonder wiphy 与接口身份，检查真实内核 ACK，按现有国家码、5745 MHz、AWDL BSSID 和固定速率写入缓存。初始化前从 stock 物理 phy 核验 149–161 的法规许可，随后仍由驱动校验。Wonder 接口 UP 时建立 PASSTHRU。设备日志显示 `phase=3 error=0 closing=0 handle=1 freq=5745`，然后才启动 Owly。PHY 由固件完成统计核对，`iw` 的 monitor 软件宽度保持 20 MHz。
- 首轮关闭 A-MSDU 和接收 Block Ack。随后比较原版 Owly 合包和独立的 1 ms IP backlog 合包补丁。补丁保留 peer HT 与 monitor 长度上限、同 peer 顺序，按实际窗口发送。接收 Block Ack 全程关闭。C/Go/Rust 产物均在 `build-host:/work/houji-build/owly-evaluation-src` 构建，用户态工具链为 `/work/houji-build/toolchains/owly-userspace` 中的 Rust 1.97.1 Android target 与 Linux NDK r30。Mac 拉回产物进行 SHA-256 校验、ADB 部署和测速。远端 Linux NDK ZIP 对照了官方 SHA-1。 [Android NDK 下载页](https://developer.android.com/ndk/downloads)
- 远端跑过四项 radiotap 测试和独立补丁的 65 项 Owly 测试，其中四项覆盖合包截止、满包提前发送、顺序和窗口关闭。原始 Owly checkout 保持固定提交且工作树干净。源码、构建、回滚和复测指令见 [独立 runner 说明](../../experiments/owly-evaluation/houji-runner/README.zh-CN.md)。

## 测量口径

探针用生成的内存数据，各轮按 Mac→小米、小米→Mac 顺序传输。两端按样本编号、方向、字节数和 SHA-256 匹配，每轮保留完整 JSON、日志与恢复证据。速度为十进制 MB/s，包含应用发送、接收及哈希回执，不经过磁盘。大样本平均按该方向成功载荷字节总数除以时间总和计算，最快值为单个完整校验样本速度。软件 A-MSDU 通过监视注入端成功提交的 QoS 帧和子帧计数核对，该计数与固件 A-MPDU / Block Ack 分开。

## 吞吐结果

| 配置 | 每方向载荷 × 次数 | Mac→小米 MB/s | 小米→Mac MB/s | 双端 SHA |
|---|---:|---:|---:|---:|
| HT20 / 单帧 / ACK | 8 MiB × 1 | 0.633 | 2.627 | 2/2 |
| VHT80 / 单帧 / LGI | 16 MiB × 1 | 2.326 | 3.330 | 2/2 |
| VHT80 / 原版合包 / LGI | 16 MiB × 1 | 2.297 | 3.410 | 2/2 |
| VHT80 / 1 ms 合包 / LGI | 16 MiB × 1 | 2.117 | 6.495 | 2/2 |
| VHT80 / 1 ms 合包 / SGI | 16 MiB × 1 | 2.406 | 6.357 | 2/2 |
| VHT80 / 1 ms 合包 / SGI / MSS 1160 | 16 MiB × 1 | 1.967 | 7.933 | 2/2 |
| VHT80 / 单帧 / LGI | 64 MiB × 2 | 2.452 / 2.177 | 2.972 / 3.032 | 4/4 |
| VHT80 / 原版合包 / LGI | 64 MiB × 2 | 2.497 / 2.527 | 2.854 / 3.011 | 4/4 |
| VHT80 / 1 ms 合包 / LGI | 64 MiB × 2 | 2.514 / 2.639 | 5.561 / 5.104 | 4/4 |
| VHT80 / 1 ms 合包 / SGI | 64 MiB × 2 | 2.545 / 2.562 | 4.974 / 5.499 | 4/4 |
| VHT80 / 1 ms 合包 / SGI / MSS 1160 | 64 MiB × 3 | 2.101 / 2.161 / 2.171 | 6.629 / 6.610 / 6.191 | 6/6 |
| VHT80 / 1 ms 合包 / SGI / MSS 1160 / TUN qlen 2048 | 64 MiB × 2 | 2.128 / 1.904 | 6.188 / 6.439 | 4/4 |

共完成 38 次有效传输，载荷失败 0 次。**小米→Mac 可重复的最高实测平均为 6.470 MB/s**，来自 VHT80/MCS7/SGI、1 ms 合包和探针 MSS 1160 的三次 64 MiB 传输，最快完整 64 MiB 样本 6.629 MB/s。该配置的 Mac→小米平均为 2.144 MB/s。**Mac→小米最高可重复平均为 2.575 MB/s**，来自 LGI、1 ms 合包、默认 TCP MSS 的两次 64 MiB 样本。16 MiB 小样本的最高发送速度为 7.933 MB/s。所有已验证样本均低于 10 MB/s。

原版 Owly 自带 A-MSDU 的 64 MiB 发送仍约 3 MB/s，重叠窗口内通常有包即发。1 ms 补丁在 IP backlog 内合包后再做窗口判定，默认 MSS 时实测每个聚合 MPDU 约 2 个 MSDU。MSS 1160 由手机测试 socket 在握手时设置，设备记录有效 MSS 1148，聚合密度增至约 3 个 MSDU。在这组对照中，小米发送速度上升，反向速度回落。固定信道、MTU 1450、接收 BA 关闭和非原生探针是结论的边界。

最佳发送长轮观察到 58,884 个已提交的聚合 MPDU、175,109 个 MSDU，平均 2.97。82,300 次 Air 队满返还触发重试，AF_PACKET fatal 和 TUN 用户态交付失败均为零。149,021 条有效固件发送完成元数据均为 80 MHz/VHT/MCS7/SGI，完成统计单独记录，不能据此判定硬件 ACK 或 Block Ack。

最佳发送配置的 TUN 接口 TX dropped 为 1,147，qdisc dropped 为零。相同 ELF、MSS、GI 下，把独立 `owly0` 的 `txqueuelen` 从 500 增至 2048，接口 TX drops 降为零。两次 64 MiB 的小米→Mac 平均为 6.311 MB/s，低于 500 时的 6.470 MB/s。该轮接口丢包归零，实测速度没有提高。默认保留 500。

## 失败与恢复

- 首次 HT20 尝试在 PREFLIGHT 退出。手机报告的 supervisor argv 为 `/system/bin/sh`，首版只接收 `sh`。只读核验同一原生服务和 stock Wi-Fi，之后允许同一脚本的两个精确 argv 形式。
- 第二次尝试收到速率带宽属性 `-EINVAL`。设备自动恢复 stock 和原生待命服务。初始化器随后按 Wonder `NLA_U16` 约束发送两字节宽度并零填充，保留真实内核 ACK 校验。
- 一次 SGI 启动遇到新旧脚本参数不匹配，未进入无线会话。旧收据已标记无效。主控改为按唯一 token 读取退出结果和日志，重新运行的 SGI 样本均通过。
- 每轮退出在共用锁内停止测试进程、回收 TUN、按引用及 holder 检查卸载候选模块，恢复 stock 模块、固件和 Wi-Fi。退出锁后恢复原 149/MCS3 supervisor 与 Binder。最终核验 stock QCA build ID、原厂固件及原生 `radio.sh`、`libxos.so`、`xos-bridge.zip` 的 SHA-256，均与试验前一致。

[汇总 JSON](../../experiments/owly-evaluation/houji-runner/results/summary.json) · [最佳发送原始轮次](../../experiments/owly-evaluation/houji-runner/results/coalesce-sgi-mss1160-64m/results.json) · [最终恢复证据](../../experiments/owly-evaluation/houji-runner/results/final-stock-verification.txt)
