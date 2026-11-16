# 小米原生互传 `owly-xos` 高速后端（`MIShare` + Owly `xos0`）

本目录为小米 14（`houji`）系统原版 **小米互传（`MIShare.apk` / `com.miui.mishare.connectivity`）** 提供替代原厂 `libxos.so` 的高速 `xos0` AWDL 后端：在不改动或重签名小米系统 APK 的前提下，保留系统分享菜单、小米超级岛弹窗与相册收发入口，同时把底层链路从 `libxos.so`（HT20 MCS3）升级为 `owly-awdl` VHT80 2SS MCS9 SGI80。

## 为什么原生互传使用 `ezif.bin` + `pt_remote_multi=1`（`pt_exclusive=1`）

原版 `MIShare` 的 `AwdlServiceClient` 只在会话开始时调用一次 `IDomesticXOsComm.startDomestXOsComm(149)`，不传入对端 MAC 地址，也不会为发现到的设备逐台重建无线会话；它在单个持续的 L2 TAP 接口（`xos0`）上同时完成 `ff02::fb` mDNS 组播发现以及对任意 Apple 设备的 TLS 收发。

- **`ezif.bin` + `pt_remote_assoc=1`（独立 App `Owly AirDrop` 路线，当前暂缓）**：可在 2.4 GHz 与非 DFS 5 GHz STA 共存下达到最高吞吐，但旧路径在确定对端 MAC 时需按单一对端地址重新关联并重载驱动。
- **`ezif.bin` + `pt_remote_multi=1` + `pt_exclusive=1`（本目录 `owly-xos` 路线）**：内核通过 `pt_remote_peer_ctl`（`0600`，`add <mac>` / `del <mac>` / `clear`）支持最多 **8 个并发对端槽位**的免预绑定动态 WMI/DP peer 生命周期管理，无需在 `insmod` 时写死 `pt_remote_mac`，即可在单一持久化 `xos0` 会话中解锁官方 `ezif-amss20.bin`（`wire506=1`）**硬件 TX A-MPDU 聚合**、`pt_rx_msdu_buf=1` REO MSDU-buffer RX 与 VHT80 2SS MCS9 SGI80；退出时自动经 `clear` 与 `pt_release` 恢复普通 Wi-Fi。

## 架构与关键适配

```text
MIShare.apk (com.miui.mishare.connectivity)
    │ AIDL Binder: com.domestic.xoscomm.IDomesticXOsComm/default
    ▼
XosBridge.java (app_process + bridge-supervisor.sh)
    │ stop/start 2s 去抖动 + Binder unlinkToDeath/linkToDeath
    ▼
radio.sh (enter / restore + 退出快照 & Wi-Fi 重连恢复 + 独占锁，实测冷启动 ~3.16s)
    │ 绑定已预部署的 ezif-amss20.bin + qca_cld3_kiwi_v2 (pt_exclusive=1, pt_remote_multi=1,
    │ pt_tx_ampdu=1, pt_dynamic_rate=1, pt_single_ring=0, pt_tx_backpressure=1,
    │ pt_rx_msdu_buf=1, pt_rx_ezif_marked=1, pt_remote_reo=2 win=256)
    ▼
owly-xosd xos0 amsdu ack (纯 L2 守护，零独立 App 依赖；HOUJI_OWLY_TX_ADDBA=2, max_peers=8, fast_discovery=true)
    ├── owly-awdl fast_discovery（启动即发 PSF+MIF，遇未知/未确认对端立即触发受限频次 MIF）
    ├── KernelPeerSync 异步写 sysfs pt_remote_peer_ctl (add/del/clear，不阻塞定时器)
    ├── PeerUp 双向 mDNS 触发（立即外发 QU _airdrop._tcp PTR 查询 + 定时向 xos0 注入入向 PTR 提示）
    ├── owly-awdl L2 TAP (xos0, mtu=1450, uid 0 策略路由表 10074)
    ├── 用户态 NDP 应答 (synthesize_na_for_ns)
    ├── RFC 2464 IPv6 组播以太网目的 MAC 修正 (ff:..:ff -> 33:33:..)
    └── 入向单播 mDNS 响应 (QR=1, :5353) 转投 ff02::fb (fanout_unicast_mdns_response)
```

