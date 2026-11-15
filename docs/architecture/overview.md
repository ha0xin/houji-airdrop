# 当前架构与双前端边界

当前仓库在同一套 Wonder/PASSTHRU + `qca_cld3_kiwi_v2` 驱动与 `houji-runner` / `owly-awdl` 原生核心之上，提供两套互斥运行入口：

1. **`runtime/owly-xos/`（当前开发主线）**：小米系统原版 `MIShare.apk` → AIDL `XosBridge`（`com.domestic.xoscomm.IDomesticXOsComm/default`，SELinux `Enforcing`）→ `radio.sh`（`ezif.bin` + `pt_remote_multi=1` 免预绑定多对端硬件 TX A-MPDU）→ `houji-owly-app` L2 TAP `xos0`。保留系统分享菜单、超级岛接收弹窗与相册入库，无需修改系统 APK。
2. **`clients/owly-app/` + `runtime/owly-app/`（独立 App 路线，当前暂缓）**：独立 Android App `Owly AirDrop` → `houji-owly-app` Rust AirDrop 协议栈（L3 TUN `owly0`）→ `radio.sh`（支持 `2.4 GHz` 与非 DFS `5 GHz` 普通 WLAN STA 共存）。

两套运行时通过共享的无线状态门禁、`pt_release` 与 `supervisor` 服务启停互锁，确保任意时刻只有一个前端持有 PASSTHRU 会话，并在退出时完整恢复原厂 `STOCK_RESTORED` 状态。实验分支与早期对照归档仍在 `experiments/`。
