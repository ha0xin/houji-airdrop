# Mosey native 初步逆向记录

## 样本与工具

- `downloads/oneui9-houji-analysis/extracted/mosey_server` SHA-256：`e7a4180beb22de0c08eafc0986bc924fe4e4a0c0551648c0dcf9a90f883ad10a`
- `downloads/oneui9-houji-analysis/extracted/libmosey_daemon_ffi.so` SHA-256：`d786d7f7cc2898229b3325bb369c33cbb5793a639b4ca0715ea9672dc66c9fd2`
- radare2 `6.2.4`；Ghidra `12.1.4`；headless 使用 OpenJDK `21.0.12.1`。
- Ghidra 实际映像基址为 `0x00100000`；语言 `AARCH64:LE:64:v8A`，compiler spec `default`。因此 Ghidra 地址比 ELF/r2 VA 多 `0x100000`。映射范围、权限及字符串查询结果见 `mosey-ghidra.log`。
- ELF 含 `.rela.dyn` 与 `.rela.plt`；节列表未见 `.relr.dyn`。Ghidra 自动分析日志出现大量指向未映射 `0x00c0....` 的 p-code 流错误；这是异常分析流的证据，具体原因未确认。

## 可确认的边界与控制流

- `mosey.rc` 将 `mosey_server` 声明为 `com.google.android.moseyservice.IMoseyService/default` AIDL 服务，运行身份为 system，权限含 `NET_ADMIN`/`NET_RAW`。
- JADX 的 `defpackage/buj.java` Binder proxy 将 transact 1/2/3 映射为 start/stop/update；`defpackage/ro.java` 优先查 Android iface，再 fallback Pixel iface。`mosey_server` 与 FFI so 是服务端/native 边界；Java 层是控制面。
- FFI so 导出符号地址（r2 VA）：`mosey_get_hw_cap` `0x14cc1c`（452 B）、`mosey_start_5` `0x14cf64`（1888 B）、`mosey_stop` `0x14d6c4`（720 B）、`mosey_update` `0x14d994`（1628 B）。这些地址与长度来自已有 `mosey-exports-imports.txt`，并用 `r2 pdf` 查看了导出函数反汇编。
- 本轮查看 `mosey_start_5` 反汇编时观察到：`0x14cf64` 函数入口先调用 `0x14e140`，继而调用 `0x150ba8`；随后按输入字段分支/校验，并出现对 `0x13af10`、`0x13aeb4`、`0x1210d0` 等内部地址的调用。**本轮没有保存该函数的反汇编命令输出或指令片段**，因此这些callee地址仅作未附工件的观察，不作为已留档控制流证据；不能据此断言具体异步或队列语义。
- FFI 导出列表及动态加载符号线索见 `mosey-exports-imports.txt`。server 内含 `DlOpen`/`DlSym`、`libmosey_daemon_ffi.so` 与 API 名称等字符串；字符串仅佐证组件接口线索。

## 队列、调度与数据面结论

- FFI `.rodata` 中三个目标串的 ELF/r2 地址为：`Setting schedule:` `0x0af96`，`Failed to queue flushed frame:` `0x0ba2c`，`Failed to queue data frame:` `0x133ff`。Ghidra 在实际映像地址分别找到 `0x10af96`、`0x10ba2c`、`0x1133ff`。
- `r2 aaa/aaaa + axt` 没有报告这三个字符串首地址的直接引用。Ghidra 脚本对每条文本只用 `findBytes` 取**首次命中**，随后仅查询该命中字符串字节区间的直接引用；这次有限扫描没有报告直接引用。它没有扫描重复命中、字符串前缀/合并常量、Rust fat-pointer 描述符，也没有沿 descriptor 指针追到代码，因此**不构成完整 xref 排除**。对 `.rela.dyn` addend 的局部检查只见邻近 `0x0ba6c`（相邻路径字符串）的指针项；没有确认目标报错串的 descriptor/间接引用链。首轮 Python post-script 未运行（该 Ghidra headless 未启用 PyGhidra）；第二轮 Java 脚本成功运行并记录在 `mosey-ghidra.log`。
- 当前不能确认发送队列容量、满队列策略、是否阻塞/背压、同流排序、窗口等待、时隙调度控制流或聚合实现。上述日志字符串不能单独证明这些行为；Ghidra 的异常 p-code 输出也未用于作行为判断。
- 若后续继续，最小下一步是只追踪 `Failed to queue data frame:` 一个目标：枚举该字节串的所有命中，定位包含它的 Rust格式/日志描述符（包括相邻前缀与 fat-pointer），再沿描述符引用到单一调用点；若无法建立该链即停止，不据字符串推断队列行为。

