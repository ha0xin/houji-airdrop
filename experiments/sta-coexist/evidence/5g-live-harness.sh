#!/system/bin/sh
# Temporary 149-only experiment. All module operations run under the native supervisor flock.
set -eu

BASE=/data/local/tmp/houji-buildtest-20261110
ROOT=$BASE/owly
STATE=$ROOT/state
RADIO=$BASE/wideband-ring
NATIVE=/data/local/tmp/xos/native
BB=/data/adb/ksu/bin/busybox
W=/sys/module/qca_cld3_kiwi_v2
AMSS=/vendor/firmware_mnt/image/kiwi/amss20.bin
STOCK=/vendor_dlkm/lib/modules/qca_cld3_kiwi_v2.ko
PCI=/sys/bus/pci/devices/0000:01:00.0/power/control
ORIG=4db7b0e1aa7e4ec071708359c9d0db8d3d7872390cd8e88ffa0bbb30d560bcc8
CANDIDATE=37d409044902fb5e4eb47aff5c543adb0dfd6a56e4e38ff723feb823bc8b522f
NOTE=a3e9bd62646444e1ecbe0f421c95ec46e6caf69a78323efb2f6ebe00e6b2bd8b
RING_SHA=481e3474ea6b42acab5bb5a35b5be7b5e291aa975d7422034ca4e750e857d71c
BA_OBSERVER_SHA=b9f0a77ec9132e57677f83e75e4e3185087b8c4a104878db532a894cb0c4637b
BA_PEER_OBSERVER_SHA=1ad02ff5e62183690c6f1d52a075f5d73001ed82d44c59cd37ec3bf2d5b2c3c4
PEER_CAPS_SHA=739abb094c277b6fa675d9a59de3502019c5344b0a7466b843d43b6eaa2ad7f0
TABLE=10075
IFACE=owly0
driver=
probe=
ping_pid=
paused=0

bounded() { "$BB" timeout -s TERM -k 2 25 "$@"; }
check() { echo "$1  $2" | sha256sum -c -; }
state() { echo "$1" > "$STATE/status"; echo "OWLY_STATE=$1"; }
uptime_s() { read -r seconds rest < /proc/uptime; echo "${seconds%.*}"; }
no_native_bridge() {
 ! ps -A -o ARGS | grep -q '^[a]pp_process /system/bin XosBridge '
}
stock_environment() {
 test ! -e "$NATIVE/saved.env"
 test ! -e "$BASE/state/saved.env"
 test ! -e /sys/module/wonder
 test ! -e /sys/module/houji_wonder_bridge
 test ! -e /sys/class/net/wonder0
 test ! -e /sys/class/net/wondertap0
 test ! -e /sys/class/net/xos0
 test ! -e /sys/class/net/"$IFACE"
 check "$ORIG" "$AMSS"
}

stock_files() {
 stock_environment
 check "$NOTE" "$W/notes/.note.gnu.build-id"
}

wait_wifi() {
 for i in $(seq 1 30); do
  if cmd wifi status | grep -q 'Wifi is connected to'; then
   echo WIFI_CONNECTED
   return 0
  fi
  sleep 1
 done
 echo WIFI_RECONNECT_TIMEOUT
 return 1
}

snapshot() {
 test ! -e "$STATE/saved.env"
 test ! -e "$NATIVE/saved.env"
 test ! -e "$BASE/state/saved.env"
 test "$(cat "$BASE/state/status")" = STOCK_RESTORED
 test "$(getprop sys.xos.exclusive)" = 0
 stock_files
 wifi=$(settings get global wifi_on)
 scan=$(settings get global wifi_scan_always_enabled)
 cnd=$(getprop init.svc.vendor.cnd)
 hal=$(getprop init.svc.vendor.wifi_hal_legacy)
 pci=$(cat "$PCI")
 test "$wifi" = 1
 case "$scan" in 0|1) ;; *) return 1;; esac
 case "$cnd:$hal" in running:running|stopped:stopped|running:stopped|stopped:running) ;; *) return 1;; esac
 case "$pci" in auto|on) ;; *) return 1;; esac
 country=$(cmd wifi get-country-code | sed -n 's/^Wifi Country Code = \([A-Z][A-Z]\)$/\1/p')
 phy=$(iw dev wlan0 info | awk '$1 == "wiphy" { print $2 }')
 case "$phy" in ''|*[!0-9]*) return 1;; esac
 test -n "$country"
 iw phy "phy$phy" reg get > "$STATE/stock-reg.txt"
 grep -q "^country $country:" "$STATE/stock-reg.txt"
 # Require a non-passive, non-DFS rule covering the whole 149-161 group.
 # The candidate kernel independently validates the live country and each channel.
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
 echo "REGULATORY_COUNTRY=$country STOCK_PHY=$phy WIDTH80_ALLOWED=yes"
 printf 'saved_scan=%s\nsaved_cnd=%s\nsaved_hal=%s\nsaved_pci=%s\nsaved_boot=%s\n' \
  "$scan" "$cnd" "$hal" "$pci" "$(cat /proc/sys/kernel/random/boot_id)" > "$STATE/snapshot.tmp"
 mv "$STATE/snapshot.tmp" "$STATE/saved.env"
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
 if test "$saved_scan" = 1; then
  cmd wifi set-scan-always-available enabled
 else
  cmd wifi set-scan-always-available disabled
 fi
 cmd wifi set-wifi-enabled enabled
 setprop sys.xos.exclusive 0
 wait_wifi
 test "$(getprop init.svc.vendor.cnd)" = "$saved_cnd"
 test "$(getprop init.svc.vendor.wifi_hal_legacy)" = "$saved_hal"
 test "$(settings get global wifi_on)" = 1
 test "$(settings get global wifi_scan_always_enabled)" = "$saved_scan"
 test "$(cat "$PCI")" = "$saved_pci"
}

