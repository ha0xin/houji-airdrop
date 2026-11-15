# 驱动重建与下一步

> 后续实机状态见[2026-11-10验证记录](09-20261110-device-validation.md)。本文中的“未连接手机”是对应离线阶段的记录。

## 来源纠正及当前状态

此前没有在本项目任一机器编译过 houji 内核驱动。设备使用上游 [Xiaomi14-PixelOS-Airdrop](https://github.com/YoWenhow/Xiaomi14-PixelOS-Airdrop) 的预编译 `open-runtime-v0.5.1-preview.1.zip`，本地位于 `downloads/oneui9-houji-analysis/reference-runtime/`。

2026-11-09 已把 `/tmp/work` 复制到项目内[持久化源码配方目录](../../driver)，避免临时目录清理。这里包含重建配方、覆盖层及bridge/wonder源码，**不是已经准备好的完整Linux内核工作树**。

2026-11-10 已在 `ssh build-host` 部署并验证构建环境，独立磁盘目录 `/work/houji-build`（约4.9GiB），构建临时目录为其下的 `tmp/`。主机为 Debian 13、x86_64、6CPU、23GiB内存。Android Clang 17.0.2（r487747c，build 10087095）、LLD 17.0.2、pahole 1.30 及构建依赖已就绪。AArch64 编译/链接、实际 BTF 生成、内核 `olddefconfig` 和 `modules_prepare` 均通过；**三个模块的基线编译及下述静态对照已完成，手机装载验收未进行**。备用pku未使用。

使用 `source /work/houji-build/host/env.sh` 进入工具链环境。本地[部署脚本与说明](../../archive/legacy-build/README.md)和[验证记录](../../archive/legacy-build/receipts)已保存。内核归档SHA256匹配，WLAN覆盖层校验 `WLAN_SOURCE_VERIFIED=5739`；远端 `src/wlan` 当前仍是 framework 版本。

2026-11-10 离线重建时进一步核实：锁定公开内核树和上游 release 的三个模块均为 **6.1.68**；手机运行内核为 **6.1.138**。上游重建说明所列目标 release 不等于预编译模块内的实际字符串。本轮最终基线保持参考模块的 `6.1.68`，不修改二进制 vermagic。公开树 `kernel/module/version.c:80` 的 `same_magic()` 在存在 CRC 时忽略 release 前缀，但仍比较其余标志；这解释了两种版本号可以并存，不能替代 ABI 和上机验收。

## 固定输入

| 输入 | 版本/位置 |
|---|---|
| 内核 | MiCode/Xiaomi_Kernel_OpenSource commit 8c66ad18fc6132fcdcbdf0cf67832f47b33010a4 |
| 内核压缩包SHA256 | ca44be412747a4da786276957c5791ba8db094a536b4d2a0ee7dfeedb841bfc3 |
| WLAN | MiCode/vendor_qcom_opensource_wlan commit a4da9e3b43864da13bc978b8cb6385ba5332a4af |
| 编译器 | Android Clang17 clang-r487747c |
| BTF工具 | pahole1.30 |
| 手机运行内核 | 6.1.138-android14-11-g0c3d559bcd85-ab14529422 |
| 参考三个模块的实际 vermagic 前缀 | 6.1.68（本轮从 release ELF 直接读取，基线重建保持一致） |

持久化目录中的关键资料：

- `docs/KERNEL.md`：重建边界与前置条件。
- `kernel/build-commands.json`：三个模块的精确make参数，包含含空格的单参数，不宜随意拼接shell命令。
- `kernel/kernel-source.json`、`kernel/wlan-source.json`：输入和文件哈希。
- `tools/prepare_kernel_sources.py`：合入覆盖层并校验。
- `kernel/configs/`：wlan/bridge/wonder配置、kernel.symvers、wlan-extra.symvers、wonder-providers.symvers、Kconfig.wonder。
- `kernel/wlan-overlay/`、`kernel/bridge/`、`kernel/wonder/`及两个include目录。

## 重建顺序与验收门槛

1. 在隔离目录获取固定输入并校验，记录工具链与源码版本。
2. 按脚本准备源码。当前独占运行时涉及pre-framework版本，须按KERNEL.md核对 `legacy-to-framework.patch` 的反向应用条件，不能混用framework版本。
3. 分离三个输出目录，使用对应config；先bridge，再按provider符号关系构建WLAN和wonder。wonder需要WONDER_SOURCE与KBUILD_KCONFIG。
4. 先复现未修改基线。核对UTS_RELEASE、模块versioned imports及CRC、provider导出和struct module布局；仅vermagic相同不够，modules_prepare也不提供所有正确符号CRC。
5. 保存构建日志、输入和产物SHA256、ABI对比结果后才进入设备装载验证。
6. 基线通过后添加受owner生命周期约束的完成计数、在途量和失败观测。保持generation/retire隔离，不恢复可能访问已释放OSIF/vdev的普通回调。
7. 有数据后再决定在途帧限制、窗口末尾停止提交/排空等措施，并同时验证吞吐、双向传输、退出恢复与反复启停。

STA/AWDL并发、5GHz、聚合和速率自适应均属后续独立能力；当前没有承诺仅改一个参数即可获得Apple设备的吞吐和联网体验。

## 2026-11-10 离线基线重建结果

构建机 `/work/houji-build/src/baseline` 保存独立工作树；WLAN 由已校验 framework 树复制后，以 `patch --fuzz=0 -R` 反向应用 legacy-to-framework.patch，匹配当前独占运行时。上游配方快照未修改。三个输出分别在 `out/baseline-{bridge,wlan,wonder}`。

| 模块 | 导入符号数 | 与参考模块静态对照 |
|---|---:|---|
| houji_wonder_bridge.ko | 30 | 通过 |
| qca_cld3_kiwi_v2.ko | 567 | 通过 |
| wonder.ko | 103 | 通过 |

对照项包含：AArch64、完整vermagic、依赖、导入符号集合及CRC、导出CRC、未定义符号集合、`.gnu.linkonce.this_module` 大小及重定位、所有可执行节的字节；并检查本次模块之间的provider CRC。可执行节逐字节一致是比单看符号表更强的证据，但不替代实机验证；符号CRC本身使用配方的symvers。参考件缺少可供完整结构对照的DWARF，因此没有声称全部内核数据类型布局已验证。

参考件取 release 压缩包的 `runtime/modules`，不是 `quickshare-runtime`。本次没有性能逻辑变更。整文件哈希因路径字符串、build-id、调试信息等构建差异而不同。

WLAN链接有两处栈帧告警（2496/3344字节）；桥接和Wonder因没有匹配vmlinux跳过模块BTF。保留真实日志，没有生成伪BTF或修改代码以隐藏告警。

[重建与审计脚本](../../archive/legacy-build/README.md)、[上机验收步骤](../../archive/legacy-build/DEVICE-TEST.md)。产物目录为 `downloads/airdrop-driver-work/artifacts/houji-baseline-20261110/`，含重建模块、上游回退件、配置、日志、静态对照和SHA256清单。没有ADB操作，没有自动安装入口。

## 2026-11-10 TX 观测版已离线准备

[源码与指标说明](../../experiments/tx-observer/README.md)。远端独立目录 `/work/houji-build/src/observer/wlan`、`out/observer-wlan`，基线源码和模块保持原样。

观测版在 raw 单帧提交前登记不解引用的描述符地址，在有效 TQM 状态路径记录完成，在release前回收登记。不访问完成时的vdev/OSIF，不增加旧owner回调；使用固定大小表、锁保护快照，模块加载时可选开启，默认关闭。统计区分单播数据、组播数据和管理/控制帧，提供在途量、完成延迟分桶、HAL状态与重试次数。FW回收不冒充有效TQM状态。它是测量工具，不是提速补丁。

离线结果：观测版WLAN编译成功；567个导入符号未新增/移除，既有导入CRC和全部导出CRC、依赖、vermagic、module节大小/重定位对照通过。可执行代码按预期变化。ASan/UBSan核心测试通过（提前完成、release、复用、容量冲突、重复登记、分桶和10万次随机事件）；报告工具验证了增量计算、重载检测及截断拒绝。没有验证真实硬件并发、观测开销或吞吐改善。

测试包：`downloads/airdrop-driver-work/artifacts/houji-tx-observer-20261110/`，含模块、回退基线、源码、采样工具和报告。下一步需要手机：先原参数验收重建基线，再对照观测关闭/开启，最后依据数据决定队列调节或149信道冷启动实验。当前没有ADB操作。
