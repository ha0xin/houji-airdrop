# houji Owly 149 信道实验

Android 小程序复用固定提交 `6f2e8ea99aed0687558d78a1dd49154ab3a5fa76` 的 `owly-awdl`。无线侧使用现有 Wonder 和 PASSTHRU 模块，用户态通过 AF_PACKET 与 `wonder0` 收发。IPv6 测试链路是小米 `owly0` TUN 与 Mac `awdl0`，MTU 固定为 1450。只接受当次 Mac `awdl0` 源 MAC 的空口帧，实际同步和吞吐依赖 Mac 的 AWDL 可用窗口。实机运行记录见 [149 信道验证](../../../../docs/airdrop/14-owly-houji-device.md)。

## 源码与构建

- `wonder-init.c` 复用 `upstream/native/wonder-probe/probe.c` 的 nl80211 协议和 ACK 校验。它只提供 `wonder0` 创建与 5745 MHz 配置缓存。Wonder 驱动在接口 UP 时建立 PASSTHRU，`radio-owly.sh` 再检查实际运行阶段及信道。
- `src/android.rs` 实现 `Air` 和 `Netif`。RX 剥离 radiotap 并按 FCS/长度校验帧。发送队列满时返还完整帧，其它发送错误计数。TUN 与 runner 文件描述符一起销毁。
- `owly-coalesce.patch` 在 IP backlog 内对大包最多等待 1 ms，然后在发送前重新检查可用窗口。每次合包遵循对端 HT A-MSDU 上限、radio frame 上限及同对端顺序。四条新增测试覆盖等待截止、满包提前发送、顺序与窗口关闭。
- `hashprobe.go` 使用合成内存数据。每个方向分别以编号、字节数和 SHA-256 关联两端收据。可选 `--mss 1160` 只应用到探针监听 socket，实际生效值会记录。
- `radio-owly.sh` 单独管理实验会话，复用原生 149/MCS3 supervisor 的互斥锁，保留已有模块和固件哈希、卸载引用及恢复检查。状态保存在 `/data/local/tmp/houji-buildtest-20261110/owly/state`。

构建在 `build-host`，源树位于 `/work/houji-build/owly-evaluation-src`，独立工具链位于 `/work/houji-build/toolchains/owly-userspace`。目录布局需保留 `owly-evaluation/owly`、`owly-evaluation/houji-runner` 和 `upstream/native/wonder-probe`。

```sh
cd /work/houji-build/owly-evaluation-src/owly-evaluation/houji-runner
userspace=/work/houji-build/toolchains/owly-userspace
export RUSTUP_HOME="$userspace/rustup"
export CARGO_HOME="$userspace/cargo"
export ANDROID_NDK_HOME="$userspace/android-ndk-r30"
export PATH="$CARGO_HOME/bin:$PATH"
sh build.sh
sh build-coalesce.sh
```

`build.sh` 产出 `dist/` 内的基线三个 ELF 及清单。合包构建解包固定提交到临时目录，应用补丁后测试并编译，产出 `dist/coalesce/`。原 Owly checkout 必须保持干净。Rust 目标为 `aarch64-linux-android`，链接 API 29，NDK r30 Linux x86_64。构建证据在 `results/build-receipt.txt`、`results/coalesce-build.log` 和 `results/coalesce-edges.log`。已保存每次测试使用的 ELF 和 SHA 清单。

## 实机操作

Mac 通过 ADB USB 操作固定序列号 `DEVICE_SERIAL`，测试走 `awdl0`，无 sudo。安装前确认手机 Wi-Fi 已连接并处于 stock，原生 149/MCS3 supervisor 在待命，无传输会话。将远端构建产物取回相同的本地 `dist/` 目录，然后从本目录运行。

```sh
python3 bench.py deploy
python3 bench.py measure trial-ht20 ht20 single ack --mib 8 --pairs 1

python3 bench.py deploy --variant coalesce
python3 bench.py measure trial-coalesce vht80 amsdu ack --mib 64 --pairs 2
```

每轮会保存 `results/<轮次>/results.json`、两端 SHA 收据、PHY 完成统计、TUN 与队列统计、状态生命周期和恢复核验。实验中断时，主控写入该轮独有的停止文件，设备脚本停止子进程并恢复。设备端实验循环另有 440 秒上限。发生 `RESTORE_FAILED` 时，先审查设备日志、引用和快照；保存状态且确认会话结束后，用同一脚本的 `recover` 在共用锁下重试。

本实验使用内存 TCP 数据和双向 SHA-256。速度单位为十进制 MB/s，各方向顺序测量。信道在全部实验中固定为 149，接收 Block Ack 保持关闭。

`--mss 1160` 需要带 MSS 支持的探针。历史产物可用 `deploy --variant mss`，从当前源码重建的 `dist/coalesce` 已包含该支持。末轮 `--queue-len 2048` 只影响临时 `owly0`，默认仍为 500。


## VHT A-MSDU 上限对照

在 `build-host` 运行 `sh build-vht-amsdu.sh` 生成 `dist/mss-vht`。以 `bench.py deploy --variant mss-vht` 部署。同一构建可用 `bench.py measure NAME vht80-sgi amsdu ack --mss 1160 --mib 16 --pairs 1` 作为关闭对照，加 `--vht-amsdu` 才尝试放宽。该候选在本机降低了小米→Mac 吞吐，默认关闭。 [实机记录](../../../../docs/airdrop/20-vht-amsdu-device.md)
