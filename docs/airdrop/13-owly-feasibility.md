# Owly 在 houji 上的设计与可行性

日期：2026-11-10。源码锁定为 [`t4t5/owly` commit `6f2e8ea99aed0687558d78a1dd49154ab3a5fa76`](https://github.com/t4t5/owly/tree/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76)，浅克隆位于 `downloads/airdrop-driver-work/owly-evaluation/owly/`。本文保留起初的离线设计，实机结果以独立记录为准。

## 实机更新

Android runner 已在 houji 验证。独立初始化器配置 Wonder，Owly 通过 `wonder0` AF_PACKET 与 `owly0` TUN 在 149 信道连接 Mac `awdl0`，两端 TCP/SHA-256 通过。构建在 `build-host`，实验退出恢复 stock Wi-Fi、固件、驱动及原 149/MCS3 native supervisor。具体吞吐、失败和恢复证据见 [149 信道实机记录](14-owly-houji-device.md)。

## 原离线结论

**离线看可以做一个小型 Android runner；不需要把 Owly 的 Linux 桌面 daemon 整体移植。** 复用 `owly-awdl` 的 AWDL 状态机/时隙调度及 Tokio driver，自行实现 `Air` 和 `Netif`；无线模块、wonder 生命周期及恢复继续由项目现有 root radio lifecycle 管理。首个集成验证可用 TUN 作为 IPv6 L3 接口，随后再确认原版 MIShare 是否能沿用预期的 `xos0` 名称和 NetworkAgent 流程。

`wonder0` AF_PACKET 与 `/dev/net/tun` ioctl/权限已在独立探针验证。MIShare 对 TUN 型 `xos0` 的接受度仍未测试。

## 分层与 Android 依赖

- `owly-awdl` 是 `#![no_std]` Sans-IO 协议状态机；`driver` feature 添加 Tokio task/timer，只依赖 Tokio 与 tracing。其 `Air` trait 提供 802.11 收发，`Netif` 提供 IPv6 packet 收发和可选 peer 通知。[`lib.rs`](https://github.com/t4t5/owly/blob/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/crates/owly-awdl/src/lib.rs)；[`driver.rs`](https://github.com/t4t5/owly/blob/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/crates/owly-awdl/src/driver.rs#L49-L81)
- `owly-radio` 的 `Backend`/`Injection` 是桌面 Linux 的 radio setup/lifecycle 层：自建 monitor、nl80211/rtnetlink 配置、hostapd/IBSS/active-monitor 模式、Linux 路径与恢复 journal。它不是本机 `wonder0` 驱动的 drop-in replacement。[`radio.rs`](https://github.com/t4t5/owly/blob/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/crates/owly-radio/src/radio.rs#L22-L54)；[`injection/mod.rs`](https://github.com/t4t5/owly/blob/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/crates/owly-radio/src/injection/mod.rs#L1-L20)
- **没有 `libpcap` 或 `libnl` Cargo 依赖。** `owly-radio/Cargo.toml` 列出的是 Rust crate `nix`、Tokio、serde 等；`src/pcap.rs` 用 `std::io` 读写 pcap capture 文件，名称不代表链接 libpcap。AF_PACKET、Netlink/Rtnetlink 是源码中的直接 syscall/协议实现。[`owly-radio/Cargo.toml`](https://github.com/t4t5/owly/blob/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/crates/owly-radio/Cargo.toml#L12-L25)；[`pcap.rs`](https://github.com/t4t5/owly/blob/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/crates/owly-radio/src/pcap.rs#L1-L10)；[`native.rs`](https://github.com/t4t5/owly/blob/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/crates/owly-radio/src/native.rs#L215-L274)
- `owlyd` 通过 `zbus` 提供 D-Bus 系统服务，CLI/策略授权是桌面管理面。它们不是 `owly-awdl` core/driver 的必需项；Android runner 可直接嵌入 crate，不移植 D-Bus、PolicyKit、systemd unit 或 hostapd。`owly-airdrop` 是另一层 AirDrop TLS/HTTP/归档实现；若让已有 MIShare 继续做发现和文件收发，初期也不需要它。
- Android 同为 Linux kernel，AF_PACKET、rtnetlink 和 TUN/TAP 是候选接口，但进程权限、SELinux、`CONFIG_PACKET`、`/dev/net/tun` 是否可用仍需设备验证。反之，Owly 的 `/etc/owly/radio.toml`、桌面 monitor 管理、hostapd 路径和 D-Bus 服务不能原样假定在 Android 存在。初期离线评估只运行了 `cargo metadata --no-deps`。后续在 build-host 安装 Android target 和 Linux NDK 并完成构建。

## 最小 runner 路线

1. **先分离无线 lifecycle 与 radio initializer。** 现有 `radio.sh` 可继续负责模块/固件切换、独占与恢复，但当前 `wonder0` 创建和 vendor 初始化是由 `xos_runner`/`libxos` 路径完成，并会启动其 AWDL 引擎；不能直接让它保持运行再启动 Owly。需要从已有流程提取/复用低层初始化序列，在不启动 XOS AWDL 数据面的情况下准备 `wonder0`；保证单一 AWDL 引擎、接口唯一所有权、停止/异常时能走原恢复流程。不把 `radio.sh` 的模块生命周期等同于 monitor/vendor 初始化器。
2. 将 `owly-awdl` 作为库嵌入小型 Rust runner，先用已验收的 channel 149 / HT20 基本 profile 验证连通性；不默认回退 channel 6。VHT80 仅作为后续独立控制变量性能对照。首轮关闭 `accept_addba` 和 `amsdu`。实现 `Air`：在上述初始化序列准备的 `wonder0` 上收发 radiotap/raw 802.11；匹配 `Air::send` 约定，仅对 `EAGAIN`/`ENOBUFS` 返回原 frame，其他发送错误记录并按约定丢弃。Owly Linux backend 的 packet socket 和 radiotap 代码可作结构参考，不能据源码代替 Rust adapter 实测。
3. 实现 TUN `Netif`：用 `/dev/net/tun` + `IFF_TUN|IFF_NO_PI` 建立 IPv6 L3 interface；读入 IP packet 用 `Ipv6Packet::from_ip`，发送给 host 时写 `packet.data`。Owly 已提供该构造器用于从 IPv6 源/目的地址导出链路层地址，不要求 TAP Ethernet 或邻居表；`peer_up/down` 可先为空实现。[`ip.rs`](https://github.com/t4t5/owly/blob/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/crates/owly-awdl/src/ip.rs#L9-L63)
4. 首次独立验证可给接口用临时名称；保留 MIShare 时再决定是否将其命名为 `xos0`，设置本机 link-local IPv6、路由和 MTU，并通过原 Binder lifecycle 启停 runner。

## 队列与聚合的直接源码证据

`Air::send(Err(frame))` 的 frame 不会立即丢掉：data unicast/multicast 分别进入 driver 的 `VecDeque`；flush 时原 frame 放回队首并在下一次可发送时隙重试。radio queue full 后约每 500 µs 再试。多播只在当前窗口最多约 100 ms 重试，超时/错过时隙会丢；单播最多等待约 2 s，peer 消失或超时也会丢。单播 backlog 达 128 时 driver 禁止 `netif.recv` 分支，让内核接口队列暂时承压；这不是无限缓冲或无损保证。AWDL action/management frame 若 radio queue full 则当次直接丢弃。证据：[`driver.rs`](https://github.com/t4t5/owly/blob/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/crates/owly-awdl/src/driver.rs#L273-L360) 与 `run` 的条件收包分支 [426 行附近](https://github.com/t4t5/owly/blob/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/crates/owly-awdl/src/driver.rs#L416-L444)。这与本地 XOS 32 项循环队列“队满释放当前待发帧”的已定位路径有架构差异，但不能据此保证适配后不丢包或吞吐更高；见[引擎对比](12-engine-comparison.md)和[吞吐记录](10-throughput-limits.md)。

Block Ack 与 A-MSDU 是两项独立开关。Owly 默认 `accept_addba=false`、`amsdu=false`；其 quirk 默认不接收 RX AMPDU BA 会话，并明确要求硬件自己对 A-MPDU 回 Block Ack。项目当前 PASSTHRU 路径固定 `tx_aggr_size=1`，没有证据能正确应答/处理硬件 BA，因此不能开启/伪造 ADDBA 成功。软件可以按 peer HT 能力把多个 IPv6 MSDU 组成一个 A-MSDU（默认关闭，最多 8 个）；这不等于硬件 A-MPDU/Block Ack。首轮禁用两者，先验证基础帧与可靠双向传输，再单独评估 A-MSDU；BA 保持关闭，直到驱动响应能力有独立证据。[`node.rs`](https://github.com/t4t5/owly/blob/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/crates/owly-awdl/src/node.rs#L98-L140)；[`quirks.rs`](https://github.com/t4t5/owly/blob/6f2e8ea99aed0687558d78a1dd49154ab3a5fa76/crates/owly-radio/src/quirks.rs#L30-L44)；本地 [`10-throughput-limits.md`](10-throughput-limits.md)。

## 保留原版 MIShare 集成的适配

原版 MIShare 的 `AwdlManager` 通过 `com.domestic.xoscomm.IDomesticXOsComm/default` 做 start/stop/status，并按 `xos0` 查找 link-local 地址、注册 NetworkAgent、设置路由。项目现有 `XosBridge.java` 已兼容 Binder 三个事务并管理 Binder death token/延迟恢复；`AirDropConfig` 默认接口名是 `xos0`。保留原 APK 的最小方案是让同一桥接服务调用新 runner，且提供 MIShare 可见的 `xos0` 与 NetworkAgent/路由生命周期；文件发现、BLE、mDNS、TLS/HTTP 继续由 MIShare 原实现负责。此时优先尝试以 TUN 形式创建 L3 `xos0`，但如果 MIShare 的 NetworkAgent/NSD 或系统栈依赖 Ethernet/TAP 语义，则改用 TAP `Netif`（`from_ethernet`/`to_ethernet`）或实现兼容层。与 MIShare 集成的替换接口尚未设备验证。证据：[XOS 路线](03-xos.md)、[`XosBridge.java`](../../downloads/madrid-analysis/native-integration/src/XosBridge.java#L6-L67)、本地 `AwdlManager.java` 中等待 `xos0` 与注册 NetworkAgent 的代码。

## 初期验证清单与剩余边界

- Android target、Linux NDK/sysroot 和三个 ARM64 产物已在远端验证。设备端验证覆盖 AF_PACKET/radiotap、TUN、独占初始化、停止恢复及原生服务待命。实验运行于现有 KernelSU 环境。
- 需要验证 TUN 形态的 `xos0` 是否满足 MIShare 的 NetworkAgent、IPv6 multicast、socket binding 和路由行为；不满足时才做 TAP 适配。实验已经验证独立初始化器与 XOS 引擎互斥。
- 先以收发抓包、分阶段 mDNS/IPv6/TCP/MIShare 端到端和哈希验证为准；接口或 crate 编译成功不代表 Android AirDrop 可用。吞吐目标需重复可靠文件传输与哈希数据，不能引用 UDP 或 A-MSDU 单次峰值宣称稳定超过 10 MB/s。
