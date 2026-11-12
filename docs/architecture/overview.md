# 当前架构与后续方向

当前实现：独立 Android App → App 专用 Rust 后端 / Owly → root 无线生命周期脚本 → Wonder/PASSTHRU 与 kiwi_v2 驱动。

正式入口分别为 `clients/owly-app/`、`runtime/owly-app/`、`driver/wlan/`；实验后端和实机验证仍在 `experiments/`。

后续目标是统一 root 守护进程，供独立 App 和小米互传 Hook 共同使用。统一 IPC、任务归属、接收确认、权限校验、客户端断开处理，以及 WLAN 共存接入都尚需设计和验证。此次目录整理没有实现这些功能。

暂不拆分 Rust workspace、合并实验 Owly 副本或添加空 Hook 模块，避免目录结构暗示尚不存在的实现。
