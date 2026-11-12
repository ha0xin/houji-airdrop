# 许可与第三方边界

本仓库汇集不同来源的代码，不以统一根目录许可证覆盖既有许可。文件中的 SPDX、版权及许可证声明优先；公开仓库不表示所有内容自动取得相同许可。本次整理没有为未明确授权的新文件自行指定许可证。

| 组件 | 许可与来源入口 |
|---|---|
| Owly 协议栈及保留的实验副本 | [Owly MIT LICENSE](clients/owly-app/native/owly/LICENSE)，保留其第三方声明 |
| MiCode / Qualcomm WLAN 修改覆盖层 | `driver/wlan/` 的各文件声明；完整源码由使用者按 BYOB 准备 |
| Wonder、bridge、内核配置和移植基础 | [上游声明](third_party/upstream/THIRD_PARTY_NOTICES.md)、[上游 LICENSE](third_party/upstream/LICENSE)、[许可文本](third_party/upstream/LICENSES/)；其中“本仓库”仅指该上游子树 |
| 实验工具及其他保留代码 | 按原文件或所在组件许可证执行；未明确许可的自有新增文件暂未另行授予统一开源许可 |

三星固件、完整 ROM、Google Mosey、`libxos.so` 等闭源组件不随本仓库分发。参见 [BYOB](docs/guides/byob.md)。不需要的网站 HTML/JavaScript 下载快照已排除。
