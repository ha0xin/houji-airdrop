#!/usr/bin/env python3
"""Reuse the audited module lifecycle; replace only the benchmark session loop."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[2] / "clients/owly-app"
source = (root / "evidence/assembled-validation-harness.sh").read_text()
text = source[:source.index("run_phase() {")]
text += source[source.index("native_pid_matches() {"):source.index('test "$(id -u)" = 0')]
text = text.replace("ROOT=$BASE/owly", "ROOT=/data/local/tmp/owly-app")
text = text.replace("RADIO=$BASE/wideband-ring", "RADIO=$ROOT/radio")
text = text.replace("CANDIDATE=13f01cb46efbe498c854352bfc253fa3665c14a14c37a0a6ed6a609a48a8709c",
                    'CANDIDATE=$(cat "$ROOT/selected-firmware.sha")')
text = text.replace('bounded insmod "$qca" "$@"',
                    'bounded insmod "$qca" "$@" pt_rx_msdu_buf="$tf_remote_assoc" pt_remote_rx_window=64')
text = text.replace(' restored=$?\n if test "$restored" != 0; then', ''' restored=$?
 APP=/data/user/0/org.houji.owly/files
 if test "$restored" = 0; then
  printf '%s\\n' '{"mode":"stopped","message":"已停止，普通 Wi-Fi 已恢复","peers":[],"request":null,"send_busy":false}' > "$APP/status.tmp"
 else
  printf '%s\\n' '{"mode":"error","message":"无线恢复未完成，请保留日志并检查","peers":[],"request":null,"send_busy":false}' > "$APP/status.tmp"
 fi
 chmod 644 "$APP/status.tmp"
 chcon "$(ls -Zd "$APP" | awk '{print $1}')" "$APP/status.tmp"
 mv "$APP/status.tmp" "$APP/status.json"
 if test "$restored" != 0; then''')
text += r'''
run_locked() {
 token=$1
 requested=$2
 profile=vht80-2ss-mcs9-sgi aggregation=amsdu ack=ack ba_mode=reference
 peer=$requested
 if test "$requested" = scan; then peer=02:00:00:00:00:01; fi
 test ! -e "$STATE/saved.env"
 no_native_bridge
 trap finish_locked EXIT
 trap 'exit 143' TERM HUP
 trap 'exit 130' INT
 enter
 APP=/data/user/0/org.houji.owly/files
 mkdir -p "$APP/tmp"
 export TMPDIR="$APP/tmp"
 app_uid=$(stat -c %u "$APP")
 test "$app_uid" -ge 10000
 ba=2 txba=1
 if test "$requested" = scan; then ba=0; txba=0; fi
 rm -f "$APP/status.json"
 HOUJI_OWLY_OUTGOING_VHT=2 HOUJI_OWLY_ACCEPT_ADDBA="$ba" HOUJI_OWLY_TX_ADDBA="$txba" \
 OWLY_COALESCE_US=1000 OWLY_APP_DIR="$APP" OWLY_APP_UID="$app_uid" \
 ./houji-owly-outgoing-vht "$requested" amsdu ack > "$STATE/runner.log" 2>&1 &
 driver=$!
 for i in $(seq 1 15); do
  if grep -q '^OWLY_READY ' "$STATE/runner.log"; then break; fi
  kill -0 "$driver"
  sleep 1
 done
 grep -q '^OWLY_READY ' "$STATE/runner.log"
 routes
 state OWLY_READY
 deadline=$(($(uptime_s) + 1800))
 while test ! -e "$STATE/stop-$token"; do
  kill -0 "$driver"
  test "$(uptime_s)" -lt "$deadline"
  if grep -q '"mode":"error"' "$APP/status.json" 2>/dev/null; then return 1; fi
  if test "$requested" != scan; then
   stats=$(cat "$W/parameters/pt_rx_msdu_stats")
   echo "$stats" > "$STATE/rx-last.txt"
   echo "$stats" | grep -q 'peer_miss=0 bad_desc=0 '
   echo "$stats" | grep -q ' dropped=0 '
  fi
  sleep 1
 done
}
test "$(id -u)" = 0
test "$(readlink /proc/self/ns/mnt)" = "$(readlink /proc/1/ns/mnt)"
umask 077
case "${1:-}" in
 run)
  test "$#" = 3
  echo "$2" | grep -Eq '^[a-zA-Z0-9-]+$'
  if test "$3" != scan; then echo "$3" | grep -Eq '^([0-9a-f]{2}:){5}[0-9a-f]{2}$'; fi
  shift; run_job "$@" ;;
 locked) shift; run_locked "$@" ;;
 recover-locked) restore ;;
 recover)
  job_lock
  "$BB" flock -n "$NATIVE/supervisor.lock" sh "$0" recover-locked 9>&-
  resume_native ;;
 stop)
  echo "${2:-}" | grep -Eq '^[a-zA-Z0-9-]+$'
  : > "$STATE/stop-$2" ;;
 *) exit 2 ;;
esac
'''
out = Path(__file__).resolve().parent / "radio.sh"
out.write_text(text)
subprocess.run(["sh", "-n", str(out)], check=True)
