# 远端 peer 降速源码核查与最小隔离实验

工作基线：three-findings-validation，模块已验证 SHA `7c647a0bb3c570f1bd2a0527c92298476ab26d96d90cc3de6a837a0c2125e10c`。本目录独立复制并修改 `modified/dp_tx.c`，未改共享实验树、未操作手机或远程构建。结论依据 C 源码及既有测试回执，没有宣称唯一根因。

## 最重要的源码事实

1. `src/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_tx.c:3277–3288`：unicast 且 RA 匹配已关联远端时，从 `pt_remote_peer_lookup_ast()` 得到映射 AST；最终仍以 `HTT_INVALID_PEER, NULL` 调用单 MSDU 发送。即远端路径没有把 HTT metadata 改成显式 peer-based，它通过 TCL AST 选择远端。
2. `dp/wifi3.0/be/dp_be_tx.c:1323–1331,1371–1380`：未覆写时使用 vdev 的 BSS AST index/hash；覆写后直接使用远端 index/hash，并把 hash 低 4 位写入 cache set。
3. `dp_be_tx.c:1579–1588`：当 search_type 为 INDEX 时，PASSTHRU 已经在 bank 配置里设置 `index_lookup_enable=1`。不能简单把没调用 per-descriptor index_lookup_override 判断成 bug；bank 路径已经明确支持索引查询。实际 bank/search_type 仍需运行时证据。
4. `qcacld-3.0/core/wma/src/wma_dev_if.c:2647–2667`：关联完成后的 AST lookup 仅一把 spinlock、检查 psoc/vdev/RA 并复制 index/hash，没有显式排队或睡眠。完成统计中的 remote_peer_active_id 只是 READ_ONCE。源码没有支持“每包等待关联确认”的说法。
5. `wma_dev_if.c:2873–2906,2960–3001`：当前远端创建使用 DEFAULT / WLAN_PEER_STA，关联参数手工填写 HT/VHT、QoS、NSS、速率集。创建/关联本身、由远端存在引发的 RX 分类、以及 TX 选择 remote AST 三个变量同时改变，现有 remote_assoc 开关不能隔离它们。
6. `wlan_hdd_passthru_control.c:430–455`：固定速率设置与软件重试阈值设置作用于 vdev；remote_assoc 两组都执行同样路径。不能仅凭有效完成 MCS9 确认失败包的 rate/NSS 相同，也不能把状态2直接当作重试超限。
7. 已拿到的三星公开 `wlan_hdd_wondertap.c` 在 376–403 行注册并授权的是 adapter 自身地址；本文件搜索不到 set_station_info/remote peer assoc 实现。不要把 EZIF 的具名远端接口或旧文档叙述直接归给此 DZF2 公开文件。

## 已准备的最小试验

补丁：`remote-ast-toggle.patch`，相对 `three-findings-validation` 根应用，已 `git apply --check` 通过。

新增只读模块参数 `pt_remote_ast_override`，bool、0400，默认 true；只控制 `dp_tx_send_passthru_internal()` 的远端 AST lookup/override，默认行为与已验证模块一致。

建议矩阵：

| 组 | remote_assoc | remote_ast_override | 用途 |
|---|---:|---:|---|
| A | 0 | 1 | 无远端 peer 基线 |
| B | 1 | 1 | 当前慢路径 |
| C | 1 | 0 | 保留远端关联，仅禁用逐包 AST 覆写 |

其他参数固定，先 1 MiB 预检，随后 16 MiB 交叉顺序。C 组必须读回 sysfs `N`，并检查 `ast_overrides=0`；还须检查 `remote_peer_compl` 和 `pt_ampdu_peak.last_peer_id`。固件可能按 RA 再次解析 peer，不能仅由开关推断流量已走 self。

解读：

- C 的完成 peer 回到 self 且吞吐恢复：主要影响可缩小到远端 TX 队列/descriptor 选择，弱化“存在远端对象本身导致下降”。
- C 仍完成到远端：此开关未真正隔离硬件路径，不能得出性能归因。
- C 完成到 self 但仍慢：检查关联造成的 RX/TCP ACK 路径变化、对象/固件状态，不能继续只盯 TX AST。
- C TX 不完成或出现异常：有界停止恢复，保留证据，不扩大发包。

审计建议：新增参数必须在 new_params 内检查0400权限，并单独检查默认字节 `01`；不能加入全零 default_off_flags。harness 增加明确环境值及 sysfs Y/N readback；同时更新组装 SHA。无需新增内核导出符号，仍执行原567导入/CRC/vermagic/PLT-GOT审计。

