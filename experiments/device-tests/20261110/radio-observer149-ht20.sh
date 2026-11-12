#!/system/bin/sh
# Called only by the serialized, UID-restricted XOS Binder compatibility service.
set -eu
D=/data/local/tmp/houji-buildtest-20261110/state
R=/data/local/tmp/houji-buildtest-20261110/observer
X=/data/local/tmp/xos
BB=/data/adb/ksu/bin/busybox
W=/sys/module/qca_cld3_kiwi_v2
AMSS=/vendor/firmware_mnt/image/kiwi/amss20.bin
STOCK=/vendor_dlkm/lib/modules/qca_cld3_kiwi_v2.ko
PCI=/sys/bus/pci/devices/0000:01:00.0/power/control
ORIG=4db7b0e1aa7e4ec071708359c9d0db8d3d7872390cd8e88ffa0bbb30d560bcc8
CANDIDATE=13f01cb46efbe498c854352bfc253fa3665c14a14c37a0a6ed6a609a48a8709c
NOTE=a3e9bd62646444e1ecbe0f421c95ec46e6caf69a78323efb2f6ebe00e6b2bd8b
bounded() { "$BB" timeout -s TERM -k 2 25 "$@"; }
check() { echo "$1  $2" | sha256sum -c -; }
setstate() {
 echo "$1" > "$D/status"; echo "RADIO_STATE=$1"
 case "$1" in ENTERING|XOS_READY) setprop sys.xos.exclusive 1;; STOCK_RESTORED) setprop sys.xos.exclusive 0;; esac
}
cleanup_rules() {
 for priority in 10980 10981 10982; do while ip -6 rule del priority "$priority" 2>/dev/null; do :; done; done
 ip -6 route flush table 10074 2>/dev/null || true
}
stop_xos() {
 if test -f "$X/port/xos-port.sh"; then sh "$X/port/xos-port.sh" stop; fi
 for pid in $(pidof xos_runner xos_runner.rate xos_runner_ht20 2>/dev/null || true); do kill "$pid"; done
 sleep 1
 test ! -e /sys/class/net/xos0
 iw dev wonder0 del 2>/dev/null || true
 cleanup_rules
}
restore() {
 . "$D/saved.env"
 if test "${saved_boot:-}" != "$(cat /proc/sys/kernel/random/boot_id)"; then
  test ! -e /sys/module/houji_wonder_bridge
  mv "$D/saved.env" "$D/previous-boot.env"
  setstate STOCK_RESTORED
  return
 fi
 setstate RESTORING
 stop_xos
 if test -e /sys/module/wonder; then bounded rmmod wonder; fi
 if test -e /sys/module/houji_wonder_bridge; then
  bounded sh -c 'echo 1 > /sys/module/houji_wonder_bridge/parameters/pt_release'
  grep -qx 'armed=0 releasing=0 released=1 result=0' /sys/module/houji_wonder_bridge/parameters/pt_release
  if test -e "$W"; then
   grep -qx 'exclusive=1 published=0 pinned=0 sealed=1' "$W/parameters/pt_activate"
   test "$(cat "$W/refcnt")" = 0
   bounded rmmod qca_cld3_kiwi_v2
  fi
  bounded rmmod houji_wonder_bridge
 fi
 if grep -q " $AMSS " /proc/1/mountinfo; then
  check "$CANDIDATE" "$AMSS"
  umount "$AMSS"
 fi
 check "$ORIG" "$AMSS"
 if ! test -e "$W"; then bounded insmod "$STOCK"; fi
 check "$NOTE" "$W/notes/.note.gnu.build-id"
 . "$D/saved.env"
 echo "$saved_pci" > "$PCI"
 if test "$saved_cnd" = running; then start vendor.cnd; fi
 if test "$saved_hal" = running; then start vendor.wifi_hal_legacy; fi
 if test "$saved_scan" = 1; then cmd wifi set-scan-always-available enabled; else cmd wifi set-scan-always-available disabled; fi
 if test "$saved_wifi" = 1; then cmd wifi set-wifi-enabled enabled; else cmd wifi set-wifi-enabled disabled; fi
 setstate STOCK_RESTORED
 mv "$D/saved.env" "$D/last-restored.env"
}
snapshot() {
 test ! -f "$D/saved.env"
 check "$ORIG" "$AMSS"
 check "$NOTE" "$W/notes/.note.gnu.build-id"
 wifi=$(settings get global wifi_on); scan=$(settings get global wifi_scan_always_enabled)
 case "$wifi" in 0|1) ;; *) echo "Unsupported Wi-Fi state: $wifi"; exit 1;; esac
 case "$scan" in 0|1) ;; *) echo "Unsupported scan state: $scan"; exit 1;; esac
 cnd=$(getprop init.svc.vendor.cnd);hal=$(getprop init.svc.vendor.wifi_hal_legacy);pci=$(cat "$PCI")
 case "$cnd:$hal" in running:running|stopped:stopped|running:stopped|stopped:running) ;; *) exit 1;; esac
 case "$pci" in auto|on) ;; *) exit 1;; esac
 umask 077
 printf 'saved_wifi=%s\nsaved_scan=%s\nsaved_cnd=%s\nsaved_hal=%s\nsaved_pci=%s\n' "$wifi" "$scan" "$cnd" "$hal" "$pci" > "$D/saved.env"
 printf 'saved_boot=%s\n' "$(cat /proc/sys/kernel/random/boot_id)" >> "$D/saved.env"
}
start_xos() {
 "$BB" setsid env LD_LIBRARY_PATH="$X" /data/local/tmp/houji-buildtest-20261110/xos_runner_ht20 149 </dev/null >>"$D/xos.log" 2>&1 &
 echo $! > "$D/xos.pid"
 for i in 1 2 3 4 5 6 7 8 9 10; do
  if ip -6 -o addr show dev xos0 scope link 2>/dev/null | grep -q fe80:; then setstate XOS_READY; return; fi
  sleep 1
 done
 echo 'XOS failed to become ready'; return 1
}
enter() {
 if test -e "$W/parameters/pt_exclusive" && grep -q Y "$W/parameters/pt_exclusive"; then
  test -f "$D/saved.env" || { echo 'Unowned experiment: restore/adopt explicitly first'; exit 1; }
  if ip link show xos0 2>/dev/null | grep -q UP; then setstate XOS_READY; return; fi
  grep -q 'phase=0.*closing=0' "$W/parameters/pt_activate"
  start_xos; return
 fi
 test "$(uname -r)" = 6.1.138-android14-11-g0c3d559bcd85-ab14529422
 test ! -e /sys/module/houji_wonder_bridge
 check "$CANDIDATE" "$R/amss20.bin"
 snapshot
 setstate ENTERING
 trap 'rc=$?; trap - EXIT; if test "$rc" != 0; then echo "ENTER_FAILED=$rc"; restore || setstate RESTORE_FAILED; fi; exit "$rc"' EXIT
 stop vendor.cnd
 cmd wifi set-scan-always-available disabled
 cmd wifi set-wifi-enabled disabled
 stop vendor.wifi_hal_legacy
 sleep 3
 test "$(getprop init.svc.vendor.cnd):$(getprop init.svc.vendor.wifi_hal_legacy)" = stopped:stopped
 test "$(cat "$W/refcnt")" = 0
 test -z "$(ls -A "$W/holders")"
 bounded rmmod qca_cld3_kiwi_v2
 test ! -e /dev/wlan
 mount --bind "$R/amss20.bin" "$AMSS"
 "$BB" mount -i -o remount,bind,ro,nosuid,relatime "$R/amss20.bin" "$AMSS"
 insmod "$R/houji_wonder_bridge.ko"
 bounded insmod "$R/qca_cld3_kiwi_v2.ko" pt_exclusive=1 pt_zero_serial=1 pt_rx_metadata=1 pt_tx_observe=1
 sleep 2
 echo on > "$PCI"
 echo 1 > "$W/parameters/pt_activate"
 insmod "$R/wonder.ko" synthetic_provider=1
 start_xos
 trap - EXIT
}
test "$(readlink /proc/self/ns/mnt)" = "$(readlink /proc/1/ns/mnt)"
case "${1:-status}" in
 enter) enter ;;
 restore) if test -f "$D/saved.env"; then restore; else echo NO_OWNED_SESSION; fi ;;
 status) cat "$D/status" 2>/dev/null || true; cmd wifi status ;;
 *) exit 2 ;;
esac
