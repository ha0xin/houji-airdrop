# 客户端

[owly-app](owly-app/README.zh-CN.md) 是已实现的独立 Android App，`app/` 为界面，`native/houji-runner/` 为 Android 后端，`native/owly/` 为它实际使用的协议栈。

目标是让独立 App 与小米互传 Hook 共用守护进程。当前尚未抽离统一 daemon/IPC，也未实现互传 Hook；不创建空模块冒充已实现功能。实验目录中的其他 Owly 副本保留用于对照，不能直接覆盖 App 使用的版本。
