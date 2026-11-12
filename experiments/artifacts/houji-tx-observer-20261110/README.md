# houji TX 观测测试包 · 2026-11-10

仅离线编译/静态检查通过，未连接手机，未证明性能改善。

modules/ 是重建桥接、Wonder及被动观测WLAN；rollback-baseline/ 是未修改逻辑的重建基线。手机原有精确模块备份与原厂恢复流程仍需保留。源文件、补丁、只读采样与分析工具在 source/，详细语义见 source/README.md。

必须先验收基线，然后单独加载本版，并比较 pt_tx_observe=0 与 pt_tx_observe=1；默认关闭，不能在线切换。不修改常用运行脚本、不自动安装、不永久替换系统驱动。启用后的开销与实际回收行为尚待测量。

既有公共符号CRC、依赖、vermagic、模块大小/关键重定位匹配基线；可执行代码按预期变化。完整报告见 reports/module-comparison.json。读取统计并不提供新的ACK反馈，也不改变发包节奏、信道或固定速率。
