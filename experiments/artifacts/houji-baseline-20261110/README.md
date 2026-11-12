# houji 离线重建基线 · 2026-11-10

状态：三个模块编译与参考件静态对照通过；未连接手机，未做设备验收，未加入提速改动。

`modules/` 是本次编译的去调试信息产物；`reference/` 是已使用的上游 open-runtime-v0.5.1-preview.1/runtime/modules 对照和回退件。去调试前后全部 SHF_ALLOC 节大小及内容哈希一致。完整 DWARF 产物保留在 build-host /work/houji-build/src/baseline。

导入符号计数：bridge 30、WLAN 567、Wonder 103。导入/导出 CRC、未解析符号列表、vermagic、依赖、module 节大小/重定位以及可执行节字节均与参考件一致；模块之间 provider CRC 亦一致。整文件哈希不相同，存在路径字符串、build-id 和调试信息等构建差异。CRC 由配方 symvers 提供，因此 CRC 一致不单独证明类型和运行时兼容。

模块 vermagic 前缀为参考件实际的 6.1.68，手机内核为 6.1.138-android14-11-g0c3d559bcd85-ab14529422。没有修改手机、固件或系统分区。

构建记录保留了两处已有 WLAN 栈帧超限告警（2496 / 3344 字节），以及无匹配 vmlinux 导致的 BTF 跳过信息。未伪造 vmlinux/BTF，也未为消除告警修改基线代码。源码为归档而无 .git，因此版本查询会打印非仓库提示，但 make 和最终对照成功。

下一步按 scripts/DEVICE-TEST.md 完成基线装载、发现、双向短文件校验、Wi-Fi 恢复和重复启停；通过后再测试性能改动。这个包没有自动安装入口。
