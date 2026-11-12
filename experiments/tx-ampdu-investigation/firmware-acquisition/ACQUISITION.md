# EZIF 官方 FUS 获取记录（已完成）

目标：SM-S928B / FWD / S928BXXU6EZIF/S928BOXM6EZIF/S928BXXU6EZIF/S928BXXU6EZIF。

工具来源：https://github.com/Azkali/samloader ，commit bea2595bb8951e8895acaf0b7e032d478f441a46。已阅读auth/request/fusclient，独立副本只增加HTTP超时。原samloader旧协议nonce解密失败，切换维护分支的Smart FUS协议成功。自编 acquire-ap.py 只请求Range并读取/解密归档，不执行固件内容。

Samsung官方NF_SmartDownloadBinaryInform返回S00；FWD/SWC均指向同一归档：SM-S928B_4_20260920035540_tjiwm69uku_fac.zip.enc4；加密长度20198115216B，metadata CRC1727450973。官方下载端https://cloud-neofussvr.samsungmobile.com/NF_SmartDownloadBinaryForMass.do支持206 Range。XML保存在fus-smart-FWD.xml/fus-smart-SWC.xml；remote official-metadata.xml为实际下载session查询。

从末尾128KiB解密后获得ZIP64目录，AP条目：AP_S928BXXU6EZIF_S928BXXU6EZIF_MQB115064488_REV00_user_low_ship_MULTI_CERT_meta_OS17.tar.md5。流式读取此条目的压缩范围，ECB解密→deflate→tar，仅保存super.img.lz4，排除userdata/boot等。完整AP流读取后检查ZIP CRC与tar总长度，并记录tar SHA256；不声称已验证整个20GB包的MD5/CRC，因为没有下载其它ZIP条目。

远端独立目录：build-host:/work/houji-build/ezif-fus-independent-20261111 。日志acquire-ap.log、extract.log；全过程禁止部署。使用uv在独立venv安装requests/pycryptodomex/lz4，无系统包修改。

super.img.lz4=11183204127B，SHA256 5581e6400af9cf148d2886089b0ec0b9ebfacf7738bc016dd2ef7acb3fd5fc8a；lz4展开为12443875744B sparse super；LP metadata vendor=1948352512B、vendor_dlkm=31551488B。峰值约25.61GB，开始时远端空闲48GB。每次展开/分区提取检查至少保留10GiB。不在本机和/tmp存大包，不展开完整raw super。

下载障碍记录：SamFOTA browser grant成功但edge-download返回404；SamMobile要求Join to Download；Samfrew普通下载需要Turnstile网页验证，未绕过；因此使用公开客户端实现的官方FUS协议。

## 提取结果

已成功完成lz4内容校验与两个EROFS分区提取。vendor/build.prop：`ro.vendor.build.fingerprint=samsung/e3qxxx/e3q:14/UP1A.231005.007/S928BXXU6EZIF:user/release-keys`，incremental=EZIF。vendor基线Android14字段不应误读成系统AP不是Android17。

- 官方vendor/firmware/kiwi/amss20.bin SHA256=`37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f`，与既有`downloads/oneui9-houji-analysis/modem.img.amss20.bin`逐字节hash一致，终于补齐该样本的官方独立来源证据。
- 官方vendor_dlkm/lib/modules/qca_cld3_kiwi_v2.ko SHA256=`8b3694f692d0acdd59f0dcab864d5de0c59c0c9e59d73e93254512a4878a0022`，20372912B。vermagic=`6.1.162-android14-11-34343818-abS928BXXU6EZIF SMP preempt mod_unload modversions aarch64`。
- 与移植包ko（20377008B，d6f2678f…）对比：只有.text和__versions section内容不同；.text追加4096B，原正文有22字节变化，集中0x45e904–0x45f114。__versions有12项CRC差异。因此移植ko并非逐字节原厂。此处不推断追加代码具体行为。
- .symtab/.rodata/.modinfo/.note.gnu.build-id及其余所有section内容hash相同。此前分析wire506、HDD capabilities、Wonder start、DP TX路径均不在.text变化区，相应机器码与官方模块一致。
- 本地小样本：official-ezif-artifacts/。比较证据：ko-section-comparison.json、ko-text-diff-ranges.json。没有部署。

## 最终完整性核对

官方AP条目全部读完，进程exit0：ZIP CRC32=3666200036，与ZIP64目录期望值一致；解压tar长度21744404603B，与目录一致；tar SHA256=09a16cb9055b8c82e02b9b67635971171ee134ca45d5356c82961defd1e7fe45。证据official-ezif-artifacts/AP-verified.json、acquire-ap.log。其它ZIP条目未下载，未声称整包MD5核验。

两份目标样本已本地/远端SHA256复核。仅清理本任务创建的super.img.lz4、super.img、vendor.img、vendor_dlkm.img及两份EROFS展开目录；保留小样本、官方XML、ZIP目录、哈希、日志、脚本。未改动任何原有共享实验目录，未操作手机。
