# 小米 XOS 路线

## 基础移植

[源材料](../../downloads/madrid-analysis)包括 `libxos.so`、调试符号、`DomesticXOsComm`、依赖库及 MIShare.apk。`xos_runner.c` 直接启动 libxos，绕开原 vendor 服务的安装约束。

原 libxos 下发 `(preamble,bw,gi,nss,mcs)=(1,0,2,2,11)`，驱动在接口拉起时返回 -95。runner 只对 OUI 0x001a11、subcmd3 和该原始元组改写。初始成功值为 `(1,0,0,1,0)`，当前经测速保留 `(1,0,0,1,3)`，即 HT20 NSS1 MCS3 GI-default。原版请求149，实际 runner 传6。命令返回成功与接口实际启动分别验证。

runner 仍含早期较宽泛的 Netlink 错误放行，后续应收窄；不能把所有返回0都当成驱动支持相应功能。

## 阶段一：独立测试应用

[port 目录](../../downloads/madrid-analysis/port)提供 `org.houji.xosport`、root 收发服务和 `xos-port.sh`。用户可开启接收、刷新设备、选文件发送和查看状态。

- iPhone → 小米：IMG_2120.pvt，7711917字节归档，HEIC/MOV/metadata完整保留。
- 小米 → iPhone：37字节文本，Ask/Upload HTTP200，用户确认能打开。
- Mac AWDL 协议探针上传也通过；不混称为 Mac 原生分享验收。
- 接收目录 `/sdcard/Download/XOS-AirDrop/received-…/`，自动接受，上限64 MiB；未自动导入相册。
- “停止接收”保留实验驱动，与原版集成的退出恢复行为不同。

## 阶段二：原版 MIShare

[集成目录](../../downloads/madrid-analysis/native-integration)保留原签名 MIShare `5.4.2_20260923_1315`，包名 `com.miui.mishare.connectivity`。旧4.13.11 APK及设备应用数据有备份。

| 组件 | 职责 |
|---|---|
| src/XosBridge.java | 实现 canonical Binder，限制 root/互传 UID，持有者及死亡通知管理 |
| runtime/bridge-supervisor.sh | 单实例锁、运行 Binder 服务，退出时尝试恢复 |
| runtime/radio.sh | 保存状态、切换模块/固件、启动 runner、验证释放并恢复 |
| hook/src/org/houji/xoscompat/Hook.java | 原版 Wi-Fi 状态与旧系统分享兼容 |

LSPosed 仅作用于互传包和 `com.android.intentresolver`：

1. 独占模式下抑制驱动主动关 Wi-Fi 引发的互传取消；补上等待锁后才读 Wi-Fi 的竞态，修改仅限相关调用栈。
2. Binder 可用时通过按需驱动的功能检测，让分享入口出现。
3. 旧分享面板按包名把 AirDrop 误当普通互传，点击后 `device is null!`；仅对 `AirDropShareTrampolineActivity` 将 targetType 0改2，保留相册隐私处理和 URI 授权流程。
4. 页面说明调整为实际会暂停普通 WLAN。

已授予 NETWORK_FACTORY。SELinux enforcing；当前额外 live rule 为 `allow platform_app default_android_service service_manager find`，服务自身仍检查 UID。受限网络 keep-alive 权限警告未彻底解决，但真实收发和 NetworkAgent 已工作。

## 原版验收与边界

iPhone → 小米 IMG_2134.JPG 接收、解包、HTTP200完成，用户确认超级岛；系统相册 → iPhone 照片发送完成，用户确认可用。原版收件目录 `/sdcard/Download/MiShare/AirDrop/`。

原版代码有拆分 .pvt、忽略 metadata 的处理，但本轮原版验收样本是 JPG；不能拿测试应用的 Live Photo 验收冒充原版实况照片相册体验验证。

同一 iPhone 曾显示两个服务 ID，旧条目随后消退，未修复去重。发现有延迟。尚无开机自启和 SELinux 规则持久化，也未对所有系统分享来源、所有文件格式、大文件完成率作全面验收。
