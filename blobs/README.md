# BYOB：外部固件

请先阅读[BYOB 输入清单](../docs/guides/byob.md)。运行固件自行提取后放到被 Git 忽略的 `blobs/local/`，或通过环境变量指定；不会自动下载或上传固件。

编译只需公开原始源码及工具链，不需要固件。没有新增版本锁定或 SHA 清单；已有历史校验保留。[提取研究材料](../experiments/tx-ampdu-investigation/firmware-acquisition/)继续留作参考。
