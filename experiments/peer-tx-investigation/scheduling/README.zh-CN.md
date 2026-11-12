# 用户态合包等待的运行时对照

## 交付

`runtime-coalesce.patch` 相对 `three-findings-validation/` 根应用。独立副本位于本目录 `userspace/`。没有改共享源码，没有操作手机。

新增 `Config::amsdu_coalesce_us`，默认1000μs，替代已有固定1000μs；Android runner读取运行时 `OWLY_COALESCE_US`，允许0..4000，非法值在打开接口之前报错。hold、pure ACK临近slot判断、下一timer计算使用同一配置。0只取消等待，并不禁用已有队列数据的A-MSDU封装。默认配置的行为不变。

旧 `OWLY_COALESCE_US` 输出读取编译期 `HOUJI_OWLY_COALESCE_US`，与node内固定值存在分离。本补丁打印实际生效配置，便于审计。

## 依据与公开实现查阅

先网上检索并查看[原项目 driver.rs](https://github.com/t4t5/owly/blob/main/crates/owly-awdl/src/driver.rs)和[原项目 node.rs](https://raw.githubusercontent.com/t4t5/owly/main/crates/owly-awdl/src/node.rs)。所查看版本仍是逐输入驱动、有限burst，并未发现可直接照搬的批量syscall改进；这是本次查看范围，不代表所有分支或PR均不存在。GitHub PR关键词页本次取回失败，不将其解释成零结果。

[Linux recvmmsg文档](https://man7.org/linux/man-pages/man2/recvmmsg.2.html)确认单调用可以收多个消息，非阻塞模式立即返回已有批次。但它需要新增buffer生命周期及调度公平性处理；当前证据不足以优先承担这部分改动。

本地 `runs/ezif-rx-adapter-20261111-on64/1/runner.log`：timer lateness均值1041..1111μs，峰值5921μs。`OWLY_RX_GAPS` matched57740、equal56049、host_delayed0、equal_host_delayed0。大量相同无线时间戳可能对应聚合/描述符时间粒度，不能作为host syscall拥塞证明；>128ms无线gap也不能被批量recv必然消除。因此先将既有软件hold做单变量实验，尤其考察ACK等待对反向/duplex TCP的影响。

计时迟到接近1ms意味着250μs timer未必能在250μs准时唤醒；它仍可能因其他RX/TUN输入触发服务而提前发送。收益只能用实测确定。更短hold可能降低每MPDU的MSDU数量并损失空口效率；0是诊断值，不是默认推荐优化。

## 已验证

- `git apply --check` 成功。
- 独立副本 `cargo test -p owly-awdl --features driver --test node`：26/26通过，覆盖新增0/250/1000/4000等待时长及既有顺序、聚合满立即发送、slot截止重查、ACK替代/SACK保留等行为。
- 独立runner `cargo check --target aarch64-linux-android` 成功；这是目标类型检查，不是Android链接产物或实机验证。正式二进制仍由根代理远端构建。

## 建议实测

同一二进制、driver、EZIF固件与已验证radio配置，依次1000→250→1000。每组64MiB×3双向及同载荷duplex，保留SHA、方向总字节/总时间、timer late、A-MSDU MSDU/MPDU、TQM retry/status/聚合分布与RX gap。只有250稳定改善且对端方向不回退，才考虑默认值；0可另做诊断，2000仅在更大合包有明显收益时再探。

参数必须传入手机启动runner的shell：`OWLY_COALESCE_US=250 .../houji-owly ...`，仅在Mac父进程设环境不足。harness必须读取runner实际 `OWLY_COALESCE_US=250` 标记再接受该组。三次串行方向结果不替代duplex，因为driver的biased select将RX放在TUN前面，持续接收可能影响双向公平；本补丁未改变这个独立变量。

## 独立公平调度补丁

`fair-select.patch` 只修改driver.rs，不包含coalesce或BA变动。去掉固定顺序 `biased;`；循环顶部检查stop channel已改变/关闭即停止，同时保留select内changed分支，让闲置时的停止也能唤醒。已到达循环边界的停止不会排在持续ready的RX后面。一个已经在执行的同步handler仍须执行完毕，无法保证硬实时停止。

[Tokio select原始文档](https://docs.rs/tokio/latest/tokio/macro.select.html#fairness)说明默认随机选择首先检查的分支，持续ready时提供一定公平性；biased则按源码顺序检查，公平性由调用者负责。本改动避免air永久位于netif、timer及peer publication前面，但随机检查不是严格轮转，也不保证某分支最多等待固定轮数。

验证：39项lib及5项driver测试通过，Android目标cargo check通过，应用检查通过。新增持续ready的模拟Air与Netif验证主机发送可以进展且stop可返回；临时恢复biased作为负对照，该测试因TX饥饿失败（`biased-negative-test.log`），随后已恢复公平版本。此证据证明结构上可发生饥饿，不证明实机每次duplex慢均由它导致。

根代理实机基线RX14.449、TX7.530 MB/s，按共同墙钟aggregate14.900，6/6SHA通过。建议固定coalesce值与同一内核/固件，旧版→fair→旧版duplex和单向各64MiB×3；记录共同墙钟总吞吐、各方向速率/结束时间、timer lateness、RX kernel drops、TX backpressure、SHA与恢复状态。公平性改动可能损失一点接收优先吞吐或引入随机选择开销，需共同指标验收。
