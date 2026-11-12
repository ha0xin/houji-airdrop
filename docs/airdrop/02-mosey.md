# Mosey 路线

## 实现与位置

链路：实验 WLAN / bridge / wonder（加 `SM-S928B` `S928BXXS6DZF2` 的 `amss20.bin`）→ One UI 9 移植包（`SM-S928B` `S928BXXU6EZIF` `system_ext_a.img`）提取的 `mosey_server`（`e7a4180b…`）与 `libmosey_daemon_ffi.so`（`d786d7f7…`）→ `mosey0` → Java AirDrop 收发探针。

- [源码与产物](../../downloads/oneui9-houji-analysis/reference-runtime/probe)：`MobileSession.java` 管理会话与路由，`Discover.java` 维持 Binder 会话和发现，`Receive.java` 提供 mDNS、TLS、接收及解包，`StreamingTransferProbe.java` 为传输探针。
- 手机历史部署目录：`/data/local/tmp/houji-hold.cxPP1y`。
- Binder：`com.google.android.moseyservice.IMoseyService/default`。
- 接收端口：TCP 8770；路由 table 10073，接口地址重建后需重新生成。
- 末版接收产物 `receive11.zip`；延长会话的发现产物 `discover2.zip`。

`userspace-resume.sh` 通过私有挂载命名空间提供库并注入 `local-stability.so`，其 Stage37 使用 HT20 NSS1 MCS0 GI-default。原会话15分钟超时会停止 Mosey；探针后来延长到2小时。会话结束可能让 pt_activate 进入单次收尾状态，需要按恢复脚本重载模块，单独重启用户态不保证成功。

## 已解决的协议问题

- Apple 的 IPv6 地址会轮换：路由由固定对端改为 `fe80::/64` 与 `ff00::/8`，对当前邻居动态公告。
- iOS 15 的 Ask 可达74886字节，原64 KiB限制不足，扩大至256 KiB。
- Ask 中可能有多个 UUID，不能把任意匹配的 UUID 当 TransferID；接收探针增加按对端短时许可，解决上传关联失败。这属于实验协议实现，不等于完整联系人身份认证。
- CPIO 读到 `TRAILER!!!` 后及时返回 HTTP 200，避免等待分块结束导致发送端超时。
- Live Photo：flags 从427变为491（加入0x40 SupportsIris），响应声明媒体能力，接收上限升至64 MiB。不能只改文件扩展名来取得实况照片。

## 验收证据

| 路径 | 结果 |
|---|---|
| 小米 → Apple phone | 原生 AirDrop 接受，上传 HTTP 200 |
| Mac 原生 → 小米 | Mac 显示已发送，解包图片269771字节 |
| Apple phone → 小米 | IMG_0010.PNG，616825字节，与源文件 SHA-256 一致 |
| Apple phone → 小米 | IMG_2130.PNG，1084825字节，与 Ask 大小一致 |
| Apple phone Live Photo → 小米 | IMG_2122.pvt 完整包含 HEIC 1521797字节、MOV 2960653字节、metadata 366字节 |

历史设备日志包括 `native-6s5.log`、`native-dual.log`、`server-resume.log`；细节与哈希见[原交接](../../downloads/oneui9-houji-analysis/AIRDROP-HANDOFF.md)。这些是当时验收，不表示当前运行的是 Mosey。

## 使用边界

没有接入小米原版相册分享或超级岛，接收文件也未完成相册导入。旧探针包含固定对端和历史临时路径；`probe/run.sh` 当前内容甚至是历史文件缺失错误文本，**不是可执行部署入口**。恢复应先检查设备端 `reload-experimental.sh`、`userspace-resume.sh`、库、固件与模块是否齐全，再使用历史流程。不要把旧交接中的一条重载命令当作从干净系统安装的完整步骤。

该路线保留用于协议及引擎对照。尚未在与 XOS 完全相同的控制条件下做吞吐对比，不能声称切回 Mosey 就能提速。
