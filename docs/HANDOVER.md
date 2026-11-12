# 交接入口

## 2026-11-12：目录整理

用户要求大致按分层 Monorepo 方向整理，**暂不增加任何版本锁定和 SHA**。本轮进行了独立构建验证，没有上机、部署、暂存或提交；既有校验逻辑和历史 SHA 记录保留。检查时发现 README 已有暂存内容，本轮保留该索引状态，没有覆盖或撤销用户暂存。

- 独立 App：`clients/owly-app/`。
- 当前驱动修改覆盖层：`driver/wlan/`。
- App root 运行脚本：`runtime/owly-app/`。
- 构建入口：`scripts/`；基础组件：`third_party/upstream/`。
- 实验工具与记录：`experiments/`；实机测试导航：`tests/device/`。
- 首次导入证据：`archive/import/`；旧 `review/` 与 `downloads/airdrop-driver-work` 及所有兼容链接已删除。

源码实体已移动，引用改用正式路径。独立构建流程见[构建指南](guides/building.md)和[BYOB 输入清单](guides/byob.md)。本轮实际编译验证另见[验证记录](guides/build-verification.md)。暂未统一守护进程、实现互传 Hook、整理有序驱动补丁集或改变安装产物选择。安装器仍使用当前实验驱动路径；不要将其视为重装手机现有版本。

下一步可继续收敛测试依赖和明确共享后端设计。没有用户进一步指示时，不新增锁定清单、不自动部署。用户现已授权公开仓库、提交和推送。

无线实现、实测数据、恢复限制与最后设备回执见[详细交接](airdrop/28-three-findings-handover.md)。设备状态属于历史回执，后续上机必须重新检查。[当前状态](STATUS.md)与[构建说明](guides/building.md)为常用入口。

## 隐私清理

已完成[隐私清理](PRIVACY.md)。原始日志和本地构建产物已移除，旧原始证据/哈希完整性描述不再适用于匿名化副本。既有构建成功记录是清理前的验证结果。再次实测需自行设置设备参数；不得恢复个人标识到可发布文件中。

## 构建平台解耦

正式入口为 `scripts/build-driver.sh`（Linux x86_64）、`scripts/build-userspace.sh` 与 `scripts/build-app.sh`（macOS/Linux）。入口不使用固定 SSH 主机或个人路径；旧主机配方移至 `archive/legacy-build/`。历史实验若依赖旧布局，必须显式设置 `HOUJI_LEGACY_ROOT`，不属于正式构建依赖。本轮通过 Python/shell 语法检查、生成器检查与已知个人标识扫描（含 Git 对象）；没有重新编译或上机。

## 首次公开发布审查

用户已授权创建公开 GitHub 仓库并提交推送。仓库保留组件原有许可，根目录说明许可边界；未新建版本锁或 SHA 清单。首次发布只包含源码、文档及匿名化汇总，不包含固件、APK、内核模块、签名密钥或原始设备日志。构建通过不等于新产物已完成手机验收。
