# Apple phone 原生 AWDL 观测对照

2026-11-10，经 USB 只读检查 Apple phone。该机能做原生 AWDL **端点**，并提供接口计数和固件身份。现有工具未读到实时 AWDL 信道序列、逐 peer/TID 的 BA 状态或空口 A-MPDU。小米与 Mac 的聚合验收仍需要第三台设备提供独立空口证据。 [实机脱敏输出](../../experiments/iphone-radio-research/live-readonly.txt) · [小米 RX 观测计划](17-rx-peer-reo-observer-plan.md)

## 实机证据

| 层 | 观测 | 证据范围 |
|---|---|---|
| 系统 | `iPhone8,2`，iOS 15.7.1 / `19H117`，Darwin 21.6.0。USB 转发使用现有 SSH key 成功获取 UID 0。 `/var/jb` 下有 Dopamine 与 Procursus 标记，安装了 `network-cmds 669` 和 `tcpdump 4.99.1-1`。 | 越狱和 USB 通道已验证。 |
| 芯片及总线 | 设备树含 `wlan-pcie,bcm4350`，设备包元数据 `gsc.wifi-chipset=4350`。运行中的 `AppleBCMWLANCore` 提供者是 `AppleBCMWLANBusInterfacePCIe`。 | Apple 规格注明本机支持 802.11ac MIMO。此次没有从实机读到 AWDL 方向的实际 PHY、带宽、MCS 或 NSS。 [Apple 规格](https://support.apple.com/en-us/111996) |
| 固件 | `FirmwareLoaded=Yes`，运行时版本 `7.64.133.15 (r936348)`，FWID `01-b2abcc1`。分别在 4350 系列候选文件中查找三个字符串，均仅命中 `/usr/share/firmware/wifi/C-4350__s-C5/pinotnoir.trx`。 | 文件归因基于三项元数据唯一匹配。未跟踪加载器打开文件的事件。 |
| 接口 | IORegistry 中 `awdl0` 属于 `AppleBCMWLANProximityInterface`。 `ifconfig` 显示 MTU 1500、`UP/RUNNING`，当时的媒体状态为 active。 `netstat -I awdl0 -b -d -n` 可读包、字节、错误与丢包。 | 接口状态与计数位于主机侧。应取受控传输前后的差值，用应用收讫字节与计时计算吞吐。 |
| BPF 链路类型 | `tcpdump -p -i awdl0 -L` 列出 `EN10MB`、`RAW` 和私用 `DLT 147`。 `en0` 在当前非监视模式只有 Ethernet 和 RAW，`llw0` 列出 Ethernet 和 DOCSIS。 | 标准 802.11 或 radiotap 未出现在枚举中。 `147` 对应私用编号 `DLT_USER0`，本机 tcpdump 无打印器，此次未捕获或解码它。模式不同会改变 DLT 列表。 [tcpdump 手册源码](https://github.com/the-tcpdump-group/tcpdump/blob/master/tcpdump.1.in) · [libpcap 定义](https://github.com/the-tcpdump-group/libpcap/blob/master/pcap/dlt.h) |

本轮仅枚举链路类型和读取系统属性与接口计数。没有采集任何包，没有更改 iPhone、Mac 或小米的无线配置，也没有安装诊断工具或启动诊断服务。历史上该机已经原生 AirDrop 上传至小米且文件哈希匹配，这一记录可用来选端点，无法推导本轮吞吐。 [历史实机验收](02-mosey.md#验收证据)

## 统计与调度

- `AppleBCMWLANCore` 的 `IOReportLegend` 含 `AWDL Metrics / AWDL Snapshot` 和 `WLAN Power / Connections` 中的 AWDL AW、扫描、收发时长标签。PCIe 驱动的 `Tx Complete Status` 还按 AWDL 区分成功、peer 不可用、芯片缓冲不足、寿命过期等完成类型。驱动公布了指标名，尚未取得指标的实时值或量纲。
- A-MPDU、Block Ack 和 BAR 标签也存在。其中 A-MPDU 指标位于 `Chip / AMPDU Counters`，BAR/BA 帧计数位于 `Chip Frames`，TX 聚合深度标签位于 `Rate / Tx MPDU in AMPDU Usage`。这些标签本身缺少 AWDL 接口、方向上的对端和 TID 三重归属。即使后续读到增量，也要排除普通 Wi-Fi 的贡献。
- 本机现有 PATH 未找到 `ioreport`、`ioreportctl`、`powermetrics`、`apple80211`、`airport`、`wl` 或相应的 Wi-Fi 调试 CLI。 `ioreg` 的公开属性和相关 sysctl 名称未提供实时 AWDL 信道序列或 BA 状态。网上的 `apple80211` 命令帮助确有 channel sequence 和同步树选项，但无法直接套用到本机已装环境。 [公开命令帮助](https://gist.github.com/networkextension/a49f5bddacdac8781cd97f817539a298)
- 本机有 `corecaptured`、CoreCapture 系列框架、`wifip2pd`、`sysdiagnose` 和 Wi-Fi 驱动的日志管道。公开研究描述过从 CoreCapture 日志解析无线信息。此次未触发 dump、profile 或 sysdiagnose，无法确认当前系统与固件能否导出所需帧、调度或逐 peer 统计。若后续有适用的只读 IOReport 采样客户端，可先核实 AWDL 子组的可见性和单位，再采集空闲与受控传输差值。 [CoreCapture 原始研究](https://arxiv.org/abs/1808.07353) · [研究者的 Wireshark 实现](https://github.com/seemoo-lab/wireshark-awdl)
- Nexmon 上游的 iPhone 6 条目对应 `bcm43451b1 / 7_63_43_0 / iOS 10.1.1`。上游支持表未列出本机的 BCM4350 / `7.64.133.15` 组合。结合本机 DLT 枚举，当前没有经验证的方式让该机充当小米与 Mac 的独立空口监听器。普通 `awdl0` 端点抓包与 Apple 的 USB RVI 可用来诊断主机交付层。 [Nexmon 支持表](https://github.com/seemoo-lab/nexmon#supported-devices) · [Apple RVI 说明](https://developer.apple.com/documentation/network/recording-a-packet-trace)

## 后续对照测量

### 原生端点计数

在唯一 USB 设备已连接且本地端口空闲时，沿用 `iproxy -s 127.0.0.1 -l 22226:22`。另开终端，使用现有 key 登录。以下命令可在受控传输的前后各执行一次，输出接口状态与计数并过滤地址。传输需用同一份生成的测试数据，以双方字节和哈希确认完成。iPhone 作为 Mac 的原生对照端点与小米作为 Mac 的实验端点应分开计时。 [已有 USB 入口](../../downloads/oneui9-houji-analysis/AIRDROP-HANDOFF.md#文件与入口)

```sh
ssh -o BatchMode=yes -p 22226 root@127.0.0.1 '
  /var/jb/usr/sbin/ifconfig awdl0 | grep -E "^awdl0:|^[[:space:]]+status:"
  /var/jb/usr/sbin/netstat -I awdl0 -b -d -n |
    sed -E -n "1p; /<Link/s/([a-f0-9]{1,2}:){5}[a-f0-9]{1,2}/[redacted]/p"
'
```

### 独立空口证据

1. 在第三台设备上用支持 5 GHz / 80 MHz 的独立接收器，保持小米和 Mac 的现有设置。基于当前实验先观察主信道 149 / 5745 MHz 的 80 MHz 信道块（中心 155 / 5775 MHz）。用 `tcpdump -p -i <monitor-if> -L` 核对已有监听接口能输出 `IEEE802_11_RADIO`。在已知有聚合的对照链路上确认接收器能保留 BAR、BA 与 radiotap A-MPDU 元数据。仅能收到控制帧或 20 MHz 管理帧的设备不足以核验 80 MHz 数据 PPDU。 [Radiotap 字段定义](https://radiotap.org/fields/defined) · [小米固定信道实测](14-owly-houji-device.md)
2. 按每轮已确认的 AWDL 对端空口地址限制观测范围，记录动作帧 TLV 的 channel sequence、AW 周期和 master。序列是该节点公告的调度。结合带时间戳的数据帧和无线元数据判断实际使用频率、带宽和空档。固定 149 只观测该信道块，不能用它证明 6 或 44 无流量。需要比较这些信道时，使用额外独立接收器或分轮观测。 [AWDL 原始论文](https://arxiv.org/abs/1808.03156) · [Wireshark AWDL dissector](https://github.com/wireshark/wireshark/blob/master/epan/dissectors/packet-awdl.c)
3. 双向各记录发起与响应 ADDBA 的 peer、TID、窗口和状态。用 QoS 数据的 TA/RA、TID、序号、重试位以及 radiotap A-MPDU reference 关联同一聚合内的多个 MPDU。核对 BAR（若出现）与数据方向一致，BA 的 TA/RA 方向与数据相反。用 BA 的 TID、起始序号和位图确认收到的 MPDU，再与端点交付顺序对齐。A-MSDU 位、ADDBA 请求、单独的 BA 帧和 TCP ACK 各自只能证明对应层的一项事件。接收器未提供聚合元数据或遗漏控制帧时，该轮缺少相应的验收证据。 [Wireshark 802.11 字段](https://www.wireshark.org/docs/dfref/w/wlan.html) · [Radiotap A-MPDU 状态](https://radiotap.org/fields/A-MPDU%20status.html) · [项目验收标准](15-aggregation-roadmap.md)
4. 两组对照均仅保存本实验的帧头字段、计数、时间差和脱敏地址标签，避免保存 TCP、TLS、mDNS 载荷。用 iPhone ↔ Mac 原生组先校验测量链路，再用小米 ↔ Mac 组定位 149 上的 peer/TID/REO 或接收聚合瓶颈。两组使用的频宽和窗口以各自观测为准。iPhone 上 BCM4350 的原生指标可以启发对照维度，无法证明小米 Qualcomm PASSTHRU 支持同等能力。

空口步骤是后续验收计划。本轮未切换 Mac 的无线模式，未验证第三接收器或发起新传输。

## IOReport 最小探针

[离线 C 源码](../../experiments/iphone-radio-research/awdl-ioreport.c)默认只枚举 `AWDL Metrics / AWDL Snapshot`。iOS 15.5、15.6 的 SDK 镜像均含 `arm64-ios` 的 `libIOReport.tbd`，列出定向枚举、订阅和采样符号。苹果开源的 macOS `pmset` 展示了订阅、取样和 `CFRelease` 的生命周期。iOS 的符号存在性、ABI 和设备进程的访问权要分别验证。iOS 15.7.1 实机默认枚举已能读取该子组的四个通道，订阅和采样另行验证。 [iOS 15.5 SDK 导出表](https://raw.githubusercontent.com/xybp888/iOS-SDKs/master/iPhoneOS15.5.sdk/usr/lib/libIOReport.tbd) · [iOS 15.6 SDK 导出表](https://raw.githubusercontent.com/theos/sdks/master/iPhoneOS15.6.sdk/usr/lib/libIOReport.tbd) · [Apple pmset](https://github.com/apple-oss-distributions/PowerManagement/blob/main/pmset/pmset.m) · [公开 ABI 示例](https://github.com/freedomtan/test-ioreport/blob/main/test_ioreport.m)

探针的 C 声明参考 macOS 的 `test-ioreport` 与 `hashcat` 调用例。两者声明 `IOReportIterate` 返回 `void`、回调返回 `int`，`IOReportCreateSubscription` 第五个参数为 `CFTypeRef` 且调用时传 `NULL`。订阅对象及返回通道字典的释放顺序沿用 Apple `pmset`。XNU 的原始类型定义是 `IOReportFormat = uint8_t`，其中 `kIOReportFormatSimple = 1`。现有 SDK 导出表不带 C 原型，以上声明仍需 iOS 15.7.1 实机确认。 [test-ioreport 声明](https://github.com/freedomtan/test-ioreport/blob/main/test_ioreport.m#L7-L33) · [hashcat 声明](https://github.com/hashcat/hashcat/blob/master/src/ext_iokit.m#L18-L32) · [XNU 格式定义](https://github.com/apple-oss-distributions/xnu/blob/xnu-8020.140.41/iokit/IOKit/IOReportTypes.h#L77-L83)

只有显式传入 `--sample-ms 100..15000`，且四个通道的组、子组、名称和 simple integer 类型均符合清单时，探针才订阅。它尝试读取两个快照，每次只取 `AWDLDuration(s)` 与 `AWDLUse(%)` 的原始值。普通错误、正常完成以及程序可处理的 SIGINT/SIGTERM 使用显式释放路径。SIGKILL 或进程崩溃会跳过该路径，尚未验证该机的 IOReport 后端是否会在连接关闭时回收所有统计订阅。

Darwin 21 同代 XNU 源码显示，报告订阅可涉及 reporter 的启用引用计数，取样可调用 provider 刷新数据。该操作是诊断订阅，有短时运行状态。 [Darwin 21 XNU IOReporter](https://github.com/apple-oss-distributions/xnu/blob/xnu-8020.140.41/iokit/Kernel/IOReporter.cpp#L604-L729)

在工作区根目录可复现离线构建。交叉编译在本地 arm64 Mac 使用已安装的 Xcode iPhoneOS 27.0 SDK。已验证输出是 Mach-O arm64、平台 iOS、最低 15.0。以模拟 IOReport 库验证了默认枚举不订阅、两次采样正常释放、两次采样之间收到 SIGTERM 时以 143 退出并释放资源，以及三个非法参数拒绝场景。这些模拟结果来自早期单通道版，只覆盖宿主工具和探针自身的控制流。

```sh
xcrun --sdk iphoneos clang -target arm64-apple-ios15.0 -fblocks \
  -Wall -Wextra -Werror -framework CoreFoundation \
  downloads/airdrop-driver-work/iphone-radio-research/awdl-ioreport.c \
  -o /tmp/awdl-ioreport
xcrun vtool -show-build /tmp/awdl-ioreport
```

### iOS 15.7.1 默认枚举实测

使用 Mac 已有的 `ldid -S` 签署探针，entitlements 为空。探针经 USB 放入 iPhone 独立临时目录，仅执行默认枚举。主机与设备执行前后的 SHA-256 三方一致。首次 SSH 返回 255，无程序输出。复核中，同一设备 timeout 包装下已有的 zsh 退出 0，探针退出 137，无程序输出。复核整轮耗时短于设备的 3 秒 timeout，退出码与进程被 SIGKILL 相符，具体触发者未知。没有取得 `dlsym` 或 AWDL 子组的结果，也没有进行采样。两个远端临时目录均已删除，随后确认 SSH 正常、未见探针路径进程并关闭本轮转发。 [枚举实测记录](../../experiments/iphone-radio-research/metadata-device.txt)

AWDL duration 即使后续可读，也只用于调度对照。空口 BA 和逐 peer/TID 的聚合仍按上节验收。

### 执行策略复核与通道元数据

在同一台 iOS 15.7.1 设备上，分别把同 SHA-256、无 entitlements 的最小 hello 和 IOReport 探针放入 `/var/tmp` 与 `/var/jb` 下各自的私有临时目录。前者两份都退出 137，内核 Sandbox 日志对两个自有程序明确记录 `deny(1) process-exec*` 和 `failed to apply exec policy`。后者两份都退出 0，hello 进入 `main`，探针报告四个通道。该对照将前次失败定位在本机临时目录的执行策略。两组二进制使用同一已有 Apple SDK 和签名方式。 [脱敏复核记录](../../experiments/iphone-radio-research/metadata-launch-diagnosis.txt) · [rootless 维护者的路径说明](https://github.com/Wawona/repo.wawona.io/blob/main/docs/packaging.md)

`AppleBCMWLANCore` 的 `IOReportLegend` 在 `AWDL Metrics / AWDL Snapshot` 列出四个通道。通过 `IOReportCopyChannelsInGroup` 及元数据访问器实机交叉核对，名称相同，四个格式均为 simple integer，API 未返回独立单位标签。括号中的单位来自通道名，实际计数语义仍待验证。

| 通道 | IORegistry channel ID |
|---|---:|
| `AWDLDuration(s)` | 4717349550861677088 |
| `AWDLUse(%)` | 4717349551146755360 |
| `SDB` | 4717349551112217120 |
| `TS(ms)` | 4717349551127863328 |

### 一次空闲订阅实测

从 `/var/jb` 私有临时目录运行 `--sample-ms 1000`。设备复核四个通道后进入订阅阶段，报 `IOReport subscription unavailable or not permitted`，退出 3，未返回数值。此分支代表订阅对象或返回通道字典为空，无法从返回值确定失败原因。该子组能通过 `IOReportLegend` 和库函数枚举。公开的旧 IOKit 头将 `IOReportLegendPublic` 声明为布尔属性，不能用其中没有子组名推断访问权限。尚无匹配 iOS 15.7.1 的 entitlement 证据。 [空闲实测记录](../../experiments/iphone-radio-research/awdl-ioreport-idle.txt)

探针前后 SHA-256 一致，`awdl0` 与 `en0` 的 flags、index 和 active 状态一致。此次未发起传输或启用 CoreCapture。临时文件和 USB 转发均已清理。现阶段仍没有实际 AWDL airtime、队列或 BA 样本。

### 订阅返回对象复核

使用 Apple 开源 `pmset` 相同的订阅参数形态 `(NULL, desired, &selected, 0, NULL)`，本次只执行一次 `--check-subscription`，未调用采样。设备正常枚举四个通道，订阅对象和输出通道字典均为 `NULL`，退出 3。按自有探针名限定的系统日志没有命中 IOReport 拒绝行。运行时 IORegistry 明确显示 `"IOReportLegendPublic" = Yes`，因此它是布尔值，不能用于推断当前进程能订阅该 AWDL 子组。未确认失败由特定 entitlement、通道选择或框架策略引起。 [实测记录](../../experiments/iphone-radio-research/awdl-ioreport-subscription-check.txt) · [Apple pmset](https://github.com/apple-oss-distributions/PowerManagement/blob/main/pmset/pmset.m)

## iPhone ↔ Mac 原生 AWDL TCP 基线

2026-11-10 在独立无线窗口测试 Apple phone ↔ Mac。Mac 和 iPhone 均使用系统原生 AWDL 栈、默认 MSS，未调整无线模式或固定信道。应用层使用现有 Owly TCP 探针的 12 字节 mode/size/sample 头、内存生成字节序列和 SHA-256 回执。Mac 端在发送头前开始计时，直到该方向的回执处理完成，和小米实验使用的 Mac 侧口径一致。iPhone 端在读完头后开始计时。实验使用 TCP 探针，未测试 AirDrop UI 和文件交付。 [Owly 测量口径](14-owly-houji-device.md#测量口径) · [原生探针源码](../../experiments/iphone-radio-research/awdl-hashprobe.m) · [复测 runner](../../experiments/iphone-radio-research/run-awdl-baseline.py)

iPhone 用一次性的随机 Bonjour 服务名开启 peer-to-peer 监听。Mac 只使用同名且包含正确 `awdl0` 接口索引的浏览结果，对实际连接设置 required interface，再以双方当前 IPv6 link-local 和 scope 建立连接。两端在传输前后都要求 current path 仅含 `awdl0`、接口索引匹配且有效端点匹配；传输中若报告路径偏离，取消连接。每轮由两端的样本编号、字节数和 SHA 共同确认，发送端 `awdl0` 的 Obytes 增量与接收端的 Ibytes 增量也都覆盖载荷大小。接口计数是旁证，包含相应窗口内的协议开销。 [Apple Network framework peer-to-peer](https://developer.apple.com/documentation/technotes/tn3213-moving-from-multipeer-connectivity-to-network-framework)

固定端口的首轮完成 Mac→iPhone，紧接着反向的 iPhone 监听未就绪。加收集就绪前诊断的同端口复核，明确得到 Darwin POSIX 48 `EADDRINUSE`，反向未进入数据传输。每轮改用独立高端口后，1 MiB 双向均验证通过，随后完成 16 MiB 三轮。 [固定端口诊断](../../experiments/iphone-radio-research/native-awdl-1mib-fixed-port-diagnostic.json) · [独立端口 1 MiB](../../experiments/iphone-radio-research/native-awdl-1mib-independent-ports.json)

| 样本 | Mac→iPhone MB/s | iPhone→Mac MB/s | 双端 SHA 和 AWDL 路径 |
|---|---:|---:|---|
| 1 MiB，独立端口 | 2.361 | 14.962 | 2/2 |
| 16 MiB，第 1 轮 | 19.995 | 6.886 | 2/2 |
| 16 MiB，第 2 轮 | 16.168 | 13.664 | 2/2 |
| 16 MiB，第 3 轮 | 19.575 | 8.078 | 2/2 |
| 16 MiB，加权速率 | **18.411** | **8.767** | **6/6** |

速率为十进制 MB/s，使用 Mac 端计时，合计载荷除以合计秒数。反向 16 MiB 在 6.886–13.664 MB/s 间波动，单个 1 MiB 峰值无法代表持续速度。同一台 Mac 作发送端时，原生 iPhone 接收组高于现有 Mac→小米实验。两组设备、调度与射频条件不同，仍应分别报告。iPhone 的实际 RF 信道、带宽、MCS、NSS、占空比、BA 与聚合深度未观测。探针的接收路径按最多 64 KiB 的回调块处理，包含应用哈希和必要复制，也可能影响测得的速度。结果不能把两端差异归因到某一项硬件聚合功能。 [16 MiB 第 1 轮](../../experiments/iphone-radio-research/native-awdl-16mib-1.json) · [第 2 轮](../../experiments/iphone-radio-research/native-awdl-16mib-2.json) · [第 3 轮](../../experiments/iphone-radio-research/native-awdl-16mib-3.json)

本地用既有 Xcode 分别编译 iOS arm64 和 Mac arm64，启用 ARC。独立的回环构建双向 SHA 通过，错误 sample 和半头断连均被拒绝。每个实机样本都记录 listener 取消回调，执行后确认无探针进程、测试端口监听或临时目录，iPhone 的 `awdl0` 和 `en0` 均 active，USB 转发已关闭。 [清理复核](../../experiments/iphone-radio-research/native-awdl-postcheck.txt)

### 可加的单连接 TCP 观测

Apple 的 `nw_connection_create_new_data_transfer_report` 自 iOS 13.0 可用。当前 SDK 声明与 iOS 15.6 SDK 的 `libnetwork.tbd` 均可核对创建、收集、路径接口、TCP 重传字节、接收重复/乱序字节和 smoothed RTT（毫秒）入口。报告仅关联传入的连接，从创建与 ready 二者中较晚的时点开始。调用 `nw_data_transfer_report_collect` 后，在指定队列收到一次回调，随后读 collected 状态和逐路径数据。连接取消或失败可触发自动收集。 [Apple 指标说明](https://developer.apple.com/documentation/network/collecting-network-connection-metrics) · [Apple C API](https://developer.apple.com/documentation/network/nw_data_transfer_report_t) · [iOS 15.6 SDK 导出表](https://raw.githubusercontent.com/theos/sdks/master/iPhoneOS15.6.sdk/usr/lib/libnetwork.tbd)

最小增量是在已验证路径的连接进入应用计时前创建报告，完成传输和记录耗时后再请求收集。用现有串行队列接回调，外层最多等待一秒，且只导出唯一 `awdl0` 路径的数值，缺报告、路径不匹配或回调超时单独记作遥测不可用。保留原 SHA、路径和接口计数的通过条件。离线先验证双向回环字节、错误样本和断连的报告生命周期，再在单独的无线窗口重跑相同样本。TCP 重传与 RTT 有助于比较交付行为，仍需独立空口证据区分 802.11 重试、可用窗口和聚合。此项仅核对了公开接口，尚无 iPhone 样本。

若后续要评估 CoreCapture，Apple 的 Wi-Fi debug profile 加 sysdiagnose 属于设备级诊断，范围比当前的自有 TCP 流大。需先明确仅为测试时段启用、卸载流程、现有 Wi-Fi 状态能否保持及日志是否包含其它 SSID、MAC、服务名或用户载荷。可以在本地提取必要的 AWDL 时间/信道/聚合字段并按对端角色脱敏，保留导出的字段表。当前没有开启 profile 或收集整机 sysdiagnose。 [Apple Wi-Fi 诊断说明](https://developer.apple.com/forums/thread/819141) · [Apple iOS Wi-Fi profile](https://developer.apple.com/feedback-assistant/profiles-and-logs/?name=Wifi&platform=ios)

### 可选 TCP 遥测实现（离线验收）

原生探针现在支持 `--report`。在连接 ready、路径验证通过后、应用传输开始前创建报告，计时结束后单独等待最多一秒。只有 collected 状态、恰好一个正确接口且应用字节覆盖载荷，才写数值。输出区分统计缺失、超时、路径不符和计数不完整。重传、重复、乱序为报告区间的 TCP **字节**，RTT 为 API 返回的 transport smoothed/min 毫秒值。RTT 不做前后相减。Mac `lo0` 上的两个 1 MiB 方向均验证双端 SHA 与报告，错误 sample 和半头断连没有输出伪造的数字。iOS 与 macOS 生产版本均编译通过，尚未追加实机遥测。复测 runner 接受同名 `--report`，把遥测和样本通过状态分开保存。 [原生探针](../../experiments/iphone-radio-research/awdl-hashprobe.m) · [复测 runner](../../experiments/iphone-radio-research/run-awdl-baseline.py)

Mac 客户端另有独立 `--existing-awdl` 模式，服务名位置传 `-`。它按当前双方 link-local 地址和 `awdl0` scope 连接现有测试服务器，不浏览 Bonjour，也没有自动激活保证。只在已有小米 Owly 测试会话中试用，连接仍须通过 `awdl0` 的单接口、索引、端点与变更检查。该模式保留现有 12 字节协议和 SHA，Mac 可在两组使用同一测量实现。公开 Network.framework 这条探针路径未设置 MSS，iPhone 原生默认 MSS 与小米最佳实验的 MSS 1160 仍是控制变量。不能用旧 Python 客户端的速率与新客户端的报告做同一轮归因。

若要保留小米现有 Mac Python 客户端的计时与 socket 行为，[独立 BSD helper](../../experiments/iphone-radio-research/mac-tcp-info.c)可直接对它已连接的同一 fd 调用 `TCP_CONNECTION_INFO`。C 使用本机 macOS SDK 定义与长度检查，向 Python 导出固定 10 项的 `uint64_t` 数组。只对累计字节或包字段取差，MSS、最近 RTT 和平滑 RTT 保留两个端点时刻的读数。macOS SDK 的公开结构不提供重复字节字段。IPv6 回环实测发送/接收字节增量均为 4096，坏 fd 和错误长度返回错误且不写结果。此接口与 Network.framework 的计数窗口和定义分别报告。 [Apple XNU TCP 结构](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp.h) · [Apple XNU 计数填充](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp_usrreq.c)

### TCP 遥测实机复测

在独立无线窗口复跑两轮 16 MiB 双向，使用默认 MSS，未改无线参数。四次传输均通过双端 SHA、严格 AWDL 路径和方向计数，八份单连接报告均为 `collected`。下表按 Mac 端应用计时，重传取发送载荷的一端，乱序与重复取接收载荷的一端。

| 轮次 | 数据方向 | MB/s | 发送端 TCP 重传 B | 接收端 TCP 乱序 B | 接收端 TCP 重复 B | 发送端平滑 / 最小 RTT ms |
|---|---|---:|---:|---:|---:|---:|
| 1 | Mac→iPhone | 17.414 | 2,856 | 1,175,244 | 0 | 38 / 5 |
| 1 | iPhone→Mac | 7.007 | 0 | 0 | 0 | 8 / 6 |
| 2 | Mac→iPhone | 14.510 | 12,852 | 1,160,964 | 1,428 | 11 / 8 |
| 2 | iPhone→Mac | 6.847 | 1,428 | 0 | 1,428 | 49 / 6 |

两轮加权速率为 Mac→iPhone **15.830 MB/s**、iPhone→Mac **6.926 MB/s**。反向较慢，同时报告的 TCP 重传和接收乱序更少。样本不支持用 TCP 重传/乱序单独解释方向差异，也没有直接观测 802.11 重试、聚合、可用窗口或实际 RF 参数。 [第 1 轮原始数据](../../experiments/iphone-radio-research/native-awdl-tcp-report-16mib-1.json) · [第 2 轮原始数据](../../experiments/iphone-radio-research/native-awdl-tcp-report-16mib-2.json)

Network.framework 报告从各端 ready 后、应用传输前创建，到本端计时结束后收集。两端窗口各自起止，接收应用字节涵盖从 socket 交付的头或回执，同一字段的传输层字节受各自报告起点影响。SDK 将重传定义为报告窗口内在该路径发送的 TCP 重传字节，将重复和乱序定义为窗口内在该路径接收的 TCP 重复和乱序字节。iPhone 纯接收方向报告的 RTT 为原始 0 ms，保留该值，不用于解释物理时延。原生这组是 Network.framework 区间值，小米 BSD helper 则是 `TCP_CONNECTION_INFO` 在同一 fd 前后取差，公开 API 没有承诺两套实现的字节计数边界相同。BSD 的 `tcpi_rttcur` / `tcpi_srtt` 是毫秒快照，保留前后原值。 [Apple 报告 API](https://developer.apple.com/documentation/network/nw_data_transfer_report_t) · [Darwin TCP 快照结构](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp.h) · [Darwin 统计填充](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp_usrreq.c)

各轮的报告状态与传输校验状态分别保存。实测结束确认 `awdl0`、`en0` active，无自有探针、测试端口监听或临时目录，USB 转发已关闭。 [清理证据](../../experiments/iphone-radio-research/native-awdl-tcp-report-postcheck.txt)

### Darwin TCP 窗口时序接口

`mac-tcp-info.c` 保留十项 `awdl_tcp_info_sample`，新增十四项 `awdl_tcp_info_sample_v2`。前十项含 MSS、当前和平滑 RTT，以及包/字节累计计数，顺序与 v1 相同。v2 追加四个瞬时字段，使用同一次 `getsockopt(TCP_CONNECTION_INFO)` 快照。检查实际返回结构长度，出错时返回 errno，输出数组保持原样。 [C helper](../../experiments/iphone-radio-research/mac-tcp-info.c)

| v2 索引 | 字段 | 含义 |
|---:|---|---|
| 10 | `snd_cwnd_bytes` | 本端 TCP 发送拥塞窗，字节 |
| 11 | `snd_wnd_bytes` | 本端发送方记录的对端通告窗，字节。它不等同对端当前空闲缓冲大小 |
| 12 | `snd_sbbytes` | 本端发送 socket 缓冲内的字节，包含在途数据。它不等同在途字节数 |
| 13 | `loss_recovery` | 0/1。XNU 在快速恢复或重传退避级别大于零时置位 |

Python `TcpTimeline` 在 socket 上下文内使用同一个 fd，按同一 monotonic 时钟每 200 ms 读一次，默认最多 512 行。调用者在退出该上下文并完成 join 后关闭 socket，避免采样落在复用的 fd 上。保存每次相对基准的 `offset_ns`；窗口、MSS、RTT、状态标志按快照保留，累计计数才取差。累计 TCP 发送字节用于看发送进展，不能直接视为对端确认字节。采样出错保留已有行及独立状态。回环验证了双 ABI、坏 fd、错误宽度、三点窗口/字节进展、传输异常时的线程回收和采样失败时应用传输继续。 [Python 采样器](../../experiments/iphone-radio-research/mac_tcp_timeline.py) · [XNU 公开字段](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp.h) · [XNU 快照填充](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp_usrreq.c)

## 原生 AWDL 调度的运行时证据

Mac 默认内核日志中的 `com.apple.p2p.awdl0` 调度序列和 RRM availability 条目可在现有权限下读取。用双端 SHA 和 scoped IPv6 `awdl0` 路径校验限定自有 TCP 传输，再用 Mac 端 `HASH_CLOCK` 的 wall 起止匹配 Mac 日志。探针同时保留 monotonic 起止，两种时钟的区间长度在本次各样本相差不超过 1 µs。日志精度为毫秒，iPhone wall 比 Mac 约早 0.22 s，因此对齐只使用 Mac 时钟。收集器保存限定字段和角色标签，不保存原始日志、设备地址或载荷。

| 样本 | 数据方向 | Mac 端 MB/s | 载荷窗口内的 149 调度证据 |
|---|---|---:|---|
| 16 MiB | Mac→iPhone | 19.372 | 窗口内无调度快照。前 174.1 ms 为 4/16 槽，后 20.9 ms 为 11/16 槽 |
| 16 MiB | iPhone→Mac | 4.954 | +331.9 ms 为 2/16 槽，+1380.9、+2718.9 ms 均为 7/16 槽，窗口内六条 RRM 记录含 149 |
| 64 MiB | Mac→iPhone | 29.055 | +322.7 ms 为 `Data`，11/16 槽，`infra_percent_raw=24`，窗口内三条 RRM 记录含 149 |
| 64 MiB | iPhone→Mac | 15.446 | +161.2 ms 为 `Idle`，5/16 槽，raw 60。+472.2 ms 为 `Data`，7/16 槽，raw 48。窗口内四条 RRM 记录含 149 |

64 MiB 两个传输窗口分别持续 2.310 和 4.345 秒。两档大小各做了一次双向对照。速率差异同时受到样本长度、热身、调度和射频状态影响，无法从这些样本分离各因素。16 MiB 的初版提取器未为 `Data` 和 `Idle` 写标签，64 MiB 的提取器保留这两类及 `Infra Priority`。原始调度三元组为 `(6/149/149)`，序列可见 36、6、149 和 0。槽数表示单条序列里的 149 出现次数，`infra_percent_raw` 保留驱动日志原值，RRM bitmap 保留原样。这些字段没有经过 RF 时间标定。

在 64 MiB 载荷窗口内，Mac→iPhone 方向每端取到 2 个 IORegistry 快照，反向每端取到 5 个。两端映射到 `en0` 的 provider 在这些快照中仍报告 ch36 / 5180 MHz / 80 MHz。结合传输前后的接口 active 状态和已验证的 AWDL 流，可确认两个接口并存。provider 的属性可能来自缓存，现有记录未连续验证普通 Wi-Fi 的关联状态或两接口同时发射。iPhone 的 `awdl0` 对应独立的 AppleBCMWLANProximityInterface，这条接口没有同样的频道属性，`en0` 的读数不能用于确定 AWDL 的 PHY 参数。

日志证明 Mac 在自有流窗口内为 149 排了 AWDL 槽位。现有样本尚不能确定实际载荷帧的信道、带宽、NSS、MCS、重试、A-MPDU/A-MSDU 或 BA。在限定的 Mac 日志里未见这些字段，也未匹配到 iPhone 的 AWDL 地址，因此未确认该 iPhone 的 peer setup 细节。清理后两端接口仍 active，自有探针、临时目录、测试监听和 USB 转发均已退出。 [16 MiB 传输](../../experiments/iphone-radio-research/native-awdl-schedule-16mib.json) · [16 MiB 调度](../../experiments/iphone-radio-research/native-awdl-schedule-evidence.json) · [64 MiB 传输](../../experiments/iphone-radio-research/native-awdl-schedule-64mib.json) · [64 MiB 调度](../../experiments/iphone-radio-research/native-awdl-schedule-64mib-evidence.json) · [清理核对](../../experiments/iphone-radio-research/native-awdl-schedule-postcheck.txt)

小米对照可复用收集器中的 `mac_schedule(start_ns, end_ns, [(role, peer_mac)])`，使用 Mac 端计时的 wall 起止，配合同一应用流标识与 strict `awdl0` 路径。收集器用 `log show --info --debug --style compact` 查询短窗口，谓词限定 `kernel` 且消息含 `com.apple.p2p.awdl0` 或 `Inserting Awdl availability in RRM table`，输出调度、RRM 与可选 peer 角色匹配。比较应保留各轮日志稀疏程度，避免把缺快照归成 0 槽。 [调度采集器](../../experiments/iphone-radio-research/observe-native-awdl-schedule.py)

### DLT 147 与私有诊断入口

本机 iOS 上的 tcpdump 4.99.1 / libpcap 1.10.4 在 `awdl0` 枚举 EN10MB、147（无打印支持）和 RAW。仅编译过滤器时，`-y USER0`、`-y DLT_USER0` 和 `-y 147` 都在名称解析阶段报 invalid data link type。Apple Darwin 21 把 147 命名为 `DLT_USER0_APPLE_INTERNAL`，PKTAP 则为 149。上游同版本 libpcap 的名称表没有这些 USER0 名称。本轮未发数字 `BIOCSDLT` 或采集 147 帧，头格式与是否包含无线元数据仍未知。 [Apple Darwin 21 BPF 定义](https://github.com/apple-oss-distributions/xnu/blob/xnu-8020.140.41/bsd/net/bpf.h#L655-L665) · [上游 libpcap 名称表](https://github.com/the-tcpdump-group/libpcap/blob/libpcap-1.10.4/pcap.c#L3170)

BCM4350 固件的限定静态字符串筛选命中 `awdl_afs_pload`、`awdl_dfsp_cfg`，没有从名称确定 GET 支持或参数布局。IORegistry 可见 `IO80211APIUserClient`、`IO80211P2PDaemonUserClient` 和 CoreCapture pipe/stream 类，尚无适用于此机的只读请求 ABI。本轮沿用已有日志，未启用 CoreCapture。公开旧 Mac 的 `wl dump awdl_advertisers` 及 entitlement 记录、较新 BCM4364/4377 的 AWDL RFC，均不能确定这部 iOS 15 设备的权限或 ABI。 [Stute 论文 §4.3](https://tuprints.ulb.tu-darmstadt.de/11457/1/dissertation_milan-stute_2020.pdf) · [brcmfmac AWDL RFC](https://lists.openwall.net/linux-kernel/2026/11/04/2272)

### DLT 147 的内核选择验证

在 iPhone8,2 / iOS 15.7.1 上，使用 Darwin 21 XNU 的 BPF 定义做一次数值 ioctl 测试。探针以 `O_RDONLY` 打开 BPF fd，先装拒绝全部数据包的过滤器，再挂接 `awdl0`。初始 `BIOCGDLT=1`，`BIOCSDLT(147)` 返回成功，复读得到 147，进程退出码 0。XNU 实现将该 fd 重挂到同一 ifnet 的指定 DLT tap。此次没有开启 promisc、读写数据包或启动传输。探针部署前后 SHA 一致，临时目录和 USB 转发已清理。这个结果确认 tcpdump 的名称错误并未反映内核的 DLT 选择能力。147 的生产者、头格式和 PHY/聚合字段仍未知。 [探针源码](../../experiments/iphone-radio-research/awdl-bpf-select.c) · [实机输出](../../experiments/iphone-radio-research/native-awdl-bpf-select.txt) · [Darwin 21 BPF 实现](https://github.com/apple-oss-distributions/xnu/blob/xnu-8020.140.41/bsd/net/bpf.c)

公开的 iOS 15.6.1 iPod9,1 原始启动日志可见 AppleBCMWLANProximityInterface 为 `awdl0` 注册 147，同一日志中的 APSTA 接口也注册 147，并另外出现标准 radiotap DLT 127 的注册。该近版本记录能辅助识别驱动入口，不能确定本机 147 的每帧布局。Wireshark 将 pcap 链路类型 147 映射为通用 `USER0`。其 AWDL dissector 从 802.11 vendor action 与 LLC Apple PID 分派，不负责解码 147 的外层头。现有公开实现未给出可用于本机的固定偏移或元数据语义，因此仍没有从 147 读包。 [公开原始启动日志](https://gist.github.com/GeoSn0w/506bedf8b85617c3aed03fc0b7d255cc) · [Wireshark 147 映射](https://github.com/wireshark/wireshark/blob/master/wiretap/pcap-common.c#L270) · [Wireshark AWDL 分派](https://github.com/wireshark/wireshark/blob/master/epan/dissectors/packet-awdl.c#L2758-L2759)

Mac 的 `netstat -s -I awdl0` 有真正的接口协议统计分支，TCP 分支从该 ifindex 的 `IFDATA_SUPPLEMENTAL` 读取握手及错误类计数。加 `-p tcp` 则走协议级统计分支，不纳入 AWDL 对照。测试前的 `netstat -S -I awdl0` 只返回接口 LQM 和 availability，没有可选的 Wi-Fi link-status 字段。即使某次返回这些字段，其 config frequency 或 effective bandwidth 也需要核实是否代表指定 peer 的实际 PHY。 [Apple netstat 分支](https://github.com/apple-oss-distributions/network_cmds/blob/main/netstat.tproj/main.c) · [接口 TCP 计数](https://github.com/apple-oss-distributions/network_cmds/blob/main/netstat.tproj/inet.c) · [接口状态读取](https://github.com/apple-oss-distributions/network_cmds/blob/main/netstat.tproj/if.c)

现有小米 64 MiB 同方向样本中，Mac→小米约 2.133 MB/s 的一轮出现 0.602 s 的连续零 `tcpi_txbytes` 增量。该段 Mac 的拥塞窗约 3.24 MB、发送方记录的对端通告窗约 6.29 MB、发送缓冲约 3.94→2.65 MB，loss recovery 标志为 0，平滑 RTT 从 492 ms 升至 1054 ms。这段窗口读数不支持把停顿归为对端通告窗耗尽。XNU 的 `tcpi_txbytes` 来自 socket TX 累计量，包含 TCP 发送路径统计，和 ACK 进度、固件发射完成或空口时间的含义不同。 [小米时序摘要](../../experiments/owly-evaluation/houji-runner/results/tcp-timeline-summary.json) · [XNU TCP 字段填充](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp_usrreq.c) · [XNU TCP 输出计数](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp_output.c)

### 同一 Mac 上的 BSD TCP 时序对照

保留 iPhone 的原生 Network.framework listener 与随机 Bonjour token。Mac 先验证 token 在 `awdl0` 上可见，再用独立 BSD socket 绑定 `IPV6_BOUND_IF`、本地 link-local 地址和远端 scope，传输前后复核绑定及双方端点，传输后再核对两端 `awdl0` 地址。iPhone 保留严格路径检查。三轮 64 MiB 只跑 Mac→iPhone，每轮独立端口，双端 SHA、路径及接口字节计数均通过。Mac 使用同一个 BSD helper 每 200 ms 对正在传输的 fd 取样，与此前 Network.framework 的单次报告分开比较。MSS 未人为设置，三轮均为 1428。 [BSD 客户端](../../experiments/iphone-radio-research/mac_awdl_bsd.py) · [运行器](../../experiments/iphone-radio-research/run-awdl-baseline.py)

| 64 MiB Mac→iPhone | MB/s | 采样数 | SRTT 最小 / 中位 / 最大 ms | 首末点间重传 B / 包 | 传输窗口内 `Data` 快照的 149 槽数 |
|---|---:|---:|---:|---:|---|
| 1 | 41.985 | 8 | 20 / 38 / 68 | 0 / 0 | 11、11 |
| 2 | 21.928 | 16 | 5 / 43 / 178 | 2616 / 2 | 11、7、7 |
| 3 | 22.162 | 16 | 5 / 38 / 173 | 2616 / 2 | 7 |

三轮按总字节 / 总 Mac 用时加权为 **26.191 MB/s**，首轮明显快于后两轮。SRTT 分布排除初始 0 ms，各轮有效值 7、15、15 个。三轮的拥塞窗及发送端记录的对端通告窗中位数均为 2,097,120 B。loss recovery 置位的采样点分别为 0、1、1。三轮的相邻样本均见 `tcpi_txbytes` 增长，只能排除采样点上观察到的连续零增量，采样之间可能仍有短暂间歇。重传差值只覆盖首末采样，不能补齐首末采样外的传输。调度按 Mac wall 窗口提取，三轮分别见 2、3、3 条 RRM 记录，限定日志没有出现 PHY/BA 字段，本轮未尝试匹配 peer 地址。 [完整时序和校验](../../experiments/iphone-radio-research/native-awdl-bsd-timeline-64mib.json) · [时序及调度摘要](../../experiments/iphone-radio-research/native-awdl-bsd-timeline-64mib-summary.json) · [转发清理](../../experiments/iphone-radio-research/native-awdl-bsd-timeline-postcheck.txt)

小米历史同方向的两轮 64 MiB BSD 时序是 1.295 / 2.133 MB/s，MSS 1148，SRTT 峰值 1000 / 1504 ms，最长连续 TX 统计持平约 0.397 / 0.602 s。相同 Mac 和采样 API 下，Apple 三轮未出现这一级别的 SRTT 峰值和发送统计停顿，且其通告窗中位数更小。两组的 MSS、对端与无线运行条件不同，也没有同窗同方法的两组调度样本，当前差异无法归因到某种 AWDL PHY 或 BA 模式。 [小米时序摘要](../../experiments/owly-evaluation/houji-runner/results/tcp-timeline-summary.json)

Mac 可选发送 MSS 对照在已连接、已验证路径的 fd 上设置 `TCP_MAXSEG=1148`，用 `getsockopt(TCP_MAXSEG)` 和 `TCP_CONNECTION_INFO` 双重核对，并要求每个有效采样点保持 1148。XNU 实现将此值作为发送侧 `t_maxseg` 上限。小米的 1160 则设在 Android listener，历史 Mac 发送端采到的实际 MSS 为 1148。Mac 回环的发送端将 1160 和 1148 分别读回原值，故这里直接匹配 1148。该设置只针对 Mac 发送分段上限，iPhone SYN MSS 协商及反向连接行为与小米测试仍可能不同。 [Apple TCP 选项定义](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp.h#L204-L210) · [XNU 设置实现](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp_usrreq.c#L2286-L2298) · [回环记录](../../experiments/iphone-radio-research/native-awdl-bsd-mss-loopback.txt)

设置有效 MSS 1148 后，另做三轮 64 MiB Mac→iPhone。每轮设置前均为 1428，全部 42 个时序点均保持 1148，双端 SHA 和 AWDL 路径均通过。

| 有效 MSS 1148 | MB/s | 采样数 | SRTT 最小 / 中位 / 最大 ms | 首末点间重传 B / 包 | `Data` 快照的 149 槽数 |
|---|---:|---:|---:|---:|---|
| 1 | 27.304 | 13 | 14 / 27 / 60 | 7448 / 8 | 11、11 |
| 2 | 24.885 | 14 | 8 / 30 / 183 | 3444 / 3 | 7、7 |
| 3 | 22.546 | 15 | 7 / 44 / 202 | 2296 / 2 | 7、7 |

加权 **24.760 MB/s**，相对默认组 26.191 MB/s 低约 5.5%。两组顺序进行，未交错，默认组首轮明显更快。因此仅可确认这组原生连接在匹配 Mac 发送端 MSS 后仍远快于小米历史样本。小 MSS 本身不足以解释约 2 MB/s 的数量级。此组也未见连续 TX 统计持平。三轮各有三条 RRM 记录，限定日志仍未见 PHY 或 BA。设备探针 SHA 在前后均通过，设备临时目录已移除，USB 转发已退出。 [有效 MSS 1148 时序](../../experiments/iphone-radio-research/native-awdl-bsd-timeline-mss1148-64mib.json) · [同窗调度摘要](../../experiments/iphone-radio-research/native-awdl-bsd-timeline-mss1148-64mib-summary.json) · [清理](../../experiments/iphone-radio-research/native-awdl-bsd-timeline-mss1148-postcheck.txt)

## 原生 iPhone 的 AWDL 能力广告对照

公开 Owly 语料的三份 iOS 26.x ↔ Linux 抓包由 MT7925e 在信道 44 记录。按同提交 TOML 的 `local` 和 `peers` 绑定发帧角色，使用 radiotap 头长度定位 802.11 帧，校验 AWDL OUI/type 后按长度解析 TLV。仓库提交、每份 pcap/manifest SHA-256、各角色完整帧、截断及无法解码计数均写入摘要。此语料里的 iPhone 型号未确认，与本文实测 Apple phone 的 iOS 15.7.1 分开。工作区此前的 `awdl-actions.pcap` 属 Mac↔小米测试，不能作为这部 iPhone 的广告证据。 [公开抓包及脱敏方法](https://github.com/t4t5/owly/tree/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/tests/captures) · [本地解析器](../../experiments/iphone-radio-research/summarize-native-awdl-mif.py) · [按角色与源哈希归档](../../experiments/iphone-radio-research/public-ios26-awdl-mif.json) · [Mac↔小米旧抓包出处](10-throughput-limits.md)

| 语料 | iPhone PSF / MIF 含 TLV 17 | Linux PSF / MIF 均无 TLV 17 | iPhone 发出的 ADDBA 请求 |
|---|---:|---:|---|
| discovery | 296 / 101 | 303 / 519 | 9，接收端无法绑定至 manifest 的 Linux 角色 |
| receive | 312 / 190 | 367 / 630 | 18，iPhone→Linux |
| send | 97 / 466 | 593 / 1019 | 14，iPhone→Linux |

三份语料共 9,373 个 pcap 记录，没有捕获长度截断或显式坏 FCS 标志。可归属两角色的全部 PSF/MIF 都通过 TLV 长度检查。iPhone 的 705 个 PSF 和 757 个 MIF 均在 HT TLV 7 之后紧接 TLV 17，容器只含 12 字节的 VHT IE 191。历史自有 Mac 的 24 个完整 TLV 17 容器均在 MIF 里，位置同样在 HT 之后。因平台样本不同，小米首测采用 MIF-only 有自有 Mac 先例，若 Mac 收到后无变化，可以单独验证 PSF 传播，避免在同一轮改变多个字段。 [自有 Mac 摘要](../../experiments/remote-peer-probe/vht-rx-next/outgoing-advert/wire-reference.json) · [Wireshark 的 AWDL 容器解析](https://github.com/wireshark/wireshark/blob/master/epan/dissectors/packet-awdl.c)

iPhone 的 HT 能力为 `0x006f`，RX MCS mask `ffff`，A-MPDU 参数字节 `0x1f`，Linux 有 HT 的帧为 `0x11ce / ff / 0x1b`。iPhone VHT 能力为 `0x03800032`，Max MPDU 11454、SGI80、A-MPDU exponent 7，RX/TX MCS map 均为 2SS 的 MCS 0–9，两个 highest rate 原字段为 0。iPhone TLV 12 长 47 B，flags 为 `0x9f23`，其中 12 个 MIF 为 `0xbf23`；Linux TLV 12 为 15 B、`0x8f24`。Wireshark 将差异位标注为基础设施 BSSID/地址、AWDL 地址、unicast options 和 Real-Time 等，尚无这些字段是 Mac 接受 VHT 必要条件的证据。两角色都宣告 44/opclass 128 槽位，因此单看该槽位也不足以判定有效 VHT。抓包只保留频道和 RSSI，不提供实际数据 PPDU 的带宽、NSS、MCS 或 A-MPDU 标识。 [Wireshark 字段定义](https://github.com/wireshark/wireshark/blob/master/epan/dissectors/packet-awdl.c) · [Linux 802.11 VHT 位域](https://github.com/torvalds/linux/blob/master/include/linux/ieee80211-vht.h)

小米候选 `bf0c00000000fcff0000fcff0000` 的 IE ID 191、body 12 B，能力字为 0，RX/TX map 都是 1SS MCS 0–7，Max MPDU 3895，80 MHz 基本支持，未声明 160/80+80、SGI80、STBC 或 BF。highest rate 为 0 有公开 iPhone 实例。A-MPDU exponent 0 仍编码 8191 B 最大长度，不能把该能力字解释成关闭聚合。候选只验证能否被 Mac 接收并采纳，当前 4224 B host 路径验证仍未确认 firmware 的 VHT 接收与聚合能力。

表中的 41 个 ADDBA 都是 TID 0、window 64、immediate BA，并允许 A-MSDU。可归属双方没有出现 ADDBA response，无法给出协商状态或推及 Apple↔Apple 链路。要取得自有 6s Plus 的实际证据，可在独立支持 radiotap 的监听机上按自有两端空口地址筛选，覆盖连接前的 action 阶段与短数据窗口，比较同一个 Mac 发往 iPhone 和小米的 PHY、QoS TID、ADDBA 及 Block Ack。若监听端仅提供 RSSI 和 TSFT，仍可比较 MIF/PSF 和 BA 管理帧，PHY 字段应保留缺失状态。无需再重复同 MSS 吞吐。

Mac 已有的 `netstat -S -I awdl0` 可作为低扰动旁路。Apple 实现用 ifindex 添加 `NSTAT_PROVIDER_IFNET`，发送 `NSTAT_MSG_TYPE_GET_SRC_DESC` 读取 `nstat_ifnet_descriptor`，接口状态走 `SIOCGIFINTERFACESTATE`。只有 descriptor 声明 Wi-Fi link-status 类型才会输出该块，单项还受 valid bitmask 约束。本机非传输样本只返回 LQM 和 availability。若有其它已批准的流量窗口，可用同一 Mac 单调时钟在流前、流中和流后分别调用一次，每次 2 秒上限，保存状态和字段存在性。即使出现 effective bandwidth，其定义也是 bps 而非 RF 信道宽度，配置频率也是频段指示。这条接口没有逐 peer 的 BW/NSS/MCS、BA 或 A-MPDU 字段。 [Apple netstat GET 实现](https://github.com/apple-oss-distributions/network_cmds/blob/main/netstat.tproj/if.c) · [XNU Wi-Fi 状态字段](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/net/if_var_status.h)

两端本轮各做一次有界查询，未发起自有数据流。Mac 与 iPhone 的 `awdl0` 均退出 0 且有接口状态，但均缺 `wifi status` 块。两端的 `en0` 正对照都返回该块，证明工具能在同一设备显示已提供的统计。查询仅保留状态和数字字段，USB 转发已清理。此结果没有覆盖主动传输期，也没有从 `en0` 读数推导 AWDL PHY。 [两端接口状态与正对照](../../experiments/iphone-radio-research/native-awdl-netstat-link-status.txt)