- **AIDL 契约兼容与防泄漏**：`XosBridge.java` 注册 `com.domestic.xoscomm.IDomesticXOsComm/default`，兼容 `startDomestXOsComm(token, chan)` / `stopDomestXOsComm()` 与生命周期 `DeathRecipient`；`bridge-supervisor.sh` 与 `radio.sh` 在 `exec` 前关闭 heredoc/flock 继承的 `3..9` 文件描述符，避免锁泄漏。
- **冷启动进入优化（`~6.2–9.2s` → `3.16s`）与状态保护**：`install.py` 预部署 `$RADIO/amss20.bin`（`radio.sh enter` 仅在缺失或哈希不一致时重拷贝），消除大文件重复 SHA-256 校验，并用 `50–100 ms` 事件轮询（Wi-Fi 下线、`cnd`/`wifi_hal_legacy` 与 `refcnt == 0`、`pt_fw_caps` + `pt_activate` 就绪、`start_xos` `OWLY_READY`）替换固定 `sleep 0.5/1/2` 等待；`capture_session_stats()` 在覆盖 `last-session-stats.txt` 前保留一份 `prev-session-stats.txt`，避免短暂空会话覆盖真实传输统计。
- **发现延迟优化（AWDL + mDNS 双向触发）**：开启 `owly-awdl` `fast_discovery`（启动零延迟发射 PSF+MIF，并在 20 ms 冷却内对首次/未校验对端即时回复 MIF），同时在 `Tap::peer_up` 时向对端立即发送（及 `+250 ms` 重发）`_airdrop._tcp.local.` `PTR QU` 查询，并在 `+50 ms` / `+900 ms` 向 `xos0` 写入对端 `QM` 查询提示 `MdnsAdvertiser.xos0` 立即广播本机服务（`QR=0` 被 `RawMdnsBrowser` 安全忽略）。
- **Android 16 SELinux `Enforcing` 下的 Binder 访问**：`bridge-supervisor.sh` 保持设备处于 `Enforcing`，通过 `ksud sepolicy patch 'allow platform_app default_android_service service_manager { find }'` 放行查询，并在每次启动首次运行 `runcon u:r:ksu:s0:c{0..7} /system/bin/service list` 驱逐 `servicemanager` 512 节点 `avc_cache` 中残留的负缓存，使系统级 `platform_app`（`MIShare`）无需降级 SELinux 即可直连。
- **`xos0` TAP 上的 Android IPv6 / mDNS 兼容**：
  1. Linux IPv6 栈（`ip6_rcv_core`）会丢弃以太网目的 MAC 为广播地址（`ff:ff:ff:ff:ff:ff`）的 IPv6 包；`owly-awdl` 在序列化以太网帧时将 IPv6 组播统一映射为标准 RFC 2464 地址 `33:33:...`。
  2. `MIShare` 使用系统 `NsdManager` 注册服务，导致 `system_server` 中的 `NsdService` 在 `xos0` 上持有 `SO_BINDTODEVICE` 绑定到 `:5353` 的 UDPv6 socket，会抢走发送给本机 link-local 的单播 mDNS 响应；`houji-runner` 在投递 `xos0` 前将对端发给本机单播 `:5353` 的合法 mDNS 响应（`QR=1`）改写投递到 `ff02::fb` 并重算 UDPv6 校验和，确保 `RawMdnsBrowser` 的 `MulticastSocket` 稳定收到组播与 `QU` 单播应答。
- **与独立 App（`Owly AirDrop`）互斥与恢复**：运行时写入 `/data/local/tmp/xos/native/status` 与 `/data/local/tmp/xos/owly/state/status`，遵循同一套 `saved.env`、驱动 `pt_release` 与 `refcnt == 0` 门禁。`clients/owly-app` 运行脚本启动前会暂停原生/桥接 AIDL 服务，停止恢复后再由 `bridge-supervisor.sh` 拉起。

## 实测结果（信道 149，`pt_exclusive=1` + `ezif.bin` + `pt_remote_multi=1` + `pt_remote_rx_window=256` + `xos0`）

### 1. 16 MiB TCP 吞吐对比

在 `xos0`（1450 MTU、1 ms A-MSDU、RX BlockAck 窗口 256、免预绑定多对端硬件 TX A-MPDU）与 Mac 原生 `awdl0` 之间实测单向 16 MiB（`16,777,216` 字节，双向 SHA-256 全通过）TCP 传输：

| 方向 | 原版 `libxos.so`（HT20 MCS3） | 旧 `dzf2.bin` 软件 A-MSDU（`xos0`） | 当前 `ezif.bin` + 多对端 A-MPDU + RX 窗口 256（`xos0`） | 对比原版提升 |
|---|---:|---:|---:|---:|
| 小米 `xos0` → Mac `awdl0` | `0.21–0.29 MB/s` | `6.85 MB/s` | **64.08 MB/s**（`0.262s`；首次 `win=64` 基准 `23.14 MB/s`） | **~220×–305×** |
| Mac `awdl0` → 小米 `xos0` | `0.48 MB/s` | `9.95 MB/s` | **9.55 MB/s**（`9.11 MiB/s`；`win=64` 基准 `8.09 MB/s`） | **~20×** |

