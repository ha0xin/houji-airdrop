# TX A-MPDU 启用链调查

2026-11-11。只读源码和已有测试数据；未操作手机、未改共享源码、未构建驱动。三星公开DZF2对应源码、EZIF移植模块及DZF2运行固件不可互相代替能力证明。

## 可以确认的缺口

1. 实验 `qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c:361–394` 的自定义聚合命令仅设置 **上限**。三星 `fw-api/fw/wmi_unified.h:8393–8416` 明确它是 max MPDUs per A-MPDU，不能由64推出已建立TX BA会话。
2. 同实验文件450–455行：`pt_tx_ampdu=0` 时同时禁用SW重试，=1时跳过禁用。这是聚合上限与重试策略耦合，必须隔离，否则吞吐变化不能归因A-MPDU。
3. 实验 `wma_dev_if.c:3032` 附近在ASSOC_CONF后立刻发`WMI_ADDBA_SEND_CMDID`，尚在`cdp_peer_state_update(...AUTH)`之前；hdd finish_start尚未返回，owly尚未完成AWDL发现。命令成功只证明WMI入队，不证明对端在有效时隙收到请求。
4. 用户态 `owly-awdl/src/node.rs:426–438` 对响应检查status和当前token，然后只设置`peer.tx_ba_active=true`，没有将协商结果交给驱动/固件。响应TID/window/policy也未在此验证。`node.rs:915–947` 重试会递增token；`houji-runner/src/android.rs:219–245`只打印第一个请求和第一个响应，所以日志token1→2并不证明状态机接受了不匹配响应。
5. DZF2能力位506本轮未声明，不代表普通BA安装接口必然不存在，也不允许伪造能力位代替真实协商。

## 更接近“安装BA会话”的候选接口

三星 `fw-api/fw/wmi_unified.h:26389–26432` 定义 `WMI_PEER_TID_ADDBA_CMDID`：vdev_id、peer MAC、tid、initiator、window_size、ssn、timeout、policy。注释规定 initiator=1；ssn仅对initiator有效；policy immediate=0/delayed=1；timeout=0无超时。该policy编码不能直接照搬802.11参数位。

`wmi_unified.h:2203–2209` 明确 `WMI_TX_ADDBA_COMPLETE_EVENTID` 是该initiator命令的完成事件，回执有vdev/peer/tid/status。TLV schema在`fw-api/fw/wmi_tlv_defs.h:2582–2585`。

**重要限制：在完整DZF2源码树搜索只找到头文件/schema，没有该命令的C调用实现。未证明三星Wonder使用此接口，未证明当前目标固件支持，未核验EZIF数值对应。不能直接按公开头文件构造TLV就上机。**

## 最小下一步

1. 保持速率、重试策略、AST路径不变。将固件ADDBA触发延后到owly就绪、peer可达、有稳定数据时隙之后；对每次请求记录MAC/TID/token/SSN与匹配响应，超时/拒绝单列。
2. 用户态补齐响应TID、窗口与policy校验，打印“已匹配并接受”回执；首个REQ和首个RESP日志不能作为协商证明。
3. 先核验目标fw-api的命令/事件/TLV定义以及当前固件对应实现证据。若证据充分，才在成功协商后安装initiator BA状态并等待匹配complete，显式保持用户态与硬件SSN一致；失败则DELBA并恢复。不要同时让固件自动协商和用户态手动协商竞争。
4. 分离tx_ampdu聚合上限和no_sw_retry两个开关，实验默认值维持已有行为，做单变量对照。
5. 观测需包括BA安装结果、每peer/TID/PPDU分组、队列在途深度及完成延迟。现有相邻completion的ppdu_id分组不是独立空口证明；Mac HighBand全局计数也不能隔离实验peer。

## 一手外部参考

- Linux mac80211注入文档说明NOACK的语义，但本项目是vendor PASSTHRU路径，不能直接套用mac80211行为：https://kernel.org/doc/html/v5.19/networking/mac80211-injection.html
- Linux ath11k调试源码区分ADDBA发送/响应控制：https://github.com/torvalds/linux/blob/master/drivers/net/wireless/ath/ath11k/debugfs_sta.c 。可用作接口角色参考，不能作为WCN7850 DZF2 PASSTHRU支持的证据。

