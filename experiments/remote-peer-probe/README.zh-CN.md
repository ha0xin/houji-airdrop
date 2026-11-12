# PASSTHRU 固件 peer 能力只读探针

该候选只在已有 PASSTHRU self-peer 初始化位置读本轮固件的 `PEER_CREATE_CONF`、`SYNC_DELETE_CMDS`、peer unmap 确认服务位和 DP getter 返回值。装载参数 `pt_peer_caps_observe` 默认关闭，输出 `pt_peer_caps` 为 `0444`。启用而 `sampled=0` 时没有取到有效快照。能力位并不证明固件接受 PASSTHRU remote peer。实机 `dp_unmap_conf=0` 是 wifi3.0 缺少后端回调时的 CDP 返回值。WMI INIT 的资源位另由 WMA 从 MLME 配置、工作模式与服务位生成，本轮未采样该资源位。

已在 `build-host:/work/houji-build/src/remote-peer-caps/wlan` 使用 Clang r487747c 构建。完整模块 SHA-256 为 `2450ca5abe4332dab0a66cfab6538c786894dee429b4b76978437e98f96aa811`，远端剥离调试段产物位于 `/work/houji-build/artifacts/remote-peer-caps/qca_cld3_kiwi_v2.ko`，SHA-256 为 `739abb094c277b6fa675d9a59de3502019c5344b0a7466b843d43b6eaa2ad7f0`。能力观察器已单轮实机初始化并读取。remote create/delete 另用下述独立候选完成单轮实机验证。

[补丁](peer-capabilities.patch) · [ABI 检查](audit-stripped.json) · [构建日志](build-caps.log) · [下一阶段设计与公开 WMI/HTT 参照](../../../docs/airdrop/19-passthru-remote-peer-probe-plan.md)

## 实机读取

```sh
cd ../owly-evaluation/houji-runner
python3 bench.py deploy --variant mss-vht --include-peer-caps
python3 ../../remote-peer-probe/read-device-caps.py
```

读取脚本与基准共用设备侧锁及恢复链，运行单独的 `caps` 初始化入口。 [设备脚本差分](device-harness.patch) · [能力快照](device-read1/result.json) · [恢复凭据](device-read1/stock-after.txt)


## 远端 peer 事务

四个补丁按 WMA、DP 回执、宿主准备、owner 入口顺序应用在能力观察器基线，由 `build-adapter.py` 在 build-host 构建。全链路实机退出 0，目标 Mac 的 generation 1 / vdev 0 / peer ID 1 通过 create confirmation、HTT map/slot、delete response、unmap/slot remove 和两类对象释放的独立核验。临时 vdev 退役，普通 Wi-Fi 和原生 149/MCS3 待命恢复。 [源哈希](adapter-source.sha256) · [ABI](audit-adapter.json) · [构建日志](build-owner-final.log) · [实机结果](device-remote1/result.json) · [stock 恢复](device-remote1/stock-after.txt)

恢复演练只使用原厂 WLAN，正常重启后按模拟账本恢复。实机 peer 事务未用重启回退。 [恢复演练](reboot-rehearsal/result.json) · [详细记录](../../../docs/airdrop/19-passthru-remote-peer-probe-plan.md#一次性候选与实机回执)

## 数据路径对照

仅 self peer 的同 worker/暂停重开对照有 31 次 TQM 完成并正常恢复。加入真实 remote create/map 后，即使保留 self AST、跳过 REO setup，115 次已入环描述符仍无 TQM 完成，普通恢复停在 BLOCKED。本次通过同一失败 token 正常重启恢复 stock 与原生 149 待命。远端 peer 活流先冻结。 [self-only](raw-flow-next/device-self-flow1/result.json) · [map-only](raw-flow-next/device-map-only1/result.json) · [恢复](raw-flow-next/device-map-only1/recovery-result.json)

4224 字节容量候选以稳定 wideband-ring 源为基线，独立于 remote-peer 实验。保留 HT 广告的容量烟测和双向 64 MiB 基线通过，旧发送侧 VHT 大 A-MSDU 继续关闭。随后独立的 MIF VHT 广告两次开启轮接收约 2.9 MB/s，完整验收和日志边界见容量审计。 [容量审计](../../../docs/airdrop/23-vht-rx-capacity.md) · [源码差分](vht-rx-next/rx-capacity-4224.patch) · [四轮结果](vht-rx-next/outgoing-advert/long-summary.json) · [PSF 覆盖候选](vht-rx-next/outgoing-psf/README.zh-CN.md)

最新 PHY、双时钟与公平轮询的实测结果以及暂停中的设备 owner，见 [Owly 交接](../../../docs/airdrop/24-owly-houji-handoff.md)。
