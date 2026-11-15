#!/system/bin/sh
set -eu
R=/data/local/tmp/owly-app
S=$R/state
APP=/data/user/0/org.houji.owly/files
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

read_coexist_pref() {
 pref=""
 if test -r "$APP/coexist.mode"; then
  pref=$(tr -d '\r\n ' < "$APP/coexist.mode" 2>/dev/null || true)
 elif test -r "$R/coexist.mode"; then
  pref=$(tr -d '\r\n ' < "$R/coexist.mode" 2>/dev/null || true)
 fi
 case "$pref" in
  off) echo off ;;
  *) echo auto ;;
 esac
}

detect_sta_coexist() {
 coexist=0
 coex_5g=0
 sta_freq=0
 pref=$(read_coexist_pref)
 if test "$pref" = auto && cmd wifi status 2>/dev/null | grep -q 'Wifi is connected to'; then
  freq=$(iw dev wlan0 link 2>/dev/null | sed -n 's/^[[:space:]]*freq:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -n 1 || true)
  case "$freq" in
   ''|*[!0-9]*) freq=0 ;;
  esac
  sta_freq=$freq
  if test "$freq" -ge 2412 && test "$freq" -le 2472; then
   coexist=1
   coex_5g=0
  elif { test "$freq" -ge 5180 && test "$freq" -le 5240; } || { test "$freq" -ge 5745 && test "$freq" -le 5825; }; then
   coexist=1
   coex_5g=1
  fi
 fi
}

case "${1:-status}" in
 set-coexist)
  case "${2:-}" in
   auto|off)
    printf '%s\n' "$2" > "$R/coexist.mode"
    if test -d "$APP"; then
     app_uid=$(stat -c %u "$APP")
     printf '%s\n' "$2" > "$APP/coexist.tmp"
     chown "$app_uid:$app_uid" "$APP/coexist.tmp"
     chmod 600 "$APP/coexist.tmp"
     chcon "$(ls -Zd "$APP" | awk '{print $1}')" "$APP/coexist.tmp"
     mv "$APP/coexist.tmp" "$APP/coexist.mode"
    fi
    echo "COEXIST_MODE=$2" ;;
   *) exit 2 ;;
  esac ;;
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
  detect_sta_coexist
  if test "$mode" = connect; then
   assoc=1
   reo=2
   tx_ampdu=1
   firmware=ezif.bin
   rx_msdu_buf=1
   rx_ezif_marked=1
  else
   assoc=0
   reo=0
   tx_ampdu=0
   if test "$coexist" = 1; then
    firmware=ezif.bin
    rx_msdu_buf=1
    rx_ezif_marked=1
   else
    firmware=dzf2.bin
    rx_msdu_buf=0
    rx_ezif_marked=0
   fi
  fi
  cp "$R/firmware/$firmware" "$R/radio/amss20.bin"
  chown system:system "$R/radio/amss20.bin"
  chmod 440 "$R/radio/amss20.bin"
  chcon u:object_r:firmware_file:s0 "$R/radio/amss20.bin"
  sha256sum "$R/radio/amss20.bin" | awk '{print $1}' > "$R/selected-firmware.sha"
  cat > "$R/three-findings.env" <<EOF
tf_remote_assoc=$assoc
tf_remote_ast_override=1
tf_remote_hash_override=1
tf_remote_reo=$reo
tf_fw_addba=0
tf_tx_ampdu=$tx_ampdu
tf_dynamic_rate=0
tf_sta_coexist=$coexist
tf_coex_5g_override=$coex_5g
tf_rx_msdu_buf=$rx_msdu_buf
tf_rx_ezif_marked=$rx_ezif_marked
EOF
  sha256sum "$R/three-findings.env" | awk '{print $1}' > "$R/three-findings.sha"
  token="app-$(date +%s)-$$"
  rm -f "$S/result"
  "$BB" setsid sh "$R/radio.sh" run "$token" "$peer" </dev/null >"$S/session.log" 2>&1 8>&- &
  echo $! > "$S/launcher.pid"
  for i in $(seq 1 90); do
   if test -f "$S/result"; then cat "$S/session.log"; exit 1; fi
   if test -f "$S/token" && test "$(cat "$S/token")" = "$token" && test "$(cat "$S/status")" = OWLY_READY; then
    echo "OWLY_READY $mode coexist=$coexist coex_5g=$coex_5g sta_freq=$sta_freq"; exit 0
   fi
   sleep 1
  done
  sh "$R/radio.sh" stop "$token"
  echo START_TIMEOUT; exit 1 ;;
 stop) stop_session; echo STOCK_RESTORED ;;
 status) cat "$S/status" 2>/dev/null || echo IDLE ;;
 *) exit 2 ;;
esac
