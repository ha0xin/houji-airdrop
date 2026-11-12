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
CANDIDATE=13f01cb46efbe498c854352bfc253fa3665c14a14c37a0a6ed6a609a48a8709c
NOTE=a3e9bd62646444e1ecbe0f421c95ec46e6caf69a78323efb2f6ebe00e6b2bd8b
RING_SHA=481e3474ea6b42acab5bb5a35b5be7b5e291aa975d7422034ca4e750e857d71c
BA_OBSERVER_SHA=b9f0a77ec9132e57677f83e75e4e3185087b8c4a104878db532a894cb0c4637b
BA_PEER_OBSERVER_SHA=1ad02ff5e62183690c6f1d52a075f5d73001ed82d44c59cd37ec3bf2d5b2c3c4
PEER_CAPS_SHA=739abb094c277b6fa675d9a59de3502019c5344b0a7466b843d43b6eaa2ad7f0
TABLE=10075
IFACE=owly0
driver=
probe=
paused=0

bounded() { "$BB" timeout -s TERM -k 2 25 "$@"; }
check() { echo "$1  $2" | sha256sum -c -; }
state() { echo "$1" > "$STATE/status"; echo "OWLY_STATE=$1"; }
uptime_s() { read -r seconds rest < /proc/uptime; echo "${seconds%.*}"; }
no_native_bridge() {
 ! ps -A -o ARGS | grep -q '^[a]pp_process /system/bin XosBridge '
}
stock_files() {
 test ! -e "$NATIVE/saved.env"
 test ! -e "$BASE/state/saved.env"
 test ! -e /sys/module/wonder
 test ! -e /sys/module/houji_wonder_bridge
 test ! -e /sys/class/net/wonder0
 test ! -e /sys/class/net/wondertap0
 test ! -e /sys/class/net/xos0
 test ! -e /sys/class/net/"$IFACE"
 check "$ORIG" "$AMSS"
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

restore() {
 if test ! -e "$STATE/saved.env"; then
  stock_files
  state STOCK_RESTORED
  return
 fi
 . "$STATE/saved.env"
 state RESTORING
 if test "$saved_boot" != "$(cat /proc/sys/kernel/random/boot_id)"; then
  stock_files
  mv "$STATE/saved.env" "$STATE/previous-boot.env"
  state STOCK_RESTORED
  return
 fi
 # Delete only exact per-run rule selectors. Interface deletion takes its routes.
 if test -f "$STATE/route-ip"; then
  ip6=$(cat "$STATE/route-ip")
  ip -6 rule del priority 10870 to fe80::/64 oif "$IFACE" uidrange 0-0 lookup "$TABLE" 2>/dev/null || true
  ip -6 rule del priority 10871 to ff00::/8 oif "$IFACE" uidrange 0-0 lookup "$TABLE" 2>/dev/null || true
  ip -6 rule del priority 10872 from "$ip6/128" lookup "$TABLE" 2>/dev/null || true
  rm "$STATE/route-ip"
 fi
 test ! -e /sys/class/net/"$IFACE"
 test ! -e /sys/class/net/xos0
 if test -e /sys/class/net/wonder0; then iw dev wonder0 del; fi
 if test -e /sys/module/wonder; then
  test -z "$(ls -A /sys/module/wonder/holders)"
  bounded rmmod wonder
 fi
 if test -e /sys/module/houji_wonder_bridge; then
  bounded sh -c 'echo 1 > /sys/module/houji_wonder_bridge/parameters/pt_release'
  grep -qx 'armed=0 releasing=0 released=1 result=0' /sys/module/houji_wonder_bridge/parameters/pt_release
  if test -e "$W"; then
   grep -qx 'exclusive=1 published=0 pinned=0 sealed=1' "$W/parameters/pt_activate"
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
 echo "$saved_pci" > "$PCI"
 if test "$saved_cnd" = running; then start vendor.cnd; fi
 if test "$saved_hal" = running; then start vendor.wifi_hal_legacy; fi
 if test "$saved_scan" = 1; then
  cmd wifi set-scan-always-available enabled
 else
  cmd wifi set-scan-always-available disabled
 fi
 cmd wifi set-wifi-enabled enabled
 setprop sys.xos.exclusive 0
 mv "$STATE/saved.env" "$STATE/last-restored.env"
 state STOCK_RESTORED
 wait_wifi
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
 stop_worker "$probe"
 probe_rc=$?
 stop_worker "$driver"
 driver_rc=$?
 test "$probe_rc:$driver_rc" = 0:0
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
 check "$CANDIDATE" "$RADIO/amss20.bin"
 check 9a8345d87ec1186a29b413935b4efdbd38566e7e22374e70a7a85510b2970e9a "$RADIO/houji_wonder_bridge.ko"
 qca="$RADIO/qca_cld3_kiwi_v2.ko"
 qca_sha=$RING_SHA
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
 mount --bind "$RADIO/amss20.bin" "$AMSS"
 "$BB" mount -i -o remount,bind,ro,nosuid,relatime "$RADIO/amss20.bin" "$AMSS"
 insmod "$RADIO/houji_wonder_bridge.ko"
 vht=0
 case "$profile" in vht80|vht80-sgi) vht=1;; esac
 set -- pt_exclusive=1 pt_zero_serial=1 pt_rx_metadata=1 pt_vht80="$vht" pt_rate_observe=1 pt_single_ring=1
 case "$ba_mode" in
  on) set -- "$@" pt_ba_observe=1 ;;
  peer) set -- "$@" pt_ba_observe=1 "pt_ba_peer=$peer" ;;
  caps) set -- "$@" pt_peer_caps_observe=1 ;;
 esac
 bounded insmod "$qca" "$@"
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
 sleep 2
 echo on > "$PCI"
 bounded sh -c 'echo 1 > /sys/module/qca_cld3_kiwi_v2/parameters/pt_activate'
 insmod "$RADIO/wonder.ko" synthetic_provider=1
 phy=$(iw phy | awk '$0 == "Wiphy wonder" { found=1; next } found && $1 == "wiphy" && $2 == "index:" { print $3; exit }')
 case "$phy" in ''|*[!0-9]*) return 1;; esac
 bounded ./wonder-init create "$phy"
 bounded ./wonder-init cache "$phy" "$(cat /sys/class/net/wonder0/ifindex)" "$profile" "$country" 149
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

