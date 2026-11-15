# 驱动（`driver/`）

本目录完整收敛了小米 14（`houji`）三个树外内核模块（`houji_wonder_bridge.ko`、`qca_cld3_kiwi_v2.ko`、`wonder.ko`）的覆盖层源码、桥接与 Wonder 源码、Kbuild 配置与符号表。

## 目录结构

| 路径 | 说明 |
|---|---|
| `wlan/` | 单一合并后的 MiCode `kiwi_v2` WLAN 完整修改覆盖层（共 136 个文件，含动态 8 对端槽位 `pt_remote_multi`、`ezif` 硬件 TX A-MPDU、REO `msdu_buf` RX、2SS VHT80 SGI 与 STA 共存支持） |
| `bridge/` | `houji_wonder_bridge.ko` 源码（在 `wonder.ko` 与 `qca_cld3_kiwi_v2.ko` 之间中转生命周期与 provider 注册） |
| `wonder/` | `wonder.ko` 源码（基于 Google `kernel/google-modules/wlan/wonder` 适配 `houji_wonder_bridge`） |
| `kernel-overlay/` | MiCode 内核树 `olddefconfig` 所需的 2 个最小 Kconfig 补齐文件 |
| `configs/` | 去重后的单一 `kernel.config`、`Kconfig.wonder`、`kernel.symvers`（GKI 导出符号）与 `vendor.symvers`（2,618 个原厂 `vendor_dlkm` 导出符号） |
| `include/` | 树外编译所需的 `dataipa/` 与 `external/` 头文件 |
| `sources.json` | MiCode 内核与 WLAN 原始归档来源、未修改基线文件 SHA-256 校验表及覆盖层完整性清单 |
| `LICENSE` / `LICENSES/` / `THIRD_PARTY_NOTICES.md` | 各子组件的许可证文本与上游来源边界声明 |

## 构建方式

按 [BYOB 输入说明](../docs/guides/byob.md) 准备好原始 MiCode kernel/WLAN 归档与 Android Clang 后执行：

```sh
bash scripts/build-driver.sh --check
bash scripts/build-driver.sh
```

编译产物自动执行 `llvm-strip --strip-debug` 并输出至 `dist/driver/`（如需保留完整 DWARF 调试节可传 `--keep-debug`）。详细说明见 [构建说明](../docs/guides/building.md)。
