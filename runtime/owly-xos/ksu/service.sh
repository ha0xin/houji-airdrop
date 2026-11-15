#!/system/bin/sh
# Boot-time supervisor for Owly-XOS AIDL bridge with strict kernel/driver safety gate.

MODDIR="${0%/*}"
EXPECTED_KERNEL="6.1.138-android14-11-g0c3d559bcd85-ab14529422"
EXPECTED_BUILD_ID_SHA="a3e9bd62646444e1ecbe0f421c95ec46e6caf69a78323efb2f6ebe00e6b2bd8b"
EXPECTED_EZIF_SHA="37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f"
BASE_DESC="小米 14 (houji, HyperOS 3 / Android 16, kernel 6.1.138-ab14529422) 原生小米互传高速 AirDrop 后端 (Owly VHT80 2SS + EZIF 多对端硬件 TX A-MPDU)。"

NATIVE_DIR="/data/local/tmp/xos/native"
RUNTIME_DIR="/data/local/tmp/xos/owly"
BUILD_ID_PATH="/sys/module/qca_cld3_kiwi_v2/notes/.note.gnu.build-id"

BB=""
for cand in /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox /data/adb/magisk/busybox; do
  if [ -x "$cand" ]; then
    BB="$cand"
    break
  fi
done
[ -n "$BB" ] || exit 1

set_desc() {
  if [ -w "$MODDIR/module.prop" ]; then
    awk -v pfx="$1" -v base="$BASE_DESC" '
      /^description=/ { print "description=" pfx " " base; next }
      { print }
    ' "$MODDIR/module.prop" > "$MODDIR/module.prop.tmp" && \
      mv "$MODDIR/module.prop.tmp" "$MODDIR/module.prop"
  fi
}

# Wait for Android boot completion and PackageManager readiness.
i=0
while [ "$i" -lt 120 ]; do
  if [ "$(getprop sys.boot_completed)" = "1" ] && pm path com.miui.mishare.connectivity >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 1
done

mkdir -p "$NATIVE_DIR" "$RUNTIME_DIR/state"

# Safety check across OTA updates: never load out-of-tree .ko if kernel or stock driver changed.
ACTUAL_KERNEL="$(uname -r)"
if [ "$ACTUAL_KERNEL" != "$EXPECTED_KERNEL" ]; then
  echo "[owly-xos] Kernel mismatch (${ACTUAL_KERNEL} != ${EXPECTED_KERNEL}); refusing to start." >> "$NATIVE_DIR/owly-bridge.log"
  set_desc "[⚠️ 已安全停用：内核已变更 (${ACTUAL_KERNEL})]"
  exit 0
fi

# Wait briefly for stock WLAN module if still initializing at boot.
j=0
while [ "$j" -lt 20 ] && [ ! -r "$BUILD_ID_PATH" ] && [ ! -e /sys/module/wonder ]; do
  j=$((j + 1))
  sleep 1
done

if [ ! -e /sys/module/wonder ] && [ -r "$BUILD_ID_PATH" ]; then
  ACTUAL_BUILD_ID="$(sha256sum "$BUILD_ID_PATH" | awk '{print $1}')"
  if [ "$ACTUAL_BUILD_ID" != "$EXPECTED_BUILD_ID_SHA" ]; then
    echo "[owly-xos] Stock driver build-id mismatch (${ACTUAL_BUILD_ID}); refusing to start." >> "$NATIVE_DIR/owly-bridge.log"
    set_desc "[⚠️ 已安全停用：原厂 WLAN 驱动版本不匹配]"
    exit 0
  fi
fi