## 若 C 仍不足以定位

下一版低风险观测应在同一模块内对两组同时启用：按 descriptor cookie 记录成功入环至 TQM completion 的单调时钟延迟、在途计数/峰值、提交失败与cookie碰撞/缺失。不要仅用累计 tx_ring 减 completed 当精确队列深度：两者覆盖的失败/完成域不同。timestamp 应在交给硬件前登记，enqueue失败撤销，避免 completion 先于登记的竞争；释放/超时路径也必须清理。观测开销与锁争用需要成对对照，不能先改发送策略再解释延迟。

当前并不建议直接改 peer 能力位、关闭省电或强开 AMPDU：这些都会增加变量，且没有已有数据证明是唯一根因。

## 用户授权恢复汇编阅读后的补充

用户随后明确允许查看汇编。只将 EZIF 样本复制至本目录 `inputs/samsung-ezif.ko` 后定点读取，未改原始样本。

- 既有 EZIF `dp_tx_send_passthru` 在 `0x40aea4/0x40aea8` 设置 peer_id=0xffff、exception metadata=NULL；其 enqueue 在 `0x3a2ba4/0x3a2ba8` 从 vdev+0x4e/+0x4c 读取 index/hash，`0x3a2bf8` 合并 hash 低4位、`0x3a2c0c` 写入 descriptor。直接路径没有查 RA 并覆写 mapped remote AST 的对应证据。
- **后续已追到上层 DA lookup，不能宣称原厂没有 remote index 覆写。** `dp_start_xmit_passthru:0x149988` 调 `qdf_nbuf_ieee80211_get_dest_mac`；`0x1499f0` 间接调用 `dp_get_info_by_peer_mac`（由 `.data dp_ops_peer+0xb8` 重定位及 KCFI 类型 `0xd0f61853` 双重确认）。返回结构+9非零且+10索引非0xffff时，`0x149a30` 将索引低8位写 skb+0x3e，`0x149a38` 置 skb+0x3f bit0；enqueue `0x3a3018` 读取该标记索引。这条分支才是早先只看 dp_tx_send_passthru 所漏掉的上层路径。
- `disasm/ezif-peer-info.txt`：`dp_get_info_by_peer_mac:0x3c73f8` 检查 vdev opmode=7，`0x3c7404` 检查 peer+0x204 非零才输出有效标记，返回索引取自 peer+0x1f0。该索引的准确成员名及+0x204 gate意义尚待 HTT map/peer setup 写入交叉核对，不能仅按偏移猜。
- 已看到的 enqueue 条件分支仅替换 index（w27）；hash（w28）仍来自 vdev+0x4c，再于0x3a2bf8把低4位合入descriptor。当前实验替换 index 和 remote hash，因此**有效性gate、hash来源、索引8bit截断**是具体差异。二进制结构不同，不能直接复制偏移。
- `disasm/ezif-peer-caps.txt` 对 `__wlan_hdd_populate_caps_peer_setup_req` 的定点分析显示：函数按调用者能力标记拷贝26字节 HT、12字节 VHT、17字节 HE 信息，并从 MCS map 推导空间流数及 MCS。当前实验的手填 peer_assoc 参数并非与真实对端能力逐字段核对后的等价实现；这是后续候选变量，未证明造成当前降速。

## 根代理提供的 AST-off 首轮回执（初步）

保持远端关联成功，仅禁用 AST 覆写：16 MiB 接收 4.001 MB/s、发送 8.219 MB/s，双向 SHA 通过；completed=5414、ast_overrides=0、remote_peer_compl=181，聚合观测仍0。96.7%的完成不计为远端，伴随发送恢复；还需 on/off2 复现。

181次远端完成的具体来源尚不能判定。现有observer不记录frame type及逐包方向，不能直接断言都是管理帧或TCP ACK。`last_peer_id=1`仅最后一次完成，不代表主体流量；也不能因off开关就称100%走self。可能包括控制/管理、固件再次按RA分类或方向边界流量，需补采才能区分。

根代理随后完成 on/off2：on 发送2.281、接收6.252 MB/s，completed=5500、remote=5500、ast_overrides=5219；off2 发送5.747、接收9.599 MB/s，completed=5255、remote=130、ast_overrides=0。均SHA通过并恢复原厂。保持关联而关覆写的两轮发送均明显高于on，支持TX选择路径是可隔离的额外代价；未证明是remote queue本身、cache hash、gate还是固件调度哪个环节。

