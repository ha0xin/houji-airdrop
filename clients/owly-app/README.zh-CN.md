> 当前构建要求及 BYOB 输入见[独立构建指南](../../docs/guides/building.md)。旧远端部署记录只用于历史参考。

# Owly AirDrop · 小米 14 独立测试 App

包名 `org.houji.owly`，显示名称 **Owly AirDrop**。使用 Owly 的 AWDL、mDNS、TLS、AirDrop 请求确认、CPIO/DVZip/gzip 文件收发；不调用 MIShare/XOS 的发现或文件传输接口，也不注册系统分享目标。

## 手机上使用

1. 打开普通 Wi-Fi，然后打开 **Owly AirDrop**。首次使用在 KernelSU 授权该 App root。
2. iPhone 开启 AirDrop「所有人（10 分钟）」，打开分享面板以激活 AWDL。
3. App 点「搜索附近设备」，在列表选择 iPhone/iPad，然后点「连接所选设备」。当普通 WLAN 位于 `2.4 GHz` 或非 DFS `5 GHz`（`5180–5240` / `5745–5825 MHz`）时，脚本会自动启用共存模式保持普通 WLAN 在线；处于 DFS 或受限状态时才会暂停普通 Wi-Fi，并在停止时自动扫描/重连恢复。
4. iPhone 发送时选择 **Xiaomi 14 Owly**，在 App 确认接收。小米发送时，在 App 选文件并点「发送文件」，在 iPhone 接受。收发过程及完成后界面均会显示实时进度、已用/总传输时间与平均速度报告。
5. 接收文件先存于 App 私有目录，通过「收到的文件 · 保存到…」导出。停止时点击「停止并恢复 Wi-Fi」。如在双 5 GHz 共存且信号偏弱下从 iPhone 接收大体积视频感觉偏慢，可临时关闭「保持普通 Wi-Fi 联网」切换独占模式，或将手机普通 Wi-Fi 改连 2.4 GHz。

首版一次连接一台设备，固定149信道。发现阶段不预设 Mac 地址；根据 AWDL 设备类型、名字、信号强度列出候选。用户选定后重新建立已有实测覆盖的单远端 peer/EZIF 聚合链路。设备地址变化时重新搜索。

普通 WLAN 为 `2.4 GHz` 或非 DFS `5 GHz` 时会在扫描与连接传输期间保持在线；DFS 或受限场景下自动暂停并在退出后恢复，且内建有界扫描与开关重试恢复逻辑。请保持 App 打开以确认接收；接收请求会超时拒绝，不自动接受。无线会话最长30分钟，正常退出、超时、后端错误均进入恢复流程。没有增加开机替换驱动。

## 实装和隔离

- Java App 使用自己的私有文件队列与状态文件；每条命令绑定本次会话 ID，避免旧确认作用于下一次会话。
- root生成的状态与收件继承App私有目录的SELinux类别标签；收件完成后交给App UID。后端退出会终止无线会话并恢复，避免仅剩界面旧状态。
- 发布本机服务与浏览对方服务共用一个mDNS socket，并通过 `SO_BINDTOIFINDEX` 绑定接口索引，避免在共存多宿主接口与复用 5353 端口的 socket 间分走单播响应。
- 原生后端由本目录 `native/` 组合顶层公共无线库 [`../../native/houji-runner`](../../native/houji-runner) 与协议工作区 [`../../native/owly`](../../native/owly) 构建，保留上游许可证。
- 根权限运行目录 `/data/local/tmp/owly-app`，App 数据 `/data/user/0/org.houji.owly/files`。
- 扫描用已验证的DZF2路径、不创建固定远端peer；连接后使用官方EZIF、接收适配gate、remote_reo=2、初始窗口64、1ms软件合包、2SS/MCS9/SGI和TX聚合（`2.4 GHz` / 非 DFS `5 GHz` 下同时保持 STA 共存，否则回落 `pt_exclusive=1`）。沿用原RX优先调度，没有采用尚未证明单向收益的公平调度候选。
- 加载前通过 `runtime-manifest.json` 核对安装清单哈希；`install.py` 从 `blobs/local/`（或环境变量）解析闭源输入并已与构建目录解耦。生命周期复用已有审核过的锁、模块释放和恢复函数。运行时会暂停原生 XOS 或 [`runtime/owly-xos`](../../runtime/owly-xos/README.zh-CN.md) 提供的 `IDomesticXOsComm/default`（`XosBridge`）服务，停止恢复后再重新拉起，避免无线所有权冲突。
- App使用root调用独立控制脚本；APK本身不含无线模块/固件。`install.py`部署该手机专用的配套运行时，因此不能把单独APK当作通用Android版。

## 构建、安装

```sh
bash build-native.sh        # Android NDK/Rust 原生构建（需按 BYOB 配置工具链）
bash build-app.sh           # Android SDK/build-tools 打包签名
python3 ../../runtime/owly-app/build-radio.py
ANDROID_SERIAL=<serial> python3 install.py
```

无线模块使用经 567 导入符号 ABI 审计的共存/远端 peer 产物（`runtime-manifest.json` 指定哈希）。当前设备部署哈希记录在 `evidence/installed-artifacts.json`。

## 已验证与待用户确认

- 设备上搜索发现了附近Mac；按选择的MAC切换至EZIF远端peer链路成功。
- 实际AWDL链路上完成TLS `/Discover`、`/Ask`、通过App命令队列确认、gzip `/Upload`、解包落盘。69字节文件SHA256一致，见 `evidence/receive-smoke.json`。
- Android Owly发送端通过mDNS发现临时测试接收端，完成TLS、DVZip打包上传、对端逐字节核对与HTTP200回执，App状态「发送完成」。61字节文件SHA256一致，见 `evidence/send-smoke.json`。
- 功能检查后安全恢复原厂无线模块、固件、普通Wi-Fi与原有待命服务，见 `evidence/restored-after-functional.txt`。
- 协议检查对应产物身份保存在 `evidence/protocol-tested-artifacts.json`，与最终安装清单对应；`evidence/app-file-labels.txt`记录App状态与本次收件的SELinux类别。mDNS回归日志在 `evidence/mdns-regression.log`。

这些是小文件功能检查，不是性能测试，也不是iPhone实机验收。手机锁屏时通过root控制入口和App命令队列检查了底层流程；用户选择稍后自行解锁，因此GUI点击和首次KernelSU授权由用户完成。iPhone上的发现、接受、照片格式和真实文件收发体验由用户接下来测试。收到的照片暂不自动导入相册；通过系统文件保存对话框导出，保留独立文件/目录。

## 恢复

App中点击停止。必要时通过已授权root shell执行：

```sh
sh /data/local/tmp/owly-app/control.sh stop
```

日志位于 `/data/local/tmp/owly-app/state/session.log` 和 `runner.log`。不要在运行会话中直接卸载模块或删除运行目录。
