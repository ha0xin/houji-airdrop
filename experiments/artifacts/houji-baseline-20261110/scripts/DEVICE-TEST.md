# 离线基线产物的上机验收

本轮不需要手机连接，也不执行 ADB。产物是未加性能改动的重建基线；不应预期传输速度提升。

## 连接后先核实

1. 核对序列号、`uname -r`、当前启动 ID、Wi-Fi 状态以及是否仍为同一系统。记录现有三个实验模块和候选 AMSS 的 SHA256。
2. 确认现有 `radio.sh`、Binder bridge 和回退路径仍与当前文档一致。先关闭互传接收页面、结束所有传输，让服务释放最后一个 holder，确认 `STOCK_RESTORED`、原厂驱动 build-id、原厂 AMSS 哈希和 Wi-Fi 联网。
3. 仅把基线包上传到一个新建的独立目录并核对 SHA256；备份手机上的现用模块。不要覆盖 `/vendor_dlkm`、刷写分区或设置开机加载。
4. 参考模块来自 `open-runtime-v0.5.1-preview.1.zip` 的 `runtime/modules`；不要误用 `quickshare-runtime` 的 framework WLAN 版本。离线包中的 reference 是应急参考件，手机原有的确切字节备份仍应优先保留。

## 首轮加载

复用已验收的 `radio.sh` 生命周期，分别指定一个独立的测试模块目录，保留原有默认目录。正式生成并检查这个测试脚本前，必须读取届时的设备状态与现行脚本；离线包不包含自动替换现用驱动的入口。

按原流程执行 stock unload → bridge → WLAN（`pt_exclusive=1 pt_zero_serial=1 pt_rx_metadata=1`）→ pt_activate → wonder（`synthetic_provider=1`），先用原来的 channel 6 / MCS3。保持原有 AMSS、XOS、速率和信道不变，避免把重建差异与性能实验混在一起。

核对加载错误、内核告警、provider 绑定、wonder0/xos0 链路、发现和短文件双向传输；每个文件对照 SHA256。之后检查结束会话是否自动恢复原厂驱动、原厂固件和 Wi-Fi；重复至少三轮启停。

## 回退

正常路径是退出会话并由原有 `radio.sh restore` 执行 Wonder → provider release → WLAN → bridge 的退出顺序、解除 AMSS bind mount、重载 stock WLAN 并恢复保存的 Wi-Fi 状态。不要越过引用计数、release 或固件哈希检查强制卸载。

如果实验模块造成系统无响应，重启是临时加载方案的恢复路径之一；连接前必须确认没有新增持久化替换或启动加载。重启后仍需核实 stock build-id、固件 SHA256、Wi-Fi 联网，不以“手机能开机”代替恢复验收。

通过基线验收后，再单独测试完成队列观测、信道 149 冷启动或其他性能改动。
