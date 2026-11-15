#!/system/bin/sh
# KernelSU / APatch / Magisk installer for Owly-XOS (Xiaomi 14 / houji).

EXPECTED_DEVICE="houji"
EXPECTED_SDK="36"
EXPECTED_KERNEL="6.1.138-android14-11-g0c3d559bcd85-ab14529422"
EXPECTED_AMSS_SHA="4db7b0e1aa7e4ec071708359c9d0db8d3d7872390cd8e88ffa0bbb30d560bcc8"
EXPECTED_BUILD_ID_SHA="a3e9bd62646444e1ecbe0f421c95ec46e6caf69a78323efb2f6ebe00e6b2bd8b"
EXPECTED_EZIF_SHA="37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f"
TARGET_MISHARE_VC=50402

NATIVE_DIR="/data/local/tmp/xos/native"
RUNTIME_DIR="/data/local/tmp/xos/owly"
AMSS_PATH="/vendor/firmware_mnt/image/kiwi/amss20.bin"
BUILD_ID_PATH="/sys/module/qca_cld3_kiwi_v2/notes/.note.gnu.build-id"
STOCK_KO="/vendor_dlkm/lib/modules/qca_cld3_kiwi_v2.ko"

# No /system overlay mount needed: MIShare.apk carries Xiaomi's platform signature.
touch "$MODPATH/skip_mount"

BB=""
for cand in /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox /data/adb/magisk/busybox; do
  if [ -x "$cand" ]; then
    BB="$cand"
    break
  fi
done
[ -n "$BB" ] || abort "! 未找到 KernelSU / APatch / Magisk 自带的 busybox"

ui_print "================================================"
ui_print "  Owly-XOS 小米原生互传高速 AirDrop 后端 v1.0.0"
ui_print "  VHT80 2SS MCS9 + EZIF 多对端硬件 TX A-MPDU"
ui_print "================================================"

ACTUAL_DEVICE="$(getprop ro.product.device)"
ACTUAL_MODEL="$(getprop ro.product.marketname)"
[ -n "$ACTUAL_MODEL" ] || ACTUAL_MODEL="$(getprop ro.product.model)"
ACTUAL_SDK="$(getprop ro.build.version.sdk)"
ACTUAL_REL="$(getprop ro.build.version.release)"
ACTUAL_OS="$(getprop ro.mi.os.version.incremental)"
[ -n "$ACTUAL_OS" ] || ACTUAL_OS="$(getprop ro.build.version.incremental)"
ACTUAL_KERNEL="$(uname -r)"

ui_print "- 当前设备: ${ACTUAL_MODEL} (${ACTUAL_DEVICE})"
ui_print "- 当前系统: HyperOS ${ACTUAL_OS} (Android ${ACTUAL_REL}, SDK ${ACTUAL_SDK})"
ui_print "- 当前内核: ${ACTUAL_KERNEL}"

if [ "$ACTUAL_DEVICE" != "$EXPECTED_DEVICE" ]; then
  abort "! 机型不匹配：仅支持小米 14 (${EXPECTED_DEVICE})，当前为 ${ACTUAL_DEVICE}"
fi

if [ "$ACTUAL_SDK" != "$EXPECTED_SDK" ]; then
  abort "! 系统版本不匹配：需要澎湃 HyperOS 3 / Android 16 (SDK ${EXPECTED_SDK})，当前为 SDK ${ACTUAL_SDK}"
fi

if [ "$ACTUAL_KERNEL" != "$EXPECTED_KERNEL" ]; then
  ui_print "! 内核版本不匹配（567 个导出符号 CRC 仅针对目标内核审计）："
  ui_print "  需要: ${EXPECTED_KERNEL}"
  ui_print "  当前: ${ACTUAL_KERNEL}"
  abort "! 已中止安装，未对系统做任何修改"
fi

if [ -e "$RUNTIME_DIR/state/saved.env" ] || \
   [ -e "/data/local/tmp/owly-app/state/saved.env" ] || \
   [ -e /sys/module/wonder ] || \
   [ -e /sys/module/houji_wonder_bridge ]; then
  abort "! 检测到正在运行的实验无线电会话，请先关闭互传并恢复普通 Wi-Fi 后再安装"
fi

[ -r "$AMSS_PATH" ] || abort "! 未找到原厂固件 ${AMSS_PATH}"
ACTUAL_AMSS_SHA="$(sha256sum "$AMSS_PATH" | awk '{print $1}')"
if [ "$ACTUAL_AMSS_SHA" != "$EXPECTED_AMSS_SHA" ]; then
  ui_print "! 原厂 Wi-Fi 固件 amss20.bin SHA-256 不匹配："
  ui_print "  需要: ${EXPECTED_AMSS_SHA}"
  ui_print "  当前: ${ACTUAL_AMSS_SHA}"
  abort "! 已中止安装"
