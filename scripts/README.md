# 构建脚本入口（`scripts/`）

| 入口 | 构建平台 | 产物与目录 |
|---|---|---|
| `build-driver.sh` | Linux x86_64 | 三个内核模块（`dist/driver/{houji_wonder_bridge,qca_cld3_kiwi_v2,wonder}.ko`） |
| `build-xos.sh` | Linux / macOS（需 NDK + SDK） | **主线 `Owly-XOS` 全套产物**（`runtime/owly-xos/dist/`：`owly-xosd`、`wonder-init`、`xos-bridge.zip`、`Owly-XOS-Compat.apk`；加 `--package` 可一并打出 KernelSU 刷机包），**零独立 App 依赖** |
| `build-userspace.sh` | Linux / macOS（需 NDK + Rust） | 主线 `Owly-XOS` 原生二进制（`owly-xosd`、`wonder-init`） |
| `build-app.sh` | Linux / macOS（需 SDK） | （可选/暂缓）独立 `Owly AirDrop` APK（`clients/owly-app/dist/Owly-AirDrop.apk`） |

以上入口均基于脚本所在仓库定位源码，外部工具链与源码包通过环境变量传入，详见 [构建说明](../docs/guides/building.md) 与 [BYOB 输入说明](../docs/guides/byob.md)。
