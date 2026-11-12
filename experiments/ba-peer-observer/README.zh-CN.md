# 小米 14 PASSTHRU 单对端 RX 观察器

该候选基于已上机的 [全局 RX 观察器](../ba-observer/README.zh-CN.md)。只有装载时同时设置 `pt_ba_observe=1` 和有效的 `pt_ba_peer`，才打开新计数。两个新增参数的 sysfs 权限都是 `0444`。地址参数读回只返回 0 或 1。补丁保留既有 RX/TX、peer、REO 命令和响应逻辑。

- raw RX 在既有验证之后核对 24 字节三地址头、TA、RA、Block Ack Action 和碎片标志。从完整请求记录 TID、最后的窗口与 SSN。FCS 判断复用现有 Kiwi 受验证 metadata 标记。无该标记且 33–36 字节的请求可能含 FCS，单列 `unknown_fcs`。不足 33 字节、或有 FCS 而不足 37 字节时记入 `incomplete_request`。
- `remote_map_event` 只在查到已登记的 PASSTHRU vdev 引用且地址匹配时记录。实际 peer ID 插入后才增加 `remote_map` 或全局的 `self_map` 正对照。取得临时 vdev 引用和释放均在观察锁之外。`self_map` 和相应 unmap 只覆盖 PASSTHRU。
- 只在每次装载的第一段 PASSTHRU vdev 生命周期归属 HTT 和 REO。已经重复出现或退役的 peer ID 保持不可归属。标量状态和位图不持有内核对象指针。HTT 查不到 peer 时记全局 `htt_missing_global`，已查到对象仍须通过单对端身份检查。HTT 的 host 处理结果不代表空口确认。
- REO 只读现有 route、qdesc 和命令提交返回。提交成功与 REO 完成、固件应答、空口 BA 和重排分别验证。该版本没有异步完成钩子。

## 远端构建与检查

从 `build-host:/work/houji-build/src/ba-observer/wlan` 派生独立源码和 Kbuild 输出，使用远端 Clang r487747c 与 6.1.68 重建配方。部署使用经过同一远端工具链剥离调试段的交付模块。

```sh
python3 build.py
python3 check-vectors.py
mkdir -p /work/houji-build/artifacts/ba-peer-observer
/work/houji-build/toolchains/clang-r487747c/bin/llvm-strip --strip-debug \
  -o /work/houji-build/artifacts/ba-peer-observer/qca_cld3_kiwi_v2.ko \
  /work/houji-build/src/ba-peer-observer/wlan/qcacld-3.0/qca_cld3_kiwi_v2.ko
python3 verify.py /work/houji-build/artifacts/ba-peer-observer/qca_cld3_kiwi_v2.ko
```

完整模块 SHA-256 为 `a7c1f27eaef55408f4b6023f02d5e55336f1cb4159f86f3b5e9d36be5314eddd`，剥离后交付模块 SHA-256 为 `1ad02ff5e62183690c6f1d52a075f5d73001ed82d44c59cd37ec3bf2d5b2c3c4`。和两份已知 WLAN 模块相比，架构、vermagic、依赖、567 项导入及 CRC、导出 CRC 和 module 结构检查一致。链接器仍报告两条既有 fast connect/配置栈帧尺寸警告。

[差分](passthru-peer-observe.patch) · [构建记录](build.log) · [ABI 检查](audit-stripped.json) · [离线向量](vectors.log) · [观测方案与证伪条件](../../../docs/airdrop/17-rx-peer-reo-observer-plan.md)

## 单轮实机

沿用 Owly runner 的共享锁、唯一 token 和恢复流程，候选名为 `qca_cld3_kiwi_v2-peer-observer.ko`。实机入口：

```sh
python3 bench.py deploy --variant mss --include-ba-peer-observer
python3 bench.py measure ba-peer1-64m vht80-sgi amsdu ack --ba-observer peer --mss 1160 --pairs 1
```

[实机入口差分](device-harness.patch) · [结果与前后计数](device-summary.json) · [原始日志](../owly-evaluation/houji-runner/results/ba-peer1-64m/) · [恢复复查](device-final-stock.txt)
