# EZIF TX A-MPDU 实装与吞吐验证（2026-11-11）

## 已验证结果

实验模块 `41113785efdb2acb58d802a696da961dba35bdb7ea360dea3e60642aeb3e18ad` 搭配官方 EZIF 固件，补齐 PASSTHRU MSDU-buffer RX 分流并启用 peer RX 设置后，已观测到硬件 TX 多 MPDU/PPDU，同时完成文件校验和提速。

64 MiB × 3 双向测试中，小米→Mac 为 **20.943、21.350、20.652 MB/s**，加权 **20.978 MB/s**；Mac→小米加权 **14.636 MB/s**。六次 SHA-256 全部一致。16 MiB 单轮小米→Mac 为 **28.139 MB/s**。这是独立 Owly 实验路径，尚未回迁日常分享组件。

## 硬件证据及解释边界

证据目录：[64 MiB 重复组](../../experiments/three-findings-validation/runs/ezif-rx-adapter-20261111-on64)。`1/result.json`、`1/rate-before`、`1/rate-after`、`1/ampdu-after` 保存原始读数与差值。

- 有效 TQM TX completion：59,622。
- 由硬件 completion 的 PPDU ID 分组：6,931 个 PPDU，其中 5,718 组含多个发送完成描述符，聚合组共 58,409 个 MPDU，峰值 18 MPDU/PPDU。
- 该路径每个 raw TX skb 对应一个 MPDU；用户态的 A-MSDU 已封装在该 MPDU 内，因此多 completion 共享 PPDU ID 的观测与“单 MPDU 内含多个 MSDU”不同。
- 计数来自 `dp_pt_rate_end()` 对有效硬件 completion 的观察，不是将配置开关读数当作聚合证据；也不是独立空口抓包。计数涵盖该阶段手机的全部 TX（含反向传输的 ACK），不按两次文件传输方向分别清零。
- `last_peer_id=0` 是最后一个完成事件的 peer，不能用它否认全阶段远端 peer 流量，也不能把它当作聚合 peer 的单独证据。
- EZIF 原始 wire506=1。用户态发送了 ADDBA 请求，但该组 `rx_resp=0`，未取得用户态验证过的 ADDBA Response；不能声称这条用户态握手链已完整验证。最终成功由硬件 TX 分组、数据传输与完整性共同支持。

## 实装内容

1. 新增 `pt_rx_msdu_buf=1` 实验 gate，识别已有 PASSTHRU peer 的 REO MSDU-buffer，持有 vdev 引用，单次 DMA unmap、描述符回收/回补及 skb 消费，复用既有安全 raw RX 交付接口。
2. 修正子代理补丁的两个实际集成问题：接口声明不再受未启用的 `WLAN_DP_FEATURE_SW_LATENCY_MGR` 约束；cookie 转换改用 BE 的 `soc->arch_ops.dp_rx_desc_cookie_2_va`，不再使用旧 RXDMA pool/page/offset 解码。
3. 使用 `remote_reo=2`，执行 `cdp_peer_setup`，默认路由 hash=false、dest=0；该模式还设置 TID0 接收窗口64，属于组合配置，不声称单独归因路由。
4. 短测监控 `peer_miss/bad_desc/dropped`，出现异常提前 stop。第一轮 cookie 错误被提前捕获，后续均无需重启恢复。

64 MiB 组适配器计数：seen=reaped=delivered=578，peer_miss=bad_desc=dropped=0。此计数只覆盖新接管的 MSDU-buffer 分支，不是所有正常数据路径的总 RX 包数。

## 有效配置及复现

官方固件 SHA256：`37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f`。型号 SM-S928B，EZIF 官方 FUS 来源见 [获取记录](../../experiments/tx-ampdu-investigation/firmware-acquisition/ACQUISITION.md)。

固定参数：149 信道、VHT80/2SS/MCS9/SGI、remote_assoc=1、AST/hash override=1、remote_reo=2、fw_addba=0、tx_ampdu=1、dynamic_rate=0、sta_coexist=0、用户态 tx-addba=win64。Mac 网络配置未修改。

在仓库根目录执行，`--run-name` 必须使用新目录名：

```sh
python3 downloads/airdrop-driver-work/three-findings-validation/run-ezif-rx-adapter.py \
  --run-name ezif-ampdu-new-run --remote-reo 2 --tx-addba win64 --mib 64
```

脚本校验模块 ABI 审计、产物和固件哈希，测试结束恢复原厂 Wi-Fi。新 RX gate 默认关闭，模块常规加载不会自动开启。

## 不能误读的对照

`pt_tx_ampdu=0` 的 EZIF 16 MiB 对照仍出现399个聚合PPDU，速度30.410 MB/s。因此它**不是已验证有效的 EZIF 聚合关闭开关**；不可把该轮标成“关闭聚合”。其当前实现只在 peer 创建前设置 vdev 聚合上限，并额外改变 no_sw_retry，不能据此给出纯开关因果结论。

未发用户态 TX ADDBA 的 EZIF 1 MiB 探针虽然有59个聚合PPDU，但发送只有0.188 MB/s。聚合出现不自动保证接收端协同和高速传输。

## 同模块、同参数、同载荷的固件对照

两组均为上述同一模块、remote_reo=2、tx_ampdu=1、tx-addba=win64、64 MiB×3 双向，仅固件选项不同。基线目录：[DZF2 control64](../../experiments/three-findings-validation/runs/dzf2-rx-adapter-20261111-control64)。

| 项目 | DZF2 | EZIF + RX 适配 |
| --- | ---: | ---: |
| wire506 | 0 | 1 |
| 小米→Mac 三次 MB/s | 0.774 / 2.188 / 2.132 | 20.943 / 21.350 / 20.652 |
| 小米→Mac 加权 MB/s | 1.352 | 20.978 |
| Mac→小米 加权 MB/s | 9.802 | 14.636 |
| 聚合 PPDU 组数 | 0 | 5,718 |
| 最大 MPDU/PPDU | 1 | 18 |
| 双向 SHA-256 | 6/6 通过 | 6/6 通过 |
| 停止恢复 | STOCK_RESTORED，无需重启 | STOCK_RESTORED，无需重启 |

按加权速度约15.5倍；DZF2首轮较慢，为避免夸大，即使采用DZF2最快单轮2.188 MB/s作基线，EZIF加权20.978 MB/s仍约9.6倍。DZF2开TX ADDBA后可能存在既有接收窗口等待，不能用该组夸大相对历史最优的提升；历史remote-peer无TX ADDBA约2.3 MB/s，仍远低于本次EZIF结果。

这组证据支持“已在实验路径实现TX A-MPDU且方案显著提速”。跨固件对照证明整套固件/宿主适配方案的差别，不能将全部提升独占归因到一个固件位或单个函数；尚未证明同一EZIF固件下的纯聚合开关因果。

## 最终状态与交付边界

所有新增实机轮次均完成停止与原厂恢复；最终检查为 `STOCK_RESTORED WIFI_CONNECTED NO_EXPERIMENT_INTERFACE_OR_RULES`。修正cookie转换后未再需要重启。未安装为日常开机驱动，未回迁MIShare/XOS，未知peer依然保留原异常路径与阈值。

目标内核编译及567个导入符号CRC、vermagic、PLT/GOT、默认参数与debug strip审计全部通过。独立33例mock测试只证明其覆盖的分支，不替代目标编译；这次目标编译与实测额外发现并修正了声明宏和BE cookie两处缺陷。
