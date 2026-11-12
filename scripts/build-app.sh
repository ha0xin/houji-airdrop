#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "$(uname -s)" in
  Linux|Darwin) ;;
  *) echo "APK build supports Linux or macOS with Android SDK/JDK." >&2; exit 1 ;;
esac
exec bash "$ROOT/clients/owly-app/build-app.sh" "$@"
