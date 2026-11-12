# 2×2 MIMO + SGI80 + 安全 ADDBA（A-MPDU + A-MSDU）实验包

完整技术报告与数据分析见：[`docs/airdrop/26-2ss-sgi80-addba-probe.md`](../../../../../docs/airdrop/26-2ss-sgi80-addba-probe.md)。

## 核心结果（64 MiB × 3，双端 SHA-256 校验通过，Mac 端零修改）

| 租约 / 阶段 | 配置 | `Mac → 小米` 加权（单样本峰值） | `小米 → Mac` 加权（单样本峰值） |
| --- | --- | ---: | ---: |
| `device-2ss-txrx1/5` | `2SS + SGI80`，`--addba off` | **5.906 MB/s**（6.037 MB/s） | **5.814 MB/s**（6.085 MB/s） |
| `device-2ss-txrx1/6` | `2SS + SGI80`（TX MCS7），`--addba amsdu` | **13.680 MB/s**（16.159 MB/s） | 4.738 MB/s（6.255 MB/s；16 MiB **8.063 MB/s**） |
| `device-2ss-mcs9/2` | 对称 `2SS + MCS9 + SGI80`，`--addba amsdu` | **14.705 MB/s**（**18.483 MB/s**） | **5.897 MB/s**（6.369 MB/s；16 MiB **7.978 MB/s**） |

## 复现命令

```bash
cd <original-workspace>/downloads/airdrop-driver-work/remote-peer-probe/vht-rx-next/2ss-sgi-next

# 启动租约（自动校验 ABI 与 SHA-256）
python3 run-2ss-sgi.py start qca_cld3_kiwi_v2-2ss-rx-phy.ko <lease-dir> --profile vht80-2ss-mcs9-sgi

# 采样测试（1 / 16 / 64 MiB）
python3 run-2ss-sgi.py sample <lease-dir> --mode 2ss --mib 64 --addba amsdu

# 安全退出并校验恢复 STOCK_RESTORED
python3 run-2ss-sgi.py stop <lease-dir>
```