run_locked() {
 token=$1 profile=$2 aggregation=$3 ack=$4 peer=$5 count=$6 size=$7 mss=$8 qlen=$9 ba_mode=${10} vht_amsdu=${11:-0}
 test ! -e "$STATE/saved.env"
 no_native_bridge
 trap finish_locked EXIT
 trap 'exit 143' TERM HUP
 trap 'exit 130' INT
 enter
 if test "$ba_mode" = caps; then
  caps=$(cat "$W/parameters/pt_peer_caps")
  echo "PEER_CAPS_AFTER $caps"
  echo "$caps" | grep -q '^schema=1 enabled=1 sampled=1 '
  return 0
 fi
 if test "$vht_amsdu" = 1; then
  test "$(cat "$W/parameters/pt_vht80")" = Y
  test "$(cat /sys/class/net/wonder0/mtu)" = 8192
 fi
 HOUJI_OWLY_VHT_AMSDU="$vht_amsdu" ./houji-owly "$peer" "$aggregation" "$ack" > "$STATE/runner.log" 2>&1 &
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
 collect > "$STATE/before.txt"
 set -- "$count" "$size"
 if test "$mss" != 0; then set -- "$@" "$mss"; fi
 ./owly-hashprobe "$@" > "$STATE/server.log" 2>&1 &
 probe=$!
 state OWLY_READY
 deadline=$(($(uptime_s) + 440))
 while test ! -e "$STATE/stop-$token"; do
  kill -0 "$driver"
  if ! kill -0 "$probe" 2>/dev/null; then wait "$probe"; probe=; break; fi
  test "$(uptime_s)" -lt "$deadline"
  sleep 1
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
 rm -f "$STATE/stop-$token" "$STATE/result"
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
  case "$#" in 10|11) ;; *) exit 2;; esac
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
   ht20:single:ack|vht80:single:ack|vht80:amsdu:ack|vht80-sgi:amsdu:ack|vht80-sgi:amsdu:noack) ;;
   *) exit 2;;
  esac
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
  # Recovery is valid only after the session has stopped and owly0 disappeared.
  restore
  ;;
 stop)
  case "${2:-}" in ''|*[!a-zA-Z0-9-]*) exit 2;; esac
  : > "$STATE/stop-$2"
  ;;
 *) exit 2;;
esac
