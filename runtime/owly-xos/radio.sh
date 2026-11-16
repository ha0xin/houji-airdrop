#!/system/bin/sh
# Audited high-speed (EZIF + dynamic multi-peer HW TX A-MPDU + VHT80 2SS MCS9 SGI80 + A-MSDU + RX BA win=256) xos0 backend for native MIShare.
set -eu

R=/data/local/tmp/xos/owly
STATE=$R/state
RADIO=$R/radio
NATIVE=/data/local/tmp/xos/native
OWLY_APP=/data/local/tmp/owly-app
LEGACY_BASE=/data/local/tmp/houji-buildtest-20261110
BB=""
for cand in /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox /data/adb/magisk/busybox; do
 if test -x "$cand"; then BB="$cand"; break; fi
done
test -n "$BB"
W=/sys/module/qca_cld3_kiwi_v2
AMSS=/vendor/firmware_mnt/image/kiwi/amss20.bin
STOCK=/vendor_dlkm/lib/modules/qca_cld3_kiwi_v2.ko
PCI=/sys/bus/pci/devices/0000:01:00.0/power/control
ORIG=4db7b0e1aa7e4ec071708359c9d0db8d3d7872390cd8e88ffa0bbb30d560bcc8
CANDIDATE=37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f
NOTE=a3e9bd62646444e1ecbe0f421c95ec46e6caf69a78323efb2f6ebe00e6b2bd8b
QCA_SHA=e425dbfa4a07e8abe786300efd8e2c873025c7d5f925f4e19e9b461f9e8d8344
BRIDGE_SHA=0ab5c986cac01f8b6bb9eefd38088bd350a2fc68ea1a3efeff478b88b4e74ead
WONDER_SHA=24ca5401ea776069c273260eb0521c3783574710a616a4bae6a50407b019f2b1
TABLE=10074
IFACE=xos0

test "$(id -u)" = 0
if test "$(readlink /proc/self/ns/mnt)" != "$(readlink /proc/1/ns/mnt)"; then
 exec "$BB" nsenter -t 1 -m /system/bin/sh "$R/radio.sh" "$@"
fi
mkdir -p "$STATE" "$NATIVE"

bounded() { "$BB" timeout -s TERM -k 2 25 "$@"; }
check() { echo "$1  $2" | sha256sum -c -; }

setstate() {
 echo "$1" > "$STATE/status"
 echo "$1" > "$NATIVE/status"
 echo "RADIO_STATE=$1"
 case "$1" in
  ENTERING|XOS_READY) setprop sys.xos.exclusive 1 ;;
  STOCK_RESTORED) setprop sys.xos.exclusive 0 ;;
 esac
}

wait_wifi() {
 if test "$(settings get global wifi_on)" != 1; then return 0; fi
 for i in $(seq 1 15); do
  if cmd wifi status 2>/dev/null | grep -q 'Wifi is connected to'; then
   echo WIFI_CONNECTED
   return 0
  fi
  if test "$i" = 2 || test "$i" = 8; then
   cmd wifi start-scan >/dev/null 2>&1 || true
  fi
  sleep 1
 done
 echo WIFI_RECONNECT_RETRY_CYCLE
 cmd wifi set-wifi-enabled disabled >/dev/null 2>&1 || true
 for i in $(seq 1 10); do
  if cmd wifi status 2>/dev/null | grep -q 'Wifi is disabled'; then break; fi
  sleep 0.5
 done
 sleep 2
 cmd wifi set-wifi-enabled enabled >/dev/null 2>&1 || true
 sleep 2
 cmd wifi start-scan >/dev/null 2>&1 || true
 for i in $(seq 1 20); do
  if cmd wifi status 2>/dev/null | grep -q 'Wifi is connected to'; then
   echo WIFI_CONNECTED
   return 0
  fi
  if test "$i" = 8; then
   cmd wifi start-scan >/dev/null 2>&1 || true
  fi
  sleep 1
 done
 echo WIFI_RECONNECT_TIMEOUT
 return 1
}