## 有界发送调用簇追踪（2026-11-10）

- 方法：不用全库分析；从已知导入 PLT 地址 `send@plt=0x26bc70` 出发，仅扫描 `.text`（ELF VA/file offset `0xecf70..0x26a318`）中的 AArch64 `BL imm26`，再对目标调用点和一层局部调用关系用 Apple LLVM objdump 反汇编。可复核脚本为 `mosey_send_callscan.py`，调用簇原始输出为 `mosey-send-cluster.txt`。脚本扫描结果：`send@plt` 有两个直接调用点 `0x1659f8`、`0x1e72fc`；本轮只分析后者。
- `0x1e72fc` 周边的反汇编显示参数为 fd `[x19+0x10]`、buffer（沿用 x1）、length `sxtw(w2)`、flags `0`。`send` 返回非 `-1`（包括短写）时，沿分支直接返回并原样保留返回值；返回 `-1` 时读取 errno、调用日志 helper，随后返回 `-1`。本片段没有短写循环。errno 未与 `EAGAIN` 比较，因此这里没有可见的 EAGAIN 专项等待或重试；这不能排除上层另有重试。缺少短写循环本身不构成缺陷证据。
- 数据面关联：一层相邻代码中，`0x1e6f78` 调用候选 socket setup helper `0x1e71a4`，随后在 `0x1e6f88` 将回调地址 `0x1e72ac` 写入对象偏移 `0x238`。setup helper 构造 sockaddr，写入 `sll_family=0x11`（AF_PACKET），调用 `bind`，再以 `SOL_SOCKET=1`/`SO_ERROR=4` 做 `getsockopt` 检查。可据此将这个调用簇判断为 packet socket 的发送回调路径（中等置信度）；真实回调调度点未在本轮追踪。objdump 对这些地址显示的 `<mosey_update+巨大偏移>` 只是最近已知符号标注，不能据此认定调用点属于该导出函数；本报告只引用 ELF 地址和实际指令，不把符号标注当函数语义证据。
- `0x1e72ac` 没有在 `.text` 中找到直接 `BL` 调用者；能确认的是 `0x1e6f88` 将它作为函数指针存入对象。没有继续追该对象的分发路径。因此，失败后是否被重新入队、队列满时所有权如何处理、是否限速/等待，仍未知。该发送簇也未与 `Failed to queue data frame:` 的 Rust 格式描述符建立引用关系，不能把两者视为同一条路径。
- 工具/样本：Apple LLVM `objdump` 21.0.0；`mosey_send_callscan.py` 只扫描上述 `.text` 范围，直接重算 BL 目标；输入 SHA 与本报告开头相同。原始片段、命令、地址映射记录见 `mosey-send-cluster.txt`。

## 留存工件与限制

- `mosey-ghidra.log`：Ghidra 映像信息、完整分析输出、错误/警告及定向字符串引用扫描结果。
- `MoseyRefscan.java`：按内存搜索三条日志串、输出 memory map 和直接引用的 Ghidra 脚本。
- 临时 Ghidra 项目保留在 `/tmp/mosey-ghidra-task/MoseyFfi.gpr` 与同目录 `.rep/`。未修改二进制、手机或驱动配置。
