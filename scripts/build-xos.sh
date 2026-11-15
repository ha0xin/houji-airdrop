#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
XOS_DIR="$ROOT/runtime/owly-xos"

do_native=1
do_java=1
do_package=0
package_args=()

while [[ $# -gt 0 ]]; do
 case "$1" in
  --native-only)
   do_native=1
   do_java=0
   shift
   ;;
  --java-only)
   do_native=0
   do_java=1
   shift
   ;;
  --package)
   do_package=1
   shift
   ;;
  --mishare-apk)
   do_package=1
   package_args+=("$1" "${2:?--mishare-apk requires a path}")
   shift 2
   ;;
  -h|--help)
   cat <<'EOF'
Usage: bash scripts/build-xos.sh [--native-only | --java-only] [--package] [--mishare-apk PATH]

Builds Owly-XOS userspace components into runtime/owly-xos/dist/ with zero dependency on clients/owly-app:
  1. Native L2 AWDL daemon & channel helper (owly-xosd, wonder-init)  [requires Rust + ANDROID_NDK_HOME]
  2. XosBridge AIDL service DEX (xos-bridge.zip)                       [requires JDK + ANDROID_HOME + BUILD_TOOLS]
  3. Owly-XOS LSPosed compatibility APK (Owly-XOS-Compat.apk)          [requires JDK + ANDROID_HOME + BUILD_TOOLS]
  4. Optional KernelSU/APatch/Magisk ZIP (--package / --mishare-apk)   [requires blobs/local + MIShare.apk]
EOF
   exit 0
   ;;
  *)
   echo "Unknown option: $1 (try --help)" >&2
   exit 1
   ;;
 esac
done

if [[ "$do_native" -eq 1 ]]; then
 bash "$XOS_DIR/build-native.sh"
fi

if [[ "$do_java" -eq 1 ]]; then
 bash "$XOS_DIR/build.sh"
 bash "$XOS_DIR/compat/build.sh"
fi

if [[ "$do_package" -eq 1 ]]; then
 if [[ ${#package_args[@]} -gt 0 ]]; then
  python3 "$XOS_DIR/package-ksu.py" "${package_args[@]}"
 else
  python3 "$XOS_DIR/package-ksu.py"
 fi
fi
