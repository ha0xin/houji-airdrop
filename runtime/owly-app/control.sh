#!/system/bin/sh
set -eu
R=/data/local/tmp/owly-app
S=$R/state
BB=/data/adb/ksu/bin/busybox
test "$(id -u)" = 0
if test "$(readlink /proc/self/ns/mnt)" != "$(readlink /proc/1/ns/mnt)"; then
 exec "$BB" nsenter -t 1 -m sh "$0" "$@"
fi
mkdir -p "$S"
exec 8>"$R/control.lock"
"$BB" flock -n 8 8>&8
stop_session() {
 if test -f "$S/token" && test ! -f "$S/result"; then
  token=$(cat "$S/token")
  sh "$R/radio.sh" stop "$token"
  for i in $(seq 1 120); do
   if test -f "$S/result"; then break; fi
   sleep 1
  done
  test -f "$S/result"
  test "$(cat "$S/status")" = STOCK_RESTORED
 fi
}
case "${1:-status}" in
 scan|connect)
  mode=$1
  peer=scan
  if test "$mode" = connect; then
   peer=${2:-}
   echo "$peer" | grep -Eq '^([0-9a-f]{2}:){5}[0-9a-f]{2}$'
  fi
  stop_session
  if test -f "$S/saved.env"; then sh "$R/radio.sh" recover; fi
  (cd "$R" && sha256sum -c installation.sha256)
  assoc=0
  firmware=dzf2.bin
  if test "$mode" = connect; then assoc=1; firmware=ezif.bin; fi
  cp "$R/firmware/$firmware" "$R/radio/amss20.bin"
  chown system:system "$R/radio/amss20.bin"
  chmod 440 "$R/radio/amss20.bin"
  chcon u:object_r:firmware_file:s0 "$R/radio/amss20.bin"
  sha256sum "$R/radio/amss20.bin" | awk '{print $1}' > "$R/selected-firmware.sha"
  printf 'tf_remote_assoc=%s\ntf_remote_ast_override=1\ntf_remote_hash_override=1\ntf_remote_reo=2\ntf_fw_addba=0\ntf_tx_ampdu=1\ntf_dynamic_rate=0\ntf_sta_coexist=0\n' "$assoc" > "$R/three-findings.env"
  sha256sum "$R/three-findings.env" | awk '{print $1}' > "$R/three-findings.sha"
  token="app-$(date +%s)-$$"
  rm -f "$S/result"
  "$BB" setsid sh "$R/radio.sh" run "$token" "$peer" </dev/null >"$S/session.log" 2>&1 8>&- &
  echo $! > "$S/launcher.pid"
  for i in $(seq 1 60); do
   if test -f "$S/result"; then cat "$S/session.log"; exit 1; fi
   if test -f "$S/token" && test "$(cat "$S/token")" = "$token" && test "$(cat "$S/status")" = OWLY_READY; then
    echo "OWLY_READY $mode"; exit 0
   fi
   sleep 1
  done
  sh "$R/radio.sh" stop "$token"
  echo START_TIMEOUT; exit 1 ;;
 stop) stop_session; echo STOCK_RESTORED ;;
 status) cat "$S/status" 2>/dev/null || echo IDLE ;;
 *) exit 2 ;;
esac
