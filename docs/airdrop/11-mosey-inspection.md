# Mosey：JADX与native边界核查

2026-11-10，应用户要求由一个低成本GPT-6 Luna子代理只读检查，主代理复核关键Binder/rc位置；复用已有jadx产物，未操作手机。

## 直接证据

- `downloads/oneui9-houji-analysis/extracted/mosey.rc:1`：独立`/system_ext/bin/mosey_server`注册`com.google.android.moseyservice.IMoseyService/default`，system/inet，NET_ADMIN/NET_RAW，disabled/oneshot。
- `downloads/oneui9-houji-analysis/mosey-jadx/sources/defpackage/ro.java:229`：优先连接Android Mosey接口，失败再找Pixel旧接口。
- 同目录`buj.java:40`：Binder事务1/2/3分别为start/stop/update；`bmc.java`、`bme.java`为两种接口封装。不要把Pixel版本参数结构套给Android接口。
- APK里未定位到直接加载`libmosey_daemon_ffi`的调用；`LivePhotoPuller`中的JNI属于另一项功能，不能当作AWDL核心。
- 提取的`mosey_server`、`libmosey_daemon_ffi.so`为AArch64 native ELF。字符串包含Rust源码路径、`mosey_start_5`及stop/update/reset/dump/get_hw_cap等接口线索；核心不在DEX，jadx不能直接给出其实现。

## 可以与不可以得出的结论

APK负责会话控制等上层工作，通过Binder调用独立服务。native样本存在调度、信道及入队失败相关字符串，表明这些子模块存在；但目前没有逐行代码证据确认其队列容量、满队列策略、是否保持同一流顺序、背压如何反馈或硬件聚合如何配置。

因此没有证据断言Mosey也存在XOS的32项单播队列轮转丢包，也没有证据断言Mosey一定更快。仍需native反汇编或相同无线配置下的受控A/B测试。三者对照见[12-engine-comparison.md](12-engine-comparison.md)。

## Native定向分析入口

主代理已用r2复核FFI动态符号，原样本SHA256为`d786d7f7cc2898229b3325bb369c33cbb5793a639b4ca0715ea9672dc66c9fd2`。

| 导出函数 | ELF虚拟地址 | 符号大小 |
|---|---|---:|
| mosey_start_5 | 0x14cf64 | 1888 |
| mosey_update | 0x14d994 | 1628 |
| mosey_stop | 0x14d6c4 | 720 |
| mosey_get_hw_cap | 0x14cc1c | 452 |
| mosey_reset | 0x14cde0 | 388 |

这些是文件中的地址，不是ASLR后的运行地址。导入表包含socket收发、ioctl、epoll、eventfd；没有动态导入pcap符号，但这不足以排除静态代码或证明具体数据路径。记录位于`downloads/airdrop-driver-work/implementation-comparison/mosey-exports-imports.txt`。
