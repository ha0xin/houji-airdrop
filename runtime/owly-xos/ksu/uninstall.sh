#!/system/bin/sh
# Clean up Owly-XOS runtime state upon module uninstall.

NATIVE_DIR="/data/local/tmp/xos/native"
RUNTIME_DIR="/data/local/tmp/xos/owly"

BB=""
for cand in /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox /data/adb/magisk/busybox; do
  if [ -x "$cand" ]; then
    BB="$cand"
    break
  fi
done

for pidfile in "$NATIVE_DIR/bridge.pid" "$NATIVE_DIR/supervisor.pid"; do
  p="$(cat "$pidfile" 2>/dev/null || true)"
  case "$p" in
    ''|*[!0-9]*) ;;
    *) kill -TERM "$p" 2>/dev/null || true ;;
  esac
done

if [ -n "$BB" ] && [ -f "$RUNTIME_DIR/radio.sh" ]; then
  "$BB" nsenter -t 1 -m /system/bin/sh "$RUNTIME_DIR/radio.sh" restore >/dev/null 2>&1 || true
fi

rm -rf "$RUNTIME_DIR" "$NATIVE_DIR"
# Note: MIShare.apk (system app update) and org.houji.xoscompat remain safe or can be uninstalled by the user if desired.