clean_routes() {
 # Delete only exact per-run rule selectors. Interface deletion takes its routes.
 if test -f "$STATE/route-ip"; then
  ip6=$(cat "$STATE/route-ip")
  ip -6 rule del priority 10870 to fe80::/64 oif "$IFACE" uidrange 0-0 lookup "$TABLE" 2>/dev/null || true
  ip -6 rule del priority 10871 to ff00::/8 oif "$IFACE" uidrange 0-0 lookup "$TABLE" 2>/dev/null || true
  ip -6 rule del priority 10872 from "$ip6/128" lookup "$TABLE" 2>/dev/null || true
  rm "$STATE/route-ip"
 fi
}

restore() {
 if test ! -e "$STATE/saved.env"; then
  stock_files
  state STOCK_RESTORED
  return
 fi
 . "$STATE/saved.env"
 state RESTORING
 if test "$saved_boot" != "$(cat /proc/sys/kernel/random/boot_id)"; then
  stock_environment
  check d3fbe4f1b62f8c3533609945b617508e8a585660f5640aeeda08f8508416137b "$STOCK"
  if test ! -e "$W"; then bounded insmod "$STOCK"; fi
  stock_files
  test ! -e "$STATE/route-ip" || rm "$STATE/route-ip"
  restore_stock_settings
  mv "$STATE/saved.env" "$STATE/previous-boot.env"
  state STOCK_RESTORED
  return
 fi
 clean_routes
 test ! -e /sys/class/net/"$IFACE"
 test ! -e /sys/class/net/xos0
 if test -e /sys/class/net/wonder0; then iw dev wonder0 del; fi
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
   cat "$W/parameters/pt_remote_peer_stats" > "$STATE/last-peer-teardown.txt"
   echo "PT_REMOTE_PEER_TEARDOWN=$(cat "$STATE/last-peer-teardown.txt")"
   if test "$(cat "$W/parameters/pt_sta_coexist" 2>/dev/null || echo N)" = Y; then
    {
     echo "=== CMD_WIFI_STATUS_AFTER_PT_RELEASE ==="
     cmd wifi status || true
     echo "=== IW_WLAN0_LINK_AFTER_PT_RELEASE ==="
     iw dev wlan0 link || true
    } > "$STATE/coexist-wlan0-after-pt-release.txt"
    stop vendor.cnd
    cmd wifi set-scan-always-available disabled
    cmd wifi set-wifi-enabled disabled
    stop vendor.wifi_hal_legacy
    sleep 2
    for i in $(seq 1 12); do
     if test "$(getprop init.svc.vendor.cnd):$(getprop init.svc.vendor.wifi_hal_legacy)" = stopped:stopped && test "$(cat "$W/refcnt")" = 0; then break; fi
     stop vendor.wifi_hal_legacy
     sleep 1
    done
    test "$(getprop init.svc.vendor.cnd):$(getprop init.svc.vendor.wifi_hal_legacy)" = stopped:stopped
   fi
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
 state STOCK_RESTORED
}

own_child() {
 # A zombie has already stopped. Let the caller reap it with wait.
 awk -v parent="$$" '
  $1 == "State:" { active = ($2 != "Z" && $2 != "X") }
  $1 == "PPid:" { child = ($2 == parent) }
  END { exit !(active && child) }
 ' "/proc/$1/status" 2>/dev/null
}

