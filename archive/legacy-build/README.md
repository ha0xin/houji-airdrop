# build-host 构建环境

远程独立目录：`/work/houji-build`，位于根分区磁盘。通过 `ssh build-host` 使用。

```sh
source /work/houji-build/host/env.sh
/work/houji-build/host/verify-environment.sh
/work/houji-build/host/prepare-smoke.sh
```

- `toolchains/clang-r487747c`：Google Android 官方 Linux x86_64 工具链。
- `upstream`：本地持久化配方副本，不在此目录改性能逻辑。
- `src/kernel`、`src/wlan`：固定基线解压并由上游脚本应用、校验覆盖层。WLAN 此处是 framework 版本；独占运行时需另外复制后反向应用 legacy-to-framework.patch。
- `out/prepare-smoke`：只验证内核构建准备，不是手机可装载模块的输出目录。
- `downloads`：来源压缩包、SHA256 和 Clang Gitiles 元信息。
- `logs`：安装版本、源码校验和构建验证记录。
- `tmp`：构建临时目录，避免使用主机 `/tmp` tmpfs。

环境脚本不更改全局 shell 配置。公开内核树 Makefile 是 6.1.68，而运行内核为 6.1.138；后续重建必须处理并验证这项边界，不能只改 vermagic 作为兼容证明。

## 离线重建与打包

```sh
python3 /work/houji-build/host/build-baseline.py bridge wlan wonder
python3 /work/houji-build/host/audit-modules.py
python3 /work/houji-build/host/package-baseline.py
```

最终基线按参考件实际 vermagic 保持 6.1.68。详细报告在 `artifacts/baseline/module-comparison.json`；包在 `artifacts/houji-baseline-20261110.tar.gz`。三个模块全部编译通过，导入CRC、导出CRC和可执行节等对照通过。脚本仅构建与打包，不连接手机。[上机步骤](DEVICE-TEST.md)另行执行。
