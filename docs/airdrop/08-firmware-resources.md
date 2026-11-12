# WLAN 固件公开资料与本地样本

检索日期：2026-11-09。本次仅做资料和文件静态检查，没有替换手机固件。

## 精确样本

据补充的拆包与校验记录，该候选来自三星 Galaxy S24 Ultra（SM-S928B / e3q）S928BXXS6DZF2 的 vendor/firmware/kiwi/amss20.bin，对应 samsung-sm8650/proprietary_vendor_samsung_e3q 提交 f749124168e9、blob 2f0e9d10728e；来源详情见[架构文档](01-architecture.md)。

当前已验收候选的本地副本为 `downloads/oneui9-houji-analysis/reference-runtime/dzf2-blob-amss20.bin`：

- 大小6426688字节。
- SHA256：13f01cb46efbe498c854352bfc253fa3665c14a14c37a0a6ed6a609a48a8709c。
- IMAGE_VARIANT_STRING：7850.wlanfw.eval_v2_TOQ。
- QC_IMAGE_VERSION_STRING：WLAN.HMT.2.0.c3-00424.1-QCAHMTSWPL_V1.0_V2.0_SILICONZ-1.95874.17.98251.417。
- ELF e_machine=40（EM_ARM）。这是容器头信息，尚未逐段确认代码指令集或内部处理器组成。

另一个 `modem.img.amss20.bin` 是不同样本，SHA256为37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f，版本为WLAN.HMT.2.0.c3-01013-QCAHMTSWPL_V1.0_V2.0_SILICONZ-1.149480.41.168922.74。不能将它的版本信息归给已验收候选，更不能只因版本编号较大就替换。

7850字符串与公开WCN7850/HMT资料吻合；确切芯片硬件revision仍应结合设备ID和启动日志确认，不能仅凭文件名判定。

## 可用公开资料

| 资料 | 用途 | 边界 |
|---|---|---|
| [Linux Wireless ath12k说明](https://wireless.docs.kernel.org/en/latest/en/users/drivers/ath12k.html) | 查支持硬件、驱动和固件入口 | 我们使用Android qcacld，不是直接运行ath12k |
| [linux-firmware WCN7850更新](https://kernel.googlesource.com/pub/scm/linux/kernel/git/firmware/linux-firmware/+/35abebaa15c1ff1a141b7c24d79220ef70f48548) | 官方维护链上的二进制版本对照，示例HMT1.1.c5 | 不包含固件完整源码；与我们HMT2.0.c3不可假定兼容 |
| [WCN7850性能讨论](https://lists.infradead.org/pipermail/ath12k/2024-February/001572.html) | SM8650上Android amss20.bin与主线方案性能差异、Qualcomm工程师的日志建议 | 普通AP吞吐讨论，不能直接解释AWDL实验低速 |
| [Linux ath12k WMI定义](https://github.com/torvalds/linux/blob/master/drivers/net/wireless/ath/ath12k/wmi.h) | 了解命令、事件、参数的公开接口 | 具体命令编号、TLV及能力应以当前Android固定基线为准 |
| [MiCode WLAN固定基线](https://github.com/MiCode/vendor_qcom_opensource_wlan/tree/a4da9e3b43864da13bc978b8cb6385ba5332a4af) | 当前最相关的WMI、HTT、HAL与fw-api参考 | 这些是主机端接口，不是固件执行代码 |
| [QCAMON](https://github.com/DanielAW/qcamon) | 公开的Qualcomm Wi-Fi二进制补丁框架，示例IPQ4019 | 面向Xtensa，未证明支持WCN7850；不能直接用于当前ARM标记样本 |
| [QDig](https://github.com/NerdTIV/QDig) | Qualcomm分段镜像重组、Ghidra导入及残留调试信息分析 | 列举的WLAN测试目标是其他型号；完整ELF未必需要它，当前样本未试用 |
| [Qualcomm WiFi Infinity War研究](https://i.blackhat.com/USA21/Wednesday-Handouts/us-21-Qualcomm-WiFi-Infinity-War.pdf) | WLAN驱动/固件逆向研究的方法背景 | 不当作当前WCN7850的操作手册或适配补丁 |

## 结论及用途

找到公开二进制、主机端协议源码和通用逆向研究；未找到当前三星DZF2/HMT2.0.c3固件的完整源代码、可重建工程，或已验证的WCN7850 AWDL提速补丁。

可优先从当前基线fw-api/WMI和本地固件字符串对照开始，核查重试、聚合、PASSTHRU相关接口。若需要深入逆向，先分析ELF段、加载地址、实际指令集和签名/认证链；不能因为文件能反汇编就认定修改后可启动，也不能没有检查就断言必然有无法越过的签名限制。

公开ath12k固件适合作为版本与接口对照，不直接替换现有候选。来源机型与构建号已由后续拆包记录补全为SM-S928B / S928BXXS6DZF2；不要与Mosey用户态的S928BXXU6EZIF来源混淆。
