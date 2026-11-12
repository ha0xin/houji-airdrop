# XOS 单播队列诊断实验（2026-11-10）

用户要求仅自动测试小米↔Mac，不再要求iPhone/原生互传操作；稳定可靠文件吞吐 >10MB/s 之前不得以UDP或短样本宣称完成。

## 已定位问题

原库SHA `0a1b6401bcbde7d50f1e503b2f581940c6f40d4c3a701a1aa1a03ee04bbfffab`。
`.gnu_debugdata` 符号和代码证实：

- `circular_buf_init(32)`：0x15c78设置单播队列容量。
- `xos_send_unicast`：0x149bc传入3TU guard。
- 0x14a48队列非空时执行队尾轮转，0x14a5c调用`circular_buf_put2`。
- 队列已满时put2返回-1；0x14b08记录rotate-failed，0x14b20释放待发帧。
- `rotate-full-evidence.log`有16995条告警，限于抓取时日志缓冲，非完整统计。

`patch-fifo.py` 严格检查原文件SHA和原指令，只改变0x14a48为跳转0x14aec，保留当前包并沿原定时器等待。可选参数12额外把单播guard从3改12TU。未修改原库、JNI或正式互传目录。

**限制：跳过轮转可能使其他对端受到队头阻塞，仅用于单对端诊断。生产修复应做每对端FIFO和公平调度，或至少保证轮转满队列不丢当前包。**

## 文件与实机边界

- `libxos.so`：FIFO/guard3，SHA44b5d8b70e590de64d5c4177e9f25123756cab8cb4151e2dcd9a457cb1ff3a49。
- `guard12/libxos.so`：FIFO/guard12，SHAa800cbe05caadc048cfc8b925031138362d3057c427a66f7983e45e374d9328b。
- `runtime-maps.txt`：实机已确认加载隔离副本。
- 模块：`../wideband/ring/qca_cld3_kiwi_v2.ko`，SHA481e3474ea6b42acab5bb5a35b5be7b5e291aa975d7422034ca4e750e857d71c。
- 测试runner：`../device-tests/20261110/xos_vhas_flow`，SHAf11d4a3655cf5b1062b4855771bc7d1d9d09d45a68b2fb0d0a8391288ec0d21d。
- 临时radio脚本：`radio-fifo.sh` / `radio-fifo-guard12.sh`。
- 手机实验根：`/data/local/tmp/houji-buildtest-20261110`；正式库`/data/local/tmp/xos/libxos.so`不动。

结果总表和后续实验状态见 `../../../docs/airdrop/10-throughput-limits.md`。探针脚本与JSON在`../device-tests/20261110/`。

每次实验必须序列化，先确认无saved.env、stock模块且Wi-Fi连接，停止native149/MCS3 supervisor；脚本finally恢复stock和Wi-Fi并重启已验收的native149/MCS3待命服务。不得同时运行多个radio脚本。失败样本可能无JSON（hash-probe只有双向完成才写JSON），须看server.log及rate-lifecycle.log，不能当成成功。

下一步优先解释剩余TCP重传和反向单MPDU性能；不要未经硬件peer/BA建立就伪造ADDBA成功响应。FQ首轮发送50秒超时，第二轮未运行，不推广此配置。
