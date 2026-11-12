# houji-airdrop

小米 14（houji）上的 AirDrop 实验实现。包括独立 App、WLAN 驱动移植与匿名化验证资料。

**当前为研究源码仓库，不是可直接安装的发布包。** 独立构建已验证；新构建产物仍需单独完成 ABI 与实机验收。普通 WLAN 共存尚未合入 App，双 5 GHz 实验退出后的自动重连问题仍未解决。

本项目探索过三条路线：

- **Google Quick Share / Mosey 路线**：使用从三星系统提取的 Mosey 服务提供 AWDL 链路，验证互操作；
- **小米 XOS 互联框架 + 小米互传 App**：利用 XOS 建立无线链路，由小米互传负责分享界面和文件传输；
- **基于 [t4t5/owly](https://github.com/t4t5/owly) 的独立实现**：由可修改的 Rust 协议栈和 Android App 完成文件收发。

从符号、依赖等证据判断，小米 XOS 的 `libxos.so` 使用了 [OWL](https://github.com/seemoo-lab/owl) 衍生实现。

本仓库前期注重把前两种方案跑通，后期转向第三种方案，方便修改代码以提高传输性能。

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

这些数字是测试 TCP 载荷的十进制 MB/s，不包含完整 AirDrop 发现、用户确认、归档和落盘耗时，也不是双向同时传输成绩。双 5 GHz 目前只通过短时收发与网关连通验证；退出后自动重连仍可能超时，需要开关 WLAN 并恢复。**共存实验尚未合入 App，不能据此宣称 App 已支持无缝 WLAN 共存。**

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
3. App 中点击“搜索附近设备”，选择对端，再点击“连接所选设备”。当前版本一次连接一个设备，切换链路约需 20–40 秒，并会暂停普通 WLAN。
4. **接收**：在 Apple 设备上选择 `Xiaomi 14 Owly`，在手机 App 确认接受；完成后通过“收到的文件 · 保存到…”导出。
5. **发送**：在 App 中选取文件并发送，在对端确认接收。
6. 完成后点击“停止并恢复 Wi-Fi”。

接收内容先保存在 App 私有目录，暂不自动导入相册。App 没有接入系统分享菜单；共存实验也尚未成为 App 的运行选项。详细说明见 [App README](clients/owly-app/README.zh-CN.md)。

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
├── runtime/owly-app/     root 会话与无线恢复脚本
├── scripts/             构建入口和构建环境配方
├── third_party/upstream/ 上游基础组件、配方与许可证
├── experiments/         实验分支、测试工具和匿名化汇总
├── tests/device/        实机验证入口导航
├── blobs/               外部固件材料入口
├── docs/                当前状态、交接、架构、构建说明与历史调查
└── archive/import/      首次导入记录的隐私清理说明
```

本轮只整理工程布局，未新增版本锁定或 SHA 清单；已有校验与历史记录保留。全部旧路径兼容链接已删除，开发使用上述正式路径。实验脚本尚未完全抽离，不能把 `experiments/` 整体当成不再使用的档案。

[交接入口](docs/HANDOVER.md)说明本轮变更及后续边界；[架构说明](docs/architecture/overview.md)区分已实现功能与双前端目标。统一守护进程和小米互传 Hook 尚未实现。

当前 App 的普通 WLAN 共存尚未实装。安装器仍读取当前实验产物；目录整理没有消除这个既有行为，详见构建说明。

## 隐私与测试记录

本仓库已移除个人路径、设备标识和原始设备日志；保留匿名化的性能汇总及构建所需的目标型号信息。详见[隐私清理说明](docs/PRIVACY.md)。实机脚本需要自行配置 `ANDROID_SERIAL` 和对端连接参数。
