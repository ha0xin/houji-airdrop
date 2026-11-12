# houji PASSTHRU RX 聚合观察版

从 `build-host:/work/houji-build/src/wideband/wlan` 派生独立 WLAN 候选。补丁只接入现有 `dp_rx_deliver_raw_passthru()`。所有现有 return、raw 交付、SKB 所有权和固件命令保持原逻辑。代码不新增 BA 响应、peer 创建、REO 写入或 TX 行为。

- `pt_ba_observe` 是默认关闭、权限 `0444` 的模块参数。
- `pt_ba_stats` 是权限 `0444` 的只读计数快照。现有前置检查之后才读 raw header，HAL 读字段另外复用完整 Kiwi TLV 布局校验，无法判断的项归入 `unknown`。
- `ampdu` 统计 HAL 给出 A-MPDU 标志的 raw buffer，`ampdu_ppdu_adjacent` 仅计连续观察中相邻且 PPDU ID/vdev 相同的记录。它不计算独立 PPDU 数、MPDU 序号窗口或真实空口 Block Ack。TID 统计限 HAL 未标 FCS 错误、长度足够的三地址 QoS Data。
- ADDBA Request、Response 和 DELBA 计数只按清晰的 802.11 Action category/subtype 头部识别，未解析 body、未绑定远端 peer、未判断协商是否成功。拒绝统计只覆盖这个 raw 交付函数已存在的四个拒绝点。
- 这版没有 HTT peer-map、HTT RX_ADDBA 或 REO 完成事件计数。那些信号需要单独处理 peer 与 generation 归属。实机四轮已确认默认关闭和显式开启的回读及恢复。独立被动接收器能力尚未确认。 [实机记录](../../../docs/airdrop/16-ba-observer-device.md)

## 构建与静态检查

在 `build-host:/work/houji-build/ba-observer` 使用 Clang 17 `r487747c` 和已有 6.1.68 Kbuild 配方。源码副本和 kernel out 分别位于 `src/ba-observer/wlan` 和 `out/ba-observer-wlan`。脚本要求独立路径尚不存在，并校验已测 ring 归档 SHA、输入文件和保留的 TX/control 文件。

```sh
cd /work/houji-build/ba-observer
python3 build.py
python3 audit.py
```

构建记录 [build.log](build.log) 保留源文件 SHA 与完整构建输出。[ABI 检查](audit.json) 分别使用已测 ring 归档和当前宽带源码树已有二进制为参照，核对架构、vermagic、依赖、导入/导出 CRC、未定义符号和模块结构。参数权限及默认标志从 ELF 核验。两份参照模块的整文件 SHA-256 不同，可执行段哈希一致。构建的两条 WLAN 栈帧尺寸警告与既有宽带构建一致。静态通过不代表手机可装载或能够回应 BA。

完整调试产物保留在 `build-host:/work/houji-build/src/ba-observer/wlan/qcacld-3.0/qca_cld3_kiwi_v2.ko`，SHA-256 为 `57f6d2b8433ac0d1f865d02a7ec7f33e2ffcdabc6e80074f04d3b0bf3090f0ea`。交付副本在服务器上用同一工具链的 `llvm-strip --strip-debug` 处理，重新通过 ABI 检查并拉回校验，SHA-256 为 `b9f0a77ec9132e57677f83e75e4e3185087b8c4a104878db532a894cb0c4637b`。 [交付副本 ABI](audit-stripped.json) · [交付副本](qca_cld3_kiwi_v2.ko) · [SHA256SUMS](SHA256SUMS)

[聚合验证路线](../../../docs/airdrop/15-aggregation-roadmap.md)
