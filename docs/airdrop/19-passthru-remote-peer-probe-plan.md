# PASSTHRU 远端 peer 创建与删除能力探测

[单对端实测](17-rx-peer-reo-observer-plan.md#单对端实机结果)在 149 信道收到测试 Mac 的 48 个 TID 0 ADDBA 请求，同时只见 self map。BA 在该基线中关闭。下一项只验证 DZF2 Kiwi 固件与现有 PASSTHRU vdev 能否为同一个未关联 Mac 建立并释放 remote peer。peer 能力与 TCP 速率分别验收。

## 公开参照

- 小米 14 的上游 PASSTHRU 适配沿用 `wma_vdev_self_peer_create()`，使用本机 vdev MAC 和 `WMI_PEER_TYPE_DEFAULT`。这是 self peer 的实现参照。公开资料未找到此 vdev 角色下未关联远端 peer 的创建和删除回执。 [上游 WMA self peer](https://github.com/YoWenhow/Xiaomi14-PixelOS-Airdrop/blob/5a8fc70d06dcf2c99feb956fc74d8cbafbf8a7ce/kernel/wlan-overlay/qcacld-3.0/core/wma/src/wma_dev_if.c#L2902)
- 同源 Qualcomm 驱动先创建 objmgr/DP 对象，再提交 WMI create。普通 `wma_create_peer()` 还增加 peer count 并触发 `cdp_peer_setup()`。create confirmation 另有 vdev、MAC 和 firmware status，旧处理函数会按 vdev 查 STA request。delete response 处理也使用 STA 请求表。探针可以复用经过审计的对象和命令实现，但需要单独匹配回执，避免借用或消费 STA request。 [WMA 创建及提交](../../driver/wlan/qcacld-3.0/core/wma/src/wma_dev_if.c#L2399) · [WMA 创建确认](../../driver/wlan/qcacld-3.0/core/wma/src/wma_dev_if.c#L3794) · [WMA 删除确认](../../driver/wlan/qcacld-3.0/core/wma/src/wma_dev_if.c#L3928)
- Linux 6.1 ath11k 在 WMI create 提交后单独等 HTT peer map。删除时分别等 peer unmap 与 WMI delete completion。这提供了拆分证据的例子，其芯片和工作模式与本项目的 DZF2 PASSTHRU 不同。 [ath11k 创建](https://github.com/torvalds/linux/blob/v6.1/drivers/net/wireless/ath/ath11k/peer.c#L350-L445) · [ath11k 删除](https://github.com/torvalds/linux/blob/v6.1/drivers/net/wireless/ath/ath11k/peer.c#L251-L328)

## 下一项可执行验证

1. 先做独立、默认关闭的一次性候选。只允许现有 owner 在有效 PASSTHRU lease、独占无线锁和单个已验证 Mac MAC 下启动。首先被动读本轮固件 service-ready 的 `PEER_CREATE_CONF`、`SYNC_DELETE_CMDS` 和 DP peer-unmap-confirm 能力，并核对实际 handler 与 vdev 释放链。先明确 objmgr/DP 准备、WMI submit 和隐式 `cdp_peer_setup` 的影响，再决定是否满足创建门槛。 [服务映射](../../driver/wlan/qca-wifi-host-cmn/wmi/src/wmi_unified_tlv.c#L22774) · [owner 生命周期](../../driver/wlan/qcacld-3.0/core/hdd/src/wlan_hdd_passthru_owner.c#L263)
2. 对照仍是 self map。实验只在独立的临时 PASSTHRU vdev 发一次 remote create，保持 149，且不在 remote peer 存活时运行文件吞吐。记录同 generation、vdev、MAC 下的 host 对象准备、WMI 发送、固件 create status、匹配 HTT peer ID 和实际 DP 槽写入。没有宣告 create confirmation 能力时，可以用匹配的 HTT map 证明固件已发出映射。此时单列 WMI confirmation 未支持。提交成功而无有效回执视为未证实。
3. 删除同一个 peer，再查匹配的 HTT unmap、能力允许时的 WMI delete response、DP 槽与 objmgr 引用释放。关闭 vdev 前排空该次事件，拒绝晚到和歧义回执。同一 vdev 不重试创建。PASSTHRU remote map 会改 `bss_ast_hash/idx`，普通 unmap 的缓存回退只在 STA 分支可见。首轮用临时 vdev 完整退役隔离该副作用。超时或清理不确定则停止试验并走 stock 恢复，保留缺失的回执和状态。 [PASSTHRU 映射](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_peer.c#L2933) · [unmap 路径](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_peer.c#L3101)

这项设计保持 BA 关闭，保留 TX PHY 与聚合设置。先证明 peer 的创建和删除闭环，再单独验证 route、REO 和空口 BA。先构建默认关闭的只读固件能力观察器，再构建独立的一次性远端 peer 候选。两份模块分别做静态 ABI 核验和实机验收。 [能力观察器](../../experiments/remote-peer-probe/README.zh-CN.md)

## 固件服务位实测

单次装载候选模块，先读取空快照，再按现有生命周期初始化 149 信道 PASSTHRU vdev。快照只在 WMA 现有 self-peer 初始化中读取本轮服务位和 DP 查询。没有运行 Owly、创建 TUN、发起 TCP 测试或请求创建远端 peer。 [能力读取补丁](../../experiments/remote-peer-probe/peer-capabilities.patch) · [生命周期日志](../../experiments/remote-peer-probe/device-read1/lifecycle.log)

| 字段 | 初始化前 | 初始化后 |
| --- | ---: | ---: |
| enabled | 1 | 1 |
| sampled | 0 | 1 |
| create_conf | 0 | 1 |
| sync_delete | 0 | 1 |
| fw_unmap_conf | 0 | 1 |
| dp_unmap_conf | 0 | 0 |

前三个能力结果来自同一 WMI service map，证明固件宣告了相应协议。它们不证明此 PASSTHRU vdev 接受未关联 remote peer。此 Kiwi wifi3.0 的 `dp_ops_cfg` 是空表，`cdp_cfg_get_peer_unmap_conf_support()` 缺回调时固定返回 0。该读数不能证明主机配置关闭。MLME 的 `CFG_DP_ENABLE_PEER_UMAP_CONF_SUPPORT`、非 FTM 条件和固件服务位共同决定 WMA 的 `wlan_res_cfg->peer_unmap_conf_support`。TLV 编码另将它写入 WMI INIT 的 `WMI_RSRC_CFG_FLAG_PEER_UNMAP_RESPONSE_SUPPORT`。本轮未读取实际资源配置值，也未发送 peer 命令验证相应回执。下一步保持当前配置，分别查固件删除确认、HTT unmap 和对象引用释放。 [实机 JSON](../../experiments/remote-peer-probe/device-read1/result.json) · [CDP getter](https://github.com/MiCode/vendor_qcom_opensource_wlan/blob/a4da9e3b43864da13bc978b8cb6385ba5332a4af/qca-wifi-host-cmn/dp/inc/cdp_txrx_cfg.h#L354)

模块 SHA 与静态 ABI 记录一致。运行退出 0，初始化前后库存哈希和服务验证一致，普通 Wi-Fi 与原生 149/MCS3 supervisor 已恢复。 [静态 ABI](../../experiments/remote-peer-probe/audit-stripped.json) · [恢复凭据](../../experiments/remote-peer-probe/device-read1/stock-after.txt)

## 创建控制面和恢复门槛

宿主类型和固件类型分开。WMI 的 `WMI_PEER_TYPE_DEFAULT` 本来就涵盖非 BSS peer。WMA 的 objmgr 类型选择没有 PASSTHRU 远端分支，默认 wrapper 在 type 0 处退出。可审查的候选是在专用 PASSTHRU 操作内给远端对象指定 `WLAN_PEER_STA`，保持 vdev 的 PASSTHRU 角色。PASSTHRU 已显式把 self peer 设为 BSS peer，满足 `WLAN_PEER_STA` 创建和销毁时成对获取与归还 BSS 引用的结构条件。实际进入前仍需校验组件创建结果、vdev/pdev/固件配额和重复 MAC。普通 `wma_create_peer()` 隐含 `cdp_peer_setup()`，会超出只证明 map/unmap 的范围。首轮不调用 setup、assoc、register 或改变聚合。 [WMA 类型选择](../../driver/wlan/qcacld-3.0/core/wma/src/wma_dev_if.c#L2110) · [objmgr 对象](https://github.com/MiCode/vendor_qcom_opensource_wlan/blob/a4da9e3b43864da13bc978b8cb6385ba5332a4af/qca-wifi-host-cmn/umac/cmn_services/obj_mgr/src/wlan_objmgr_peer_obj.c#L242) · [PASSTHRU self/BSS](https://github.com/YoWenhow/Xiaomi14-PixelOS-Airdrop/blob/5a8fc70d06dcf2c99feb956fc74d8cbafbf8a7ce/kernel/wlan-overlay/qca-wifi-host-cmn/umac/cmn_services/obj_mgr/src/wlan_objmgr_vdev_obj.c)

在 owner 已持有正确 vdev 的独立操作中暂停数据入口，按 generation 取得现有 gate ticket。执行和等待不得持 RTNL、owner 控制锁或 WMI/HTT 锁。WMI 确认在现有 TLV 验证后识别专用的 vdev/MAC，HTT 只认当前 peer ID。无法归属的回执不得消费普通 STA 请求。WMI delete 回执若走专用分派，仍要保留实际需要的 DP CP 删除回执处理。事件不在锁内调用 owner 或持裸指针。正常关闭必须独立确认固件 delete、HTT unmap、DP 槽移除、DP 与 objmgr 最后引用和临时 vdev 的完整退役。 [owner gate](../../driver/wlan/qcacld-3.0/core/hdd/src/wlan_hdd_passthru_owner.c#L720) · [WMA delete 回执](../../driver/wlan/qcacld-3.0/core/wma/src/wma_dev_if.c#L3956)

若已证明 WMI 从未提交，只释放已经分配的宿主对象。若 create 状态失败或超时但同一临时 vdev 的 HTT map 和实际 DP 槽都证明目标对象存在，只做一次配对的补偿删除，实验仍为失败。提交结果不明或删除少回执时保留引用和错误状态。正常 `dp_peer_delete_wifi3()` 要等 HTT unmap 才能释放 map 持有的引用，普通 vdev detach 只在目标失联或 reset 时进入 peer flush。没有找到正常 vdev delete 必然补发 remote unmap 的公开或本机证据。不同 Qualcomm 固件甚至要求不同的 peer delete 与 vdev stop 顺序，不能将 ath11k 的成功路径直接套给 DZF2。 [DP 删除](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_main.c#L7009) · [DP vdev 退役](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_main.c#L5693) · [ath11k 芯片顺序差异](https://github.com/torvalds/linux/commit/b4a0f54156ac)

现有 `pt_release` 会在 owner 无法干净退役时保留模块 pin。已有 SSR 代码需独立的 PE purge、DP 和 WMA 回执，并且只接受符合记录的退役步骤，尚不能证明它覆盖新加的远端 peer。最后回退可采用事先获授权的正常系统重启。测试模块和固件 bind mount 属于本次运行，原厂文件未被覆盖。重启后仍要验证 boot ID、原厂 WLAN/AMSS 哈希和无实验对象，再按快照恢复普通 Wi-Fi、扫描设置以及临时 native149 待命。原跨 boot 分支只验库存并迁移账本。候选恢复脚本增加 Wi-Fi、扫描、服务和 PCI 前态的恢复与读回，并在首个 remote create 前用原厂模块演练。 [桥释放条件](../../driver/wlan/qcacld-3.0/core/hdd/src/wlan_hdd_passthru_runtime.c#L957) · [当前跨 boot 分支](../../experiments/owly-evaluation/houji-runner/radio-owly.sh#L101)

离线事务模型只接受独立、可归属的证据。它覆盖正常路径、未提交的宿主回滚、拥有远端槽时的一次补偿删除，以及无法确认时保持关闭。该模型尚未连接 WMI 或 DP，无法验证固件行为。 [模型](../../experiments/remote-peer-probe/remote-peer-state.h) · [离线测试](../../experiments/remote-peer-probe/check-remote-peer-state.c)


## 一次性候选与实机回执

候选在原厂能力观察器上叠加四个独立补丁。WMA 仅在试验入口显式选择宿主 `WLAN_PEER_STA`，固件仍收 `WMI_PEER_TYPE_DEFAULT`。匹配 WMA 实例、vdev、MAC 的试验确认先于 STA request lookup，删除回执仅首次调用 DP callback。HTT 和 DP map 槽分开记账，实际释放发生后再记 DP/objmgr 完成。试验对象在公开到 DP 表之前分配 TID，分配失败不留下公开对象或 CHILD 引用。 [WMA](../../experiments/remote-peer-probe/remote-peer-wma-stage1.patch) · [DP 回执](../../experiments/remote-peer-probe/remote-peer-dp-stage2.patch) · [宿主准备](../../experiments/remote-peer-probe/remote-peer-host-prep-stage3.patch)

模块只接受一枚加载时指定的 Mac 地址。触发器仅在独占、临时 vdev 已就绪、5745 MHz 和固件两种聚合关闭时排队。worker 取得 DSC operation 和现有 owner gate ticket 后暂停 DP，在无 RTNL、无 owner 控制锁的执行上下文发送命令并有限等待。若已确证远端槽存在，可做一次补偿删除。证据不齐则保留 BLOCKED 与模块 pin。 [owner 与触发](../../experiments/remote-peer-probe/remote-peer-owner-stage4.patch) · [设备验收脚本](../../experiments/remote-peer-probe/run-remote-peer.py)

远端 `build-host` 构建退出 0。剥离调试段后的 SHA-256 为 `40f845c7430ab50aa31ec758931b1e464d038d43f44dd4ea568e284d6a56188e`，源补丁与本地哈希一致，567 个导入及 CRC、导出、vermagic 和 module 布局与两个宽带参照一致。运行内核版本仍为此前已加载参照的 `6.1.138-android14-11`，模块 vermagic 的 `6.1.68` 与参照一致。设备用普通 `insmod`。 [构建日志](../../experiments/remote-peer-probe/build-owner-final.log) · [源哈希](../../experiments/remote-peer-probe/adapter-source.sha256) · [静态 ABI](../../experiments/remote-peer-probe/audit-adapter.json)

| 149 信道单次回执 | 结果 |
| --- | --- |
| 事务归属 | generation 1、vdev 0、peer ID 1，目标 MAC 在加载前两次读取本轮 Mac `awdl0` |
| 创建 | objmgr attach/create、DP create、WMI 提交返回 0、固件确认 status 0、HTT map 和槽写入齐全 |
| 删除 | WMI 提交返回 0、固件删除确认、HTT unmap、槽清除、DP 与 objmgr 释放齐全 |
| 退役 | 两次终态一致，无重复确认、归属冲突或补偿，Wonder 删除后 runtime DRAINED |
| 恢复 | radio 退出 0，原厂模块/固件哈希、普通 Wi-Fi、native149/MCS3 Binder 通过 |

首次立即读取退役前 runtime 状态遇到控制锁忙，终态 WMA 和 Wonder 删除后的 DRAINED 快照均完整。此次只验证单个未关联 Mac 的 remote peer 创建与删除。BA 协商、REO 队列和空口 BlockAck 尚未测试，本轮没有 TCP 吞吐结果。 [实机 JSON](../../experiments/remote-peer-probe/device-remote1/result.json) · [生命周期](../../experiments/remote-peer-probe/device-remote1/lifecycle.log) · [恢复凭据](../../experiments/remote-peer-probe/device-remote1/stock-after.txt)

此前单独用原厂 WLAN 模块完成一次正常重启演练。用明确标记的模拟账本保存前态，普通 Wi-Fi 暂设关闭后正常重启。新 boot 自动取得同 serial 的 ADB 与 KernelSU root，复核磁盘和运行中 WLAN、AMSS 与原生文件哈希，再恢复 Wi-Fi=1、scan=0、CND/HAL=running、PCI=auto 和 native149 待命，最后核对原实验 runner SHA。演练没有制造固件故障；正式 peer 试验通过普通恢复，无需使用重启回退。 [演练结果](../../experiments/remote-peer-probe/reboot-rehearsal/result.json) · [跨 boot 恢复差分](../../experiments/remote-peer-probe/peer-cross-boot-harness.patch)
