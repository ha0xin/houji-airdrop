# 2026-11-10 实机基线、TX 观测与 149 信道

## 已完成

同一小米14，内核仍为6.1.138-android14-11-g0c3d559bcd85-ab14529422。现用实验模块逐字节备份与上游runtime参考件匹配。原厂驱动build-id、AMSS SHA256、Wi-Fi联网均已核验。

重建基线三个模块成功加载，wonder0/xos0创建，实机build-id匹配本次ELF；三次成功进入/恢复。一次快速连续启停在进入阶段被既有检查拦住并自动恢复；当次没有shell trace，不能断言具体是哪条检查。稍后带trace重试成功，未强制卸载或跳过检查。Mac双向2MiB SHA256相同。

观测版分别在关闭和开启统计时完成加载、Mac双向2MiB哈希校验、退出恢复。当前测试未发现新增内核Oops、Unknown symbol或版本CRC错误；不能用这句话替代长期稳定性验收。

## 测速结果

同为channel6或149、HT20/NSS1/MCS3/GI-default，普通Wi-Fi在实验会话期间关闭。

| 组合 | 数据量（每方向） | Mac→小米 MB/s | 小米→Mac MB/s |
|---|---:|---:|---:|
| 重建基线 / channel6 / Java探针 | 2MiB | 0.598 | 0.336 |
| 观测版关闭 / channel6 / 原生探针 | 2MiB | 0.286 | 0.379 |
| 观测版开启 / channel6 / 原生探针 | 2MiB | 0.330 | 0.334 |
| 观测版开启 / channel149 / 原生探针 | 2MiB | 0.791 | 1.039 |
| 观测版开启 / channel149 / 原生探针 | 8MiB | 1.001 | 1.072 |

每项都双向校验SHA256。2MiB为 `91d3beb88a9b2f778a6c44a1c53b63d3c79931845a9aef84b3fb414610bd1938`；8MiB为 `7d212b9c884f5c77896de960ae17cc341cda43b14d6a971f34ca29ebd4badf7f`。这是原始TCP，不是iPhone原生AirDrop的验收结果；样本少且时段有变化，不能精确量化观测开销或保证固定倍率提升。Java与Go原生探针的套接字默认参数也可能不同，优先比较同一原生探针的结果。

## 149 真正的兼容问题

直接 `xos_runner 149`：libxos下发5745MHz、bandwidth=2（80MHz），固定速率原始参数为 `(2,2,2,2,3)`；此前runner只匹配信道6对应的 `(1,0,2,2,11)`，因此149的固定速率未被降级。wondertap初始化报-95，随后自动回退stock。

独立runner `xos_runner_ht20` 对Google OUI/subcmd1只将5745MHz的width2改为0，并对结构验证通过的subcmd3设置 `(1,0,0,1,3)`。两项一起改后冷启动成功，`pt_activate` 明确报告 `freq=5745 phase=3 error=0`，XOS返回0，随后双向传输成功。

结论：当前硬件/候选固件/适配驱动组合能在149信道以HT20运行。不能再称“只能请求6”或“固件不支持149”。本轮同时改变带宽和速率，未分别证明两者各自是必要条件；也没有验证80MHz、VHT/NSS2等更高配置。

测试runner保留旧NL错误兼容包装，其返回值本身不是成功证据；以实际接口、驱动频率及双向哈希传输作为验收。

## 队列观测

channel6窗口25.13秒：3566条登记/完成，末尾active=0，无overflow/duplicate/unmatched；峰值145。单播数据2953条，平均提交至状态73.76ms，242条处于256–1024ms桶，平均transmit_cnt=1.796。状态0有2815条、状态2有138条；后者是HAL REM_CMD_TX，不直接等同于空口丢包。

channel149的2MiB测试登记/完成4055条，异常计数0，峰值674；单播平均提交至状态279.16ms，平均transmit_cnt=1.171。更高吞吐伴随更深在途队列，说明不能简单把“队列越短”当作提速目标。

149的8MiB复测发生365次表容量/组冲突溢出，对应365次unmatched；已登记11288条全部完成，末尾active=0。该轮峰值1271和延迟统计只涵盖被跟踪子集，**不能当作精确全量队列结论**。吞吐和文件SHA256不受观测表漏记影响。后续若扩大观测容量，须独立编译、检查和验证。

## 测速探针就绪问题

新建xos0后地址可能仍在IPv6 DAD tentative阶段，原ready判定仅匹配fe80过早。Java探针BindException(EADDRNOTAVAIL)由AndroidRuntime终止，shell只打印Killed；在stock状态重跑的NullPointerException则因为xos0不存在。不是已确认的系统主动杀后台或驱动崩溃。

探针已改为等待非tentative、非dadfailed地址。临时原生互传脚本也采用相同就绪判定；原有常用脚本未覆盖。

## 当前待用户验收的临时原生149路径

手机独立目录 `/data/local/tmp/houji-buildtest-20261110/`：

- `radio-native149.sh`：沿用原生会话生命周期，使用本次重建baseline模块和独立HT20 runner，固定149；D仍是原native状态目录。
- `xos-bridge149.zip`：RADIO指向上面的独立脚本；日志/状态effectiveChannel同时改为149；UID限制、holder和death处理保持原样。
- `bridge-supervisor149.sh`：使用同一排他锁运行测试bridge，退出时调用测试radio恢复。
- 原 `/data/local/tmp/xos/native/radio.sh`、xos-bridge.zip和现用参考模块均未覆盖，没有bind覆盖或开机持久化。

