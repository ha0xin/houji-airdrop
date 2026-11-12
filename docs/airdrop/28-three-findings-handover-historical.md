> 历史存档：以下要求和状态仅代表旧会话，不是当前执行指令。请使用[最新交接](28-three-findings-handover.md)。

# 三项技术发现实机验证进度与交接文档（2026-11-11）

> 本文为接手时的历史计划，后续实测及修正见 [续测报告](28-three-findings-device-validation.md)。wire 506 实测未置位；默认 REO、模式 2、固件 ADDBA、动态速率与并发均已尝试；64 MiB×3 双向校验通过，但 TX 聚合和联网并发未通过。下文“精确根因”“原因确认”等表述不应当作已隔离变量验证的结论。

本文档记录 [27-firmware-ba-passthru-verification.md](27-firmware-ba-passthru-verification.md) 三项技术发现实机验证的已完成成果、三星公开 C 源码与独立对照报告的四项关键勘误、已采集实机数据，以及下一会话直接接手执行的精确步骤。

---

## 一、核心目标与硬性红线（接手必读）

1. **最终交付目标**：完成三项技术发现（① 远端 Peer 关联闭环消除 TX 卡死与干净卸载；② 固件 `wire 506` / TX A-MPDU 与远端 Peer RX REO 实测边界；③ `wlan0` 普通 Wi-Fi STA 与 `PASSTHRU` 不断网并发共存）的实机闭环验证，向用户输出完整**实测数据表**，并沉淀正式验证报告与第 27 篇勘误。
2. **绝对红线**：
   - **严禁反汇编或读取任何反汇编文本**：用户已明确指示“别看汇编了”。禁止运行 `objdump` / `llvm-objdump`，禁止读取任何 `disasm/*.txt` 文件；所有源码核查仅使用 `.c` / `.h` 源码与已完成的中文报告 [report.zh-CN.md](../../downloads/stock-wlan-comparison-20261111-113210/report.zh-CN.md)。
   - **直接修改文件并由 Git 管理**：直接使用 `edit_file` 修改仓库中的 `.c` / `.h` / `.sh` / `.py` / `.patch` 文件，禁止通过 Python 字符串拼接生成 C 补丁。
   - **Mac 对端绝对只读**：严禁修改 macOS 网络、`awdl0`、防火墙或任何系统设置。
   - **手机安全与恢复闭环**：每次在 `build-host` 编译 `qca_cld3_kiwi_v2.ko` 必须通过 567 符号 CRC / vermagic / plt-got 严格审计并执行 `llvm-strip --strip-debug`；每轮实机测试结束后必须执行 `stop` 并确认 `STOCK_RESTORED`（原厂 Wi-Fi 正常联网，无残留模块/挂载/lease）。
   - **四份样本来源严格区分**（严禁混用或交叉归因）：
     1. **小米 `houji` 原厂模块**：`OS3.0.307.0.WNCCNXM`，磁盘 SHA-256 `d3fbe4f1b62f8c3533609945b617508e8a585660f5640aeeda08f8508416137b`（约 8 万符号，无 `passthru` / `wondertap` 具名路径）。
     2. **三星 `EZIF` 模块**：本地 One UI 9 移植包提取件，vermagic `6.1.162-android14-11-34343818-abS928BXXU6EZIF`，SHA-256 `d6f2678f86c0c9bbcd1dd49fb25792b153995f802fd52ce211e3b38eea7968ae`。
     3. **三星公开 C 源码树**：`downloads/SM-S928B_16_Opensource.zip` 解压至 `downloads/samsung-s928b-16-src/`（2026-06-09，`e3q_eur_openx` / `pineapple`，`WONDER_VERSION_1_4_1`，`WMI_MAX_EXT2_SERVICE = 485`）。
     4. **实验运行固件 `amss20.bin`**：源自 `S928BXXS6DZF2`（`WLAN.HMT.2.0.c3-00424.1`），SHA-256 `13f01cb46efbe498c854352bfc253fa3665c14a14c37a0a6ed6a609a48a8709c`。