stop_worker() {
 child=$1
 test -n "$child" || return 0
 if own_child "$child"; then kill -TERM "$child" 2>/dev/null || true; fi
 for i in $(seq 1 5); do
  if ! own_child "$child"; then break; fi
  sleep 1
 done
 if own_child "$child"; then
  kill -KILL "$child" 2>/dev/null || true
  sleep 1
 fi
 if own_child "$child"; then echo "WORKER_STOP_TIMEOUT pid=$child"; return 1; fi
 wait "$child" 2>/dev/null || true
}

stop_workers() {
 stop_worker "$ping_pid"
 ping_rc=$?
 stop_worker "$probe"
 probe_rc=$?
 stop_worker "$driver"
 driver_rc=$?
 test "$ping_rc:$probe_rc:$driver_rc" = 0:0:0
}

collect() {
 cat "$W/parameters/pt_activate" "$W/parameters/pt_rate_stats"
 if test "$ba_mode" != reference; then
  echo "BA_OBSERVER_PARAM=$(cat "$W/parameters/pt_ba_observe")"
  cat "$W/parameters/pt_ba_stats"
 fi
 if test "$ba_mode" = peer; then
  echo "BA_PEER_CONFIGURED=$(cat "$W/parameters/pt_ba_peer")"
  cat "$W/parameters/pt_ba_peer_stats"
 fi
 ip -s link show dev "$IFACE"
 ip -s link show dev wonder0
 tc -s qdisc show dev wonder0
 tc -s qdisc show dev "$IFACE"
}