fi

if [ ! -r "$BUILD_ID_PATH" ] && [ -f "$STOCK_KO" ]; then
  insmod "$STOCK_KO" >/dev/null 2>&1 || true
fi
[ -r "$BUILD_ID_PATH" ] || abort "! 原厂驱动 qca_cld3_kiwi_v2 未加载，请先打开系统 Wi-Fi 后重试"
ACTUAL_BUILD_ID_SHA="$(sha256sum "$BUILD_ID_PATH" | awk '{print $1}')"
if [ "$ACTUAL_BUILD_ID_SHA" != "$EXPECTED_BUILD_ID_SHA" ]; then
  ui_print "! 原厂 WLAN 驱动 build-id 不匹配："
  ui_print "  需要: ${EXPECTED_BUILD_ID_SHA}"
  ui_print "  当前: ${ACTUAL_BUILD_ID_SHA}"
  abort "! 已中止安装"
fi

ui_print "- 正在校验模块内固件、内核模块与 APK 完整性..."
(cd "$MODPATH/payload" && sha256sum -c installation.sha256 >/dev/null) || abort "! payload SHA-256 校验失败"
(cd "$MODPATH/apks" && sha256sum -c APKS.sha256 >/dev/null) || abort "! apks SHA-256 校验失败"

ui_print "- 正在停止旧版桥接守护（若存在）..."
for pidfile in "$NATIVE_DIR/bridge.pid" "$NATIVE_DIR/supervisor.pid"; do
  p="$(cat "$pidfile" 2>/dev/null || true)"
  case "$p" in
    ''|*[!0-9]*) ;;
    *) kill -TERM "$p" 2>/dev/null || true ;;
  esac
done
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if "$BB" flock -n "$NATIVE_DIR/supervisor.lock" true 2>/dev/null && \
     ! /system/bin/ps -A -o ARGS 2>/dev/null | grep -q '^[a]pp_process /system/bin XosBridge '; then
    break
  fi
  sleep 0.3
done

# Stage APKs via /data/local/tmp so system_server (PackageManager) can read them under SELinux Enforcing.
STAGE_MISHARE="/data/local/tmp/owly-xos-stage-mishare.apk"
STAGE_COMPAT="/data/local/tmp/owly-xos-stage-compat.apk"
rm -f "$STAGE_MISHARE" "$STAGE_COMPAT"

CUR_VC="$(dumpsys package com.miui.mishare.connectivity 2>/dev/null | sed -n 's/.*versionCode=\([0-9][0-9]*\).*/\1/p' | head -n 1)"
if [ -n "$CUR_VC" ] && [ "$CUR_VC" -gt "$TARGET_MISHARE_VC" ] 2>/dev/null; then
  ui_print "- 检测到已安装更高版本小米互传 (versionCode=${CUR_VC})，保留现有版本"
else
  ui_print "- 正在安装支持 Apple 互传的原厂签名小米互传 (5.4.2 / ${TARGET_MISHARE_VC})..."
  cp "$MODPATH/apks/MIShare.apk" "$STAGE_MISHARE"
  chmod 644 "$STAGE_MISHARE"
  if ! pm install -r "$STAGE_MISHARE" >/dev/null 2>&1; then
    rm -f "$STAGE_MISHARE"
    abort "! 安装 MIShare.apk 失败"
  fi
  rm -f "$STAGE_MISHARE"
fi

for perm in \
  android.permission.NETWORK_FACTORY \
  android.permission.NEARBY_WIFI_DEVICES \
  android.permission.BLUETOOTH_CONNECT \
  android.permission.BLUETOOTH_ADVERTISE \
  android.permission.BLUETOOTH_SCAN \
  android.permission.POST_NOTIFICATIONS; do
  pm grant com.miui.mishare.connectivity "$perm" >/dev/null 2>&1 || true
done
dumpsys package com.miui.mishare.connectivity 2>/dev/null | grep -q 'android.permission.NETWORK_FACTORY: granted=true' || \
  abort "! 授予小米互传 NETWORK_FACTORY 权限失败"

ui_print "- 正在安装 LSPosed 兼容模块 Owly-XOS 小米互传兼容 (org.houji.xoscompat)..."
cp "$MODPATH/apks/Owly-XOS-Compat.apk" "$STAGE_COMPAT"
chmod 644 "$STAGE_COMPAT"
COMPAT_OUT="$(pm install -r "$STAGE_COMPAT" 2>&1 || true)"
rm -f "$STAGE_COMPAT"
case "$COMPAT_OUT" in
  *Success*)
    ui_print "- Owly-XOS-Compat.apk 安装成功"
    ;;
  *INSTALL_FAILED_UPDATE_INCOMPATIBLE*)
    ui_print "! 检测到本机已存在不同签名的 org.houji.xoscompat，保留现有模块与作用域配置"
    ui_print "  （如需替换为包内版本，请先卸载旧版 Owly-XOS 兼容模块后重刷）"
    ;;
  *)
    ui_print "! Owly-XOS-Compat.apk 安装输出: ${COMPAT_OUT}"
    abort "! 安装 Owly-XOS-Compat.apk 失败"
    ;;