---

## 二、当前环境与仓库快照（2026-11-11 11:48）

- **手机（小米 14 / `houji`，ADB `DEVICE_SERIAL`）**：**100% 处于 `STOCK_RESTORED` 原厂状态**（`WIFI_CONNECTED`，已加载原厂 `qca_cld3_kiwi_v2`，无 `wonder` / `houji_wonder_bridge`，无实验挂载）。
- **Mac 对端**：`awdl0`（MAC `02:00:00:00:00:01`，IPv6 `2001:db8::31%awdl0`），零改动。
- **远程编译机 `build-host`**（`ssh -o ConnectTimeout=5 build-host`）：
  - 基线内核 WLAN 树：`/work/houji-build/src/wlan`
  - 实验构建同步目录：`/work/houji-build/src/three-findings/{wlan,userspace}`
- **本地核心路径**：
  - 实验工程总目录：[downloads/airdrop-driver-work/three-findings-validation/](../../experiments/three-findings-validation)
    - 内核改动源码副本：`src/wlan/`（注：其中 `qca-wifi-host-cmn/dp/wifi3.0/dp_rings_main.c` 与 `dp_rx_tid.c` 已从 `build-host` 拷入干净未修改基线，可直接编辑）
    - 用户态 `houji-owly`：`houji-owly-three-findings.c`
    - 远程构建与审计脚本：[build-driver.sh](../../experiments/three-findings-validation/build-driver.sh)
    - 手机端 Harness 补丁：[device-three-findings-harness.patch](../../experiments/three-findings-validation/device-three-findings-harness.patch)
    - Mac 侧编排器：[run-three-findings.py](../../experiments/three-findings-validation/run-three-findings.py)
    - 已采集实机回执：`runs/finding1-assoc-peer/`、`runs/finding2-tx-ampdu/`
  - 三星公开 C 源码树：`downloads/samsung-s928b-16-src/vendor/qcom/opensource/wlan/`
  - 6.1 sol 子代理独立核查报告：[downloads/stock-wlan-comparison-20261111-113210/report.zh-CN.md](../../downloads/stock-wlan-comparison-20261111-113210/report.zh-CN.md)（保持该目录只读不改动）

---

## 三、四项关键勘误与 C 源码新发现（须同步至代码与第 27 篇）

结合三星公开 C 源码树与 [report.zh-CN.md](../../downloads/stock-wlan-comparison-20261111-113210/report.zh-CN.md)，此前第 27 篇存在以下必须修正的结论：

1. **WMI 服务编号勘误（主机枚举 `338` vs 固件 Wire ID `506`）**
   - `EZIF` 中的十进制 `338` 是驱动内部 `wmi_handle->services[]` 主机枚举下标，实际映射到**固件 Wire `WMI_SERVICE` 编号 `506`（`0x1fa`）**。
   - 公开头文件 `fw-api/fw/wmi_services.h:591` 中 Wire ID `338` 是 `WMI_SERVICE_TDLS_6GHZ_SUPPORT`。此前实验直接调用 `wmi_service_enabled(handle, 338)` 返回 `0` 仅查询了未映射的主机下标，**不能证明 `DZF2` 固件不支持 `506`**。
   - **精确运行时验证方法**：在 `wma_dev_if.c` 中 `#include "wmi_unified_priv.h"`，直接读取 `wma->wmi_handle->soc->wmi_ext2_service_bitmap_len` 与 `wmi_ext2_service_bitmap[7] & (1U << 26)`（因 `WMI_MAX_EXT_SERVICE = 256`，`(506 - 256) / 32 = 7`，`(506 - 256) % 32 = 26`），绕开所有主机枚举定义限制直接观测 `DZF2` `amss20.bin` 原始能力位。

