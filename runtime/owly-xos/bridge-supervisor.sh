#!/system/bin/sh
set -eu
D=/data/local/tmp/xos/native
OWLY_XOS=/data/local/tmp/xos/owly
BB=""
for cand in /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox /data/adb/magisk/busybox; do
 if test -x "$cand"; then BB="$cand"; break; fi
done
test -n "$BB"

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
if test -x /data/adb/ksu/bin/ksud; then
 /data/adb/ksu/bin/ksud sepolicy check "$SEPOLICY_RULE"
 /data/adb/ksu/bin/ksud sepolicy patch "$SEPOLICY_RULE"
elif test -x /data/adb/ap/bin/magiskpolicy; then
 /data/adb/ap/bin/magiskpolicy --live "$SEPOLICY_RULE"
elif test -x /data/adb/magisk/magiskpolicy; then
 /data/adb/magisk/magiskpolicy --live "$SEPOLICY_RULE"
elif command -v magiskpolicy >/dev/null 2>&1; then
 magiskpolicy --live "$SEPOLICY_RULE"
else
 echo "Missing ksud or magiskpolicy for live SELinux rule" >&2
 exit 1
fi
boot_id=$(cat /proc/sys/kernel/random/boot_id)
if test "$(cat "$OWLY_XOS/state/sepolicy.boot_id" 2>/dev/null || true)" != "$boot_id"; then
 for dom in ksu magisk su; do
  for k in 0 1 2 3 4 5 6 7; do
   runcon "u:r:$dom:s0:c$k" /system/bin/service list >/dev/null 2>&1 || true
  done
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
