#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
: "${SDK_STUB:=${ANDROID_HOME:-}/platforms/android-35/android.jar}"
if ! test -f "$SDK_STUB"; then
 echo "BYOB: set ANDROID_HOME or SDK_STUB; see docs/guides/byob.md" >&2
 exit 1
fi
: "${BUILD_TOOLS:?BYOB: set BUILD_TOOLS to an Android SDK build-tools directory}"
if test -n "${JAVA_HOME:-}"; then
 export PATH="$JAVA_HOME/bin:$PATH"
fi
mkdir -p build/classes build/dex dist
javac --release 8 -encoding UTF-8 -cp "$SDK_STUB" -d build/classes src/*.java
"$BUILD_TOOLS/d8" --lib "$SDK_STUB" --min-api 33 --output build/dex build/classes/*.class
zip -qj dist/xos-bridge.zip build/dex/classes.dex