enter() {
 cd "$ROOT"
 sha256sum -c SHA256SUMS
 app_sha=$(cat "$ROOT/outgoing-vht.sha")
 test "${#app_sha}" = 64
 case "$app_sha" in *[!0-9a-f]*) return 1;; esac
 check "$app_sha" "$ROOT/houji-owly-outgoing-vht"
 echo "OUTGOING_VHT_BUILD_SHA=$app_sha"
 winit_sha=$(cat "$ROOT/wonder-init-2ss.sha")
 test "${#winit_sha}" = 64
 case "$winit_sha" in *[!0-9a-f]*) return 1;; esac
 check "$winit_sha" "$ROOT/wonder-init-2ss"
 echo "WONDER_INIT_2SS_SHA=$winit_sha"
 tf_sha=$(cat "$ROOT/three-findings.sha")
 test "${#tf_sha}" = 64
 case "$tf_sha" in *[!0-9a-f]*) return 1;; esac
 check "$tf_sha" "$ROOT/three-findings.env"
 . "$ROOT/three-findings.env"
 case "$tf_remote_reo" in 0|1|2) ;; *) return 1;; esac
 for v in "$tf_remote_assoc" "$tf_remote_ast_override" "$tf_remote_hash_override" "$tf_fw_addba" "$tf_tx_ampdu" "$tf_dynamic_rate" "$tf_sta_coexist"; do
  case "$v" in 0|1) ;; *) return 1;; esac
 done
 echo "THREE_FINDINGS_ENV assoc=$tf_remote_assoc reo=$tf_remote_reo tx_ampdu=$tf_tx_ampdu dyn_rate=$tf_dynamic_rate sta_coexist=$tf_sta_coexist"
 check "$CANDIDATE" "/data/local/tmp/houji-buildtest-20261110/owly/ezif-official-37d40904.bin"
 check 9a8345d87ec1186a29b413935b4efdbd38566e7e22374e70a7a85510b2970e9a "$RADIO/houji_wonder_bridge.ko"
 qca="$ROOT/qca_cld3_kiwi_v2-remote-peer.ko"
 qca_sha=$(cat "$ROOT/remote-peer.sha")
 test "${#qca_sha}" = 64
 case "$qca_sha" in *[!0-9a-f]*) return 1;; esac
 case "$ba_mode" in
  off|on)
   qca="$ROOT/qca_cld3_kiwi_v2-observer.ko"
   qca_sha=$BA_OBSERVER_SHA
   ;;
  peer)
   qca="$ROOT/qca_cld3_kiwi_v2-peer-observer.ko"
   qca_sha=$BA_PEER_OBSERVER_SHA
   ;;
  caps)
   qca="$ROOT/qca_cld3_kiwi_v2-peer-caps.ko"
   qca_sha=$PEER_CAPS_SHA
   ;;
 esac
 check "$qca_sha" "$qca"
 printf '%s %s\n' "$qca_sha" "$qca" > "$STATE/candidate.tmp"
 mv "$STATE/candidate.tmp" "$STATE/candidate.sha"
 echo "WLAN_BUILD_SHA=$qca_sha BA_OBSERVER_REQUEST=$ba_mode"
 check 9d60988f896669604ff2e8cdfa1ba91a7047c1ddd2210b1fa494f506f5eb039c "$RADIO/wonder.ko"
 test "$(uname -r)" = 6.1.138-android14-11-g0c3d559bcd85-ab14529422
 snapshot
 state ENTERING
 setprop sys.xos.exclusive 1
 stop vendor.cnd
 cmd wifi set-scan-always-available disabled
 cmd wifi set-wifi-enabled disabled
 stop vendor.wifi_hal_legacy
 sleep 3
 for i in $(seq 1 10); do
  if test "$(getprop init.svc.vendor.cnd):$(getprop init.svc.vendor.wifi_hal_legacy)" = stopped:stopped; then break; fi
  stop vendor.wifi_hal_legacy
  sleep 1
 done
 test "$(getprop init.svc.vendor.cnd):$(getprop init.svc.vendor.wifi_hal_legacy)" = stopped:stopped
 test "$(cat "$W/refcnt")" = 0
 test -z "$(ls -A "$W/holders")"
 bounded rmmod qca_cld3_kiwi_v2
 test ! -e /dev/wlan
 mount --bind "/data/local/tmp/houji-buildtest-20261110/owly/ezif-official-37d40904.bin" "$AMSS"
 "$BB" mount -i -o remount,bind,ro,nosuid,relatime "/data/local/tmp/houji-buildtest-20261110/owly/ezif-official-37d40904.bin" "$AMSS"
 insmod "$RADIO/houji_wonder_bridge.ko"
 vht=0
 vht_2ss=0
 case "$profile" in
  vht80|vht80-sgi) vht=1 ;;
  vht80-2ss-sgi|vht80-2ss-mcs8-sgi|vht80-2ss-mcs9-sgi) vht=1; vht_2ss=1 ;;
 esac
 pt_excl=$(if test "$tf_sta_coexist" = 1; then echo 0; else echo 1; fi)
 set -- pt_exclusive="$pt_excl" pt_sta_coexist="$tf_sta_coexist" pt_zero_serial=1 pt_rx_metadata=1 pt_vht80="$vht" pt_rate_observe=1 pt_single_ring=1
 case "$ba_mode" in
  on) set -- "$@" pt_ba_observe=1 ;;
  peer) set -- "$@" pt_ba_observe=1 "pt_ba_peer=$peer" ;;
  caps) set -- "$@" pt_peer_caps_observe=1 ;;
 esac
 set -- "$@" pt_rx_capacity_4224=1 "pt_rx_phy_peer=$peer" pt_vht80_2ss="$vht_2ss"
 set -- "$@" pt_remote_assoc="$tf_remote_assoc" pt_remote_ast_override="$tf_remote_ast_override" pt_remote_hash_override="$tf_remote_hash_override" pt_remote_reo="$tf_remote_reo" pt_fw_addba="$tf_fw_addba" pt_tx_ampdu="$tf_tx_ampdu" pt_dynamic_rate="$tf_dynamic_rate"
 if test "$tf_remote_assoc" = 1; then
  set -- "$@" "pt_remote_mac=$peer"
 fi
 bounded insmod "$qca" "$@" pt_rx_msdu_buf=1 pt_remote_rx_window=64 pt_coex_5g_override=1 pt_rx_ezif_marked=1
 test "$(cat "$W/parameters/pt_rx_phy_peer")" = "$peer"
 echo RX_PHY_PEER_READBACK=Y
 test "$(cat "$W/parameters/pt_rx_capacity_4224")" = Y
 echo RX_CAPACITY_LOAD_READBACK=Y
 expected_2ss=$(if test "$vht_2ss" = 1; then echo Y; else echo N; fi)
 test "$(cat "$W/parameters/pt_vht80_2ss")" = "$expected_2ss"
 echo "VHT80_2SS_LOAD_READBACK=$expected_2ss"
 expected_coex=$(if test "$tf_sta_coexist" = 1; then echo Y; else echo N; fi)
 test "$(cat "$W/parameters/pt_sta_coexist")" = "$expected_coex"
 expected_assoc=$(if test "$tf_remote_assoc" = 1; then echo Y; else echo N; fi)
 test "$(cat "$W/parameters/pt_remote_assoc")" = "$expected_assoc"
 expected_ast=$(if test "$tf_remote_ast_override" = 1; then echo Y; else echo N; fi)
 test "$(cat "$W/parameters/pt_remote_ast_override")" = "$expected_ast"
 expected_hash=$(if test "$tf_remote_hash_override" = 1; then echo Y; else echo N; fi)
 test "$(cat "$W/parameters/pt_remote_hash_override")" = "$expected_hash"
 expected_ampdu=$(if test "$tf_tx_ampdu" = 1; then echo Y; else echo N; fi)
 test "$(cat "$W/parameters/pt_tx_ampdu")" = "$expected_ampdu"
 case "$ba_mode" in
  off) test "$(cat "$W/parameters/pt_ba_observe")" = N ;;
  on|peer) test "$(cat "$W/parameters/pt_ba_observe")" = Y ;;
  caps)
   test "$(cat "$W/parameters/pt_ba_observe")" = N
   test "$(cat "$W/parameters/pt_ba_peer")" = 0
   test "$(cat "$W/parameters/pt_peer_caps_observe")" = Y
   caps=$(cat "$W/parameters/pt_peer_caps")
   echo "PEER_CAPS_BEFORE $caps"
   test "$caps" = 'schema=1 enabled=1 sampled=0 create_conf=0 sync_delete=0 fw_unmap_conf=0 dp_unmap_conf=0'
   ;;
 esac
 if test "$ba_mode" = peer; then
  test "$(cat "$W/parameters/pt_ba_peer")" = 1
 fi
 if test "$tf_sta_coexist" = 1; then
  . "$STATE/saved.env"
  restore_service vendor.cnd "$saved_cnd"
  restore_service vendor.wifi_hal_legacy "$saved_hal"
  cmd wifi set-scan-always-available disabled
  cmd wifi set-wifi-enabled enabled
  wait_wifi
  network_ready=0
  for network_try in $(seq 1 30); do
   if ip -4 addr show dev wlan0 | grep -q 'inet ' && ip -4 route show table all dev wlan0 | grep -q '^default '; then
    network_ready=1
    break
   fi
   sleep 1
  done
  {
   echo "=== CMD_WIFI_STATUS_BEFORE_PT ==="
   cmd wifi status
   echo "=== IW_WLAN0_LINK_BEFORE_PT ==="
   iw dev wlan0 link
   echo "=== IP_ADDR_WLAN0_BEFORE_PT ==="
   ip -4 addr show dev wlan0
   echo "=== IP_ROUTE_WLAN0_BEFORE_PT ==="
   ip -4 route show dev wlan0
  } > "$STATE/coexist-wlan0-before.txt"
  echo "COEXIST_NETWORK_READY=$network_ready"
  test "$network_ready" = 1
  grep -q 'Connected to ' "$STATE/coexist-wlan0-before.txt"
  echo COEXIST_WLAN0_CONNECTED=1
  echo on > "$PCI"
  pt_ok=0
  for i in $(seq 1 10); do
   if bounded sh -c 'echo 1 > /sys/module/qca_cld3_kiwi_v2/parameters/pt_activate' 2>/dev/null; then
    pt_ok=1
    break
   fi
   sleep 1
  done
  test "$pt_ok" = 1
 else
  sleep 2
  echo on > "$PCI"
  bounded sh -c 'echo 1 > /sys/module/qca_cld3_kiwi_v2/parameters/pt_activate'
 fi
 cat "$W/parameters/pt_fw_caps" > "$STATE/fw-caps.txt"
 echo "PT_FW_CAPS=$(cat "$STATE/fw-caps.txt")"
 insmod "$RADIO/wonder.ko" synthetic_provider=1
 phy=$(iw phy | awk '$0 == "Wiphy wonder" { found=1; next } found && $1 == "wiphy" && $2 == "index:" { print $3; exit }')
 case "$phy" in ''|*[!0-9]*) return 1;; esac
 bounded ./wonder-init-2ss create "$phy"
 bounded ./wonder-init-2ss cache "$phy" "$(cat /sys/class/net/wonder0/ifindex)" "$profile" "$country" 149
 # Allow the complete radiotap+MPDU length on the monitor.
 if test "$aggregation" = amsdu; then ip link set dev wonder0 mtu 8192; fi
 bounded ip link set dev wonder0 up
 if ! iw dev wonder0 info | grep -q 'channel 149 (5745 MHz)'; then
  bounded iw dev wonder0 set channel 149 HT20
 fi
 info=$(iw dev wonder0 info)
 echo "$info"
 echo "$info" | grep -q 'channel 149 (5745 MHz)'
 live=$(cat "$W/parameters/pt_activate")
 echo "$live"
 echo "$live" | grep -q 'phase=3 error=0 closing=0 handle=1 freq=5745 '
 test -e /sys/class/net/wondertap0/address
 cat "$W/parameters/pt_remote_peer_stats" > "$STATE/peer-setup.txt"
 echo "PT_REMOTE_PEER_SETUP=$(cat "$STATE/peer-setup.txt")"
 if test "$tf_sta_coexist" = 1; then
  {
   echo "=== IW_WLAN0_LINK_DURING_WONDER ==="
   iw dev wlan0 link
   echo "=== IW_WONDER0_INFO ==="
   iw dev wonder0 info
  } > "$STATE/coexist-wlan0-during-wonder.txt"
  grep -q 'Connected to ' "$STATE/coexist-wlan0-during-wonder.txt"
 fi
}