下一步最小字段核查顺序：先只保留remote index并使用BSS hash，与当前index+remote hash比较；同步记录实际BSS/remote index/hash（至少打印一次，不在每包日志中泄露地址）。再确认EZIF peer+0x204由何事件置位，以及peer+0x1f0是否HTT map提供的ast_idx。只有这些字段确认后才考虑复刻其gate；不要把8bit截断当作通用修复。

## 后续 hash 单变量补丁（待根代理集成实测）

`remote-hash-toggle.patch` 对当前实验目录 `git apply --check` 通过，仅修改独立副本，未构建或操作手机。

- `pt_remote_hash_override` bool / 0400 / 默认 true：既有 `pt_remote_ast_override=1` 时，true 保留当前 remote index + remote hash；false 为 remote index + BSS hash。remote AST override 关闭时此参数不起作用。
- `pt_ast_selection` 0400 回调：首次进入 remote-index 分支捕获 `vdev,bss_index,bss_hash,remote_index,remote_hash,selected_index,selected_hash,selected_cache_set,hash_override`。`valid=0` 表示本模块周期尚未观察到此路径。快照表示 descriptor 选择输入，不证明实际入环成功或完成 peer。
- 首次捕获与 sysfs getter 受 spinlock 保护；稳定路径仅检查 valid，无逐包日志。记录在模块重载时归零，期间不会覆盖，因此只适用于本次固定 peer 的试验。
- 构建审计：两个参数权限均0400；bool 默认字节01需 expected-on，不可放入 defaults-off；快照回调不属于bool默认审计。
- 建议同模块同关联，AST override=1 固定，hash on/off/on；均保存 sysfs快照与完成peer分布、吞吐和SHA结果。若 BSS hash 低4位等于 remote hash低4位，则此切换没有改变硬件cache_set，不能据无差异排除索引路径问题。

## EZIF 索引来源交叉核对（已确认）

`disasm/ezif-peer-info.txt` 中 `0x3c73ec/0x3c73f0` 把 `peer+0x10` 写入 output+2；这是原有软件 peer_id。新增 output+10 从 peer+0x1f0 读取，与软件 peer_id 不同。

新定点 `disasm/ezif-peer-map.txt` 中：入口 `0x3c4a7c` 保存第3参数 w2→w23，`0x3c4a74` 保存第6参数 w5→w22；`0x3c4b70` 将 w23 写 peer+0x1f0，`0x3c4b6c` 将 w22 写 peer+0x1f2；`0x3c4c40/0x3c4c3c` 又分别写 vdev+0x4e/+0x4c。公开 `dp_peer.c:2938` 的函数签名第3参数为 hw_peer_id、第6参数 ast_hash，`2992` 附近对应赋值 `peer->ast_idx=hw_peer_id`、`peer->ast_hash=ast_hash`。这条交叉证据确认 peer+0x1f0 为 AST index，+0x1f2 为 AST hash；output+10 不是软件 peer_id。

所以仍应将低8位截断列为待解释的原厂实现差异，而非未经验证地把当前 AST index 改成软件 peer_id。+0x204 gate 的字段名仍待确认。

## gate 写入与调用者（继续核实）

`disasm/ezif-peer-param.txt`：`dp_set_peer_param_wrapper:0x3bf9e4..0x3bf9f0` 将传入参数低8位非零比较结果写 peer+0x204。跳转表重定位指向 `.rodata+0x6b1ac`，其第10项为0x3d；分支基址0x3bf8f0 + 0x3d*4 = 0x3bf9e4。因此该gate由 CDP peer parameter ID10 设置。DZF2公开 `cdp_txrx_cmn_struct.h:1365` 枚举仅0..6，不能套用公开字段名。

`disasm/ezif-assoc-conf.txt`：在 `wma_peer_assoc_conf_handler` 中，找到待处理req且msg_type==0x1021、非空请求数据、interface存在并且其+0x10字段==0x11之后，0x363ff0/0x364008构造bool1，0x364010调用特化 `cdp_txrx_set_peer_param`。`disasm/ezif-cdp-peer-param-helper.txt:0x364468` 固定w3=10，然后通过 soc ops+8、回调+0x70调用 DP setter。

所以此gate至少已确认由关联确认处理路径置位；此处没有BA协商或service506条件。关联状态在0x364014以后读取，不宜把该gate描述成“仅关联成功才设置”。当前实验在tx_ready关联成功后才允许RA lookup，时机看起来并不比该原厂路径更早；仍需对照失败处理语义，不能把gate差异单独归为降速根因。

## Hash 对照回执与能力参数后续（2026-11-11）