2. **4 字节 `wonder_txd` 控制头与 40 字节 HTT Metadata 勘误**
   - 三星公开源码 `wonder/wondertap_internal.h` 与 `dp_tx.c:8782–8848`（`dp_tx_set_metadata_passthru`）证实：`struct wonder_txd` 为 `{ bool is_unicast; u8 frame_type; u8 tid; u8 reserved[1]; }`，仅传递单播标记、帧类型与 TID（偏移 3 为 `reserved[1]`，从未被读取），**不存在逐包 A-MPDU 开关字段**。
   - `dp_tx_prepare_desc_single` 中的 40 字节 `htt_tx_msdu_desc_ext2_t` 分支是通用 exception metadata 路径，并非逐包 A-MPDU 开关。

3. **能力位在 DP 层的真实消费者是 Peer RX REO 分流（以及为何 `remote_reo=1` 接收停在 `2.9–4.4 MB/s`）**
   - `EZIF` 中 `passthru_ampdu_support`（`soc+0x48e8`）已确认的消费者位于 **`dp_vdev_get_default_reo_hash`** 与 **`dp_peer_setup_wifi3`**：当 `vdev->opmode == wlan_op_mode_passthru` 且能力为真时，将 `hash_based = 0`、`reo_dest = 0`（`cdp_host_reo_dest_ring_unknown`）清零，影响 **Peer RX REO 分流**；目前未建立该字段直接控制 TX 聚合的因果链。
   - 对照三星公开 C 源码 `dp_rx_tid.c:1285–1292` 与 `dp_rings_main.c:2571–2592`，我们找到了已关联远端 Peer 开启默认 `cdp_peer_setup`（`remote_reo=1`）时 `Mac → 小米` 仅 `2.9–4.4 MB/s` 的两处精确原因：
     1. `dp_peer_rx_tid_setup()` 初始化 TID 0 时硬编码 `dp_rx_tid_setup_wifi3(peer, BIT(0), 1, 0)`（**`ba_window_size = 1`**！），常规 STA 依赖 `dp_addba_requestprocess_wifi3()` 扩窗至 64，但 `PASSTHRU` 模式下 Mac 发来的 `ADDBA Request` 直通用户态 `houji-owly`，硬件 REO 队列一直卡在 `ba_window_size = 1`。
     2. `dp_vdev_get_default_reo_hash()` 默认设 `hash_based = 1`（Toeplitz 五元组哈希对原始 802.11 帧无效），未按 `EZIF` 行为清零 `hash_based = 0, reo_dest = cdp_host_reo_dest_ring_unknown`。

4. **`wlan0` STA + `PASSTHRU` 并发的官方源码实锤**
   - 三星公开源码 `qcacld-3.0/core/hdd/src/wlan_hdd_wondertap.c:312–402, 720–725, 1007–1008` 与 `wlan_policy_mgr_get_set_utils.c:10219–10266` 完整展示了官方并发流程：保持 `wlan0` 不断连，在拉起 `wondertap` 前仅调用 `wlan_hdd_set_powersave(sta_link_info, false, 0)` 临时关闭 STA 省电，并在 DBS 硬件（WCN7850）上直接声明 `features->bits.sta_coexist = 1`。

---

## 四、已完成实机验证与实测数据汇总

### 1. 发现一实机验证（`runs/finding1-assoc-peer`）—— **已完整闭环通过**