本轮没有提交“开启AMPDU”的补丁：尚缺已验证的BA安装契约，盲目设置窗口、SSN或事件号会把不确定性带入设备。优先交付可验证的协商回执和延后触发方案。

## 独立协商验证补丁

`validate-addba-response.patch` 可应用于实验 owly 根目录，只改 node.rs 的响应接受条件及日志。副本为 `userstate/node.rs`。要求已有请求、匹配token/status、TID0、立即BA、窗口非零且不超过请求、AMSDU不超请求、timeout0才置active；新日志 `OWLY_TX_BA_RESPONSE_VALIDATED` 包含peer及各验证字段。当前请求timeout0，保守拒绝非零返回值；这不是通用BA协议实现，后续支持非零timeout需另设计生命周期。

已通过 git apply --check，且在独立 owly-test 副本编译测试。driver功能73项通过（38+5+25+5）；no-default-features 67项通过（37+25+5）。日志见 test-driver.log 与 test-no-default.log。新增一个端到端测试遍历11组响应，通过真实解析器及后续请求重试行为验证：合法AMSDU、合法无AMSDU、更小窗口、错token、错TID、零窗口、超窗口、延迟policy、拒绝status、未请求AMSDU、非零timeout。首次测试用普通重复MIF触发重试失败，查源码后改为真实outgoing数据触发，最终全部通过。配置核对修正：要求cfg.initiate_addba启用；窗口上限与实际请求的clamp(1,256)保持一致。未集成共享实验、未部署。SSN仍需在发请求时保存给未来固件安装；此补丁不伪装已经建立硬件BA。

额外一手检索：https://android.googlesource.com/kernel/msm-modules/wlan-fw-api/+/refs/tags/android-10.0.0_r0.74/fw/wmi_unified.h 与旧qcacld2 fw-api仍只提供接口定义。本次搜索没有发现可信的Qualcomm下游C调用者，不能由“未找到”断言绝不存在。

## 目标协议核对与可先执行的诊断

已只读核对 build-host 当前目标 `/work/houji-build/src/three-findings/wlan/fw-api/fw/`：wmi_unified.h:1203/2051/25097–25116 同样声明 PEER_TID_ADDBA 与 TX_ADDBA_COMPLETE；wmi_tlv_defs.h:2416–2419、5987–5989存在command/event schema。应始终使用目标具名定义，不复制EZIF数值。EZIF符号表存在 send_addba_send_cmd_tlv(0xec3dc)和wmi_unified_addba_send_cmd_send(0xd2500)，但未建立 PEER_TID_ADDBA 的实际调用证据。

提供 `delayed-addba-trigger.patch`，仅在独立 kernel/wma_dev_if.c 副本新增0200诊断参数 `pt_addba_trigger`。向其写入当前peer generation，在确认active/tx_ready/assoc成功且未删除后，调用已有目标API发送TID0/window64 ADDBA。默认不触发、不改变自动协商/序号，输出PT_ADDBA_TRIGGER，rc仍通过已有pt_fw_addba_rc可读。没有猜TLV；尚未编译，需更新参数审计再构建。必须在测试编排的同一session锁下调用，禁止与stop并发；generation检查不替代完整的并发生命周期锁。

最小诊断流程：pt_fw_addba=0启动；等待OWLY_PEERS与双向小流量成功；用户态主动ADDBA关闭，避免两个发起者竞争；记录收到的BA action，再写generation触发固件请求。观察REQ是否真正到达Mac、是否收到同token成功RESP及后续数据聚合。此模式由固件管理握手，不把用户态active当硬件状态。若仍无REQ/RESP，记录时机失败而不是直接安装猜测状态。

用户态协商后安装BA的进一步设计必须冻结TID0数据（仍允许AWDL/action/握手），记录request_ssn、等待匹配成功响应，再发PEER_TID_ADDBA并等complete后释放数据；超时应DELBA/清状态并恢复原非聚合路径。当前node在请求期间仍发数据、递增tx_qos_seq，最初request_ssn会过时。Kiwi v2硬件 tx_raw_or_native_frame_setup.h:44/63–65有sequence_control_mask/source/number，但未建立PASSTHRU实际固件配置，不能假定TQM接管后用户态序号仍会被遵守，也不能仅安装一次SSN就宣布序号闭环成立。

## EZIF 能力声明与固件版本分界（追加复核）