routes() {
 # The temporary interface and the exact selectors must be ours.
 ! ip -6 rule show | grep -qE '^1087[012]:'
 test -z "$(ip -6 route show table "$TABLE" 2>/dev/null || true)"
 addr=$(ip -6 -o addr show dev "$IFACE" scope link | awk '$3=="inet6"{sub(/\/.*/, "", $4);print $4}')
 case "$addr" in fe80:*) ;; *) return 1;; esac
 echo "$addr" > "$STATE/route-ip"
 ip -6 route add fe80::/64 dev "$IFACE" table "$TABLE"
 ip -6 route add ff00::/8 dev "$IFACE" table "$TABLE"
 ip -6 rule add priority 10870 to fe80::/64 oif "$IFACE" uidrange 0-0 lookup "$TABLE"
 ip -6 rule add priority 10871 to ff00::/8 oif "$IFACE" uidrange 0-0 lookup "$TABLE"
 ip -6 rule add priority 10872 from "$addr/128" lookup "$TABLE"
}

finish_locked() {
 rc=$?
 trap - EXIT TERM INT HUP
 set +e
 if test -n "$driver" && kill -0 "$driver" 2>/dev/null; then collect > "$STATE/after.txt" 2>&1; fi
 stop_workers
 workers_stopped=$?
 if test "$workers_stopped" != 0; then state RESTORE_FAILED; exit "$workers_stopped"; fi
 # Re-enable errexit inside the subshell so a failed guard cannot permit a later unload.
 (set -e; restore)
 restored=$?
 if test "$restored" != 0; then
  state RESTORE_FAILED
  exit "$restored"
 fi
 exit "$rc"
}

