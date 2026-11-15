#!/system/bin/sh
set -eu
D=/data/local/tmp/xos/native
OWLY_XOS=/data/local/tmp/xos/owly
BB=/data/adb/ksu/bin/busybox
KSUD=/data/adb/ksu/bin/ksud
test "$(id -u)" = 0
if test "$(readlink /proc/self/ns/mnt)" != "$(readlink /proc/1/ns/mnt)"; then
 exec "$BB" nsenter -t 1 -m /system/bin/sh "$OWLY_XOS/bridge-supervisor.sh" "$@"
fi
mkdir -p "$D" "$OWLY_XOS/state"
if test "${1:-}" != locked; then
 exec "$BB" flock -n "$D/supervisor.lock" /system/bin/sh "$OWLY_XOS/bridge-supervisor.sh" locked
fi
uid=$(cmd package list packages -U com.miui.mishare.connectivity | sed -n 's/^package:com.miui.mishare.connectivity uid:\([0-9]*\)$/\1/p')
test -n "$uid"
SEPOLICY_RULE='allow platform_app default_android_service service_manager { find }'
"$KSUD" sepolicy check "$SEPOLICY_RULE"
"$KSUD" sepolicy patch "$SEPOLICY_RULE"
boot_id=$(cat /proc/sys/kernel/random/boot_id)
if test "$(cat "$OWLY_XOS/state/sepolicy.boot_id" 2>/dev/null || true)" != "$boot_id"; then
 for k in 0 1 2 3 4 5 6 7; do
  runcon "u:r:ksu:s0:c$k" /system/bin/service list >/dev/null 2>&1 || true
 done
 printf '%s\n' "$boot_id" > "$OWLY_XOS/state/sepolicy.boot_id"
fi
echo $$ > "$D/supervisor.pid"
export CLASSPATH="$OWLY_XOS/xos-bridge.zip"
export XOS_RADIO_SCRIPT="$OWLY_XOS/radio.sh"
/system/bin/app_process /system/bin XosBridge "$uid" 3>&- 4>&- 5>&- 6>&- 7>&- 8>&- 9>&- &
child=$!
echo "$child" > "$D/bridge.pid"
trap 'kill "$child" 2>/dev/null || true' TERM INT
set +e
wait "$child"
rc=$?
echo "BRIDGE_EXIT=$rc"
/system/bin/sh "$OWLY_XOS/radio.sh" restore
