# PASSTHRU 单对端 RX peer 与 REO 观测方案

[四轮实测](16-ba-observer-device.md)的 Mac→小米为 1.99–2.20 MB/s。打开观察的两轮分别出现 45、46 个 ADDBA Request 头，raw RX 的 A-MPDU 都是 0。目前首先要弄清这些请求是否来自测试 Mac、是否有目标 peer 映射，以及现有驱动是否尝试了 REO 配置。同方向的 MT7612U 硬件 BA 对照在固定 149 把 30 MB 从 47 秒缩短到 14 秒，后续重排将越过空洞的 TCP 段比例从 57% 降至 13%，但单次总传输时间未变。AX201 选主 metric 550 单变量没有复现收益，本机扩大 TUN 队列也没有提高已测发送平均。RX 观测优先于再次调选主、TUN qlen 或 TX 发射间隔，因为这些调整无法回答当前 Mac→小米的聚合前置条件是否存在。 [Owly #58](https://github.com/t4t5/owly/pull/58) · [Owly #59](https://github.com/t4t5/owly/pull/59) · [Owly #81](https://github.com/t4t5/owly/pull/81) · [本机队列对照](14-owly-houji-device.md)

## 最小候选

沿用独立模块、默认关闭、仅装载时指定一个 Mac AWDL 地址。保留原计数作为参照。事件仅在已验证的 PASSTHRU vdev 内归属。遇到 vdev 关闭、peer 解映射或同 ID 复用，退役旧标识。输出标量计数、范围与是否可关联，不输出地址、负载、内核指针或 DMA 地址。首版只需做下面表中的前两行与 REO 同步观测。异步完成必须能随已有命令记录绑定身份，分开审计。

| 层 | 从现有路径读取 | 可证伪的判断 |
| --- | --- | --- |
| raw RX | 保持已有 FCS/线性/长度检查。在完整、未保护的三地址 BA Action 中核对 TA 是指定 Mac、RA 是当前 vdev MAC。按 Request TID 和窗口记录，截短、不可归属、匹配头无可验证 body 单独计数。 | 全局 Request 大于 0 而匹配 Request 为 0，说明当前原始计数不足以说明测试 Mac 正在请求。匹配且 TID 不为 0，则否决直接以 TID 0 做首个协商试验。 |
| HTT peer、RX_ADDBA | `dp_rx_peer_map_handler` 的事件到达与 `dp_peer_find_add_id` 成功分开计数。用活着的 vdev 核对 PASSTHRU，分开 self、allowlist remote、其余 peer。只在已持有真实引用时读取成功对象，在移除映射前记录 unmap。普通与扩展 RX_ADDBA 都经过同一处理函数。只在按 ID 拿到当前有效、地址匹配的 peer 后记录 TID 与窗口，查不到对象的事件留作未归属。 | 同轮有匹配的 raw Request、已见 self map 而一直无 remote map，证伪现有路径已经为该 Mac 建立映射的假设。出现 remote map 事件而无成功 host map，区分固件通知与宿主对象建立。remote map 正常而没有可归属 HTT RX_ADDBA，则协商通知链还未被观察到。 |
| REO 现有同步路径 | 在已有 peer 引用下分别记录默认 route、目标 TID 的 qdesc 存在性、setup 走向和已有 REO update 提交结果。初始单包 qdesc、窗口大于 1 的更新、WMI 或共享 QREF 分开计数。 | qdesc 存在或软件窗口大于 1 只说明宿主侧状态。若同一 peer 无 route 或没有成功提交，不能进入受控 BA。 |
| REO 异步状态 | 仅在后续补丁能将不可变 `soc/vdev generation/peer map generation/TID` 快照随现有 `dp_reo_cmd_info` 发布，才按现有 command number 配对。保留原锁顺序、引用和 handler。区分成功、失败、DRAIN、身份过期和没有配对。首版不从回调的裸 `rx_tid` 反推 peer。 | `HAL_REO_CMD_DRAIN` 是清理信号。真实 REO SUCCESS 与当前 peer 世代相符才计入该会话，且仍需独立空口验证 BA。 |

源码定位采用当前宽带重建树。原始 peer map 计数在实际查找之前，`dp_peer_find_add_id` 对已逻辑删除或重复映射对象可返回空。raw RX 持有 vdev 引用。REO 提交先入硬件环，已有回调记录分配失败时可返回 `E_NOMEM`。状态环按命令号查现有记录，销毁路径在锁下发 DRAIN。因此异步状态须依靠现有记录的身份和释放边界，不建立旁路队列去推断配对。 [dp_peer.c 2655、2875、3017](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_peer.c#L2655) · [dp_htt.c 3580、4134](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_htt.c#L3580) · [dp_rx.c 1135](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_rx.c#L1135) · [dp_rx_tid.c 262、353、749](https://github.com/MiCode/vendor_qcom_opensource_wlan/blob/a4da9e3b43864da13bc978b8cb6385ba5332a4af/qca-wifi-host-cmn/dp/wifi3.0/dp_rx_tid.c#L353) · [dp_reo.c 87–196](https://github.com/MiCode/vendor_qcom_opensource_wlan/blob/a4da9e3b43864da13bc978b8cb6385ba5332a4af/qca-wifi-host-cmn/dp/wifi3.0/dp_reo.c#L87-L196)

## TX 参照范围

四轮原有 TQM 观察均开启。按开始至结束快照扣除启动基线后，每轮有 49,618–50,854 个完成记录，仅见 `FRAME_ACKED` 和 `REM_CMD_TX`。前者涵盖普通 ACK 和 BA，后者是软件命令移除已发送 MPDU，不能直接当成空口失败。已有 TQM 分类还包括 aged、drop threshold、link descriptor unavailable 等。Owly 的 `EAGAIN/ENOBUFS` 属于 host 提交背压，不在同一统计阶段。 [TQM 观察器](../../experiments/wideband/dp_pt_rate_observer.h#L15) · [HAL release reason](https://github.com/MiCode/vendor_qcom_opensource_wlan/blob/a4da9e3b43864da13bc978b8cb6385ba5332a4af/qca-wifi-host-cmn/hal/wifi3.0/hal_tx.h#L305) · [Owly 提交](../../experiments/owly-evaluation/houji-runner/src/android.rs#L218)

Apple phone 的独立只读调查看到 Apple IOReportLegend 定义了 AWDL airtime/snapshot 与 TX complete 类别，包括 peer unavailable、buffer unavailable 和 lifetime expired。当前只确认通道定义。将来若能读出实测值，aging 与 lifetime、descriptor 与 buffer 可作为各自平台的资源和时限线索。两者的计数单位、时钟、作用域、状态来源尚无映射，peer unavailable 也没有现成的 PASSTHRU 同义计数。本轮只保留既有 Qualcomm TX 数据，不新增 TX 钩子。

## 验收边界

在 `build-host` 校验构建、符号、CRC 和关闭默认值。先离线覆盖无地址、错误地址、截断、TID 解码和映射 ID 复用。随后独占原实验锁，在 iPhone 只读工作协调结束后才考虑实机。保持 149、RX BA=false、同一 PHY/MSS/A-MSDU 配置，每轮冷启动并双向各 64 MiB SHA。先核对 on/off 读回、self map 正对照、远端帧归属以及恢复。若观察到匹配 raw Request 而无 remote map，下一步定位 PASSTHRU peer 创建和固件完成能力。若 map、route 和 REO 已有可核验进展，再按单 peer/TID 设计受控协商。真正 BA 的验收仍需要独立接收器验证同 PPDU 内多个 MPDU、匹配的 Block Ack 位图和交付顺序。 [聚合路线](15-aggregation-roadmap.md)

## 单对端实机结果

已在 `build-host` 构建并核对交付模块。两份已知 WLAN 参照的 567 项导入及 CRC、导出、vermagic 与 module 结构一致，观察和地址配置默认均为 0。离线向量覆盖地址、长度与 FCS 边界、后续碎片、TID/窗口/SSN、重复或退役 ID，以及二次 vdev 生命周期。交付模块推送前后 SHA-256 一致。 [构建和检查](../../experiments/ba-peer-observer/README.zh-CN.md)

在相同 149/VHT80-SGI、A-MSDU、正常 ACK、MSS 1160、qlen 500、RX BA 关闭下，双向各 64 MiB 均通过两端 SHA-256。Mac→小米为 **2.021 MB/s**，小米→Mac 为 **5.634 MB/s**。这是一轮诊断数据，未改变已有吞吐上限。 [结果与快照](../../experiments/ba-peer-observer/device-summary.json)

| 验证项 | 读数 |
| --- | --- |
| 两份快照的观察器、地址配置 | `Y`、`1` |
| vdev 生命周期、self map | 1、1 |
| Mac 发给小米的完整 ADDBA Request | 48 个，全部 TID 0 |
| 最后一条匹配请求 | 窗口 64，SSN 2840 |
| 地址不符、长度不足、FCS 不确定、身份歧义 | 均为 0 |
| Mac 的 HTT remote map 到达、宿主 map 写入、unmap | 均为 0 |
| 可归属的 HTT RX_ADDBA、REO qdesc 或更新提交 | 均为 0 |
| 两快照之间 PASSTHRU raw RX | 62,693，全为非 A-MPDU |

这轮确认 Mac 正在请求 TID 0 BA，且 self map 钩子已工作。现有 PASSTHRU 路径没有观察到该 Mac 的远端 peer 通知或 ID 映射。结果支持继续追踪此角色是否发出 remote peer 创建命令、建立默认 route，以及能否得到可核对的固件回执。现有路径可能没有发起创建，所以不能将无映射解读为固件拒绝。未启用 BA、未验证 REO 异步完成或真实空口 Block Ack。

轮次退出码为 0。退出后与最后一次只读复查均核对库存 WLAN build ID、固件与原生文件哈希。普通 Wi‑Fi 已连接，原生 149/MCS3 supervisor 与 Binder 已恢复，实验接口和规则已清理。 [最后一次恢复复查](../../experiments/ba-peer-observer/device-final-stock.txt)