已启动临时supervisor，canonical Binder注册成功。等待用户用小米系统相册向iPhone发送10–30MB视频，之后还需iPhone反向接收与原生退出Wi-Fi恢复验证。启动临时服务时手机为stock驱动/固件且Wi-Fi已联网；服务空闲本身不切换无线。

回退顺序：先让互传结束并退出页面；核对supervisor.pid对应测试supervisor后发TERM，等待其退出并确认测试radio的恢复结果，然后启动原 `/data/local/tmp/xos/native/bridge-supervisor.sh`。若仍有saved.env或实验模块，必须先完成现有恢复检查，不能直接切回原脚本遗漏 `xos_runner_ht20`。禁止强制rmmod。

所有源码、精确脚本、哈希探针、生命周期日志和测量JSON均在[本次实机记录](../../experiments/device-tests/20261110)。手机原模块备份在该目录的backup/及手机测试目录backup/。

## 原生 iPhone 发送追加结果

230,240,470字节大文件由用户因速度慢主动取消，监测最后看到TCP累计确认约50.18MB；不是完整成功样本，不能用Mac测速预测所有大文件体验。

随后任务60707（38,149,367字节）成功：12:07:06.173开始Upload，12:07:41.255完成发送body，12:07:43.975收到HTTP200，12:07:44.005记录TASK_SUCCESS/CALLBACK_SUCCESS及超级岛成功通知。Upload至任务成功37.832秒，约1.008MB/s。dvzip记录compressedChunks=0、storedChunks=292，未在这一步进行deflate压缩。

此为149/HT20原生小米→iPhone成功发送证据；iPhone端正常播放仍待用户确认。文件完成后分享会话仍活跃，XOS_READY、Wi-Fi关闭；需退出分享会话再验收自动恢复，不能把文件完成等同于会话释放。日志与结果在本次记录native-small/，未中途切换参数或重启服务。

用户随后确认iPhone视频正常播放。12:08:53只读核验：会话client-stop已完成，状态STOCK_RESTORED，saved.env不存在，Wonder/bridge已卸载，原厂AMSS和WLAN build-id哈希匹配，Wi-Fi已自动重连5GHz（5180MHz）。因此本次38.1MB原生发送、播放、退出恢复闭环通过。临时149 supervisor仍待命，未做开机持久化；149原生iPhone→小米接收和更大文件稳定性仍未完成验收。

## 原生 iPhone→小米接收追加结果

12:12:06.254开始Upload，12:12:23.945结束body读取（17,693ms），12:12:24.019文件提交并返回HTTP200，12:12:24.127记录TASK_SUCCESS(send=false)。解包保存1个视频，实际大小38,149,367字节，SHA256为 `01472c89996f7485da5f6a3c8268389e212a6024affc4e8bf5f21a59d08ba2a2`，媒体库已扫描收录。用户确认完成传输，尚未单独确认小米端播放。按文件字节/接收body耗时约2.16MB/s；HTTP载荷为37,744,394字节，不能与文件大小混用。

接收结束退出后，STOCK_RESTORED、原厂固件/驱动hash和Wi-Fi重连再次核验通过。接收服务按BLE发现触发XOS，空闲接收页可能暂时仍为stock状态；不能只因未立即拉起xos0判断服务失败。正式接收页是MiShareAirDropSettingActivity，避免打开内部调试AirDropReceiveActivity再切页造成启停竞态。

随后进入独立Mac链路的MCS3/5/7速率对照测试，测试过程中原生服务暂停；测试脚本finally要求stock恢复后重启已验证的149/MCS3原生服务。高档位尚不代表已通过iPhone原生验收。

## HT20 速率对照与 MCS7 待验收状态

使用 baseline 模块、149 信道、同一原生 TCP 探针，每方向 8MiB，均通过 SHA256 校验；单位为十进制 MB/s。

| 速率 / 轮次 | Mac→小米 | 小米→Mac |
|---|---:|---:|
| MCS3 / 1 | 0.987 | 1.070 |
| MCS5 / 2 | 0.619 | 0.841 |
| MCS7 / 1 | 1.063 | 1.924 |
| MCS3 / 2 | 0.722 | 0.613 |
| MCS7 / 复测 | 1.068 | 2.222 |

MCS5 没有改善；MCS7 两轮发送更快，但 MCS3 本身也有波动，不能解释为严格固定倍率，更不能代替 iPhone 原生验证。每轮退出均恢复 stock。结果文件为 rate3-run1.json、rate5-run2.json、rate7-run1.json、rate3-run2.json、rate7-run1-repeat.json。

连续切换时，曾在实验模块加载前发现 Wi-Fi HAL 被 framework 重新启动，原 stopped 状态校验拒绝继续。独立速率脚本增加有上限的停止重试，保留 holder/refcount 检查和恢复路径；没有强制卸载，没有修改原常用 radio.sh。

当前临时启用 `bridge-supervisor149-rate7.sh`、`xos-bridge149-rate7.zip`、`radio-native149-rate7.sh`，runner 为 `xos_rate7`，参数 HT20/NSS1/MCS7/GI-default。Binder 已注册，启动时 STOCK_RESTORED 且 Wi-Fi 开启。等待同一约38.1MB视频的小米→iPhone原生对照；尚未证明 MCS7 原生发送成功或退出恢复。保留 MCS3 版本作为回退。

回退 MCS7 必须先结束会话，使用 rate7 测试 supervisor/radio 完成恢复后，再启动 `bridge-supervisor149.sh`；原脚本不认识 xos_rate7，不能替代正在运行的恢复路径。所有变更均为临时服务，无开机持久化。
