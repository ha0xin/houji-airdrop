# EZIF 官方固件单变量探测（2026-11-11）

> 本文保留首次失败与恢复证据。后续 RX 适配、TX 聚合和吞吐成功结果见 [第32篇](32-ezif-tx-ampdu-validation.md)。

## 结论

官方 EZIF `amss20.bin` 成功启动，原始服务位 `506=1`；既有 DZF2 测试为 `506=0`。这证明两个固件在该能力声明上不同，不代表 TX A-MPDU 已实际工作。

当前实验驱动搭配 EZIF、`remote_reo=0` 时，启动后出现 REO 接收异常并触发恢复。没有完成双向文件传输，不能给出吞吐、SHA-256 验收或 A-MPDU 成功结论。

## 配置与证据

- 驱动 SHA256：`d62ccde636055b5bca6afbf7cb59f0ba688ef68444193766f4d3e5f8c62532fb`，沿用已通过 567 符号审计的实验模块。
- 固件 SHA256：`37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f`，Samsung 官方 FUS 提取件，来源见 [获取记录](../../experiments/tx-ampdu-investigation/firmware-acquisition/ACQUISITION.md)。
- 沿用 BA 验证配置：remote_assoc=1、AST/hash override=1、remote_reo=0、fw_addba=0、tx_ampdu=1、dynamic_rate=0、sta_coexist=0、VHT80/2SS/MCS9/SGI。
- 独立入口：[run-ezif-firmware.py](../../experiments/three-findings-validation/run-ezif-firmware.py)。未替换原有 DZF2 文件，Mac 网络设置未修改。
- 有效启动回执：[retry 目录](../../experiments/three-findings-validation/runs/ezif-firmware-20261111-retry)。

## 实测

| 项目 | 结果 |
| --- | --- |
| 固件挂载内容 | `firmware-live.txt` 确认官方 EZIF SHA256 |
| 原始服务位 | ready=1、wire_506_valid=1、wire_506_passthru_ampdu=1 |
| 服务位图 | ext2_len_words=8、ext2_w7=0x24010000 |
| 旧主机枚举查询 | wmi_service_338_passthru_ampdu=0；尚未映射，不能替代原始位图 |
| 远端 peer | peer_id=23、ast_idx=2、create_status=0、assoc_status=0、tx_ready=1 |
| RX 队列设置 | reo_enabled=0、reo_setup=0 |
| 1 MiB 探针 | device state deadline、samples=[]、verified=false |
| 用户态接收 | rx_action=0、rx_data=0；未完成 BA 握手 |
| 16 MiB 探针 | 前项失败后没有执行 |

内核时间 68.099356 秒：`dp_rx_err_exception: rate threshold breached! reo_err_msdu_buf_rcved 13`。随后 68.099973 秒 `cds_trigger_recovery_handler` 报 reason code 25。停止时 `pt_release` 返回 -11，模块仍 pinned；需要重启恢复。完整证据为 retry 目录下 `failure-dmesg-full.txt`、`failure-state.txt` 和 `1/result.json`。

三星 EZIF 已核实的服务位消费者涉及 PASSTHRU 的 RX 路由。本轮 `remote_reo=0` 跳过 `cdp_peer_setup`，是后续需对齐的配置。现有 mode 2 同时设置默认路由和 BA 窗口，若使用需注明耦合变量，不能直接归因单一字段。不能仅靠关闭异常保护来掩盖问题。

## 首次部署失误与恢复

首次目录 `ezif-firmware-20261111`：新入口将固件副本设置为 root:root / 0600 / shell_data_file，未沿用基线 system:system / 0440 / firmware_file。加载器报 EACCES，固件未启动；卸载卡在 CNSS unregister，需要重启。这是测试入口错误，不是 EZIF 不兼容证据。

已修正固件副本属性并保留首次失败日志。两次重启后均已检查 `STOCK_RESTORED WIFI_CONNECTED NO_EXPERIMENT_INTERFACE_OR_RULES`。第二轮恢复证据为 retry 目录的 `stock-after.txt`、`reboot-recovery.txt`、`recovery.json`；测试暂存程序移入独立归档，未覆盖原厂文件。

进一步的官方二进制核查发现新增 `dp_rx_err_handle_passthru_msdu_buf`，涉及 REO 异常环中的 PASSTHRU MSDU buffer 处理；旧版源码没有对应处理。该差异与本轮错误形态吻合，仍需核对分流条件和缓冲区所有权，不能仅切换路由就宣称修复。
