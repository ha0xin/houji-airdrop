#!/system/bin/sh
set -eu
D=/data/local/tmp/xos/native
BB=/data/adb/ksu/bin/busybox
if test "${1:-}" != locked; then exec "$BB" flock -n "$D/supervisor.lock" /system/bin/sh "$0" locked; fi
uid=$(cmd package list packages -U com.miui.mishare.connectivity | sed -n 's/^package:com.miui.mishare.connectivity uid:\([0-9]*\)$/\1/p')
test -n "$uid"
echo $$ > "$D/supervisor.pid"
export CLASSPATH=/data/local/tmp/houji-buildtest-20261110/xos-bridge149.zip
/system/bin/app_process /system/bin XosBridge "$uid" &
child=$!
echo "$child" > "$D/bridge.pid"
trap 'kill "$child" 2>/dev/null || true' TERM INT
set +e
wait "$child"
rc=$?
echo "BRIDGE_EXIT=$rc"
/system/bin/sh /data/local/tmp/houji-buildtest-20261110/radio-native149.sh restore