动态发现并并发注册最多 `active=2 tx_ready=2`（`ast_valid=1`、`setup_rc=0`）两台周边设备且无预设 `pt_remote_mac`。双向 16 MiB 传输期间，内核 `pt_rate_stats` 单轮 TX 观测 **`agg_ppdus +307`**、**`agg_mpdus +6103`**、100% `bw=2`（80 MHz）/ MCS9 / SGI / LDPC，峰值 **`max_ppdu_mpdus=31`**（284 个 PPDU 位于 17–32 MPDU 档）；`pt_rx_msdu_stats` 全程 **`peer_miss=0`、`bad_desc=0`、`dropped=0`**（`seen=reaped=delivered=5928`），`OWLY_RX_GAPS` 记录 `matched=24298 missing=0 host_delayed=0`；`tap_rx_invalid=0`、`tap_delivery_drops=0`；离线与退出自动触发 `op=clear`。

### 2. 原版 `MIShare.apk` 端到端发现与 `157.80 MB` 视频收发实测

对同一 `157,799,892` 字节（`157.80 MB`）MP4 视频与 Apple 手机执行系统原版 `MIShare.apk` 双向收发（匿名化日志与内核快照见 [`evidence/`](evidence/README.md)，完整分析见 [`docs/airdrop/34-owly-xos-validation.md`](../../docs/airdrop/34-owly-xos-validation.md)）：

- **冷启动与发现时延**：
  - `startDomestXOsComm(149)` 驱动+固件冷启动：发送 **`3.616s`**、接收 **`3.444s`**（CLI `radio.sh enter` **`3.161s`**，较优化前 `6.17–9.19s` 缩短约 45%–60%）；
  - 接收侧：`NsdAdvertiser` 在 `xos0` 注册完成后仅 **`1.462s`**（自 BLE 唤醒触发起共 **`6.176s`**）即收到 Apple 设备 TLS `POST /Discover` 并返回 `200 OK`；
  - 发送侧：`XOS_READY` 并开启 BLE 唤醒后 **`2.425s`** 即由 `RawMdnsBrowser` 发现对端服务，点击分享至 `/Discover 200 OK` 出头像共 **`9.027s`**。
- **发送方向（小米 `xos0` → Apple 手机，`157.80 MB`）**：
  - 点击发送后 TCP 建连 **`8 ms`**、TLSv1.3 握手 **`17 ms`**；`/Upload` 正文 **`8.398s`** 发完（**`18.79 MB/s`**），含对端落盘返回 `HTTP 200` 共 **`9.422s`**（**`16.75 MB/s`**；此前同视频后半程稳态 **`22.73 MB/s`**）；
  - 内核 `pt_rate_stats`：`valid=23,308` 帧中 **`99.97%`** 为 VHT80 MCS9 SGI LDPC，**`agg_ppdus=3,338/3,713`（`89.9%`）**、**`agg_mpdus=22,933`（`98.4%`）**，反向 RX `seen=delivered=372`、`dropped=0`。
- **接收方向（Apple 手机 → 小米 `xos0`，`157.80 MB`）**：
  - 单对端 `win=64` 基线：`/Upload` 全程 **`11.970s`**（**`13.18 MB/s`**），后半程稳态 **`20.35 MB/s`**，`0.804s` 窗口 **`26.08 MB/s`**，单块突发 **`41.12 MB/s`**；
  - 3 个 AWDL 节点在场 `win=256` 实测：前 `15.95s` 对端以单包模式保守发送（`3.42 MB/s`），切入双包 A-MSDU 后 `7.91s` 收完剩余 `102.87 MB`（阶段均速 **`13.00 MB/s`**、双块窗口 **`23.56 MB/s`**、单块峰值 **`29.54 MB/s`**）；全程交付 `114,598` 个 IPv6 包，`OWLY_RX_GAPS` `matched=37,093 missing=0 host_delayed=0`（平均每 PPDU `16.1` 个 A-MSDU 大帧），全链路 `dropped=0`。
- **退出恢复**：停止 `AirDropService` / `MiShareService` 后，自动经 `op=clear` 与 `pt_release` 卸载实验模块、恢复固件挂载并重连原厂 5 GHz Wi-Fi（`STOCK_RESTORED`）。

## 系统、内核与运行环境要求