esac

ui_print "- 正在部署 owly-xos 高速后端运行时与预暂存固件..."
mkdir -p "$NATIVE_DIR" "$RUNTIME_DIR/state" "$RUNTIME_DIR/radio" "$RUNTIME_DIR/firmware"
chmod 700 "$NATIVE_DIR" "$RUNTIME_DIR" "$RUNTIME_DIR/state"

rm -f "$RUNTIME_DIR/houji-owly-app"
cp "$MODPATH/payload/xos-bridge.zip" "$RUNTIME_DIR/xos-bridge.zip"
cp "$MODPATH/payload/bridge-supervisor.sh" "$RUNTIME_DIR/bridge-supervisor.sh"
cp "$MODPATH/payload/radio.sh" "$RUNTIME_DIR/radio.sh"
cp "$MODPATH/payload/owly-xosd" "$RUNTIME_DIR/owly-xosd"
cp "$MODPATH/payload/wonder-init" "$RUNTIME_DIR/wonder-init"
cp "$MODPATH/payload/qca_cld3_kiwi_v2-three-findings.ko" "$RUNTIME_DIR/qca_cld3_kiwi_v2-three-findings.ko"
cp "$MODPATH/payload/firmware/ezif.bin" "$RUNTIME_DIR/firmware/ezif.bin"
cp "$MODPATH/payload/radio/houji_wonder_bridge.ko" "$RUNTIME_DIR/radio/houji_wonder_bridge.ko"
cp "$MODPATH/payload/radio/wonder.ko" "$RUNTIME_DIR/radio/wonder.ko"
cp "$MODPATH/payload/installation.sha256" "$RUNTIME_DIR/installation.sha256"
cp "$MODPATH/payload/installation.sha256" "$RUNTIME_DIR/SHA256SUMS"

chmod 644 "$RUNTIME_DIR/xos-bridge.zip"
chmod 700 "$RUNTIME_DIR/bridge-supervisor.sh" "$RUNTIME_DIR/radio.sh" "$RUNTIME_DIR/owly-xosd" "$RUNTIME_DIR/wonder-init"
chmod 600 "$RUNTIME_DIR/qca_cld3_kiwi_v2-three-findings.ko" "$RUNTIME_DIR/firmware/ezif.bin" \
  "$RUNTIME_DIR/radio/houji_wonder_bridge.ko" "$RUNTIME_DIR/radio/wonder.ko" \
  "$RUNTIME_DIR/installation.sha256" "$RUNTIME_DIR/SHA256SUMS"

(cd "$RUNTIME_DIR" && sha256sum -c installation.sha256 >/dev/null) || abort "! 运行时部署校验失败"

rm -f "$RUNTIME_DIR/radio/amss20.bin"
cp "$RUNTIME_DIR/firmware/ezif.bin" "$RUNTIME_DIR/radio/amss20.bin"
chown system:system "$RUNTIME_DIR/radio/amss20.bin"
chmod 440 "$RUNTIME_DIR/radio/amss20.bin"
chcon u:object_r:firmware_file:s0 "$RUNTIME_DIR/radio/amss20.bin"
echo "${EXPECTED_EZIF_SHA}  $RUNTIME_DIR/radio/amss20.bin" | sha256sum -c - >/dev/null || abort "! amss20.bin 暂存校验失败"

rm -f "$RUNTIME_DIR/state/sepolicy.boot_id"
printf 'STOCK_RESTORED\n' > "$RUNTIME_DIR/state/status"
printf 'STOCK_RESTORED\n' > "$NATIVE_DIR/status"

set_perm "$MODPATH/service.sh" 0 0 0755
set_perm "$MODPATH/uninstall.sh" 0 0 0755

ui_print "- 正在启动 XosBridge AIDL 守护..."
"$BB" setsid "$BB" nsenter -t 1 -m /system/bin/sh "$RUNTIME_DIR/bridge-supervisor.sh" \
  </dev/null >>"$NATIVE_DIR/owly-bridge.log" 2>&1 &
sleep 1
am force-stop com.miui.mishare.connectivity >/dev/null 2>&1 || true

ui_print "================================================"
ui_print "✅ 安装完成！请完成最后两步："
ui_print "1. 打开 LSPosed 管理器 -> 启用「Owly-XOS 小米互传兼容」"
ui_print "   （已内置默认推荐作用域：小米互传 + 系统分享 Intent Resolver）"
ui_print "2. 重启手机后即可在控制中心/相册直接与 Apple 设备高速互传"
ui_print "================================================"