run_phase() {
 HOUJI_OWLY_OUTGOING_VHT="$outgoing_vht" HOUJI_OWLY_ACCEPT_ADDBA="$accept_addba" HOUJI_OWLY_TX_ADDBA="$tx_addba" ./houji-owly-outgoing-vht "$peer" "$aggregation" "$ack" > "$STATE/runner.log" 2>&1 &
 driver=$!
 for i in $(seq 1 10); do
  if grep -q '^OWLY_READY ' "$STATE/runner.log"; then break; fi
  kill -0 "$driver"
  sleep 1
 done
 grep -q '^OWLY_READY ' "$STATE/runner.log"
 if test "$vht_amsdu" = 1; then
  grep -q '^OWLY_READY .* vht_amsdu=true$' "$STATE/runner.log"
 fi
 test -e /sys/class/net/"$IFACE"/tun_flags
 ip -6 -o addr show dev "$IFACE" scope link | grep -q fe80:
 if test "$qlen" != 500; then ip link set dev "$IFACE" txqueuelen "$qlen"; fi
 test "$(cat /sys/class/net/"$IFACE"/tx_queue_len)" = "$qlen"
 routes
 cat "$W/parameters/pt_rx_phy_stats" > "$phase_dir/before"
 cat "$W/parameters/pt_rate_stats" > "$phase_dir/rate-before"
 cat "$W/parameters/pt_ampdu_peak" > "$phase_dir/ampdu-before"
 cat "$W/parameters/pt_remote_peer_stats" > "$phase_dir/peer-before"
 ping_pid=
 if test "$tf_sta_coexist" = 1; then
  iw dev wlan0 link > "$phase_dir/wlan0-link-before"
  gw=$(ip -4 route show table all dev wlan0 | awk '$1=="default"{print $3; exit}')
  if test -z "$gw"; then
   gw=$(ip -4 neigh show dev wlan0 | awk 'NR==1{print $1}')
  fi
  echo "WLAN0_PING_TARGET=$gw" > "$phase_dir/wlan0-ping-target"
  if test -n "$gw"; then
   ping -I wlan0 -i 0.2 -W 1 "$gw" > "$phase_dir/wlan0-ping.log" 2>&1 &
   ping_pid=$!
  fi
 fi
 set -- "$count" "$size"
 if test "$mss" != 0; then set -- "$@" "$mss"; fi
 ./owly-hashprobe "$@" > "$STATE/server.log" 2>&1 &
 probe=$!
 echo "$seq" > "$STATE/phy-ready-$token"
 state OWLY_READY
 deadline=$(($(uptime_s) + (count == 6 ? 440 : 150)))
 while test ! -e "$STATE/stop-$token"; do
  kill -0 "$driver"
  if test -e "$phase_dir/abort"; then break; fi
  if ! kill -0 "$probe" 2>/dev/null; then wait "$probe"; probe=; break; fi
  test "$(uptime_s)" -lt "$deadline"
  sleep 1
 done
 if test -n "$ping_pid"; then
  kill -INT "$ping_pid" 2>/dev/null || true
  wait "$ping_pid" 2>/dev/null || true
  ping_pid=
 fi
 if test "$tf_sta_coexist" = 1; then
  iw dev wlan0 link > "$phase_dir/wlan0-link-after"
 fi
 test ! -e "$STATE/stop-$token"
 cat "$W/parameters/pt_rx_phy_stats" > "$phase_dir/after"
 cat "$W/parameters/pt_rate_stats" > "$phase_dir/rate-after"
 cat "$W/parameters/pt_ampdu_peak" > "$phase_dir/ampdu-after"
 cat "$W/parameters/pt_remote_peer_stats" > "$phase_dir/peer-after"
 stop_workers
 probe= driver=
 clean_routes
 test ! -e /sys/class/net/"$IFACE"
 cp "$STATE/runner.log" "$STATE/server.log" "$phase_dir/"
 : > "$phase_dir/done"
}

