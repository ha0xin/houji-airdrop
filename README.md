# houji-airdrop

小米 14（houji）上的 AirDrop 实验实现。包括独立 App、WLAN 驱动移植与匿名化验证资料。

**当前为研究源码仓库，不是可直接安装的发布包。** 独立构建已验证；当前开发主线聚焦 [`runtime/owly-xos/`](runtime/owly-xos/README.zh-CN.md)，为小米系统原版 `MIShare` 提供基于官方 `ezif-amss20.bin` 与免预绑定多对端硬件 TX A-MPDU 的高速 `xos0` 后端；独立 App `Owly AirDrop` 在合入普通 WLAN 共存后暂缓迭代。

本项目先后探索并对照了四条路线：

1. **Google Quick Share / Mosey 路线**：使用从三星系统提取的 Mosey 服务提供 AWDL 链路，配合探针验证互操作；
2. **小米 XOS（`libxos.so`）+ 小米互传路线**：使用小米闭源 `libxos.so` 建立无线链路，由小米互传负责分享界面和文件传输；
3. **基于 [t4t5/owly](https://github.com/t4t5/owly) 的独立 App 路线**：由可修改的 Rust 协议栈和独立 Android App 完成收发；
4. **小米原生互传 + Owly 高速后端路线（[`runtime/owly-xos`](runtime/owly-xos/README.zh-CN.md)，当前主线）**：不使用小米 `libxos.so`，通过自研 AIDL `XosBridge`、`owly-awdl`（`xos0`）与适配驱动底座接管无线链路，上层完整复用小米原版分享菜单、BLE 近场唤醒、超级岛弹窗与相册入库体验。

从符号、依赖等证据判断，小米 XOS 的 `libxos.so` 使用了 [OWL](https://github.com/seemoo-lab/owl) 衍生实现（速率受限于 HT20 MCS3）；第四条路线 `owly-xos` 完全以自研 Rust `owly-awdl` + 适配驱动替代了 `libxos.so`。

### 四条路线的能力对照

> 以下状态基于在小米 14（`houji`）上的拆包分析与实机验证整理；“待确认”仅表示未在 `houji` 上完成实机或源码级核实，不代表来源机型原厂不具备对应能力。

#### 1. 用户态 AWDL 引擎层

| 功能特性 | 小米 XOS（`libxos.so`） | Google / 三星（`Mosey`） | Owly 独立 App | Owly-XOS（当前主线） |
|---|---|---|---|---|
| PSF / MIF 时钟同步与主节点选举 | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 |
| 动态多信道调度（非固定 149） | ⚠️ 待确认 | ⚠️ 待确认 | ❌ 未实现 | ❌ 未实现 |
| 普通 Wi-Fi（STA）并发共存 | ❌ 未实现 | ✅ 底座支持 | ✅ 已实现 | ❌ 未启用 |
| 远端 Peer 与硬件 TX 聚合联动 | ❌ 未启用 | ✅ 底座支持 | ✅ 已实现（单对端） | ✅ 已实现（动态多对端） |
| 802.11 ↔ TUN/TAP + 邻居/路由映射 | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 |
| mDNS ↔ AWDL 服务发现适配 | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 |

#### 2. 上层 AirDrop 传输协议层

| 功能特性 | 小米 XOS + 原版 `MIShare` | Google / 三星（`Mosey` 探针） | Owly 独立 App | Owly-XOS（当前主线） |
|---|---|---|---|---|
| `_airdrop._tcp` mDNS 服务发现 | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 |
| TLS + `/Discover` / `/Ask` / `/Upload` | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 |
| CPIO / DVZip / gzip 归档收发 | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 |
| Live Photo（`.pvt`）解析 / 兼容 | ⚠️ 部分实现 | ⚠️ 部分实现 | ❌ 未适配 | ⚠️ 部分实现 |
| “仅限联系人”身份证书校验 | ❌ 未实现 | ❌ 未实现 | ❌ 未实现 | ❌ 未实现 |

#### 3. 系统体验与集成层

| 功能特性 | 小米 XOS + 原版 `MIShare` | Google / 三星（`Mosey` 移植） | Owly 独立 App | Owly-XOS（当前主线） |
|---|---|---|---|---|
| BLE 近场唤醒（免对方常开界面） | ✅ 已实现 | ⚠️ 待确认 | ❌ 未实现 | ✅ 已实现 |
| 系统分享菜单（Share Sheet） | ✅ 已实现 | ❌ 未实现 | ❌ 未实现 | ✅ 已实现 |
| 超级岛 / 系统接收弹窗与相册入库 | ✅ 已实现 | ❌ 未实现 | ❌ 未实现 | ✅ 已实现 |
| 后台常驻守护进程 + 开机自启 | ⚠️ 需手动拉起 | ❌ 未实现 | ❌ 未实现 | ✅ 已实现 |

## 性能

### 测试环境

| 项目 | 配置 |
|---|---|
| 手机 | 小米 14，代号 `houji`，KernelSU root + LSPosed |
| OS 版本 | HyperOS `OS3.0.307.0.WNCCNXM`，Android 16（SDK 36） |
| 内核版本 | `6.1.138-android14-11-g0c3d559bcd85-ab14529422` |
| WLAN 驱动 | 基于 MiCode `kiwi_v2` 驱动适配 PASSTHRU 多对端动态槽位与硬件 TX A-MPDU，模块名 `qca_cld3_kiwi_v2` |
| 无线固件 | 三星 SM-S928B 官方 `S928BXXU6EZIF` 的 `amss20.bin` |
| Apple 对端 | macOS 测试端（原生 `awdl0`）及 iOS 测试手机（原生隔空投送）；个人硬件标识已移除 |

实验模块的 vermagic 前缀为 `6.1.68`，与手机运行内核不同；本项目通过 567 个导出符号 CRC/ABI 审计及实机装载验证兼容性。详见[驱动构建说明](docs/airdrop/06-driver-build.md)。

### 最近一次测试：小米原生互传 + Owly-XOS 高速后端

2026-11-15，[`runtime/owly-xos`](runtime/owly-xos/README.zh-CN.md) 通过 AIDL `XosBridge` + `owly-awdl`（L2 TAP `xos0`）替代小米闭源 `libxos.so`，并通过内核 `pt_remote_multi=1` 动态远端槽位与用户态 `KernelPeerSync` 解锁官方 `ezif-amss20.bin` **免预绑定多对端硬件 TX A-MPDU** 与 `pt_remote_rx_window=256` REO MSDU-buffer RX。

#### 1. 裸链路 16 MiB TCP 吞吐对比（信道 149，`xos0` ↔ Mac `awdl0`）

| 方向 | 小米 XOS `libxos.so` | 旧 `dzf2.bin` 软件 A-MSDU | 当前 `Owly-XOS` | 对比 `libxos.so` 提升 |
|---|---:|---:|---:|---:|
| 小米 `xos0` → Mac `awdl0` | `0.21–0.29 MB/s` | `6.85 MB/s` | **64.08 MB/s** | **~220×–305×** |
| Mac `awdl0` → 小米 `xos0` | `0.48 MB/s` | `9.95 MB/s` | **9.55 MB/s** | **~20×** |

双向 SHA-256 校验全部通过。单轮 16 MiB 发送期间，内核 `pt_rate_stats` 观测 `agg_ppdus +307`、`agg_mpdus +6103`（峰值 `max_ppdu_mpdus=31`），100% VHT80 2SS MCS9 SGI LDPC；接收期间 `pt_rx_msdu_stats` 记录 `seen=reaped=delivered=5928`（`peer_miss=0`、`bad_desc=0`、`dropped=0`），`OWLY_RX_GAPS` 记录 `matched=24298 missing=0 host_delayed=0`。

#### 2. 原版 `MIShare.apk` 对 Apple 手机双向收发 `157.80 MB` 视频实测

使用同一 `157,799,892` 字节（`157.80 MB`）MP4 视频，通过系统原版小米互传与 Apple 手机执行完整发现、确认、归档传输与相册落盘（匿名化遥测与完整时间线见 [`runtime/owly-xos/evidence/`](runtime/owly-xos/evidence/README.md) 与 [第 34 篇验证报告](docs/airdrop/34-owly-xos-validation.md)）：

- **冷启动与设备发现**：
  - 经固件预暂存、`50–100 ms` 事件轮询与 `fast_discovery` + `peer_up` 双向 mDNS 触发，`startDomestXOsComm(149)` 驱动+固件冷启动耗时从优化前 `6.17–9.19s` 缩短至 **`3.44–3.62s`**（CLI `radio.sh enter` 仅 **`3.16s`**）；
  - **被 Apple 手机发现**：接收侧 `NsdAdvertiser` 在 `xos0` 注册完成后仅 **`1.46s`**（自 BLE 近场唤醒触发起共 **`6.18s`**）即收到对端 TLS `POST /Discover` 并返回 `200 OK`；
  - **搜索到 Apple 手机**：发送侧 `XOS_READY` 并开启 BLE 唤醒后 **`2.43s`** 即由 `RawMdnsBrowser` 解析出对端服务，点击系统分享至头像就绪共 **`9.03s`**。
- **发送方向（小米 `xos0` → Apple 手机，`157.80 MB`）**：
  - 点击头像后 TCP 建连 **`8 ms`**、TLSv1.3 握手 **`17 ms`**；`/Upload` 正文 **`8.40s`** 发完（**`18.79 MB/s`**），含对端校验落盘返回 `HTTP 200` 共 **`9.42s`**（**`16.75 MB/s`**；此前同视频后半程稳态 **`22.73 MB/s`**）；
  - 内核 `pt_rate_stats`：`valid=23,308` 帧中 **`99.97%`** 为 VHT80 MCS9 SGI LDPC，**`agg_ppdus=3,338/3,713`（`89.9%`）**、**`agg_mpdus=22,933`（`98.4%`）** 经硬件 TX A-MPDU 聚合发出，反向 TCP ACK RX `dropped=0`。
- **接收方向（Apple 手机 → 小米 `xos0`，`157.80 MB`）**：
  - 单对端 `win=64` 基线：`/Upload` 全程 **`11.97s`（`13.18 MB/s`）**，后半程稳态 **`20.35 MB/s`**，`0.80s` 窗口 **`26.08 MB/s`**，单块突发 **`41.12 MB/s`**；
  - 3 个 AWDL 节点在场 `win=256` 实测：切入双包 A-MSDU 后 `7.91s` 收完剩余 `102.87 MB`（阶段均速 **`13.00 MB/s`**、双块窗口 **`23.56 MB/s`**、单块峰值 **`29.54 MB/s`**）；全程交付 `114,598` 个 IPv6 包，`OWLY_RX_GAPS` `matched=37,093 missing=0 host_delayed=0`（平均每 PPDU `16.1` 个 A-MSDU 大帧），全链路 `dropped=0`。
- **安全边界与恢复**：全程保持 Android 16 SELinux `Enforcing`；关闭互传会话后自动执行 `pt_remote_peer_ctl` `clear` 与 `pt_release` 卸载实验模块，并在 3–5 秒内重连原厂 `5 GHz` Wi-Fi（`STOCK_RESTORED`）。

以上实测使用的无线配置为：

- 固定信道 149（5745 MHz），`pt_exclusive=1` 会话期独占无线电，VHT（802.11ac）80 MHz、2 条空间流（2SS）、MCS9、短保护间隔（SGI）；
- 用户态软件 A-MSDU 合包（`OWLY_COALESCE_US=1000`）+ `HOUJI_OWLY_TX_ADDBA=1` 配合 `ezif` 固件 `wire_506_passthru_ampdu=1` 硬件 TX A-MPDU 聚合；
- `pt_remote_multi=1` 动态多对端远端 peer 槽位（最多 8 台对端，无需 `insmod` 预填 MAC），`pt_remote_reo=2`，TID 0 REO 接收窗口 `256`；
- `pt_rx_msdu_buf=1` + `pt_rx_ezif_marked=1` MSDU-buffer RX 适配。

## 工作原理

AirDrop 不只是发送文件：它需要先建立 AWDL 无线链路，再完成设备发现、接收确认与上层传输。本项目将这些工作分为三层：

```text
小米原版互传 MIShare（XosBridge AIDL，当前主线） / 独立 Owly AirDrop App（暂缓）
    ↓
Rust 后端：owly-awdl（xos0 L2 TAP 桥接 / owly0 L3 TUN + mDNS + TLS 文件传输）
    ↓
PASSTHRU 接口、wonder/bridge 与适配后的 kiwi_v2 驱动（动态多对端槽位 + 硬件 TX A-MPDU）
    ↓
无线固件（EZIF amss20.bin）与硬件 ←→ iPhone / Mac 原生 AirDrop
```

1. **无线接入**：在小米的 MiCode WLAN 驱动上移植 PASSTHRU/Wonder 相关实现，参考三星公开源码与对应版本二进制补足行为。并非直接把三星 `.ko` 装到小米上；模块仍需针对小米内核构建并审计 ABI。
2. **AWDL 与数据路径**：`owly-awdl` 处理同步、信道时间安排和 802.11 帧，在主线 `owly-xos` 中提供 L2 TAP 接口 `xos0`（含用户态 NDP 与组播/单播 mDNS 投递修正），并通过 `KernelPeerSync` 协同驱动 `pt_remote_peer_ctl` 动态创建、关联和释放最多 8 个远端 peer 槽位与 REO 队列。
3. **文件协议与界面**：主线 `owly-xos` 直接由系统原版 `MIShare` 在 `xos0` 上完成 BLE 近场唤醒、mDNS 发现、TLS `/Discover` / `/Ask` / `/Upload`、超级岛确认与相册入库；独立 App 路线则由 Rust 后端内置协议栈完成收发。
4. **会话与恢复**：root 控制脚本负责保存无线状态、核对模块和固件哈希、加载运行时、建立接口与路由，并在停止或错误时清理远端 peer、卸载实验模块、恢复原厂环境并重连 Wi-Fi。

A-MSDU 将多个数据单元装入一个 MPDU；A-MPDU 则由硬件将多个 MPDU 聚合发送。两者作用于不同层，单独看到软件合包或一个配置开关为 1，都不能证明硬件聚合已生效。

## 构建和使用

### 构建范围

目前是**针对小米 14（`houji`）特定内核版本的适配仓库**，不是下载一个 APK 即可在任意手机上运行的通用移植版。需要严格匹配的小米内核（`6.1.138-android14-11-g0c3d559bcd85-ab14529422`）与原厂驱动、KernelSU（或 APatch / Magisk）root、Android SDK/JDK、Rust/NDK，以及已准备的 Linux 内核构建环境。

正式构建入口现在仅使用本仓库和显式提供的外部输入，不要求原工作目录或固定远端主机。完整步骤见[独立构建指南](docs/guides/building.md)。

**BYOB（自行提供外部输入）**：驱动编译需要 MiCode 原始 kernel/WLAN 源码归档和 Android Clang；用户态与 App 需要 Rust、NDK、SDK/JDK。手机运行另需自行提取三星 `ezif` 固件（主线 `owly-xos` 打包还需提供原厂平台签名 `MIShare.apk`），详见 [BYOB 清单](docs/guides/byob.md)。闭源系统包不随源码提供。

```sh
# 1. 在 Linux 上配置 BYOB 源码和 Clang 后构建三个驱动模块
bash scripts/build-driver.sh
# 2. 配置 Rust/NDK 后构建用户态原生程序（houji-owly-app、wonder-init）
bash scripts/build-userspace.sh

# 3a. 构建主线 Owly-XOS 桥接 DEX、LSPosed 兼容模块并打包 KernelSU 模块
ANDROID_HOME=/path/to/android-sdk BUILD_TOOLS=/path/to/android-sdk/build-tools/35.0.0 \
  bash runtime/owly-xos/build.sh
ANDROID_HOME=/path/to/android-sdk BUILD_TOOLS=/path/to/android-sdk/build-tools/35.0.0 \
  bash runtime/owly-xos/compat/build.sh
python3 runtime/owly-xos/package-ksu.py --mishare-apk /path/to/MIShare.apk

# 3b. （可选）构建暂缓的独立 Owly AirDrop APK
bash scripts/build-app.sh
python3 runtime/owly-app/build-radio.py
```

### 主线 `Owly-XOS`（小米原生互传高速后端）使用方式

1. 在匹配的 HyperOS 3（Android 16，内核 `6.1.138-...-ab14529422`）小米 14 上，通过 KernelSU / APatch / Magisk 刷入打包生成的模块（或开发调试时运行 `python3 runtime/owly-xos/install.py`）。
2. 在 LSPosed 管理器中启用 **「Owly-XOS 小米互传兼容」**（默认已自动勾选 `小米互传` 与 `Intent Resolver` 推荐作用域），重启一次手机。
3. **接收**：下拉控制中心开启「小米互传」（互传设置设为所有人可见），Apple 设备隔空投送选择小米 14，在小米超级岛弹窗点击接收，文件自动存入 `/MiShare/AirDrop/` 并入库相册。
4. **发送**：在系统相册或文件管理点击分享 → 选择「小米互传 / 隔空投送」，搜索到 Apple 设备头像后点击发送。
5. 关闭互传或会话结束后，后台自动卸载实验模块并在 3–5 秒内恢复原厂 Wi-Fi。详见 [`runtime/owly-xos/README.zh-CN.md`](runtime/owly-xos/README.zh-CN.md)。

### 独立 `Owly AirDrop` App（暂缓）使用方式

1. 打开普通 WLAN 和 **Owly AirDrop**，首次使用在 KernelSU 中授予该 App root。
2. iPhone / Mac 将 AirDrop 接收范围设为“所有人（10 分钟）”，或保持 AirDrop 界面打开。
3. App 中点击“搜索附近设备”，选择对端，再点击“连接所选设备”。当手机连接在 `2.4 GHz` 或非 DFS `5 GHz`（如 `5180–5240`、`5745–5825 MHz`）时会自动启用 STA 共存保持普通 WLAN 在线；处于 DFS 或其他受限状态时则自动回落独占模式并在停止后恢复。
4. **接收**：在 Apple 设备上选择 `Xiaomi 14 Owly`，在手机 App 确认接受；完成后通过“收到的文件 · 保存到…”导出。
5. **发送**：在 App 中选取文件并发送，在对端确认接收。完成后点击“停止并恢复 Wi-Fi”。详细说明见 [App README](clients/owly-app/README.zh-CN.md)。

## 致谢

本工作参考或使用了以下项目与材料：

- [YoWenhow/Xiaomi14-PixelOS-Airdrop](https://github.com/YoWenhow/Xiaomi14-PixelOS-Airdrop)：小米 14 驱动移植、Wonder/PASSTHRU 基础组件与构建配方。
- [t4t5/owly](https://github.com/t4t5/owly)：Rust AWDL / AirDrop 实现，独立 App 的协议基础。
- [seemoo-lab/owl](https://github.com/seemoo-lab/owl)：AWDL 开源实现及协议研究，帮助理解早期 XOS 路线。
- MiCode 小米内核与 WLAN 开源代码、三星 SM-S928B 公开源码：驱动适配和行为对照的基础。
- 小米 14 One UI 9 移植包及其制作者：提供了前期 Mosey 和三星组件分析的线索；后续 EZIF 固件另以官方包核对来源。

第三方源码保留各自的许可证和版权声明；本仓库不以统一许可证替代它们。许可边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 从这里开始审阅

1. [审阅导航与目录用途](docs/INDEX.md)：App、驱动、测试、实验报告分别在哪里。
2. [当前状态与未完成项](docs/STATUS.md)：区分已安装版本和共存实验版本。
3. [构建依赖与整理边界](docs/guides/building.md)：独立构建入口、BYOB 输入与构建验证边界。
4. [隐私与公开范围](docs/PRIVACY.md)：原始日志及导入清单已清理，保留匿名化汇总。

## 目录结构

```text
houji-airdrop/
├── clients/owly-app/     独立 App、Android 后端与所用 Owly 协议栈
├── driver/wlan/          当前驱动修改文件覆盖层
├── runtime/owly-app/     App 路线 root 会话与无线恢复脚本
├── runtime/owly-xos/  小米互传原生前端的高速 xos0 桥接与无线后端
├── scripts/             构建入口和构建环境配方
├── third_party/upstream/ 上游基础组件、配方与许可证
├── experiments/         实验分支、测试工具和匿名化汇总
├── tests/device/        实机验证入口导航
├── blobs/               外部固件材料入口
├── docs/                当前状态、交接、架构、构建说明与历史调查
└── archive/import/      首次导入记录的隐私清理说明
```

`install.py` 安装入口现通过 `clients/owly-app/runtime-manifest.json` 校验哈希并从 `blobs/local/`（或环境变量）解析闭源输入，与构建脚本解耦。

[交接入口](docs/HANDOVER.md)说明近期变更及后续边界；[架构说明](docs/architecture/overview.md)区分两条互传前端的实现。`runtime/owly-xos/` 与 `runtime/owly-app/` 共享锁与模块恢复门禁，保证双前端互斥安全切换。

当前 `Owly AirDrop` App 已合入 `2.4 GHz` / 非 DFS `5 GHz` 普通 WLAN 共存及退出重连恢复；小米原生 `MIShare` 高速 `xos0` 后端可通过 [`runtime/owly-xos`](runtime/owly-xos/README.zh-CN.md) 单独安装并开机守护。

## 隐私与测试记录

本仓库已移除个人路径、设备标识和原始设备日志；保留匿名化的性能汇总及构建所需的目标型号信息。详见[隐私清理说明](docs/PRIVACY.md)。实机脚本需要自行配置 `ANDROID_SERIAL` 和对端连接参数。
