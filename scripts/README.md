# 按平台构建

| 入口 | 构建平台 | 产物 |
|---|---|---|
| `build-driver.sh` | Linux x86_64 | bridge、WLAN、wonder 内核模块 |
| `build-userspace.sh` | Linux / macOS（需对应 NDK 主机工具） | Android ARM64 原生程序 |
| `build-app.sh` | Linux / macOS（需对应 SDK 工具） | Android APK |

以上入口均在当前主机执行，不固定 SSH 主机、用户名、SDK 临时目录或工作区。源码以脚本所在仓库定位；外部输入通过环境变量提供，见[构建指南](../docs/guides/building.md)。如使用远端 Linux，自行选择主机并在那里运行相同入口。

旧实验构建配方位于 [archive/legacy-build](../archive/legacy-build/README.md)，不属于正式构建依赖。其他历史实验脚本如需旧目录布局，必须显式设置 `HOUJI_LEGACY_ROOT`，默认不会猜测或读取任何个人机器路径。