run_locked() {
 token=$1 profile=$2 aggregation=$3 ack=$4 peer=$5 count=$6 size=$7 mss=$8 qlen=$9 ba_mode=${10} vht_amsdu=${11:-0}
 test ! -e "$STATE/saved.env"
 no_native_bridge
 trap finish_locked EXIT
 trap 'exit 143' TERM HUP
 trap 'exit 130' INT
 enter

 phy_dir="$STATE/phy-$token"
 mkdir "$phy_dir"
 seq=1
 while test ! -e "$STATE/stop-$token"; do
  state PHY_IDLE
  lease_deadline=$(($(uptime_s) + 600))
  phase_dir="$phy_dir/$seq"
  mkdir "$phase_dir"
  while test ! -e "$phase_dir/request"; do
   if test -e "$STATE/stop-$token"; then return 0; fi
   test "$(uptime_s)" -lt "$lease_deadline"
   sleep 1
  done
  read -r outgoing_vht accept_addba tx_addba count size extra < "$phase_dir/request"
  test -z "$extra"
  case "$outgoing_vht:$accept_addba:$tx_addba:$count:$size" in
   0:0:0:2:1048576|2:0:0:2:1048576|2:2:0:2:1048576|2:2:1:2:1048576|2:2:2:2:1048576|0:0:0:2:16777216|2:0:0:2:16777216|2:2:0:2:16777216|2:2:1:2:16777216|2:2:2:2:16777216|2:2:0:6:67108864|2:2:1:6:67108864|2:2:2:6:67108864) ;;
   *) return 2 ;;
  esac
  run_phase
  seq=$((seq + 1))
 done

}

native_pid_matches() {
 case "$(tr '\0' ' ' < "/proc/$1/cmdline")" in
  "sh $BASE/bridge-supervisor149.sh locked "|"/system/bin/sh $BASE/bridge-supervisor149.sh locked ") return 0;;
  *) return 1;;
 esac
}

suspend_native() {
 test ! -e "$NATIVE/saved.env"
 test ! -e "$BASE/state/saved.env"
 stock_files
 p=$(cat "$NATIVE/supervisor.pid")
 case "$p" in ''|*[!0-9]*) return 1;; esac
 test -r "/proc/$p/cmdline"
 native_pid_matches "$p"
 paused=1
 kill -TERM "$p"
 for i in $(seq 1 15); do
  if ! kill -0 "$p" 2>/dev/null && no_native_bridge; then return 0; fi
  sleep 1
 done
 return 1
}

resume_native() {
 stock_files
 test ! -e "$STATE/saved.env"
 test ! -e "$NATIVE/saved.env"
 test ! -e "$BASE/state/saved.env"
 if no_native_bridge; then
  "$BB" setsid sh "$BASE/bridge-supervisor149.sh" </dev/null >> "$BASE/native149-bridge.log" 2>&1 9>&- &
 fi
 for i in $(seq 1 12); do
  if service check com.domestic.xoscomm.IDomesticXOsComm/default | grep -q ': found'; then
   p=$(cat "$NATIVE/supervisor.pid")
   native_pid_matches "$p"
   echo NATIVE149_MCS3_BINDER_FOUND
   return 0
  fi
  sleep 1
 done
 echo NATIVE149_RESTART_TIMEOUT
 return 1
}