stock_environment() {
 test ! -e "$NATIVE/saved.env"
 test ! -e "$OWLY_APP/state/saved.env"
 test ! -e "$LEGACY_BASE/state/saved.env"
 test ! -e /sys/module/wonder
 test ! -e /sys/module/houji_wonder_bridge
 test ! -e /sys/class/net/wonder0
 test ! -e /sys/class/net/wondertap0
 test ! -e /sys/class/net/owly0
 test ! -e /sys/class/net/"$IFACE"
 check "$ORIG" "$AMSS"
}

stock_files() {
 stock_environment
 check "$NOTE" "$W/notes/.note.gnu.build-id"
}

clean_routes() {
 for priority in 10980 10981 10982; do
  while ip -6 rule del priority "$priority" 2>/dev/null; do :; done
 done
 ip -6 route flush table "$TABLE" 2>/dev/null || true
}

ensure_rule() {
 prio="$1"
 pattern="$2"
 shift 2
 matches=$(printf '%s\n' "$rules_dump" | grep -cE "^$prio:[[:space:]]+$pattern[[:space:]]*$" || true)
 total=$(printf '%s\n' "$rules_dump" | grep -cE "^$prio:[[:space:]]" || true)
 if test "$matches" = 1 && test "$total" = 1; then
  return 0
 fi
 while ip -6 rule del priority "$prio" 2>/dev/null; do :; done
 ip -6 rule add priority "$prio" "$@"
}

routes() {
 addr=$(ip -6 -o addr show dev "$IFACE" scope link 2>/dev/null | grep -vE 'tentative|dadfailed' | awk '$3=="inet6"{sub(/\/.*/, "", $4); print $4; exit}')
 case "$addr" in fe80:*) ;; *) return 1 ;; esac
 ip -6 route replace fe80::/64 dev "$IFACE" table "$TABLE"
 ip -6 route replace ff00::/8 dev "$IFACE" table "$TABLE"
 rules_dump=$(ip -6 rule show 2>/dev/null || true)
 ensure_rule 10980 "from all[[:space:]]+to fe80::/64[[:space:]]+oif $IFACE[[:space:]]+uidrange 0-0[[:space:]]+lookup $TABLE" to fe80::/64 oif "$IFACE" uidrange 0-0 lookup "$TABLE"
 ensure_rule 10981 "from all[[:space:]]+to ff00::/8[[:space:]]+oif $IFACE[[:space:]]+uidrange 0-0[[:space:]]+lookup $TABLE" to ff00::/8 oif "$IFACE" uidrange 0-0 lookup "$TABLE"
 ensure_rule 10982 "from $addr(/128)?[[:space:]]+lookup $TABLE" from "$addr/128" lookup "$TABLE"
}

runner_alive() {
 pid=$(cat "$STATE/runner.pid" 2>/dev/null || true)
 case "$pid" in ''|*[!0-9]*) return 1 ;; esac
 if ! kill -0 "$pid" 2>/dev/null; then return 1; fi
 tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "owly-xosd $IFACE "
}

stop_runner() {
 pid=$(cat "$STATE/runner.pid" 2>/dev/null || true)
 case "$pid" in
  ''|*[!0-9]*) ;;
  *)
   if kill -0 "$pid" 2>/dev/null && tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -qE "(owly-xosd|houji-owly-app) $IFACE "; then
    kill -TERM "$pid" 2>/dev/null || true
    for i in $(seq 1 15); do
     if ! kill -0 "$pid" 2>/dev/null; then break; fi
     sleep 0.2
    done
    if kill -0 "$pid" 2>/dev/null; then
     kill -KILL "$pid" 2>/dev/null || true
     sleep 0.5
    fi
   fi ;;
 esac
 rm -f "$STATE/runner.pid"
 for i in $(seq 1 15); do
  if test ! -e "/sys/class/net/$IFACE"; then break; fi
  sleep 0.2
 done
 test ! -e "/sys/class/net/$IFACE"
 if test -e /sys/class/net/wonder0; then iw dev wonder0 del 2>/dev/null || true; fi
 clean_routes
}