本目录加载的树外内核模块（`blobs/local/qca_cld3_kiwi_v2-owly-xos.ko`、`houji_wonder_bridge-owly-xos.ko`、`wonder-owly-xos.ko`）已完成 **570 个导入符号 CRC** 严格审计（见 [`audit-f2-f6-f10.json`](audit-f2-f6-f10.json)），仅在完全匹配的目标内核与原厂固件环境下放行运行（安装脚本 `customize.sh`、开机守护 `service.sh` 与切换脚本 `radio.sh` 均执行硬校验，OTA 变更内核后会自动安全停用）：

| 检查项 | 要求值 | 说明 |
|---|---|---|
| **设备机型** | 小米 14（`ro.product.device = houji`） | 仅适配 `houji`（如 `23127PN0CC`），不适用 `shennong` / `aurora` |
| **系统版本** | 澎湃 HyperOS 3 / Android 16（`SDK = 36`） | 实测基线 **`OS3.0.307.0.WNCCNXM`**（适配 SDK 36 原厂互传与系统分享 `ChooserActivityStubImpl`） |
| **内核版本（`uname -r`）** | `6.1.138-android14-11-g0c3d559bcd85-ab14529422` | 必须一字不差完全一致 |
| **原厂驱动 Build-ID** | `a3e9bd62646444e1ecbe0f421c95ec46e6caf69a78323efb2f6ebe00e6b2bd8b` | `/sys/module/qca_cld3_kiwi_v2/notes/.note.gnu.build-id` SHA-256 |
| **原厂 WLAN 固件哈希** | `4db7b0e1aa7e4ec071708359c9d0db8d3d7872390cd8e88ffa0bbb30d560bcc8` | `/vendor/firmware_mnt/image/kiwi/amss20.bin` SHA-256 |
| **Root 与模块框架** | KernelSU（或 APatch / Magisk）+ Zygisk + LSPosed | 原厂提取 `MIShare.apk`（`5.4.2`）持有小米平台签名，支持 `pm install -r` 直装；模块声明 `skip_mount`，**无需** OverlayFS / `magic_mount` 元模块，也**无需** CorePatch 核心破解 |

### LSPosed 兼容模块（`compat/`，`org.houji.xoscompat`）作用域说明

`compat/` 提供自包含的轻量 LSPosed 模块 **「Owly-XOS 小米互传兼容」**，采用 **`AndroidManifest.xml` `<meta-data android:name="xposedscope" android:resource="@array/xposed_scope" />`（默认推荐作用域）+ `Hook.java` 包名硬白名单** 双重约束，而非 `libxposed` API 100 专属的 `staticScope=true`：

- 在 LSPosed 1.8.x / 1.9.x / 2.x 全系列管理器中，首次启用模块时会自动勾选且仅推荐以下两个进程（无需手动翻找系统应用）：
  1. `com.miui.mishare.connectivity`（小米互传）：放行 `IDomesticXOsComm/default` 可用性检测，并在 `sys.xos.exclusive=1` 独占无线电切换期间抑制延迟 Wi-Fi 关闭广播误判；
  2. `com.android.intentresolver`（系统分享）：修复相册点击「隔空投送 / 小米互传」时的 URI 授权跳转。
- `Hook.java` 入口对 `LoadPackageParam.packageName` 执行精确匹配，即便用户误勾选其他应用（如 `android` 系统框架）也立即空返回，不注入任何 Hook。

## 构建、直连部署与 KernelSU 模块打包

`owly-xos` 构建过程完全自包含，**不依赖 `clients/owly-app`（独立 App）**；原生守护 `owly-xosd` 直接从 [`native/houji-runner/`](../../native/houji-runner/) 构建（仅链接 L2 `owly-awdl`，体积约 `1.6 MB`），清单由本目录 [`runtime-manifest.json`](runtime-manifest.json) 独立定义。需按 [BYOB 输入说明](../../docs/guides/byob.md) 与 [构建说明](../../docs/guides/building.md) 准备好工具链及 `blobs/local/`。

```sh
# 1. 一键构建 owly-xosd、wonder-init、xos-bridge.zip 与 Owly-XOS-Compat.apk（全部输出至 runtime/owly-xos/dist/）
ANDROID_NDK_HOME=/path/to/android-ndk \
ANDROID_HOME=/path/to/android-sdk \
BUILD_TOOLS=/path/to/android-sdk/build-tools/35.0.0 \
  bash scripts/build-xos.sh

# 2a. 开发调试：通过 ADB 直接部署并拉起守护
ANDROID_SERIAL=<serial> python3 runtime/owly-xos/install.py

# 2b. 打包单文件 KernelSU / APatch / Magisk 刷机包（输出至 runtime/owly-xos/dist/，已被 git 忽略）
python3 runtime/owly-xos/package-ksu.py --mishare-apk /path/to/MIShare.apk
```
