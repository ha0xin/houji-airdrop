# 公开资料对照：低吞吐的可能原因

检索日期：2026-11-09。结论是排查优先级，不是已经确认 houji 根因。本次未修改手机或部署新参数。

## 1. 固定信道与有效时间窗口

omdrop-owl 作者报告 AX201 固定信道6时 iPhone 空闲通常只提供16个时隙中的1–2个，传输期间可能增加。这能解释“无线PHY不算低，但平均吞吐低”。该项目也记录了固定信道下按实际数据对端可用窗口发送的修改。[项目说明](https://github.com/PterX/omdrop-owl#exclusive-mode)、[fixed-radio补丁](https://github.com/PterX/omdrop-owl/blob/main/patches/owl-03-fixed-radio.patch)。

我们同样固定2437MHz，但尚未测出本次iPhone/Mac实际提供多少信道6窗口。不能直接把别人的占空比套入我们的吞吐。应同时采集同步/信道序列和数据突发，以窗口数、每窗口有效字节及空闲间隔分解瓶颈。尤其要分清同步master与文件传输对端。

另一MT7921项目有反例：直接跟随数据对端的序列曾使吞吐更差，随后发现相位与多设备切换问题；文档后续又修正早期判断。只摘早期“根因已找到”会误导。[作者实验记录，§16–21](https://github.com/jedbillyb/airdrop-mt7921/blob/main/docs/FINDINGS.md)。

## 2. 重试越过可用窗口，阻塞后续帧

omdrop-owl 的NOACK补丁描述AX201上错过对端窗口的单播会重试15次，耗时75–140ms，后续帧被拖住；该实现对固定无线电模式使用一次发送策略。[补丁与原因](https://github.com/PterX/omdrop-owl/blob/main/patches/owl-05-fixed-radio-noack.patch)。

我们的完成事件有tries16、较长延迟及TCP重传，与此机制相符，但不能证明完全同因。应关联每帧提交、完成、重试次数与对端离开窗口时刻；先分离单播数据和广播/管理帧，避免把status2一律当丢包。

这里的ACK是802.11无线确认，不是TCP ACK。完全关闭无线重试可能增加TCP超时，只适合作为受控对照，不直接作为正式修复。

## 3. NOACK补丁不能原样套到Wonder

Linux文档定义radiotap TX_NOACK用于不等待单播ACK，但这需要实际驱动路径落实。[Linux mac80211 injection文档](https://www.kernel.org/doc/html/latest/networking/mac80211-injection.html)。

本地发布源码进一步显示：

- `kernel/wonder/mac80211.c` 的TX路径重新构造radiotap头，并调用 `wonder_txs_direct_report`（源码标注Fake TX status）后才向物理接口提交。
- `kernel/wlan-overlay/qca-wifi-host-cmn/dp/wifi3.0/dp_tx.c` 的透传提交保存后清空skb控制区、剥离radiotap，再走raw TX。

因此不能假定只给pcap_inject输入添加NOACK就能控制高通硬件。需要核对从mac80211标志、Wonder元数据到固件descriptor/WMI的完整传播，并用完成事件验证。假状态上报也说明不能用上层成功状态替代真实硬件完成；它对积压的具体影响尚待测量。

## 4. 配置本身限制性能

上游控制实现只接受HT20、NSS1、MCS0–7，拒绝开启AMSDU/AMPDU和显式retry limit。我们本地同文件还明确把软件重试设0、TX聚合大小设1，并注明前者不代表硬件重试关闭，后者命令提交不代表固件应用已获确认。[上游控制源码](https://github.com/YoWenhow/Xiaomi14-PixelOS-Airdrop/blob/main/kernel/wlan-overlay/qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c)。

这些限制会影响效率，但尚不能量化各自贡献。网上main的控制源码与本地副本存在差异，后续重建须固定commit和已验收模块对应版本，不能混用最新页面代码。

## 下一步优先级

1. 先检查已有抓包能否提取AWDL Action帧；若仅为xos0的IPv6/TCP抓包，只能分析突发，不能还原信道窗口。
2. 在下一轮受控采集中关联窗口、发送提交与完成，判断是可用窗口少，还是窗口内吞吐低/提交后迟迟未发出。
3. 基线驱动重建继续作为前置工作。增补生命周期安全的完成反馈与在途量，再分别测试在途限制和有效重试限制。
4. 待完成信道控制及固件验证后，再对照5GHz；不把用户态149参数改成功当作硬件已切换。

没有找到适用于当前houji预编译组合的、已验证可直接应用的提速补丁。本次公开资料增强了“AWDL窗口与驱动发送时序配合”的排查依据，没有排除RF干扰、固件行为或其他因素。
