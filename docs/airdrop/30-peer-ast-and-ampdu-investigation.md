# 远端 AST 隔离与 TX A-MPDU 启用链调查（2026-11-11）

本轮用户明确允许读取汇编、三星及小米源码，覆盖此前交接文档的禁止反汇编约束。两个代理分别在独立目录调查；手机试验由主代理串行执行。Mac 网络设置未改动。

## 远端发送路径：最小隔离试验

新增 `pt_remote_ast_override` 只读模块参数，默认 true，保持原有行为。false 时只跳过 PASSTHRU 单播的远端 AST 查找/覆写，保留远端 peer 的创建、关联及删除。其余为 REO=0、fw_addba=0、tx_ampdu=0、dynamic_rate=0、vht80-2ss-mcs9-sgi。采用 off→on→off 顺序，双向各16 MiB。

候选模块 SHA-256 `315c4f4c5894523995c55816dd54aa5f3c945815a3b51c5d1f8760763fe72fed`，在 build-host 构建，567导入及CRC、vermagic、PLT/GOT、去调试信息与参数权限审计通过。harness 校验 Y/N 读回，默认参数为开启，不把实验结果擅自设为长期配置。

| AST覆写 | Mac→小米 MB/s | 小米→Mac MB/s | 完成数 | 远端完成 | AST覆写数 | 聚合观测 |
|---|---:|---:|---:|---:|---:|---:|
| off1 | 4.001 | 8.219 | 5414 | 181 | 0 | 0 |
| on | 6.252 | 2.281 | 5500 | 5500 | 5219 | 0 |
| off2 | 9.599 | 5.747 | 5255 | 130 | 0 | 0 |

六次单向SHA校验全部通过。关闭覆写、重新开启、再次关闭时，发送吞吐随之恢复、下降、恢复；远端关联均保持成功。这把额外降速定位到AST选择所改变的发送路径，尚不能进一步断言是索引错误、cache配置、远端队列调度还是固件策略。

完成计数覆盖双向阶段。`last_peer_id` 只代表最后一条完成；关闭覆写后仍有少量 remote_peer_compl，不能称为全部走self。现有计数没有区分帧类型，不能把剩余远端完成直接认定为管理帧或TCP ACK。

静态依据：BE PASSTHRU bank 已启用 index lookup，当前实验覆写 search_index/cache；三星公开直接发送路径与已检查EZIF直接路径使用vdev BSS AST。后续追踪发现EZIF上层 dp_start_xmit_passthru 也按目的MAC查询并在skb标记中传递索引，再由enqueue条件覆盖；因此不能声称原厂没有远端索引选择，也不能直接认定实验覆写多余。索引与hash语义仍在核对。独立证据报告：[远端路径调查](../../experiments/peer-tx-investigation/report.zh-CN.md)。

## A-MPDU：找到缺口，尚未实现启用

1. 当前 `pt_tx_ampdu` 同时改变AMPDU上限与软件重试策略，存在混杂；上限64不等于聚合已生效。
2. 当前内核ADDBA命令在AUTH、owly发现就绪之前发出；WMI提交成功不是对端协商成功。
3. 用户态匹配token/status后只设置内部tx_ba_active，没有把成功协商的TID/window/policy/SSN安装到固件。
4. 公开头文件的 `WMI_PEER_TID_ADDBA_CMDID` 和 `WMI_TX_ADDBA_COMPLETE_EVENTID` 是候选状态安装契约，但尚未找到目标源码中的C调用实现或验证其在本固件PASSTHRU中可用，未猜TLV上机。
5. 独立补丁新增ADDBA响应的请求存在性、token/status、TID、窗口、立即BA等校验和接受日志；尚不承担硬件BA配置职责，也未混入AST实测。

独立报告：[TX A-MPDU调查](../../experiments/tx-ampdu-investigation/report.zh-CN.md)。下一步需要先验证协商回执，再验证固件状态安装、SSN与重试策略；不能通过伪造wire506或仅调大聚合上限宣称开启AMPDU。

## 本轮收尾与下一步

EZIF 上层查询已进一步定位为 `dp_get_info_by_peer_mac`。已观察到其通过额外peer字段门控，传递索引低8位，enqueue覆盖索引时仍保留vdev/BSS hash低4位；实验路径则在tx_ready/ast_valid下传递完整remote索引及remote hash。门控字段和索引字段的准确结构成员名仍未确认。下一步最小变量是分离index与hash来源，不能直接把偏移当作稳定ABI或断言已找到错误字段。

ADDBA响应验证补丁只存在于独立目录，包含端到端测试；原生driver配置73项通过、no-default-features配置67项通过、git apply --check通过。未Android交叉编译，未应用到本轮用户态、未上机，也没有启用固件BA会话。

三轮试验结束均成功恢复，最终审计为 `STOCK_RESTORED WIFI_CONNECTED NO_EXPERIMENT_INTERFACE_OR_RULES`。本轮不将AST关闭作为默认修复，保留原默认值，等待索引/hash语义及更多负载验证。源码与脚本、审计和JSON回执纳入Git；大二进制及反汇编留在独立目录，不提交。