- **回执目录**：[runs/finding1-assoc-peer/](../../experiments/three-findings-validation/runs/finding1-assoc-peer)（含 [start.json](../../experiments/three-findings-validation/runs/finding1-assoc-peer/start.json)、[sample-1m.json](../../experiments/three-findings-validation/runs/finding1-assoc-peer/sample-1m.json)、[sample-16m.json](../../experiments/three-findings-validation/runs/finding1-assoc-peer/sample-16m.json)、[stop.json](../../experiments/three-findings-validation/runs/finding1-assoc-peer/stop.json)）
- **配置**：`--profile vht80-2ss-mcs9-sgi --remote-assoc 1 --remote-reo 1 --tx-ampdu 0 --dynamic-rate 0 --sta-coexist 0`
- **实测结果**：
  - **远端 Peer 状态机**：`WMI_PEER_CREATE`（`create_status=0`）→ `HTT_T2H_MSG_TYPE_PEER_MAP_V2`（`peer_id=1, ast_idx=2, ast_hash=2`）→ `WMI_PEER_ASSOC_CMDID` → `WMI_PEER_ASSOC_CONF_EVENTID`（`assoc_status=0, tx_ready=1`）。
  - **1 MiB×2 双向 SHA-256 通过**：`Mac → 小米` **`1.587 MB/s`**（`0.661 s`），`小米 → Mac` **`2.325 MB/s`**（`0.451 s`）。
  - **16 MiB×2 双向 SHA-256 通过**：`Mac → 小米` **`4.464 MB/s`**（`3.758 s`），`小米 → Mac` **`2.248 MB/s`**（`7.464 s`）。
  - **TX 完成度（彻底推翻“固件互斥导致 TX 卡死”旧假设）**：16 MiB 阶段内核 `completed=+5673`、`ast_overrides=+5369`、`remote_peer_compl=+5673`、`ppdus=+5329`、`last_peer_id=1`，**0 卡死**（对比 [第 22 篇](22-passthru-rx-reo-probe.md) `115 入环 / 0 完成` 100% 卡死）。
  - **干净卸载恢复**：`delete_confirm=1 htt_unmap=1 dp_released=1 obj_released=1 dp_conflict=0 teardown_rc=0`，约 2 秒无重启恢复 `TF_STOCK_RESTORED`。

### 2. 发现二首轮实测（`runs/finding2-tx-ampdu`）—— **已获取基准对照与退化根因**

- **回执目录**：[runs/finding2-tx-ampdu/](../../experiments/three-findings-validation/runs/finding2-tx-ampdu)（含 [sample-16m-no-txaddba.json](../../experiments/three-findings-validation/runs/finding2-tx-ampdu/sample-16m-no-txaddba.json)、[sample-16m-txaddba64.json](../../experiments/three-findings-validation/runs/finding2-tx-ampdu/sample-16m-txaddba64.json)）
- **配置**：`--profile vht80-2ss-mcs9-sgi --remote-assoc 1 --remote-reo 1 --tx-ampdu 1 --dynamic-rate 0`
- **实测结果**：
  - **Phase 1（`--tx-addba off`，16 MiB×2，SHA-256 通过）**：`Mac → 小米` **`2.945 MB/s`**（`5.697 s`），`小米 → Mac` **`2.139 MB/s`**（`7.845 s`），`completed=+5578`，`agg_ppdus=0`。
  - **Phase 2（`--tx-addba win64`，16 MiB×2，SHA-256 通过）**：
    - Mac `awdl0` 成功接受小米发出的 `ADDBA Request`：`OWLY_TX_ADDBA_REQ_SENT token=1 params=0x1003 ssn=0` → **`OWLY_TX_ADDBA_RESP_RCVD token=2 status=0 params=0x1003 win=64`**。
    - 吞吐表现：`Mac → 小米` **`2.593 MB/s`**，`小米 → Mac` 降至 **`0.647 MB/s`**（`25.936 s`，`agg_ppdus=0`，`invalid=+881`，Mac `LQM-WiFi` 出现 `rxholes=9, rxstuck=4, rxQueued=84, txdelba=1`）。
    - **原因确认**：仅用户态发送了 `ADDBA Request`，而固件未建立对应 TX BA 会话且未发 `BAR`（BlockAckReq），当单包发送失败（`ts->status=2`）产生序号空洞时，Mac 侧 `win=64` 重排缓冲发生乱序等待超时。
  - **附带发现的小瑕疵**：`stop` 时 `rmmod wonder` 后立即写 `pt_release` 偶发撞上 `vdev 0` 异步 `PEER_UNMAP` 约 60 ms 窗口返回 `-EBUSY`（`result=-16`），自动 `recover` 已立即恢复 `STOCK_RESTORED`；下一步在 `device-three-findings-harness.patch` 中给 `pt_release` 加重试循环即可彻底消除。