sync_runtime() {
  mkdir -p "$NATIVE_DIR" "$RUNTIME_DIR/state" "$RUNTIME_DIR/radio" "$RUNTIME_DIR/firmware"
  chmod 700 "$NATIVE_DIR" "$RUNTIME_DIR" "$RUNTIME_DIR/state"
  rm -f "$RUNTIME_DIR/houji-owly-app"
  cp "$MODDIR/payload/xos-bridge.zip" "$RUNTIME_DIR/xos-bridge.zip"
  cp "$MODDIR/payload/bridge-supervisor.sh" "$RUNTIME_DIR/bridge-supervisor.sh"
  cp "$MODDIR/payload/radio.sh" "$RUNTIME_DIR/radio.sh"
  cp "$MODDIR/payload/owly-xosd" "$RUNTIME_DIR/owly-xosd"
  cp "$MODDIR/payload/wonder-init" "$RUNTIME_DIR/wonder-init"
  cp "$MODDIR/payload/qca_cld3_kiwi_v2-three-findings.ko" "$RUNTIME_DIR/qca_cld3_kiwi_v2-three-findings.ko"
  cp "$MODDIR/payload/firmware/ezif.bin" "$RUNTIME_DIR/firmware/ezif.bin"
  cp "$MODDIR/payload/radio/houji_wonder_bridge.ko" "$RUNTIME_DIR/radio/houji_wonder_bridge.ko"
  cp "$MODDIR/payload/radio/wonder.ko" "$RUNTIME_DIR/radio/wonder.ko"
  cp "$MODDIR/payload/installation.sha256" "$RUNTIME_DIR/installation.sha256"
  cp "$MODDIR/payload/installation.sha256" "$RUNTIME_DIR/SHA256SUMS"
  chmod 644 "$RUNTIME_DIR/xos-bridge.zip"
  chmod 700 "$RUNTIME_DIR/bridge-supervisor.sh" "$RUNTIME_DIR/radio.sh" "$RUNTIME_DIR/owly-xosd" "$RUNTIME_DIR/wonder-init"
  chmod 600 "$RUNTIME_DIR/qca_cld3_kiwi_v2-three-findings.ko" "$RUNTIME_DIR/firmware/ezif.bin" \
    "$RUNTIME_DIR/radio/houji_wonder_bridge.ko" "$RUNTIME_DIR/radio/wonder.ko" \
    "$RUNTIME_DIR/installation.sha256" "$RUNTIME_DIR/SHA256SUMS"
}

if [ ! -f "$RUNTIME_DIR/installation.sha256" ] || \
   ! cmp -s "$MODDIR/payload/installation.sha256" "$RUNTIME_DIR/installation.sha256" || \
   ! (cd "$RUNTIME_DIR" && sha256sum -c installation.sha256 >/dev/null 2>&1); then
  sync_runtime
fi

if [ ! -f "$RUNTIME_DIR/radio/amss20.bin" ] || \
   ! echo "${EXPECTED_EZIF_SHA}  $RUNTIME_DIR/radio/amss20.bin" | sha256sum -c - >/dev/null 2>&1; then
  rm -f "$RUNTIME_DIR/radio/amss20.bin"
  cp "$RUNTIME_DIR/firmware/ezif.bin" "$RUNTIME_DIR/radio/amss20.bin"
  chown system:system "$RUNTIME_DIR/radio/amss20.bin"
  chmod 440 "$RUNTIME_DIR/radio/amss20.bin"
  chcon u:object_r:firmware_file:s0 "$RUNTIME_DIR/radio/amss20.bin"
fi

if [ ! -e "$RUNTIME_DIR/state/saved.env" ]; then
  printf 'STOCK_RESTORED\n' > "$RUNTIME_DIR/state/status"
  printf 'STOCK_RESTORED\n' > "$NATIVE_DIR/status"
fi

set_desc "[✅ 守护运行中 | ch149 VHT80 2SS EZIF]"

while [ ! -f "$MODDIR/disable" ] && [ ! -f "$MODDIR/remove" ]; do
  "$BB" nsenter -t 1 -m /system/bin/sh "$RUNTIME_DIR/bridge-supervisor.sh" \
    </dev/null >>"$NATIVE_DIR/owly-bridge.log" 2>&1 || true
  sleep 3
done

# Module was disabled or marked for removal at runtime: stop bridge and restore stock Wi-Fi.
for pidfile in "$NATIVE_DIR/bridge.pid" "$NATIVE_DIR/supervisor.pid"; do
  p="$(cat "$pidfile" 2>/dev/null || true)"
  case "$p" in
    ''|*[!0-9]*) ;;
    *) kill -TERM "$p" 2>/dev/null || true ;;
  esac
done
if [ -x "$RUNTIME_DIR/radio.sh" ]; then
  "$BB" nsenter -t 1 -m /system/bin/sh "$RUNTIME_DIR/radio.sh" restore >>"$NATIVE_DIR/owly-bridge.log" 2>&1 || true
fi
set_desc "[⏸ 已停用]"