restore_service() {
 if test "$(getprop "init.svc.$1")" != "$2"; then
  case "$2" in
   running) start "$1" ;;
   stopped) stop "$1" ;;
   *) return 1 ;;
  esac
 fi
 for i in $(seq 1 12); do
  if test "$(getprop "init.svc.$1")" = "$2"; then return 0; fi
  sleep 1
 done
 return 1
}

restore_stock_settings() {
 echo "$saved_pci" > "$PCI"
 restore_service vendor.cnd "$saved_cnd"
 restore_service vendor.wifi_hal_legacy "$saved_hal"
 if test "$saved_hal" = running; then
  for i in $(seq 1 16); do
   if test -e /sys/class/net/wlan0; then break; fi
   sleep 0.5
  done
 fi
 setprop sys.xos.exclusive 0
 if test "$saved_scan" = 1; then
  cmd wifi set-scan-always-available enabled
 else
  cmd wifi set-scan-always-available disabled
 fi
 if test "${saved_wifi:-1}" = 1; then
  cmd wifi set-wifi-enabled enabled
  wait_wifi
 else
  cmd wifi set-wifi-enabled disabled
 fi
 test "$(getprop init.svc.vendor.cnd)" = "$saved_cnd"
 test "$(getprop init.svc.vendor.wifi_hal_legacy)" = "$saved_hal"
 test "$(settings get global wifi_on)" = "${saved_wifi:-1}"
 test "$(settings get global wifi_scan_always_enabled)" = "$saved_scan"
 test "$(cat "$PCI")" = "$saved_pci"
}

snapshot() {
 test ! -e "$STATE/saved.env"
 if test -e "$OWLY_APP/state/status"; then
  test "$(cat "$OWLY_APP/state/status")" = STOCK_RESTORED
 fi
 case "$(getprop sys.xos.exclusive)" in ''|0) ;; *) return 1 ;; esac
 stock_files
 wifi=$(settings get global wifi_on)
 scan=$(settings get global wifi_scan_always_enabled)
 cnd=$(getprop init.svc.vendor.cnd)
 hal=$(getprop init.svc.vendor.wifi_hal_legacy)
 pci=$(cat "$PCI")
 case "$wifi" in 0|1) ;; *) return 1 ;; esac
 case "$scan" in 0|1) ;; *) return 1 ;; esac
 case "$cnd:$hal" in running:running|stopped:stopped|running:stopped|stopped:running) ;; *) return 1 ;; esac
 case "$pci" in auto|on) ;; *) return 1 ;; esac
 country=$(cmd wifi get-country-code | sed -n 's/^Wifi Country Code = \([A-Z][A-Z]\)$/\1/p')
 phy=$(iw dev wlan0 info 2>/dev/null | awk '$1 == "wiphy" { print $2 }')
 if test -n "$phy" && test -n "$country"; then
  case "$phy" in ''|*[!0-9]*) return 1 ;; esac
  iw phy "phy$phy" reg get > "$STATE/stock-reg.txt"
 elif test -s "$STATE/stock-reg.txt"; then
  country=$(sed -n 's/^country \([A-Z][A-Z]\):.*/\1/p' "$STATE/stock-reg.txt" | head -n 1)
 else
  return 1
 fi
 test -n "$country"
 grep -q "^country $country:" "$STATE/stock-reg.txt"
 awk '
  {
    rule=$0
    gsub(/[()@,]/, " ", rule)
    sub(/^[[:space:]]+/, "", rule)
    split(rule, f, /[[:space:]]+/)
    if (f[2] == "-" && f[1] + 0 <= 5735 && f[3] + 0 >= 5815 &&
        f[4] + 0 >= 80 && $0 !~ /NO-IR|PASSIVE-SCAN|DFS/) allowed=1
  }
  END { exit !allowed }
 ' "$STATE/stock-reg.txt"
 umask 077
 printf 'saved_wifi=%s\nsaved_scan=%s\nsaved_cnd=%s\nsaved_hal=%s\nsaved_pci=%s\nsaved_boot=%s\n' \
  "$wifi" "$scan" "$cnd" "$hal" "$pci" "$(cat /proc/sys/kernel/random/boot_id)" > "$STATE/snapshot.tmp"
 mv "$STATE/snapshot.tmp" "$STATE/saved.env"
 echo "REGULATORY_COUNTRY=$country WIDTH80_ALLOWED=yes"
}

