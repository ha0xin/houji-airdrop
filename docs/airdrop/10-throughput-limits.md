# 2026-11-10：约 2 MB/s 的限制与扩展实验

用户要求取消下一轮原生互传人工验证，先在小米与 Mac 之间自动排查；达到大于 10 MB/s 后再请求用户协助。当前未达到此目标。

## 已证实的源码限制

`kernel/wlan-overlay/qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c`：

- `hdd_pt_control_validate()` 只接受 HT、20 MHz、NSS1、MCS0–7，并拒绝非零 AMPDU/AMSDU enable。
- `hdd_pt_control_submit()` 将信道宽度及 PHY 请求固定为 20 MHz / 11n。
- `hdd_pt_control_finish_start()` 将发送编码固定为 HT/NSS1，WMI chwidth 为0，peer 带宽为20 MHz。
- `hdd_pt_control_single_tx()` 对发送聚合类别均设置 `tx_aggr_size=1`。因此初始化日志中的 AMPDUCFG 成功不是“聚合已开启”的证据。
- runtime 的 capabilities 清零，不宣称动态速率、聚合或共存能力。
- 数据路径使用 raw 帧、`HTT_INVALID_PEER`；启用真正的聚合还需核对 peer/TID、Block ACK、序号和完成处理，不能简单将 1 改成 64。

