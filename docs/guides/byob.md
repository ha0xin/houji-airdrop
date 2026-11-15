# BYOB：自行提供外部输入

BYOB（Bring Your Own Blobs）表示仓库不附带完整闭源系统包和固件。构建另需自行准备公开源码归档与工具链。**编译不需要从手机拉取文件，也不需要原 android 工作区或旧远端实验输出。**

本轮不新增版本锁定或 SHA 清单，沿用上游原有来源说明及校验。

## 编译输入

| 构建目标 | 自备输入 | 配置 |
|---|---|---|
| 驱动三个模块 | Linux x86_64；Python 3.12+；make、flex、bison、perl、pahole、patch、libelf/OpenSSL 开发包；Android Clang | `HOUJI_CLANG_DIR` 指向包含 `bin/clang` 的目录；原配方使用 clang-r487747c |
| 驱动源码 | MiCode kernel 与 WLAN 原始 tar.gz，每个包内有一个顶层目录 | `HOUJI_KERNEL_ARCHIVE`、`HOUJI_WLAN_ARCHIVE`；来源见下文 |
| Rust App 后端 | Rust/Cargo、`aarch64-linux-android` target、Android NDK | `ANDROID_NDK_HOME`；原验证环境 NDK r30。Cargo 首次构建需要获取依赖 |
| Android APK | JDK 21、Android SDK API 35、build-tools（aapt2/d8/apksigner）、zip | `JAVA_HOME`、`ANDROID_HOME`、`BUILD_TOOLS`；也可用 `SDK_STUB` 明确指定 android.jar |

源码版本与下载来源使用仓库已有记录：[kernel-source.json](../../third_party/upstream/kernel/kernel-source.json)、[wlan-source.json](../../third_party/upstream/kernel/wlan-source.json)。前者提供归档 URL；后者提供仓库和 commit，可从对应 GitHub commit 下载源码 tar.gz。新脚本调用原有 prepare 工具检查基线，然后应用仓库覆盖层。不要把旧实验目录中的修改树打包当作原始输入。

## 手机运行输入（不属于编译依赖）

- 三星 SM-S928B EZIF 的 `amss20.bin`：自行从官方固件提取，默认放在 `blobs/local/ezif-amss20.bin`，或配置 `HOUJI_EZIF_FIRMWARE`。
- 旧安装流程还使用 DZF2 的 `amss20.bin`：默认 `blobs/local/dzf2-amss20.bin`，或配置 `HOUJI_DZF2_FIRMWARE`。
- 原有固件提取材料见[获取目录](../../experiments/tx-ampdu-investigation/firmware-acquisition/)。默认不自动下载 ROM，也不运行手机操作。
- Samsung 完整源码包、小米系统包及 `libxos.so` 是逆向研究输入，当前独立 App 的编译不依赖它们。

`blobs/local/` 被 Git 忽略。固件默认不随源码发布。既有安装器的固件校验没有移除；不能仅改文件名来使用不同固件。

当前安装器仍属于旧实验部署流程，依赖原有 ABI 审计及手机运行环境。新的独立构建入口产出不自动接入安装器、不自动部署。