capture_session_stats() {
 test -d "$W/parameters" || return 0
 (
  umask 077
  if test -s "$STATE/last-session-stats.txt"; then
   cp "$STATE/last-session-stats.txt" "$STATE/prev-session-stats.txt" || true
  fi
  {
   for p in pt_remote_rx_window pt_fw_caps pt_rate_stats pt_ampdu_peak pt_rx_msdu_stats pt_remote_peer_ctl pt_remote_peer_stats pt_tx_stats pt_hdd_tx_stats pt_tx_observe; do
    if test -r "$W/parameters/$p"; then
     printf '== %s ==\n' "$p"
     cat "$W/parameters/$p" || true
     printf '\n'
    fi
   done
  } > "$STATE/last-session-stats.txt"
 ) 2>/dev/null || true
}

restore() {
 if test ! -e "$STATE/saved.env"; then
  stock_files
  setstate STOCK_RESTORED
  return 0
 fi
 . "$STATE/saved.env"
 setstate RESTORING
 if test "${saved_boot:-}" != "$(cat /proc/sys/kernel/random/boot_id)"; then
  stock_environment
  if test ! -e "$W"; then bounded insmod "$STOCK"; fi
  stock_files
  clean_routes
  restore_stock_settings
  mv "$STATE/saved.env" "$STATE/previous-boot.env"
  setstate STOCK_RESTORED
  return 0
 fi
 capture_session_stats
 stop_runner
 if test -e /sys/module/wonder; then
  test -z "$(ls -A /sys/module/wonder/holders)"
  bounded rmmod wonder
 fi
 if test -e /sys/module/houji_wonder_bridge; then
  for release_try in $(seq 1 15); do
   bounded sh -c 'echo 1 > /sys/module/houji_wonder_bridge/parameters/pt_release' || true
   if grep -qx 'armed=0 releasing=0 released=1 result=0' /sys/module/houji_wonder_bridge/parameters/pt_release; then break; fi
   sleep 0.2
  done
  grep -qx 'armed=0 releasing=0 released=1 result=0' /sys/module/houji_wonder_bridge/parameters/pt_release
  if test -e "$W"; then
   grep -qE '^exclusive=[01] published=0 pinned=0 sealed=1$' "$W/parameters/pt_activate"
   test "$(cat "$W/refcnt")" = 0
   test -z "$(ls -A "$W/holders")"
   bounded rmmod qca_cld3_kiwi_v2
  fi
  bounded rmmod houji_wonder_bridge
 fi
 if grep -q " $AMSS " /proc/1/mountinfo; then
  check "$CANDIDATE" "$AMSS"
  umount "$AMSS"
 fi
 check "$ORIG" "$AMSS"
 if test ! -e "$W"; then bounded insmod "$STOCK"; fi
 stock_files
 restore_stock_settings
 mv "$STATE/saved.env" "$STATE/last-restored.env"
 setstate STOCK_RESTORED
}

start_xos() {
 rm -f "$STATE/runner.log"
 (
  for fd in 3 4 5 6 7 8 9; do eval "exec $fd>&-" 2>/dev/null || true; done
  exec "$BB" setsid env \
   HOUJI_OWLY_OUTGOING_VHT=2 \
   HOUJI_OWLY_ACCEPT_ADDBA=2 \
   HOUJI_OWLY_TX_ADDBA=2 \
   HOUJI_OWLY_RX_GAPS="${HOUJI_OWLY_RX_GAPS:-0}" \
   OWLY_COALESCE_US=1000 \
   "$R/owly-xosd" "$IFACE" amsdu ack </dev/null >"$STATE/runner.log" 2>&1
 ) &
 echo $! > "$STATE/runner.pid"
 ready=0
 for i in $(seq 1 300); do
  if grep -q "^OWLY_READY iface=$IFACE " "$STATE/runner.log" 2>/dev/null && \
     ip -6 -o addr show dev "$IFACE" scope link 2>/dev/null | grep -vE 'tentative|dadfailed' | grep -q 'fe80:'; then
   ready=1
   break
  fi
  runner_alive
  sleep 0.05
 done
 test "$ready" = 1
 routes
 setstate XOS_READY
}