job_lock() {
 exec 9>"$ROOT/job.lock"
 "$BB" flock -n 9 9>&9
}

finish_job() {
 job_rc=$?
 trap - EXIT TERM INT HUP
 set +e
 if test "$paused" = 1; then
  # The regular locked EXIT handler restored. If it failed, reacquire the
  # same lock before retrying; only restore a recorded, owned transition.
  if test -e "$STATE/saved.env"; then
   "$BB" flock -n "$NATIVE/supervisor.lock" sh "$0" recover-locked 9>&-
   recovery_rc=$?
  else
   recovery_rc=0
  fi
  if test "$recovery_rc" = 0; then
   (set -e; stock_files; wait_wifi; resume_native)
   recovery_rc=$?
  fi
  if test "$recovery_rc" != 0; then
   state RESTORE_FAILED
   job_rc=$recovery_rc
  else
   state STOCK_RESTORED
  fi
 else
  (set -e; stock_files; service check com.domestic.xoscomm.IDomesticXOsComm/default | grep -q ': found$')
  preflight_stock=$?
  if test "$preflight_stock" = 0; then state STOCK_RESTORED; fi
 fi
 echo "$job_rc" > "$STATE/result"
 exit "$job_rc"
}

run_job() {
 job_lock
 token=$1
 for file in runner.log server.log before.txt after.txt; do rm -f "$STATE/$file"; done
 rm -f "$STATE/stop-$token" "$STATE/result" "$STATE/candidate.sha" "$STATE/purpose"
 printf 'capacity\n' > "$STATE/purpose"
 echo "$token" > "$STATE/token"
 state PREFLIGHT
 trap finish_job EXIT
 trap 'exit 143' TERM HUP
 trap 'exit 130' INT
 suspend_native
 # Each mutating step and the trial itself have deadlines. flock is
 # nonblocking so a foreign owner cannot stall recovery or be displaced.
 "$BB" flock -n "$NATIVE/supervisor.lock" sh "$0" locked "$@" 9>&-
}

test "$(id -u)" = 0
test "$(readlink /proc/self/ns/mnt)" = "$(readlink /proc/1/ns/mnt)"
umask 077
case "${1:-}" in
 run)
  shift
  test "$#" = 12
  test "${12}" = 0
  case "$2" in vht80-sgi|vht80-2ss-sgi|vht80-2ss-mcs8-sgi|vht80-2ss-mcs9-sgi) ;; *) exit 2 ;; esac
  test "$3:$4:$8:$9:${10}:${11}" = amsdu:ack:1160:500:reference:0
  test "$6:$7" = 0:0
  case "${11:-0}" in
   0) ;;
   1) test "$2:$3:$4:$8:$9:${10}" = vht80-sgi:amsdu:ack:1160:500:reference ;;
   *) exit 2 ;;
  esac
  case "${10}" in
   reference) ;;
   off|on|peer) test "$2:$3:$4:$8:$9" = vht80-sgi:amsdu:ack:1160:500 ;;
   *) exit 2 ;;
  esac
  case "$8:$9" in 0:500|1160:500|1160:2048) ;; *) exit 2;; esac
  case "$1" in ''|*[!a-zA-Z0-9-]*) exit 2;; esac
  case "$2:$3:$4" in
   ht20:single:ack|vht80:single:ack|vht80:amsdu:ack|vht80-sgi:amsdu:ack|vht80-sgi:amsdu:noack|vht80-2ss-sgi:amsdu:ack|vht80-2ss-mcs8-sgi:amsdu:ack|vht80-2ss-mcs9-sgi:amsdu:ack) ;;
   *) exit 2;;
  esac
  echo "$5" | grep -Eq '^([0-9a-f]{2}:){5}[0-9a-f]{2}$'
  run_job "$@"
  ;;
 caps)
  test "$#" = 2
  case "$2" in ''|*[!a-zA-Z0-9-]*) exit 2;; esac
  run_job "$2" vht80-sgi single ack - 0 0 0 500 caps
  ;;
 locked) shift; run_locked "$@" ;;
 recover)
  job_lock
  "$BB" flock -n "$NATIVE/supervisor.lock" sh "$0" recover-locked 9>&-
  resume_native
  ;;
 recover-locked)
  trap 'rc=$?; trap - EXIT; if test "$rc" != 0; then state RESTORE_FAILED; fi; exit "$rc"' EXIT
  # Recovery is valid only after the session has stopped and owly0 disappeared.
  restore
  ;;
 stop)
  case "${2:-}" in ''|*[!a-zA-Z0-9-]*) exit 2;; esac
  : > "$STATE/stop-$2"
  ;;
 *) exit 2;;
esac
