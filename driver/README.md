# 驱动

`wlan/` 是当前 MiCode kiwi_v2 修改文件覆盖层，不是完整 WLAN 源码。独立构建入口直接读取这里，与 BYOB 原始源码组合构建三个模块。

- [构建入口](../scripts/build-driver.sh)
- [上游配方和基础组件](../third_party/upstream/kernel/)
- [现有补丁、ABI 审计和实验构建](../experiments/three-findings-validation/)
- [共存实验](../experiments/sta-coexist/README.zh-CN.md)

此次只整理目录，未重新导出补丁、修改无线逻辑或新增版本锁定。现有实验产物不等同于 App 已安装版本。

独立构建已补齐旧快照遗漏的 RX 容量、PHY 与 buffer pool 前置文件；来源与验证过程见[构建记录](../docs/guides/build-verification.md)。构建使用 [BYOB 原始源码归档](../docs/guides/byob.md)，不再读取旧实验工作树。