根代理完成 hash0/1：hash0 BSS index54/hash6、remote index2/hash2、selected2/6，TX2.306/RX10.543 MB/s；hash1 BSS index30/hash14、selected2/2，TX2.296/RX6.212 MB/s，双向SHA与恢复均通过。hash0 completed=5512且remote=5512，agg0。该次改变cache_set没有恢复TX；remote index2也不受8bit截断影响。因此本轮不再将hash或截断作为优先修复。

### 已实读的 Mac 历史能力，不冒充当前接收

重新读取 `device-tests/20261110/awdl-actions.pcap` 并复用现有解析器：能力完整的HT TLV为 `00006f001fffff0000`，VHT body为 `36008003faff0000faff0000`（24个）；原pcap尾部有snaplen截断，能力元素在截断前完整。输出 `historical-mac-capabilities.json`。历史对端归属沿用既有 `vhtcap-reference.json` 的Mac样本证据；没有据SUBMITTED日志推断当前Mac。

| 字段 | 手填当前值 | 历史Mac参考 | 解读 |
|---|---|---|---|
| HT caps | 0x01ef | 0x006f | 当前额外声明TX STBC与一流RX STBC；其独立stbc_flag仍为0，不能假设硬件正在用STBC |
| VHT caps | 0x03800030 | 0x03800036 | 当前单MPDU上限3895，Mac宣告11454，Mac还宣告160MHz支持；本实验PHY仍80MHz |
| MPDU density | 0μs | 1μs | HT AMPDU参数0x1f的density编码3经wma表映射为1μs |
| max RX A-MPDU | 65535 | 65535（按HT factor3） | 字段peer_max_mpdu不是单MPDU长度，不能改成11454 |
| NSS / MCS map | 2 / fffa | 2 / fffa | 匹配 |
| RX/TX最高速率原字段 | 867/867 | 0/0 | 0表示IE未提供该额外速率字段，原厂源码按IE原值发送 |
| SGI/LDPC/BW80 | 开启 | 支持 | 未见此处2SS MCS9能力冲突 |

证据：三星公开 `wma_mgmt.c:912` spacing表、`1785–1853` flags构造、`1889–1892` HT因子和density、`1929–1934` VHT rates复制；`wmi_unified_tlv.c:4182–4185`将HT caps/maxRXAMPDU/density/VHT caps直接写WMI命令；`fw-api/fw/wmi_unified.h:22663–22675`明确字段单位与语义。固件如何消费这些字段仍属闭源行为，不由主机赋值本身证明。

当前profile传输统计约3个MSDU/MPDU，未采完整每包长度；不能声称已出现超过3895导致丢包。密度主要影响聚合内间隔，而当前agg0，修正它不等于会恢复4倍速度。手填能力不一致值得修正，但只是候选机制。

### 独立可审查补丁

- `peer-caps-reference.patch`：`pt_peer_caps_profile` uint/0400默认0维持原行为；1为明确固定历史Mac参考，仅2SS可用，否则EINVAL。替换HT/VHT caps、density_us1、RX/TX highest rate0，保持maxRXAMPDU65535、MCS map、NSS、PHY、关联时序不变；setup发送前记录实际WMI输入摘要。组合修正只用于判定能力集合是否有影响，若改善需再拆分单字段，不能直接定位到其中之一。
- `peer-caps-rx-observer.patch`：Monitor已过滤目标transmitter的接收路径，首个完整PSF/MIF各记录一次原始HT TLV和80211容器、UNIX毫秒、目标peer CRC32。CRC32仅用于关联相同目标，非密码学匿名保证；复用已有依赖。解析失败或重复HT/容器不记录。收到第一条后本次运行不再报告变化，因此是起始快照，不证明整轮能力从未变化。
- 两补丁均对当前实验 `git apply --check` 通过，尚未独立编译；只改本目录副本。请先profile0取实时接收原始字段，核对目标和时间及完整VHT body与参考一致，再profile1对照。新参数审计为uint0400默认0。

### PAUSE/调度边界

当前自建关联在 `wma_dev_if.c:3039` 调用 `cdp_peer_state_update(...AUTH)` 成功后才设tx_ready；assoc.auth_flag=1且APSD/static/dynamic MIMO-PS均未启用，spatial_mux与Mac HT SMPS no-limit相容。没有源码证据表明当前自建函数在逐包host pause，也没有队列时延数据证明固件peer被pause。若能力修正仍2.3MB/s，应优先增加同模块成功入环→完成延迟/在途峰值，再区分生产速率与固件peer调度；不要把关联后低速直接解释为省电。