---

## 五、下一会话接手执行清单（Step-by-Step）

### 第一步：直接修改本地源码并在 `build-host` 重建内核模块

直接用 `edit_file` 修改以下文件（均位于 `downloads/airdrop-driver-work/three-findings-validation/`）：

1. **[`src/wlan/qca-wifi-host-cmn/qdf/inc/qdf_pt_remote_peer.h`](../../driver/wlan/qca-wifi-host-cmn/qdf/inc/qdf_pt_remote_peer.h)**：
   - 新增声明 `u32 pt_remote_peer_reo_mode(void);`（`0` = 关闭 `cdp_peer_setup` 走原生 `rxdma2sw`；`1` = 默认 `cdp_peer_setup`；`2` = 对齐 `EZIF` 的 `hash_based=0, reo_dest=0` + `ba_win=64`）。
2. **[`src/wlan/qcacld-3.0/core/wma/inc/wma_api.h`](../../driver/wlan/qcacld-3.0/core/wma/inc/wma_api.h)**、**[`src/wlan/qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c`](../../driver/wlan/qcacld-3.0/core/hdd/src/wlan_hdd_passthru_control.c)**、**[`src/wlan/qcacld-3.0/core/wma/src/wma_dev_if.c`](../../driver/wlan/qcacld-3.0/core/wma/src/wma_dev_if.c)**：
   - 在 `wma_dev_if.c` 中 `#include "wmi_unified_priv.h"`，更新 `wma_pt_fw_caps_get()` 直接读取 `wma->wmi_handle->soc->wmi_ext2_service_bitmap_len` 与 `wmi_ext2_service_bitmap[7]`，输出：
     `ready=1 wire_506_passthru_ampdu=%u ext2_len_words=%u ext2_w7=0x%08x wmi_service_338_passthru_ampdu=%u peer_create_conf=%u sync_delete=%u max_peers=%u\n`
   - 将 `pt_remote_reo` 从 `bool` 改为 `uint`（`0|1|2`），并在 `wma_pt_remote_peer_setup()` 中记录到全局状态供 `pt_remote_peer_reo_mode()` 返回。
   - 新增模块参数 `pt_fw_addba`（`bool`）：当 `pt_fw_addba=1` 时，在 `WMI_PEER_ASSOC_CONF_EVENTID` 成功后调用 `wmi_unified_addba_send_cmd_send(wma->wmi_handle, remote_copy, &(struct addba_send_params){ .vdev_id = vdev_id, .tidno = 0, .buffersize = 64 })`，并将 `fw_addba_sent` / `fw_addba_rc` 输出至 `pt_remote_peer_stats`。
3. **[`src/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_rings_main.c`](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_rings_main.c)** 与 **[`src/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_rx_tid.c`](../../driver/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_rx_tid.c)**：
   - `dp_rings_main.c`（`dp_vdev_get_default_reo_hash`）：`#ifdef DRIVER_PASSTHRU_OWNER` 下若 `vdev->opmode == wlan_op_mode_passthru && pt_remote_peer_reo_mode() >= 2`，置 `*hash_based = 0; *reo_dest = cdp_host_reo_dest_ring_unknown; return;`。
   - `dp_rx_tid.c`（`dp_peer_rx_tid_setup`）：`#ifdef DRIVER_PASSTHRU_OWNER` 下若 `vdev->opmode == wlan_op_mode_passthru && pt_remote_peer_reo_mode() >= 2`，对 TID 0 调用 `dp_rx_tid_setup_wifi3(peer, BIT(0), 64, 0)`（窗口设为 `64` 而非 `1`）。
