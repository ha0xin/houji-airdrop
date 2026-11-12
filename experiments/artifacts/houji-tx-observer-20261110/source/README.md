# PASSTHRU TX 只读观测版

本版用于区分队列滞留、发送状态及重试；不做限流、不改速率/信道/聚合，不改变提交结果和回收行为。默认 `pt_tx_observe=0`；仅加载模块时可启用 `pt_tx_observe=1`。没有运行中 reset/enable 接口，统计持续到卸载。

## 生命周期与边界

- 只改 `dp_tx.c` 并加入两个私有头文件，不改公共结构体、内核API或用户态接口。
- 在 raw 单帧路径调用 `tx_hw_enqueue` 前登记。完成可能早于发送函数返回，因此成功后不再解引用描述符。
- 仅在 PASSTHRU 的 `dp_tx_comp_process_tx_status` 中读已解析 TQM 状态。FW 回收分支传给普通处理函数的状态未必有效，不能从那里读取重试次数。
- 所有描述符 release 在复用前清理登记；未收到 TQM 状态的释放计入 `release_without_status`。这是失败、取消、FW回收等的合并项，不等于无线丢包。
- 登记表仅保存不解引用的描述符地址、时间、帧长度和类别，不保存 skb/vdev/peer 指针或 MAC/文件内容。模块生命周期内静态存储，用 irqsave 自旋锁串行化；不调 OSIF 完成回调、不唤醒旧 owner、不改变 generation/retire 规则。
- 1024组×4槽固定容量，无运行中分配。冲突计入 overflow；重复 key 和未匹配完成单独计数。任何一个异常计数增长时，在途量只可当下界，不能据此调限流。
- 默认关闭时只增加条件判断；启用后有时钟/锁/统计开销，需要在实机对比关闭/开启的吞吐，不能假设零开销。

## 指标

`/sys/module/qca_cld3_kiwi_v2/parameters/pt_tx_stats` 只读，单次读取为锁保护的一致快照；没有逐帧 printk。

- `calls/accepted/rejected`：PASSTHRU提交入口及返回结果，accepted不是ACK。
- `admitted/active/highwater`：成功登记、尚未收回及模块生命周期峰值；恒等式为 `admitted = active + completed + release_without_status`。
- `completed`：已记录的 TQM 状态完成。状态0..15及其他值分别计数，不把状态2直接解释为单播重试失败。
- 类别0：单播数据；1：组播/广播数据；2：管理/控制帧（包括单播和组播）。
- `tries`：HAL `transmit_cnt` 的原值累加。
- `delay_ns`：提交给 `tx_hw_enqueue` 前至状态处理之间的单调时钟间隔，包含排队、空口重试及完成上报；不是纯空口时延。
- `delay_bins`：小于1/4/16/64/256/1024/4096毫秒，最后一桶为大于等于4096毫秒；不能从桶推断精确P95。
- `epoch_ns`：每次加载后的首次观测时间，报告工具拒绝合并跨模块重载样本。
- 长时间在途记录必须结合停止/释放与模块退出结果解释；没有超时自动删账，以免掩盖未完成记录。

## 离线检查

`test-ledger.c` 直接运行实际使用的核心逻辑，覆盖完成早于返回、释放不重复扣减、描述符复用、容量冲突、重复登记、分桶边界和10万次乱序事件，验证计数守恒。使用 ASan/UBSan。内核并发与实际硬件回收仍需上机验证。

远端 `python3 /work/houji-build/observer/build-observer.py` 在独立 observer 源码/输出目录构建；保持 baseline 产物不变。参数名称和符号/布局差异必须在ELF对照中检查；可执行代码预期变化，不要求与基线相同。

## 手机连接后的顺序

先按 build-host/DEVICE-TEST.md 验收重建基线；再使用同样的AMSS、channel 6、MCS3加载观测版，先关闭再开启统计对照。参数只能加在独立测试版 `insmod` 命令中，不能覆盖原有常用运行脚本。

开启后先空闲采样，再单向固定大小文件、反向文件、结束会话、三次启停。观察未完成量、单播完成延迟与重试，必要时同步抓取AWDL窗口/链路包。停止会话前后分别读快照；卸载会清零统计。

```sh
python3 collect-observer.py --serial DEVICE_SERIAL --seconds 90 --output capture.jsonl
python3 summarize-observer.py capture.jsonl
```

采集脚本只读取指定手机的统计，不启动互传、不切换Wi-Fi、不安装模块。文件速率应从应用日志/文件大小测量，不能把本工具的raw frame完成速率当作文件吞吐。