enter() {
 if test -e "$STATE/saved.env"; then
  if test -e "$W/parameters/pt_exclusive" && grep -qx Y "$W/parameters/pt_exclusive" && \
     grep -q 'phase=3 error=0 closing=0' "$W/parameters/pt_activate" 2>/dev/null; then
   if runner_alive && \
      ip link show dev "$IFACE" 2>/dev/null | grep -q 'UP' && \
      ip -6 -o addr show dev "$IFACE" scope link 2>/dev/null | grep -vE 'tentative|dadfailed' | grep -q 'fe80:'; then
    routes
    setstate XOS_READY
    return 0
   fi
   stop_runner
   start_xos
   return 0
  fi
  echo "Unhealthy existing xos/owly session; restoring first" >&2
  restore
 fi

 cd "$R"
 sha256sum -c installation.sha256
 grep -qx "$CANDIDATE  firmware/ezif.bin" installation.sha256
 grep -qx "$QCA_SHA  qca_cld3_kiwi_v2-three-findings.ko" installation.sha256
 grep -qx "$BRIDGE_SHA  radio/houji_wonder_bridge.ko" installation.sha256
 grep -qx "$WONDER_SHA  radio/wonder.ko" installation.sha256
 test "$(uname -r)" = 6.1.138-android14-11-g0c3d559bcd85-ab14529422

 snapshot
 trap 'rc=$?; trap - EXIT; if test "$rc" != 0; then echo "ENTER_FAILED=$rc"; restore || setstate RESTORE_FAILED; fi; exit "$rc"' EXIT
 setstate ENTERING
 amss_ok=0
 if test -f "$RADIO/amss20.bin" && check "$CANDIDATE" "$RADIO/amss20.bin" >/dev/null 2>&1; then
  amss_ok=1
 else
  rm -f "$RADIO/amss20.bin"
  cp "$R/firmware/ezif.bin" "$RADIO/amss20.bin"
 fi
 chown system:system "$RADIO/amss20.bin"
 chmod 440 "$RADIO/amss20.bin"
 chcon u:object_r:firmware_file:s0 "$RADIO/amss20.bin"
 if test "$amss_ok" != 1; then
  check "$CANDIDATE" "$RADIO/amss20.bin"
 fi

 cmd wifi set-scan-always-available disabled >/dev/null 2>&1 || true
 cmd wifi set-wifi-enabled disabled >/dev/null 2>&1 || true
 for i in $(seq 1 80); do
  if cmd wifi status 2>/dev/null | grep -q 'Wifi is disabled'; then break; fi
  sleep 0.1
 done
 stop vendor.cnd
 stop vendor.wifi_hal_legacy
 for i in $(seq 1 240); do
  if test "$(getprop init.svc.vendor.cnd):$(getprop init.svc.vendor.wifi_hal_legacy)" = stopped:stopped && test "$(cat "$W/refcnt")" = 0; then break; fi
  if test $((i % 20)) = 0; then stop vendor.wifi_hal_legacy; fi
  sleep 0.05
 done
 test "$(getprop init.svc.vendor.cnd):$(getprop init.svc.vendor.wifi_hal_legacy)" = stopped:stopped
 test "$(cat "$W/refcnt")" = 0
 test -z "$(ls -A "$W/holders")"
 bounded rmmod qca_cld3_kiwi_v2
 test ! -e /dev/wlan

 mount --bind "$RADIO/amss20.bin" "$AMSS"
 "$BB" mount -i -o remount,bind,ro,nosuid,relatime "$RADIO/amss20.bin" "$AMSS"
 insmod "$RADIO/houji_wonder_bridge.ko"
 echo on > "$PCI"
 bounded insmod "$R/qca_cld3_kiwi_v2-three-findings.ko" \
  pt_exclusive=1 \
  pt_sta_coexist=0 \
  pt_zero_serial=1 \
  pt_rx_metadata=1 \
  pt_vht80=1 \
  pt_vht80_2ss=1 \
  pt_rx_capacity_4224=1 \
  pt_rate_observe="${OWLY_RATE_OBSERVE:-0}" \
  pt_tx_observe="${OWLY_TX_OBS:-0}" \
  pt_single_ring="${OWLY_SINGLE_RING:-0}" \
  pt_rx_phy_peer=02:00:00:00:00:01 \
  pt_remote_multi=1 \
  pt_remote_assoc=0 \
  pt_remote_ast_override=1 \
  pt_remote_hash_override=1 \
  pt_remote_reo=2 \
  pt_remote_rx_window=256 \
  pt_tx_ampdu=1 \
  pt_dynamic_rate="${OWLY_DYNAMIC_RATE:-1}" \
  pt_tx_backpressure="${OWLY_TX_BACKPRESSURE:-1}" \
  pt_fw_addba=0 \
  pt_rx_msdu_buf=1 \
  pt_rx_ezif_marked=1 \
  pt_coex_5g_override=0
 test "$(cat "$W/parameters/pt_vht80")" = Y
 test "$(cat "$W/parameters/pt_vht80_2ss")" = Y
 test "$(cat "$W/parameters/pt_rx_capacity_4224")" = Y
 test "$(cat "$W/parameters/pt_sta_coexist")" = N
 test "$(cat "$W/parameters/pt_remote_multi")" = Y
 test "$(cat "$W/parameters/pt_remote_rx_window")" = 256
 test "$(cat "$W/parameters/pt_tx_ampdu")" = Y
 test "$(cat "$W/parameters/pt_rx_msdu_buf")" = Y
 test "$(cat "$W/parameters/pt_rx_ezif_marked")" = Y
 echo on > "$PCI"
 activated=0
 for i in $(seq 1 100); do
  if grep -q '^ready=1 ' "$W/parameters/pt_fw_caps" 2>/dev/null && \
     sh -c 'echo 1 > /sys/module/qca_cld3_kiwi_v2/parameters/pt_activate' 2>/dev/null && \
     grep -q 'published=1' "$W/parameters/pt_activate" 2>/dev/null; then
   activated=1
   break
  fi
  sleep 0.05
 done
 if test "$activated" != 1; then
  echo on > "$PCI"
  bounded sh -c 'echo 1 > /sys/module/qca_cld3_kiwi_v2/parameters/pt_activate'
 fi
 insmod "$RADIO/wonder.ko" synthetic_provider=1
 phy=$(iw phy | awk '$0 == "Wiphy wonder" { found=1; next } found && $1 == "wiphy" && $2 == "index:" { print $3; exit }')
 case "$phy" in ''|*[!0-9]*) return 1 ;; esac
 bounded "$R/wonder-init" create "$phy"
 bounded "$R/wonder-init" cache "$phy" "$(cat /sys/class/net/wonder0/ifindex)" vht80-2ss-mcs9-sgi "$country" 149
 ip link set dev wonder0 mtu 8192
 bounded ip link set dev wonder0 up
 if ! iw dev wonder0 info | grep -q 'channel 149 (5745 MHz)'; then
  bounded iw dev wonder0 set channel 149 HT20
 fi
 iw dev wonder0 info | grep -q 'channel 149 (5745 MHz)'
 grep -q 'phase=3 error=0 closing=0 handle=1 freq=5745 ' "$W/parameters/pt_activate"
 grep -q 'wire_506_passthru_ampdu=1' "$W/parameters/pt_fw_caps"
 start_xos
 trap - EXIT
}

case "${1:-status}" in
 enter) enter ;;
 restore) restore ;;
 status)
  cat "$STATE/status" 2>/dev/null || echo UNKNOWN
  cmd wifi status 2>/dev/null || true ;;
 *) exit 2 ;;
esac