4. **[`device-three-findings-harness.patch`](../../experiments/three-findings-validation/device-three-findings-harness.patch)** 与 **[`run-three-findings.py`](../../experiments/three-findings-validation/run-three-findings.py)**：
   - 在 harness 的 `stop_session` 中对 `pt_release` 增加最多 15 次（间隔 `0.2 s`）重试直到 `armed=0 releasing=0 released=1 result=0`。
   - 支持 `--remote-reo {0,1,2}` 与 `--fw-addba {0,1}`，同步更新 `run-three-findings.py` 中的 `HARNESS_TF_SHA`，并移除 `tx_rate_delta` 中不存在的 `"submitted"` 键。
5. 运行 `./downloads/airdrop-driver-work/three-findings-validation/build-driver.sh`，确认输出 `KERNEL_BUILD_AND_AUDIT_PASS`（567 符号 CRC 全量一致）。

### 第二步：执行剩余实机验证并采集实测数据

1. **发现三实机验证（`runs/finding3-sta-coexist`）—— `wlan0` Wi-Fi STA + `PASSTHRU` 不断网并发**：
   - 保持小米 14 `wlan0` 正常连接路由器 Wi-Fi，运行 `--sta-coexist 1 --remote-assoc 1 --remote-reo 0 --profile vht80-2ss-mcs9-sgi`。
   - 在传输 `16 MiB`×2 双向 SHA-256 载荷期间，后台持续运行 `ping -I wlan0 -i 0.2 <gateway>`，完整采集：
     - `fw-caps.txt` 中 `DZF2` `amss20.bin` 的 **`wire_506_passthru_ampdu` 真实读数**（`ext2_len_words`、`ext2_w7`）；
     - `wlan0` 全程关联状态、IP 地址、Ping 发包/收包/丢包率与 RTT；
     - `wonder0` / `owly0` 双向 SHA-256 吞吐与 `stop` 后 `wlan0` 零闪断恢复 `TF_STOCK_RESTORED`。
2. **发现二补完矩阵（`runs/finding2-reo-and-fw-addba` + `64 MiB`×3）**：
   - 测试 `--remote-assoc 1 --remote-reo 2`（对齐 `EZIF` 的 `hash=0, reo_dest=0, ba_win=64`）对比 `--remote-reo 0`（原生 `rxdma2sw`）与 `--remote-reo 1`（`4.464 MB/s`）的 `Mac → 小米` 接收吞吐。
   - 测试 `--tx-ampdu 1 --fw-addba 1`（`WMI_ADDBA_SEND_CMDID`）配合 `--dynamic-rate 0/1`，观测 `agg_ppdus`、`max_ppdu_mpdus` 与 Mac `LQM-WiFi` `rxAmpdu` / `txBACK`，给出 `DZF2` 固件下 TX A-MPDU 是否触发的实测结论。
   - 在最优已关联远端 Peer 配置下执行 `64 MiB`×3 双向 SHA-256 验收，与 [第 26 篇](26-2ss-sgi80-addba-probe.md) `self-peer` 64 MiB×3 基线（`14.705 MB/s` / `6.590 MB/s`）做完整同条件对比。
   - 结束后执行 `stop` 并校验 `TF_STOCK_RESTORED`。

### 第三步：文档定稿、Git 提交与实测汇报

1. 再次运行恢复审计，确认小米 14 100% 处于 `STOCK_RESTORED`。
2. 为 [27-firmware-ba-passthru-verification.md](27-firmware-ba-passthru-verification.md) 增补顶部勘误说明（纳入上述四项源码/二进制对照修正）。
3. 将最终完整实机验证报告写入 `docs/airdrop/28-three-findings-device-validation.md`（或直接升级本文档），更新 [README.md](README.md)。
4. 检查 `.gitignore`（确保忽略 `downloads/stock-wlan-comparison-*/disasm/` 与 `inputs/` 中的大文件，不把几十 MB 二进制/反汇编纳入 Git），提交所有源码、脚本、实测 JSON 回执与文档，并向用户输出完整**实测数据汇总表**。