以上在本地固定源码及[公开上游 control.c](https://github.com/YoWenhow/Xiaomi14-PixelOS-Airdrop/blob/main/kernel/wlan-overlay/qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c) 均可核对。它们是适配实现的边界，不是三星固件能力不足的证明。

## 为什么只调 MCS 无法达到目标

单流 HT20 MCS7 的 PHY 速率为长 GI 65 Mbps、短 GI 约72.2 Mbps，分别只有8.125和9.025 MB/s，尚未扣除协议开销。因此该配置下不可能实现大于10 MB/s的有效文件吞吐。来源：[Cisco 速率表](https://www.cisco.com/c/en/us/td/docs/routers/access/1900/software/configuration/guide/Software_Configuration/radio_config.html)。

当前最佳约2.22 MB/s低于PHY上限；单包发送固定开销和AWDL可用时隙是候选原因，但尚未分离测量其贡献，不能称已精确证明“2MB硬上限”。

## 公开研究提供的排查方向

- [OWL 官方 README](https://github.com/seemoo-lab/owl)：接收帧的硬件 ACK 缺失可能导致重传和吞吐下降。我们已有传输成功和部分发送完成统计，不能照搬其他芯片的问题，认定本机缺失 ACK。
- [Google Project Zero 的 AWDL 研究](https://projectzero.google/2020/12/an-ios-zero-click-radio-proximity.html)：节点按可用窗口与信道序列协调，无线共存占用和同步误差会影响有效吞吐。当前 Mac 的基础网络与149不同信道，仍需通过包时间分布测量影响，不能直接假定固定50%占空比。
- [Cisco 802.11ac 参数表](https://www.cisco.com/c/en/us/products/collateral/wireless/aironet-2800-series-access-points/datasheet-c78-736497.html)：更宽信道提供更高PHY速率。资料只用于解释标准速率，不作为本实验硬件/固件支持VHT80的验收。

## 实验边界与顺序

独立目录 `downloads/airdrop-driver-work/wideband/` 与远端 `/work/houji-build/src/wideband/wlan` 保存VHT80实验。默认关闭的只读加载参数 `pt_vht80=1` 仅放行主信道149、80MHz、VHT、NSS1、MCS0–7；逐一检查149/153/157/161的法规可用性，并继续调用驱动信道校验。未绕过国家码、DFS/被动信道、模块引用或卸载检查。

同时调整信道请求、PHY、固定速率、WMI带宽与peer带宽。保留原HT20路径及单包聚合限制，以分离带宽效果。首先编译和ABI校验，再做接口启动、双向哈希、退出恢复；不把编译成功或参数下发成功当作空口80MHz已经生效。

后续需独立验证聚合及peer/BA生命周期，再评估AWDL窗口和TX反馈。达到大于10MB/s必须有多轮足量双向传输和哈希证据；不能仅用PHY配置数值或一次峰值宣称成功。

## 首轮宽带配置测试

实验版编译、ABI检查通过，导入符号集合未增加，模块SHA256为 `dcf3c1bf5b686390449d39e3d9a7df1f8488e126af831ad5fc40933d2690a35d`。这是独立模块，未覆盖基线或系统模块。

| 样本 | Mac→小米 MB/s | 小米→Mac MB/s |
|---|---:|---:|
| 8MiB / vht7-run1 | 2.574 | 2.684 |
| 16MiB / vht7-16m-repeat | 2.842 | 1.378 |

两轮均通过双向SHA256，随后恢复stock驱动/固件并重新启动已验收的149/MCS3原生待命服务。宽带版未接入日常原生互传。样本尚不足以建立稳定提升，特别是第二轮发送回落。

必须区分三层信息：vendor初始化字段是80MHz/VHT；`iw dev wonder0` 显示的是软件接口40MHz；Wonder的 `wonder_config()` 仅记录日志并返回0，不能用该显示反推底层真实空口宽度。当前证据仅确认宽带请求被接受且双向可用，实际发送PHY仍需底层状态验证。

为此新增默认关闭的只读观测 `pt_rate_observe` / `pt_rate_stats`，在有效TQM完成解析后统计rate-valid、带宽、MCS、packet type、GI、状态和尝试次数；只统计valid的速率字段。它不改变peer、队列、聚合或回调，不将TQM完成直接等同于成功ACK。该观测版本另行编译和验收。

## 发送状态与用户态合包追加

观测版编译/ABI/手机SHA通过（模块 `a67399963c60ddc964958e46b53bb160602fef910ef632d215b9c3b3822c71a0`）。`vht7-observed` 样本有效状态10935条，全部 bw=2（80MHz）、pkt_type=3（VHT）、mcs=7、gi=0，状态0；另有无有效速率的管理类完成记录，不能把其全零rate字段混入速率统计。此轮发送2.22MB/s。因此“80MHz未生效”不符合固件回报证据；软件接口40MHz显示不能取代底层完成状态。

用户明确再次取消旧的MCS7原生验证问题，后续仅进行Mac自动实验。

独立 `amsdu/` 实验在测试runner中拦截 `pcap_inject`，对同一对端、TID且未加密/未分片的普通QoS大数据帧合包。保留原RT头、AWDL子帧载荷和内层序号，限制聚合大小、200µs等待目标、最多4个缓冲帧；小包和不匹配帧触发刷新。实际线程调度可能超过200µs目标，不能把它称为硬实时期限。pcap关闭与发送串行化；注入失败立即退回原始帧。主机ASan/UBSan验证了填充、载荷、边界、跨对端拒绝、关闭刷新以及失败回退不丢输入。原libxos未改。

第一版未调整monitor MTU，聚合注入失败，传输因TCP重试降速。随后仅将临时wonder0 MTU设为8192（xos0 MTU不变；接口销毁即撤销），聚合注入通过。

| 实验 / 每方向8MiB | Mac→小米 MB/s | 小米→Mac MB/s |
|---|---:|---:|
| 两帧合包、MTU8192 | 2.179 | 1.018 |
| 四帧合包、MTU8192 | 2.305 | 7.636 |

均通过双向SHA256并恢复stock。四帧合包日志显示合包注入错误为0；这是发送路径改善证据，但仅一轮小样本，尚未达到10MB/s，也未证明反向改善。两帧效果反而较差，后续要用大样本与重复实验排除窗口/调度波动。A-MSDU预组帧不等同于已实现固件A-MPDU/Block ACK或真实peer管理。

## 稳定性失败与后续定位

四帧合包16MiB复测发送仅1.084MB/s。快轮平均接近4个MSDU/MPDU；慢轮多为2个MSDU/MPDU且大量单帧。扩展为最多5帧（仍限制body≤7935）、1ms等待目标后，16MiB长GI发送2.629MB/s，短GI发送9.947MB/s；固件完成记录确认sgi=1。**9.947不是大于10，也不是稳定速度。**

首轮64MiB短GI测试：Mac→小米2.831MB/s并校验；反向在50秒探针期限内未完成，客户端EOF。期间TCP出现cwnd27、retrans462、reordering19、RTT约67ms，不能作为成功样本。脚本finally恢复stock并重启MCS3待命服务，第二轮未运行。

随后修正外层802.11序号：每个实际发出的MPDU递增一次，按handle/地址/TID分隔；包含小QoS包，注入失败不消耗序号，AWDL内层序号保持不变。ASan/UBSan用25001个输入验证序号绕回和失败回退。该版本64MiB反向仍超时，期间reordering78，故序号空洞并非唯一原因。不得将这次尝试描述为稳定性已修复。

`awdl-actions.pcap` 在约2.1秒内捕获Mac的3次ADDBA请求（TID0、窗口64、immediate BA、允许AMSDU），同一抓包窗口未见响应。这里只说明该窗口的协商现象，不证明所有时段均无响应。为反向低速后续的peer/BA实现提供线索，不能未经硬件BA支持就回复成功。

继续新增默认关闭的 `pt_single_ring` 对照和 `tx_ring` 计数，测试同一PASSTHRU流是否受多队列影响；所有原有ring/pool范围检查保留。用户态同时加入实际合包等待时长分桶，区分目标等待时间与调度延迟。实验仍在独立目录，不接入原生日常路径。

## 11月10日追加：队列定位（尚未达到稳定目标）

单ring实验默认发送实际已全部落在ring1，强制ring0后一次16MiB发送达到10.209MB/s并通过SHA256；但64MiB复测只有1.896/1.598MB/s。因此没有证据证明跨ring乱序是原因，也不能把短样本峰值称为稳定达标。仅针对测试IPv6路由将TCP最小RTO提高到600ms，一轮发送2.063MB/s、另一轮超时，未解决问题。

新增UDP探针发送40000个1378B数据报，逐包验证序号与载荷。UDP不是可靠文件传输验收。最初收到23429个（丢41.43%，约6.20MB/s）；Mac UDP socket-buffer drop计数前后未增加。进一步测试 `udp-tun-observe` 收到26089个，xos0接口和qdisc丢包均为0，提示缺口在用户态或之后。

`xos_vhas_flow` 增加只读的 `__read_chk` / `pcap_inject`计数，数据不落日志。`udp-xos-flow` 收到22855个（42.86%丢包，173个乱序，约6.12MB/s），末次周期快照已读39202个、注入22704个，接口丢包仍为0。快照不是精确最终计数，但与缺口位置相符。

关键证据来自原版libxos的日志与反汇编：单播循环队列容量32；`xos_send_unicast` 遇到不可用时隙且队列非空时，把当前待发帧重新加入队尾。若此时队列已满，`circular_buf_put2`失败，随后释放当前帧。抓到16995条 `TXDISC-C rotate-failed queue-full size=32`（日志环形缓冲边界限制，不能将条数当成完整丢包计数）。这条软件路径既会丢帧，也会重排同一流。

隔离实验 `downloads/airdrop-driver-work/xos-fifo/patch-fifo.py` 对SHA锁定原版的0x14a48分支作单指令修改：保留当前待发帧，并沿原有可用时隙定时器等待，不执行队尾轮转。原libxos SHA `0a1b6401bcbde7d50f1e503b2f581940c6f40d4c3a701a1aa1a03ee04bbfffab`；实验副本SHA `44b5d8b70e590de64d5c4177e9f25123756cab8cb4151e2dcd9a457cb1ff3a49`。通过独立LD_LIBRARY_PATH加载，`runtime-maps.txt`确认实际映射；未覆盖正式文件。

**这只是单对端诊断修复：多对端情况下可能产生队头阻塞，不能直接推广为生产级调度实现。**

`udp-xos-fifo` 接收30276个，约8.13MB/s，乱序0、坏载荷0；xos0此时明确丢9606个。说明修复消除了本轮大量用户态丢弃/乱序，压力转移到1000项TUN/TAP队列；尚余118个端到端缺口未分离。正在用64MiB双向TCP与SHA进行可靠吞吐验证，UDP速率不作为达标结论。

FIFO两轮64MiB TCP均通过双向SHA256：小米→Mac为3.377/3.012MB/s，Mac→小米为2.590/2.762MB/s。第二轮xos0出现371个丢包，首轮为0，说明容量压力并非每轮完全相同。

FIFO加临时xos0 `txqueuelen 8192` 后，`udp-xos-fifo-q8192` 收到39847/40000包（0.3825%丢包、乱序0、坏载荷0），实收10.676MB/s；xos0和qdisc丢包均为0。**这是UDP诊断结果，不是可靠文件达标。** FIFO+8192队列+仅测试路由RTO600ms的64MiB TCP发送4.290/3.516MB/s，反向2.700/2.697MB/s，均通过SHA；发送中仍记录164次重传、cwnd降至28。未修改系统TCP默认参数。

后续对照包括临时接口FQ队列和更大的AWDL边界保护。历史MCS3下guard12曾无收益（见05文档）；本次结合已定位的FIFO丢包修复和VHT80合包重新测量，不沿用历史结果作为本配置结论。

FQ对照：首轮Mac→小米2.413MB/s完成校验，反向50秒超时，第二轮未执行，已恢复。FIFO+guard12+8192队列的UDP为39935/40000包（0.1625%丢包、乱序0、重复2、约10.989MB/s）；但两轮64MiB TCP发送仅2.408/2.992MB/s，反向2.895/2.839MB/s，均校验通过。因此扩大guard没有证明可靠吞吐改善，不推广。

本轮结束检查：`STOCK_RESTORED`、无实验bridge模块、两处saved.env均不存在，普通Wi-Fi已重新连接；已验收的native149/MCS3服务恢复待命。所有FIFO、FQ、guard12、VHT80/A-MSDU实验未接入日常互传配置。引擎逻辑对照见[12-engine-comparison.md](12-engine-comparison.md)。
