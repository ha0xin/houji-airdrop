# houji-airdrop

小米 14（houji）上的 AirDrop 实验实现。包括独立 App、WLAN 驱动移植与匿名化验证资料。

**当前为研究源码仓库，不是可直接安装的发布包。** 独立构建已验证；当前开发主线聚焦 [`runtime/owly-xos/`](runtime/owly-xos/README.zh-CN.md)，为小米系统原版 `MIShare` 提供基于官方 `ezif-amss20.bin` 与免预绑定多对端硬件 TX A-MPDU 的高速 `xos0` 后端（16 MiB TCP 小米→Mac **64.08 MB/s**、Mac→小米 **9.55 MB/s**，原生互传收发 `157.80 MB` 视频端到端均通过，冷启动 **3.16–3.62 s**）；独立 App `Owly AirDrop` 在合入 `2.4 GHz` / 非 DFS `5 GHz` 普通 WLAN 共存后暂缓迭代。

本项目探索过三条路线：

- **Google Quick Share / Mosey 路线**：使用从三星系统提取的 Mosey 服务提供 AWDL 链路，验证互操作；
- **小米 XOS 互联框架 + 小米互传 App**：利用 XOS 建立无线链路，由小米互传负责分享界面和文件传输；
- **基于 [t4t5/owly](https://github.com/t4t5/owly) 的独立实现**：由可修改的 Rust 协议栈和 Android App 完成文件收发。

从符号、依赖等证据判断，小米 XOS 的 `libxos.so` 使用了 [OWL](https://github.com/seemoo-lab/owl) 衍生实现。

本仓库前期验证了前两种方案与独立 `Owly` 协议栈，当前重点收敛在 `runtime/owly-xos` 方式：直接复用小米原版分享菜单、超级岛与相册入库界面，同时把底层 `xos0` 替换为可演进的 `owly-awdl` + `ezif` 多对端硬件聚合后端。

### 三条路线的能力对照

> 以下状态基于在小米 14（`houji`）上的拆包分析与实机验证整理；“待确认”仅表示未在 `houji` 上完成实机或源码级核实，不代表来源机型原厂不具备对应能力。

#### 1. 用户态 AWDL 引擎层

| 功能特性 | 小米 XOS（`libxos.so`） | Google / 三星（`Mosey`） | 当前 Owly 路线 |
|---|---|---|---|
| PSF / MIF 时钟同步与主节点选举 | ✅ 已实现（基于 `OWL` 改造） | ✅ 已实现（`mosey_server` 内置） | ✅ **已实现**（`owly-awdl` 原生支持） |
| 动态多信道调度（非固定 149） | ⚠️ 待确认（`houji` 测试曾改写到 6 信道） | ⚠️ 待确认（有调度模块，未在 `houji` 验切换） | ❌ **未实现**（App 与实验均固定信道 149） |
| 普通 Wi-Fi（STA）并发共存 | ❌ 未在 `houji` 实现（走 `pt_exclusive=1`） | ✅ 底座原生支持（`Mosey` 调度策略待确认） | ✅ **已实现**（App 已合入 2.4G 与非 DFS 5G 共存，退出重连已修） |
| 远端 Peer 与硬件 TX 聚合联动 | ✅ 原生 `libxos` 仅 `0.21–0.48 MB/s`；本仓 [`runtime/owly-xos`](runtime/owly-xos/README.zh-CN.md) 启用 `ezif` + `pt_remote_multi=1` 免预绑定多对端硬件 TX A-MPDU 后，16 MiB TCP 发送达 **64.08 MB/s**、原生发 `157.80 MB` 视频落盘均速 **16.75 MB/s** | ✅ 底座支持（`EZIF` + `wondertap` 原生支持） | ✅ **已实现**（扫描用 `dzf2`，连接后启用 `EZIF` 远端 peer + TX 聚合） |
| 802.11 ↔ TUN/TAP + RFC 4291 映射 | ✅ 已实现（`xos0` + IPv6 邻居） | ✅ 已实现（`mosey0`） | ✅ **已实现**（`owly0` TUN + `radio.sh` `10075` 策略路由） |
| mDNS ↔ AWDL TLV 代理转换 | ✅ 已实现 | ✅ 已实现 | ✅ **已实现**（内置 mDNS ↔ TLV 同步与复用 socket） |

#### 2. 上层 AirDrop 传输协议层

| 功能特性 | 小米原版（`MIShare`） | Google / 三星（`Mosey` 探针） | 当前 Owly 路线 |
|---|---|---|---|
| `_airdrop._tcp` mDNS 服务发现 | ✅ 已实现 | ✅ 已实现（`houji` 探针由 Java 辅助） | ✅ **已实现** |
| TLS + `/Discover` / `/Ask` / `/Upload` | ✅ 已实现（`sharop` 层负责） | ✅ 已实现（`houji` 探针由 Java 补齐） | ✅ **已实现**（Rust 后端完整实现） |
| CPIO / DVZip / gzip 归档收发 | ✅ 已实现 | ✅ 已实现 | ✅ **已实现**（已通过双向 SHA-256 校验） |
| Live Photo（`.pvt`）解析 / 兼容 | ⚠️ 部分实现（测试应用收过 `.pvt`；原版仅验 JPG） | ⚠️ 部分实现（探针收过 `.pvt`，未导入相册） | ❌ **未专门适配**（按普通文件/目录落盘导出） |
| “仅限联系人”身份证书校验 | ❌ 未实现（走“所有人”模式） | ❌ 未实现（探针仅短时许可，无证书链校验） | ❌ **未实现**（仅支持“所有人 10 分钟”） |

#### 3. 系统体验与集成层

| 功能特性 | 小米原版（`MIShare`） | Google / 三星（`Mosey` 移植） | 当前 Owly 路线 |
|---|---|---|---|
| BLE 近场唤醒（免对方常开界面） | ✅ 已验证（发送广播唤醒对端、接收扫到 BLE 自动激活 `xos0`） | ⚠️ 待确认（原版具备，提取件未在 `houji` 验证） | ❌ **未实现**（需对端手动保持“所有人 10 分钟”界面） |
| 系统分享菜单（Share Sheet） | ✅ 已实现（系统相册分享接入） | ❌ 未在小米系统实现 | ❌ **未实现**（仅可在 App 内选文件发送） |
| 超级岛 / 系统接收弹窗与相册入库 | ✅ 已实现（超级岛弹窗 + `/MiShare/AirDrop/`） | ❌ 未在小米系统实现 | ❌ **未实现**（App 内确认，私有目录需手动导出） |
| 后台常驻守护进程 + 开机自启 | ✅ 原版自带（`houji` 上依赖脚本拉起） | ✅ 原版自带（`houji` 上未完成开机自启） | ❌ **未实现**（前台拉起临时脚本，退出即恢复原厂 Wi-Fi） |

## 性能

### 测试环境

| 项目 | 配置 |
|---|---|
| 手机 | 小米 14，代号 `houji`，KernelSU root |
| OS 版本 | HyperOS `OS3.0.307.0.WNCCNXM`，Android 16 |
| 内核版本 | `6.1.138-android14-11-g0c3d559bcd85-ab14529422` |
| WLAN 驱动 | 基于 MiCode `kiwi_v2` 驱动适配 PASSTHRU，模块名 `qca_cld3_kiwi_v2` |
| 无线固件 | 三星 SM-S928B 官方 `S928BXXU6EZIF` 的 `amss20.bin` |
| Mac | macOS 测试端，使用原生 `awdl0`；个人硬件标识已移除 |

实验模块的 vermagic 前缀为 `6.1.68`，与手机运行内核不同；本项目通过符号 CRC/ABI 审计及实机装载验证兼容性。详见[驱动构建说明](docs/airdrop/06-driver-build.md)。

### 最近一次测试：双 5 GHz 共存

2026-11-11，普通 WLAN 连接 TEST_WIFI 的 **信道 40 / 5200 MHz**，Owly 使用 **信道 149 / 5745 MHz**。每轮依次进行 Mac→小米、小米→Mac，各传输 16 MiB，并校验 SHA-256。

| 轮次 | 小米→Mac | Mac→小米 | 普通 WLAN 网关 Ping | 平均 / 最大 RTT |
|---|---:|---:|---:|---:|
| 1 | 16.04 MB/s | 2.34 MB/s | 52/52，零丢包 | 26.56 / 68.54 ms |
| 2 | 16.59 MB/s | 4.17 MB/s | 36/36，零丢包 | 30.18 / 71.72 ms |

四次文件校验全部通过，RX 适配分支的 397 个描述符全部交付，`peer_miss`、`bad_desc`、`dropped` 均为零。匿名化汇总见[实测目录](experiments/three-findings-validation/runs/coexist-5g40-16m-20261111)和[共存报告](experiments/sta-coexist/README.zh-CN.md)。

以上使用的无线配置为：

- VHT（802.11ac）80 MHz、2 条空间流（2SS）、MCS9、短保护间隔（SGI）；
- 用户态软件 A-MSDU 合包，等待时间 1000 μs；启用硬件 TX A-MPDU 相关路径；
- 完整远端 peer 关联，`remote_reo=2`，初始 TID 0 接收窗口 64；用户态启用 `tx-addba=win64`；
- EZIF MSDU-buffer RX 适配及带 bit 30 标记的 PASSTHRU 收包处理；
- 仅实验启用双 5 GHz 准入放行，限制为一个普通 STA 和固定信道 149 的 PASSTHRU。

硬件 TX 多 MPDU/PPDU 已在[独占模式实验](docs/airdrop/32-ezif-tx-ampdu-validation.md)中观测到；共存模式下统计混有普通 STA 的发送事件，不能把所有聚合计数都归于 Owly。初始 REO 窗口也不等于已经确认的最终空口 BlockAck 窗口。

此前 **2.4 GHz WLAN + 5 GHz Owly** 共存的两轮同尺寸测试，小米→Mac 为 **37.89 / 45.42 MB/s**，Mac→小米为 **10.22 / 4.49 MB/s**。测试时段与无线条件不同，不将两组差异直接归因于频段或调度方式。

这些数字是测试 TCP 载荷的十进制 MB/s，不包含完整 AirDrop 发现、用户确认、归档和落盘耗时，也不是双向同时传输成绩。上述 `2.4 GHz` 与非 DFS `5 GHz`（`5180–5240` / `5745–5825 MHz`）共存、mDNS `SO_BINDTOIFINDEX` 绑定及退出 Wi-Fi 自动重连恢复现已合入 `Owly AirDrop` App；DFS 等受限信道仍会自动回落独占模式并在停止后恢复原厂 WLAN。

### 小米原生互传（`runtime/owly-xos` 高速 `xos0` 后端，当前主线）

2026-11-15，[`runtime/owly-xos`](runtime/owly-xos/README.zh-CN.md) 通过 AIDL `XosBridge`（`com.domestic.xoscomm.IDomesticXOsComm/default`）+ `owly-awdl`（L2 TAP `xos0` + 用户态 NDP + 组播/单播 mDNS 投递修正）替代原厂低速 `libxos.so`，并通过内核 `pt_remote_multi=1`（`pt_remote_peer_ctl` 最多 8 个动态槽位）与用户态 `KernelPeerSync` 解锁官方 `ezif-amss20.bin` **免预绑定多对端硬件 TX A-MPDU** 与 `pt_remote_rx_window=256` REO MSDU-buffer RX。

1. **裸链路 16 MiB TCP 吞吐对比（信道 149，`xos0` ↔ Mac `awdl0`，SHA-256 全通过）**：

| 方向 | 原版 `libxos.so`（HT20 MCS3） | 旧 `dzf2.bin` 软件 A-MSDU | 当前 `ezif.bin` + 多对端硬件 TX A-MPDU | 对比原版提升 |
|---|---:|---:|---:|---:|
| 小米 `xos0` → Mac `awdl0` | `0.21–0.29 MB/s` | `6.85 MB/s` | **64.08 MB/s**（`0.262s`，`max_ppdu_mpdus=31`） | **~220×–305×** |
| Mac `awdl0` → 小米 `xos0` | `0.48 MB/s` | `9.95 MB/s` | **9.55 MB/s**（`1.757s`，`dropped=0`） | **~20×** |

2. **原版 `MIShare.apk` 对 Apple 手机收发 `157.80 MB` 视频实测**（匿名化遥测与完整时间线见 [`runtime/owly-xos/evidence/`](runtime/owly-xos/evidence/README.md) 与 [第 34 篇验证报告](docs/airdrop/34-owly-xos-validation.md)）：
   - **冷启动与发现**：经固件预置、`50–100 ms` 事件轮询与 `fast_discovery` + `peer_up` 双向 mDNS 触发，`startDomestXOsComm(149)` 冷切驱动耗时从 `6.17–9.19s` 缩短至 **`3.44–3.62s`**（CLI `radio.sh enter` **`3.16s`**）；接收侧 NSD 注册完成后仅 **`1.46s`** 即收到 Apple 设备 `POST /Discover`。
   - **发送视频（小米 → Apple 手机）**：`/Upload` 正文 **`8.40s`** 发完（**`18.79 MB/s`**），含对端落盘返回 `HTTP 200` 共 **`9.42s`**（**`16.75 MB/s`**），`99.97%` VHT80 MCS9 SGI LDPC、`98.4%` MPDU 经硬件 A-MPDU 聚合发出。
   - **接收视频（Apple 手机 → 小米）**：单对端 `win=64` 全程 **`11.97s`（`13.18 MB/s`）**、后半程稳态 **`20.35 MB/s`**（单块突发 **`41.12 MB/s`**）；多对端在场 `win=256` 下开启双包 A-MSDU 阶段 **`13.00 MB/s`**（双块窗口 **`23.56 MB/s`**、单块峰值 **`29.54 MB/s`**），全程 `114,598` 个 IPv6 包零丢包、`OWLY_RX_GAPS` `host_delayed=0`；全程保持 SELinux `Enforcing`，退出自动恢复原厂 `5 GHz` Wi-Fi（`STOCK_RESTORED`）。

## 工作原理

AirDrop 不只是发送文件：它需要先建立 AWDL 无线链路，再完成设备发现、接收确认与上层传输。本项目将这些工作分为三层：

```text
Android App（选择设备、文件，确认接收）
    ↓
Rust 后端：Owly / AWDL + IPv6 + mDNS + TLS 文件传输
    ↓
PASSTHRU 接口、wonder/bridge 与适配后的 kiwi_v2 驱动
    ↓
无线固件与硬件 ←→ iPhone / Mac 原生 AirDrop
```

1. **无线接入**：在小米的 MiCode WLAN 驱动上移植 PASSTHRU/Wonder 相关实现，参考三星公开源码与对应版本二进制补足行为。并非直接把三星 `.ko` 装到小米上；模块仍需针对小米内核构建并审计 ABI。
2. **AWDL 与数据路径**：Owly 处理同步、信道时间安排和 802.11 帧，并向用户态网络栈提供 IPv6 数据路径。当前实验固定信道 149；远端 peer 的创建、关联、REO 设置和删除由适配驱动协同完成。
3. **文件协议与界面**：独立 App 调用 Rust 后端，使用 mDNS 发现、TLS 会话、`/Discover`、`/Ask`、`/Upload` 以及 CPIO/DVZip/gzip 归档完成收发。用户明确接受后才接收文件。
4. **会话与恢复**：root 控制脚本负责保存无线状态、核对模块和固件哈希、加载运行时、建立接口与路由，并在停止或错误时释放 peer、卸载实验模块、恢复原厂环境。

A-MSDU 将多个数据单元装入一个 MPDU；A-MPDU 则由硬件将多个 MPDU 聚合发送。两者作用于不同层，单独看到软件合包或一个配置开关为 1，都不能证明硬件聚合已生效。

## 构建和使用

### 构建范围

目前是**针对小米 14 的适配仓库**，不是下载一个 APK 即可在任意手机上运行的通用移植版。需要匹配的小米内核/驱动、KernelSU root、Android SDK/JDK、Rust/NDK，以及已准备的 Linux 内核构建环境。

正式构建入口现在仅使用本仓库和显式提供的外部输入，不要求原工作目录或固定远端主机。完整步骤见[独立构建指南](docs/guides/building.md)。

**BYOB（自行提供外部输入）**：驱动编译需要 MiCode 原始 kernel/WLAN 源码归档和 Android Clang；App 需要 Rust、NDK、SDK/JDK。手机运行另需自行提取三星固件，详见 [BYOB 清单](docs/guides/byob.md)。闭源系统包不随源码提供，研究用三星源码和小米系统包并非 App 编译依赖。

```sh
# 在 Linux 上配置 BYOB 源码和 Clang 后构建三个驱动模块
bash scripts/build-driver.sh
# 配置 Rust/NDK 后构建 App 原生程序
bash scripts/build-userspace.sh
# 配置 SDK/JDK 后构建 APK
bash scripts/build-app.sh
python3 runtime/owly-app/build-radio.py
```

构建不会安装到手机。旧实验安装器仍需配套 ABI 审计及设备运行环境，不是新构建产物的一键部署入口。

### 已安装 App 的使用方式

1. 打开普通 WLAN 和 **Owly AirDrop**，首次使用在 KernelSU 中授予该 App root。
2. iPhone / Mac 将 AirDrop 接收范围设为“所有人（10 分钟）”，或保持 AirDrop 界面打开。
3. App 中点击“搜索附近设备”，选择对端，再点击“连接所选设备”。当手机连接在 `2.4 GHz` 或非 DFS `5 GHz`（如 `5180–5240`、`5745–5825 MHz`）时会自动启用 STA 共存保持普通 WLAN 在线；处于 DFS 或其他受限状态时则自动回落独占模式并在停止后恢复。
4. **接收**：在 Apple 设备上选择 `Xiaomi 14 Owly`，在手机 App 确认接受；完成后通过“收到的文件 · 保存到…”导出。
5. **发送**：在 App 中选取文件并发送，在对端确认接收。
6. 完成后点击“停止并恢复 Wi-Fi”。

接收内容先保存在 App 私有目录，暂不自动导入相册。App 没有接入系统分享菜单；如需直接从小米系统分享菜单收发并提速原厂互传，见 [`runtime/owly-xos`](runtime/owly-xos/README.zh-CN.md)。详细说明见 [App README](clients/owly-app/README.zh-CN.md)。

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
