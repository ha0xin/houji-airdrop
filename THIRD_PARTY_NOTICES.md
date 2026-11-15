# 许可与第三方边界

本仓库汇集不同来源的代码，不以统一根目录许可证覆盖既有许可。文件中的 SPDX、版权及许可证声明优先；公开仓库不表示所有内容自动取得相同许可。

| 组件 | 许可与来源入口 |
|---|---|
| Owly 协议栈及保留的实验副本 | [Owly MIT LICENSE](native/owly/LICENSE)，保留其第三方声明 |
| MiCode / Qualcomm WLAN 修改覆盖层 | `driver/wlan/` 的各文件声明；完整原始源码由使用者按 [BYOB](docs/guides/byob.md) 准备 |
| Wonder、bridge、内核配置和驱动许可 | [驱动第三方声明](driver/THIRD_PARTY_NOTICES.md)、[驱动 LICENSE](driver/LICENSE)、[许可文本](driver/LICENSES/) |
| `wonder-init` 无线初始化工具 | `native/wonder-init/`（`SPDX-License-Identifier: GPL-2.0-only`） |
| 实验工具及其他保留代码 | 按原文件或所在组件许可证执行；未明确许可的自有新增文件暂未另行授予统一开源许可 |

三星固件、完整 ROM、原厂 `MIShare.apk`、`libxos.so` 等闭源组件不随本仓库分发，参见 [BYOB](docs/guides/byob.md)。
