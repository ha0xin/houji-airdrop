# 原生无线守护与协议栈（`native/`）

本目录提供独立于任何上层客户端的 Android ARM64 原生协议栈与无线接口工具：

| 子目录 | 产物 / 角色 | 依赖边界 |
|---|---|---|
| `owly/` | Rust AWDL 与 AirDrop 协议工作区（MIT，保留原 [LICENSE](owly/LICENSE)） | 公共协议库（`owly-awdl`、`owly-radio`、`owly-airdrop`） |
| `houji-runner/` | `owly-xosd` 可执行守护与 `houji_runner` 库（`wonder0` Radiotap 收发、`xos0` L2 TAP、动态内核 peer 同步、用户态 NDP 与单播 mDNS 转投） | **仅依赖 `owly-awdl`、`crc32fast`、`libc`、`tokio`、`tracing`**；不依赖 `owly-airdrop`、`rustls`/`ring`、`serde_json` 或 `clients/owly-app` |
| `wonder-init/` | `wonder-init` 独立静态 C 工具（零 libc，通过 `nl80211` 创建 `wonder0` 并配置 149 信道 VHT80 2SS MCS9 SGI） | 自包含（`wonder-init.c` + `probe.c` + `uapi-values.h`，GPL-2.0-only） |

主线 `Owly-XOS` 通过 [`runtime/owly-xos/build-native.sh`](../runtime/owly-xos/build-native.sh)（或顶层 `bash scripts/build-xos.sh`）直接构建 `owly-xosd` 与 `wonder-init`；暂缓的独立 App 路线在 [`clients/owly-app/native/`](../clients/owly-app/native/) 中按需链接 `houji_runner` 与 `owly-airdrop` 构建 `houji-owly-app`。