延迟触发补丁目前禁止集成：自动lease超时/recover也可在状态快照后销毁vdev，测试编排锁不能解决内核生命周期竞争。必须改用已核实串行WMA路径/同步保护后才能运行。

DZF2公开源码中，wonder/mac80211.c 的 wonder_ampdu_action仅日志并返回0；wondertap.h声明ampdu_enable但完整WLAN源码未找到消费该字段的实现；HDD get_capabilities也未设置AMPDU bit7。这说明此份公开源码不能直接提供完整TX AMPDU实现，不能据此推断固件绝无相关能力。

EZIF调用链现已闭合（证据 ezif-all.txt、ezif-capabilities.txt、ezif-start.txt）：
- wma_rx_ready_event 0x37aac8以host338查询wmi_service_enabled，0x37aafc存结果于sp+0xe1。targetcfg基址sp+0x88，services偏移0x1c，所以对应services+0x3d。
- hdd_update_tgt_cfg 0x1faa40取targetcfg+0x1c传hdd_update_tgt_services；后者0x1fb498读取services+0x3d并置hdd_ctx+0x21f8 bit1。
- __wlan_hdd_get_wondertap_capabilities 0x5cce98测试此bit1，真时0x5cceb0将0x81 OR进特性位，即rate_adaptation bit0与ampdu_aggregation bit7。
- host338映射wire506及另一EXT2→DP配置路径参见独立对照报告。
- __wlan_hdd_start_wondertap_intf实际仅测试init+0x26的bit3与bit2（后者选择rate mask替代固定速率），未发现读取旧ABI ampdu_enable bit1或显式发送ADDBA。因此“只设置init ampdu_enable即可开启”没有证据。

本地固件重新SHA256核对：
- reference-runtime/dzf2-blob-amss20.bin 与对照inputs/dzf2-amss20.bin均13f01cb46efbe498c854352bfc253fa3665c14a14c37a0a6ed6a609a48a8709c。
- modem.img.amss20.bin为37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f，版本c3-01013，与DZF2不同。但它来自小米移植包modem.img，不能仅凭同包证明是三星EZIF vendor WLAN固件；移植包可能保留目标设备modem。

下一步应优先获取身份可核对的三星EZIF vendor/firmware/kiwi/amss20.bin，先只启动观测原始506、版本、兼容性，再进行已有peer/TID路径的短测；不是对DZF2不断调聚合上限。项目docs已记录原始约20GB分区镜像被清理，本地未找到完整EZIF固件包。公开目录存在EZIF完整包但未下载、未检验： https://www.sammobile.com/samsung/galaxy-s24-ultra/firmware/SM-S928B/EUY/download/S928BXXU6EZIF/2087951/ 。目录信息只能用于寻找样本，不证明其内核/固件与移植件一致。

## 官方EZIF样本已取得（Samsung FUS，2026-11-11）

获取过程与证据见 `firmware-acquisition/ACQUISITION.md`。使用Azkali/samloader Smart FUS实现从Samsung官方端点查询并Range下载FWD构建，target AP精确EZIF/OXM6EZIF；完成super LZ4内容校验和EROFS提取。

关键结果：官方 `vendor/firmware/kiwi/amss20.bin` SHA256 **37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f**，与项目原有modem.img.amss20.bin一致。这一候选现在有独立官方来源证明，可以交主代理作为“仅固件版本变量”运行wire506探测；本代理未部署。

官方ko SHA256为 **8b3694f692d0acdd59f0dcab864d5de0c59c0c9e59d73e93254512a4878a0022**。移植包ko有.text尾部4096B追加、正文0x45e904–0x45f114共22字节差异，且12个__versions CRC修改；并非原样原厂模块。其余section内容一致。现有AMPDU相关函数不在差异范围，机器码相同，因此本报告对这些函数的分析可与官方样本交叉确认。

样本位于 `firmware-acquisition/official-ezif-artifacts/`。该时间点完整AP ZIP CRC仍在读取userdata尾部，不能声称整包MD5/CRC验证完毕；已取得super/分区/目标文件各层证据不依赖未读取的userdata内容。

最终更新：AP流已完整读取并通过ZIP CRC32=3666200036及长度21744404603B核对，AP tar SHA256=09a16cb9055b8c82e02b9b67635971171ee134ca45d5356c82961defd1e7fe45。此前“AP CRC待验证”状态已解除。目标样本与证据本地/远端保留，独立大镜像中间件已安排清理。
