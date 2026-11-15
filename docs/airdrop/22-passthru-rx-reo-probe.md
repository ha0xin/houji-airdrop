# PASSTHRU RX route 与 REO 队列诊断

149 信道单次实机已完成同一 Mac 的 remote peer create、HTT map、delete、unmap 和两层对象释放。下一项保留这条事务，只加已映射远端 peer 的 legacy DP setup。首轮仍不运行 Owly 或 TCP，不主动发送 ADDBA 成功，也不扩大窗口。 [远端 peer 实机闭环](19-passthru-remote-peer-probe-plan.md#一次性候选与实机回执)

## 公开参照与本机路径

- MiCode 同版的普通 TID setup 先检查默认 route，再分配并 DMA 映射描述符，最后交给架构 REO 绑定。非 MLO 默认初始化 Non-QoS 与 TID 0，窗口为 1，BA 状态保持 INACTIVE。Linux ath11k 也把描述符准备和 WMI queue setup 分开记录。ath11k 的芯片和工作模式不同。 [MiCode TID setup](https://github.com/MiCode/vendor_qcom_opensource_wlan/blob/a4da9e3b43864da13bc978b8cb6385ba5332a4af/qca-wifi-host-cmn/dp/wifi3.0/dp_rx_tid.c#L754-L858) · [Linux ath11k REO](https://github.com/torvalds/linux/blob/v6.1/drivers/net/wireless/ath/ath11k/dp_rx.c)
- Owly 的公开 RX BA 实验走支持硬件 BA 的 MT7612U 路径，作为性能方向参照。它没有给出当前 Qualcomm PASSTHRU 目标的 queue 绑定、空口回应或吞吐证据。 [Owly PR 58](https://github.com/t4t5/owly/pull/58)
- 在本机 Kiwi，`cdp_peer_setup` 经过 `dp_main.c:6500`、`be/dp_be.c:1611` 到 `dp_rings_main.c:2593`。普通 WMA wrapper 丢掉返回值。DP legacy setup 创建 `txrx_peer` 并初始化 RX TID、无加密/PN 默认状态和 monitor 统计。默认 route 的 target callback 是 void，`is_default_route_set` 在 callback 后无条件置位。实际 WMI route submit 状态只在 `target_if_dp.c:58–103`。BE `dp_be_rx.h:424–535` 根据运行态和编译选项走共享 Qref 或 WMI，Qref 路径还可能在无 peer ID 时返回成功而没写表。本试验在真实 map/slot 后才 setup。 [本机构建源片段与 SHA](../../experiments/remote-peer-probe/reo-next/kiwi-reo-source-excerpts.txt)
- PASSTHRU 接收仍由 vdev role 选择，已知 peer 路径在 BE RX 分支保留 raw TLV 并走 PASSTHRU callback，common 路径也检查角色并走 raw 交付。setup 本身没有修改 vdev decap 参数。默认 REO route 会改变可达 ring，源码不足以确认合法 raw MPDU、异常帧和 FCS 在 setup 后仍按同样方式交付。需另做限量非 BA 数据对照，首轮 setup 诊断期间保持 DP endpoint 暂停。 [Kiwi BE raw 接收](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/be/dp_be_rx.c#L821) · [common raw 交付](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_rx.c#L1140)

## 首个候选的验收

1. 复用已通过的独占 149/一枚实时 Mac MAC/单 generation 事务。对照保持默认 off；候选只在同一已确认且有真实 DP 槽的 peer 上调用一次 `cdp_peer_setup`，分开记录调用的 issued、returned、status。路线、Non-QoS、TID0 各记首个状态及矛盾重复。
2. 记录 target route 的 WMI 提交、初始 TID 的描述符已映射、BA inactive 和 window=1。进一步在实际共享 Qref 写入或 WMI queue 下发点记录绑定方式；不能把 DMA 地址非零、route flag 或 setup 成功当成硬件使用。若走异步 WMI，提交成功与固件执行分开。
3. 继续原有 peer delete 和 vdev drain。共享 Qref 清除、描述符交给 REO 清理列表与最终 DMA unmap/free 分开。正常路径会按批量或后续 callback 推进，两条 deferred list 在 `dp_soc_deinit` 也有原有释放尾。最终 DMA 释放不阻塞已经验证的 peer/vdev 退役。候选必须在自然回调或正常模块退役的原有释放位置留下可关联记录，再进行设备验收。
4. 只有第一轮通过后，才在独立实验中让同一 peer 有界存活并比较实际 raw RX。随后单独审核 TID0 ADDBA，空口真实 Block Ack、重排与数据校验是协商后验收。现有实验没有独立监听设备。

候选以通过实机的 remote-peer 源为基线。实际编译参数在 RX TID 和 target-interface 两个编译单元均开启 `SERIALIZE_QUEUE_SETUP`、`DP_LFR` 与延后 REO 释放。试验只为同一个 peer 跳过 LFR 额外队列和 HTT ADDBA 更新，普通 peer 走原路径。每个初始 TID 的 scheduler post 和真实 target send/参数释放尾分别计数。两者不一致时保留 BLOCKED，避免排队命令触及已回收的描述符。发送和参数释放只能证明主机侧走完提交路径。 [编译 flags](../../experiments/remote-peer-probe/reo-next/kiwi-reo-build-flags.txt) · [setup 差分](../../experiments/remote-peer-probe/reo-next/remote-peer-reo-setup-draft.patch) · [绑定差分](../../experiments/remote-peer-probe/reo-next/remote-peer-reo-binding-draft.patch)

释放证据从原有实际 DMA unmap/free 的普通、延后与 SoC 清理位置采集。同一 generation/TID 首次释放后关闭对应身份，不输出 DMA 地址。主控在卸载后按同一 boot 和本轮前后的内核日志差集读取，两条新记录必须对应 Non-QoS/TID0。HAL callback 的 SUCCESS、DRAIN 和未观测分开，明确的 callback 失败使整轮验收失败，即使内存已经回收。 [释放差分](../../experiments/remote-peer-probe/reo-next/remote-peer-reo-free-draft.patch) · [主控](../../experiments/remote-peer-probe/run-remote-peer.py) · [验收脚本离线检查](../../experiments/remote-peer-probe/reo-next/device-reo-harness-check.log)

候选在 build-host 编译退出 0，剥离调试段后的 SHA-256 为 `0d98d07be6d2e662a618ee98ee87ea02a390c49af6207a364e792b0490386b47`。两套已测参照的 567 个导入及 CRC、导出、vermagic 和 module 布局一致。新增 `pt_remote_reo` 为 `0400`，默认关闭。 [ABI](../../experiments/remote-peer-probe/reo-next/audit-reo.json) · [构建日志](../../experiments/remote-peer-probe/reo-next/build-reo.log) · [源哈希](../../experiments/remote-peer-probe/reo-next/reo-source.sha256)

只读 stock `wiphy` 在 149 所属的 5 GHz band 报告 VHT 1/2SS MCS 0–9、最高 866 Mbps 与 Max MPDU 11454。这是 stock wiphy 能力。PASSTHRU 的实际 raw buffer 容量和分链处理仍需独立证据，本轮不改 Owly 出站广告。 [stock wiphy 预检](../../experiments/remote-peer-probe/reo-next/stock-vht-capabilities-preflight.txt)

## Setup-only 实机结果

同一代 peer 的 route WMI 提交返回 0，route 参数为 3。CDP setup、Non-QoS TID 16 和 TID 0 返回 0，两个 TID 都报告 DMA 描述符、窗口 1、BA INACTIVE。实际走序列化 WMI，post、target send 尾和描述符交接掩码均为 `0x10001`，没有重复、冲突或 HTT ADDBA 更新。peer 删除和 runtime DRAINED 通过，普通 Wi-Fi 与原生 149/MCS3 Binder 恢复。 [初次结果](../../experiments/remote-peer-probe/reo-next/device-reo1/result.json) · [生命周期](../../experiments/remote-peer-probe/reo-next/device-reo1/lifecycle.log)

首次收集器将内核环的完整输出放入单个 shell 参数，Android 报 `Argument list too long`，因此原 JSON 保留未验证状态。改为流式读取并核对同一 boot 和原 token 的终态及退出文件时间。两条释放日志都在该区间内，与 HTC cleanup/SoC 退役日志相邻。TID 0 和 16 均由 SoC freelist 完成实际 DMA unmap/free。没有可归属的硬件 FLUSH 完成或固件 REO 执行回执。 [同次卸载补充审计](../../experiments/remote-peer-probe/reo-next/device-reo1/post-unload-dma-audit.json) · [连续内核片段](../../experiments/remote-peer-probe/reo-next/device-reo1/dmesg-unload-excerpt.txt)

## 非 BA 有界 raw RX 验证

本阶段开始前查阅 Owly 的公开驱动表、协议文档及已知 peer 对照。原版 Owly 在 WCN6855/WCN7850 上的 monitor 失败记录来自其他驱动和模式。MT7921 的独立抓包报告说明已关联与未关联 peer 的 raw QoS 行为可能不同，不能移植成 DZF2 的结论。Owly 的 RX A-MPDU 依赖设备侧 Block Ack 能力。公开检索没有找到相同 Qualcomm PASSTHRU 角色下，未关联、已映射 peer 的 raw 交付与退役闭环。 [Owly 驱动记录](https://github.com/t4t5/owly/blob/main/README.md) · [Owly 协议](https://github.com/t4t5/owly/blob/main/docs/SPEC.md) · [MT7921 独立抓包](https://github.com/t4t5/owly/issues/14) · [ath11k RX 队列关闭参照](https://github.com/torvalds/linux/blob/v6.1/drivers/net/wireless/ath/ath11k/dp_rx.c)

下一候选在 REO setup/target 尾齐后，用已经成功启动的同一 5745 MHz 状态重开 DP 的 CPU admission，保持原 owner/gate/DSC 引用。至多 45 秒，原测试客户端只在 ready 后发两份 1 MiB 已知 SHA-256 载荷。测试结束自动停止发送，正常 stop、超时或 peer 意外失效均先关闭 host admission，等待 CPU users 和正常 pdev TX descriptor 归零，再进入已验证的 peer delete。原 raw TX 分配及 TQM/FW 完成走同一 pdev outstanding 计数，归零表示发送描述符已收回。 [hold 差分](../../experiments/remote-peer-probe/raw-flow-next/remote-peer-flow-hold.patch) · [DP 同频恢复](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_main.c#L5234) · [pdev TX 完成](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_tx.c#L6785)

RX 诊断分开统计同 peer ID 的 BE 普通路径放行与拒绝、实际配置 buffer/TLV 及观测容量、TA/RA 与 peer 匹配且 clean FCS 的 QoS Data 交给 OSIF。应用端仍需两向 SHA。Mac 调度日志在流后按每个样本的 wall clock 时间窗采集。完成这些对照后再独立判断 BA 协商与真实空口回执。 [RX 差分](../../experiments/remote-peer-probe/raw-flow-next/remote-peer-flow-rx.patch)

### 首轮有界 raw 结果与下一项

本轮确认了 remote peer 与两个初始 TID、数据入口开启、正常 stop 和 CPU admission 排空。Mac 的两个 TCP 连接都立即返回 `EHOSTUNREACH`，没有生成任何应用 SHA 回执。小米用户态已发现一个 AWDL peer，收尾时有 57 个 raw TX 在途描述符，未见可归属的 known-peer raw QoS 交付。入口尚开启时的两份快照分别记录 raw ring 提交尝试 29、56，TQM 完成都为 0；CPU 排空后 pdev outstanding 在 1.5 秒内保持 57。仅凭这些计数无法区分 TCL 消费与完成回传。候选保留 BLOCKED 和 peer 引用，未发 peer delete。一次正常重启按真实失败账本恢复了 stock WLAN、固件、普通 Wi-Fi 及原生 149/MCS3 待命。 [原失败 JSON](../../experiments/remote-peer-probe/raw-flow-next/device-flow1/result.json) · [入口开启时计数](../../experiments/remote-peer-probe/raw-flow-next/device-flow1/after.txt) · [真实恢复凭据](../../experiments/remote-peer-probe/raw-flow-next/device-flow1/recovery-result.json)

下一项开始前查询了公开 Qualcomm/ath11k 的 raw TX 地址搜索及 Xiaomi PASSTHRU peer map。公开 MiCode 普通路径只在 STA 分支更新 vdev BSS AST。Xiaomi 公开 overlay 把 PASSTHRU 也加入该分支，每个远端 map 会覆盖 vdev 的 TX 搜索索引和哈希；同版 BE TX 在写 raw TCL 描述符时也使用该索引和低四位缓存号。公开资料没有同一设备上保留 self AST 的实测。该变化可以解释为什么 no-flow setup/delete 通过而 flow 在真实 TX 时失败，目前只是待验证的候选原因。Mac 的立即路由错误另需结合本机发现和路由状态判断。 [MiCode STA map](https://github.com/MiCode/vendor_qcom_opensource_wlan/blob/a4da9e3b43864da13bc978b8cb6385ba5332a4af/qca-wifi-host-cmn/dp/wifi3.0/dp_peer.c#L2744-L2765) · [Xiaomi PASSTHRU map](https://github.com/YoWenhow/Xiaomi14-PixelOS-Airdrop/blob/5a8fc70d06dcf2c99feb956fc74d8cbafbf8a7ce/kernel/wlan-overlay/qca-wifi-host-cmn/dp/wifi3.0/dp_peer.c#L2744-L2769) · [Xiaomi raw TCL 字段](https://github.com/YoWenhow/Xiaomi14-PixelOS-Airdrop/blob/5a8fc70d06dcf2c99feb956fc74d8cbafbf8a7ce/kernel/wlan-overlay/qca-wifi-host-cmn/dp/wifi3.0/be/dp_be_tx.c#L1275-L1295) · [ath11k 原代码导入](https://cgit.freebsd.org/src/commit/?h=stable%2F12&id=774e8002bfa23b51b93431cda33d60f8441c7b3e)

单变量候选只在本次已映射的 remote peer 上保留 vdev 原 self TX AST 缓存，同时保留远端自己的 map、peer AST 和其它状态。先以真实 self DP 引用确认当前 vdev 缓存的来源，记录 self/remote 索引与哈希，允许零值。没有 self 证据或两组字段相同时，不启动流。发送时记录实际提交到 TCL 的选择。下一次验收分开观察已提交/完成、known-peer raw、Mac 路由与双向 SHA，完成后执行原有 TX/peer/REO 排空和恢复门槛。

### 保留 self TX AST 对照

AST 补丁以首轮 raw flow 构建为基线，新增只读加载开关 `pt_remote_preserve_self_ast`，默认关闭。remote map 已释放原 hash/map 锁，新增的 self hash 引用与真实 ID 槽引用在分别释放各自查找锁后用于同一对象核对。TX 提交尾仍处在原 raw 发送入口的 vdev 引用和 CPU admission 区间，记录的是填给 TCL 的局部索引和低四位缓存号。候选在 build-host 构建并通过 567 个导入 CRC、导出、vermagic 和布局检查。 [AST 差分](../../experiments/remote-peer-probe/raw-flow-next/remote-peer-preserve-self-ast.patch) · [锁与引用源片段](../../experiments/remote-peer-probe/raw-flow-next/ast-lock-lifetime-source.txt) · [ABI 和模块哈希](../../experiments/remote-peer-probe/raw-flow-next/audit-ast.json)

实机核对 self AST 为索引 11/缓存 11，remote 为 2/2。58 次成功 enqueue 全部选用 self 字段，无 mismatch。开流时快照有 55 次 raw ring 提交尝试和 0 次 TQM 完成。停止并完成 CPU 排空后，pdev outstanding 仍是 58→58，等待超时，试验没有发 peer delete。双向连接仍立即返回 `EHOSTUNREACH`，无 SHA 回执，已知远端 raw RX 数据计数为 0。保持 self AST 对该次故障没有产生可见改善。现有记录仍不足以区分 ring 未消费与完成未送达，也未证实路由错误和 TX 失效是否同因。 [AST 实机 JSON](../../experiments/remote-peer-probe/raw-flow-next/device-ast1/result.json) · [TX 快照](../../experiments/remote-peer-probe/raw-flow-next/device-ast1/after.txt)

该轮普通恢复因在途描述符未归零而停在 `RESTORE_FAILED`。同一失败 token 走一次正常重启，复核新 boot、stock WLAN 和 AMSS、普通 Wi-Fi、scan、CND/HAL、PCI 和原生 149 Binder。原 runner 哈希一致，只移除本轮暂存文件。 [恢复凭据](../../experiments/remote-peer-probe/raw-flow-next/device-ast1/recovery-result.json)

### 拆分暂停与 peer setup

开始前核对了公开 Xiaomi overlay、Google Qualcomm TX 完成路径和 Linux ath11k HAL 的 ring publish。公开 ath11k 源码把软件填环、硬件通知与完成分开，不能据此推定 Kiwi ring 的实际进展。未找到相同 PASSTHRU owner 与 DSC 持有下的公开对照。 [Xiaomi DP overlay](https://github.com/YoWenhow/Xiaomi14-PixelOS-Airdrop/blob/5a8fc70d06dcf2c99feb956fc74d8cbafbf8a7ce/kernel/wlan-overlay/qca-wifi-host-cmn/dp/wifi3.0/dp_main.c) · [Google Qualcomm TX 源码](https://android.googlesource.com/kernel/google-modules/wlan/qcom/wcn6740/wlan/+/refs/heads/android-gs-akita-android16/qca-wifi-host-cmn/dp/wifi3.0/dp_tx.c) · [Linux ath11k HAL](https://github.com/torvalds/linux/blob/master/drivers/net/wireless/ath/ath11k/hal.c)

本机的同频 DP reopen 只复位 host CPU admission，没发 WMI 或刷写硬件 ring。Handoff ticket 可并发持有，既有 TX 也经过独立 ticket。完成入口从 DP 读 TX completion ring。源码中 runtime PM 的提交尾在特定状态会只登记待 flush，不能把提交计数等同硬件已消费。先只保留同一 worker、parent DSC 引用、暂停与同频重开、退出 TX drain。候选独立 self generation，不 arm remote key，不发 create/delete 或 setup。只运行原 Owly 的 action frame，采到实际 TQM 完成或达到短采样期限就收尾。只有正对照通过才有理由单独增加 remote create/map，再比较 REO setup。 [DP pause/reopen](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_main.c#L5210) · [Handoff](../../driver/wlan/qcacld-3.0/core/hdd/src/wlan_hdd_wonder_handoff.c#L23) · [TX publish wrapper](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_tx.c#L1696) · [self-only 差分](../../experiments/remote-peer-probe/raw-flow-next/self-flow-control.patch)

仅 self peer 的实机对照通过。相同暂停与重开后，在入口开放时已观察到 29 次 raw ring 尝试和 29 次 TQM 完成，收尾快照分别为 31 和 31。用户态的 33 次 send 成功是另一层计数。31 次 TQM 的 PHY metadata 均未通过 valid 标志，无法据此确认空口 ACK 或 PHY 速率。退出时 pdev outstanding 为 0→0，owner DRAINED。未创建或删除远端 peer，也没有 route、REO setup 或 DMA 试验步骤。普通恢复后 stock 与 native 指纹逐字一致。该结果证明共同的 worker、DSC 持有及暂停重开允许 TX 完成，仍可能与远端 peer 状态产生交互。 [仅 self 实机 JSON](../../experiments/remote-peer-probe/raw-flow-next/device-self-flow1/result.json) · [TQM 快照](../../experiments/remote-peer-probe/raw-flow-next/device-self-flow1/after.txt) · [计数调用点与代际来源](../../experiments/remote-peer-probe/raw-flow-next/self-flow-completion-calls.txt)

下一项开始前复核了公开 ath11k 的 peer 与 REO 源码，以及 Xiaomi overlay 的 DP peer map。ath11k 也独立管理 peer map 与 RX TID 硬件队列，不能作为同一 PASSTHRU 模式已实测的依据。候选以已测 self AST 补丁为基线，保留同样的短 action 采样、REO 观测及匹配 ADDBA 抑制。只创建并映射远端，跳过实际 `cdp_peer_setup`，检查 `setup_issued=0` 及 route/TID/queue/DMA 观测为零。只有同一远端的 create 确认、HTT map、真实 slot 和 self AST 均就绪才开流。退出检查真实 delete/unmap 和两层对象释放。后置日志零记录需要同 boot 且时间覆盖试验起点。 [Linux peer map](https://github.com/torvalds/linux/blob/v6.1/drivers/net/wireless/ath/ath11k/peer.c) · [Linux RX TID](https://github.com/torvalds/linux/blob/v6.1/drivers/net/wireless/ath/ath11k/dp_rx.c) · [Xiaomi PASSTHRU peer map](https://github.com/YoWenhow/Xiaomi14-PixelOS-Airdrop/blob/5a8fc70d06dcf2c99feb956fc74d8cbafbf8a7ce/kernel/wlan-overlay/qca-wifi-host-cmn/dp/wifi3.0/dp_peer.c#L2744-L2769) · [仅 map 差分](../../experiments/remote-peer-probe/raw-flow-next/remote-map-only-flow.patch)

仅 create/map 的实机候选取得 WMI create 确认 0、HTT map 和真实 DP slot。REO 观测开关保持 1，setup 调用、route/TID 事件掩码、WMI queue post/tail 和本代 DMA 事件计数为 0。self AST 为索引 17、低四位缓存 1，remote 为 2/2。115 次成功 enqueue 都选择 17/1，mismatch 为 0。入口仍开启时，raw ring 尝试由 33 增至 112，TQM 始终为 0。CPU 排空后 pdev outstanding 保持 115→115，进入 BLOCKED，未发 peer delete。只创建和映射远端就复现了停滞，因而不需要 `cdp_peer_setup` 即可触发。当前证据未定位是远端创建、映射还是该状态和 PASSTHRU raw TX 的交互。该轮没有运行 TCP。 [实机结果](../../experiments/remote-peer-probe/raw-flow-next/device-map-only1/result.json) · [TQM 快照](../../experiments/remote-peer-probe/raw-flow-next/device-map-only1/after.txt) · [最终 ABI](../../experiments/remote-peer-probe/raw-flow-next/audit-map-only.json)

本轮原结果保留 `restored=false`。普通恢复未能安全回收在途描述符，故同一失败 token 使用一次正常重启回退。相同 serial 恢复到新 boot `428f3996-861d-4bf7-af96-a31d3a5a35a4`，复核了原厂 WLAN、AMSS、普通 Wi-Fi、scan、CND/HAL、PCI 和原生 149 Binder，原 runner 哈希一致。此轮没有正常模块卸载后的连续 dmesg 覆盖，因此零 DMA 只描述退役前实际计数。 [恢复 JSON](../../experiments/remote-peer-probe/raw-flow-next/device-map-only1/recovery-result.json) · [独立 stock 预检](../../experiments/remote-peer-probe/raw-flow-next/device-map-only1/stock-after-reboot.txt)

> **后续根因定位补充（2026-11-11）**：对照三星 S24 Ultra 原厂配套 `qca_cld3_kiwi_v2.ko` 反汇编与公开 Qualcomm WMI/DP 源码后已查明，上文 `device-flow1` / `device-ast1` / `device-map-only1` 的 TQM 零完成停滞**并非 `amss20.bin` 固件互斥**，而是因为只下发了 `WMI_PEER_CREATE_CMDID` 而漏发 `WMI_PEER_ASSOC_CMDID`（使硬件 AST 映射到了固件未关联暂停态的 peer 队列），外加移植仓 `dp_peer.c` 的 `bss_peer` 条件写反及缺失逐包 TCL `search_index` 覆写。完整指令级证据见 [27-firmware-ba-passthru-verification.md](27-firmware-ba-passthru-verification.md)。
