# EZIF 首轮失败：RX 契约只读审计

对象：官方FWD EZIF `amss20.bin`（37d40904…b522f）、官方EZIF ko（8b3694f6…8a0022）、当前three-findings源码与 `runs/ezif-firmware-20261111-retry/failure-dmesg-full.txt`。本轮未改共享源码、未部署、未操作手机。实测由根代理执行并已恢复STOCK_RESTORED。

## 已确认结论

1. wire506已实测为1，不能继续用“固件没有声明此能力”解释失败。但506成立不代表旧宿主RX路径自动兼容，更不是TX多MPDU已验证。
2. 日志14268行：`dp_rx_err_exception: rate threshold breached! reo_err_msdu_buf_rcved 13`；下一行host主动触发reason25。该错误的直接含义是REO异常环收到**MSDU buffer地址型描述符**，旧处理路径预期MSDU link descriptor，把这种形态计作错误。不能解释为普通重传、BA窗口空洞或固件崩溃的直接证据。
3. DZF2公开dp_rx_err.c:2001–2036的阈值为累计>20包，或5秒窗口内>10包。此次13包、约5秒后恢复与该分支相符；旧路径还会释放接收到的nbuf，能解释用户态rx_action/data均为0。真实运行源码对应行号需以目标编译树为准。
4. 官方EZIF新增了两条旧公开源码没有的函数：`dp_rx_is_passthru_msdu_buf`(0x3d87c8)及`dp_rx_err_handle_passthru_msdu_buf`(0x3d82c8)。其后者调用`dp_rx_deliver_raw_passthru`(0x3d86c8)。该区域在官方ko与已有移植ko的机器码完全相同，因此既有ezif-all.txt可用作逐指令证据。

## 官方入口分流证据

`dp_rx_err_process` 0x3d8b04–0x3d8b0c检查buffer type是否为1；非link descriptor跳0x3d95d0：

- 0x3d95d0读取已解出的mpdu描述信息结构`sp+0x88`的第11字节，并测试bit6；真则直接调用PASSTHRU handler。
- 否则0x3d95e4调用`dp_rx_is_passthru_msdu_buf(soc, &mpdu_desc_info)`；返回真也调用该handler。
- handler返回值在0x3d95f8与1比较，值<1绕过通用exception；否则进入0x3d9608通用`dp_rx_err_exception`。

**边界**：第11字节bit6按现有结构布局对应peer metadata bit30，但EZIF的具名字段/定义尚未核实，不应直接把猜测常量写入实现。handler返回值看起来沿用lmac/pool ID与回补计数协议，不能改写成普通成功/失败bool。

辅助predicate从mpdu信息+8取得peer metadata，经soc函数指针解析peer id，再查peer、处理关联对象/txrx peer并检查vdev opmode==7；查不到对象返回false并限频记录。具体MLO对象转换与ref API配对须用目标版本接口复核，不可照抄结构偏移。

## handler中的所有权要求

官方0x3d82c8–0x3d87c4可确认以下步骤：

1. 通过HAL从ring desc提取buffer地址/cookie，经soc cookie→rx_desc回调取得描述符；无rx_desc/nbuf有失败返回。
2. 取pool_id，持有RX SMMU mapping锁，检查unmapped标志，执行IPA映射处理与DMA unmap，然后标记unmapped并释放锁。DMA解除映射必须恰好一次。
3. 从原nbuf数据取RX TLV，用HAL调用取长度/偏移，调整skb长度。确切HAL函数及padding语义待核对，必须采用目标具名HAL helper，检查分配容量及TLV长度边界。
4. 清空rx_desc->nbuf、清除in_use，将rx_desc归还相应pdev free list；原nbuf此后归交付路径所有，不能再由通用exception释放。
5. 在pdev vdev列表锁下找到PASSTHRU vdev；无vdev时释放nbuf；有vdev时解锁、调用dp_rx_skip_tlvs，再传给dp_rx_deliver_raw_passthru。移植实现应采用当前宿主已存在的vdev引用接口/交付wrapper保证并发teardown安全，不能裸指针跨解锁。
6. 返回值控制外层reaped计数/补充RX buffer和是否继续通用exception。必须确保每个descriptor只回补一次、每个skb只消费一次。

因此不得通过提高错误阈值或关闭reason25恢复掩盖此缺口：那只会延迟恢复并持续丢RX。

## 506→RX默认路由也需对齐，但不是唯一补丁

已确认官方506→soc能力字段(soc+0x48e8)→PASSTHRU hash=false/REO dest=0：`dp_vdev_get_default_reo_hash`0x3cb248–264，`dp_peer_setup_wifi3`0x3cb358–374及0x3cb3bc–3dc。

当前wma_dev_if.c:2944–2957在remote_reo=0时完全跳过cdp_peer_setup，本轮reo_setup=0。当前dp_rings_main.c:2397起仅remote_reo=2时强制hash0/dest0；但mode2还将dp_rx_tid.c:1278的TID0窗口设64。因此**不建议仅换reo2再次上机**：它没有补新增MSDU-buffer交付handler，而且把路由和REO队列窗口绑在一起。

## 下一最小实现与验证边界（本轮不实现）

先做可审查的完整RX适配片段：识别PASSTHRU MSDU-buffer → 单次unmap/descriptor归还 → 使用当前安全raw交付wrapper → 外层计数/回补；以具名capability缓存及具名enum对齐默认RX路由。保留非PASSTHRU通用异常处理和恢复阈值，失败分支必须明确是否已消费skb/descriptor。

首轮仅验证启动、AWDL action/少量数据接收与干净恢复；具名计数至少覆盖buffer类型、predicate匹配原因、cookie解析失败、unmap/回补/交付/丢弃。出现普通routing错误计数增长就停止诊断并恢复，不进入大流量或聚合性能结论。RX闭环稳定后才继续ADDBA与TX A-MPDU验证。当前证据不能断言补完handler即保证TX聚合成功。

### 当前实际编译树补充核对

只读build-host `/work/houji-build/src/three-findings/wlan/qca-wifi-host-cmn/dp/wifi3.0/`：dp_rx_err.c:55–57为20/10/5秒阈值；1901是实际exception；2121–2124对非MSDU_LINK直接进入通用exception，确实缺少EZIF分流。dp_rx.c:1142的现有raw wrapper明确要求真实DP vdev引用，OSIF一旦调用即消费nbuf（即使OSIF返回错误），调用前失败仍由caller拥有。它还拒绝nonlinear/fragment/continuation、要求chfrag_start/end及TLV在headroom中、RAW decap。新handler必须建立这些CB/headroom状态或定义适配接口，不能只把裸skb直接塞进函数。
