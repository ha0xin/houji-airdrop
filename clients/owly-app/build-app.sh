#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
: "${SDK_STUB:=${ANDROID_HOME:-}/platforms/android-35/android.jar}"
if ! test -f "$SDK_STUB"; then echo "BYOB: set ANDROID_HOME or SDK_STUB; see docs/guides/byob.md" >&2; exit 1; fi
: "${BUILD_TOOLS:?BYOB: set BUILD_TOOLS to an Android SDK build-tools directory}"
if test -n "${JAVA_HOME:-}"; then export PATH="$JAVA_HOME/bin:$PATH"; fi
mkdir -p build/classes build/dex dist
javac --release 8 -encoding UTF-8 -cp "$SDK_STUB" -d build/classes app/MainActivity.java
"$BUILD_TOOLS/d8" --lib "$SDK_STUB" --min-api 33 --output build/dex build/classes/org/houji/owly/*.class
"$BUILD_TOOLS/aapt2" link -I "$SDK_STUB" --manifest app/AndroidManifest.xml -o build/unsigned.apk
zip -qj build/unsigned.apk build/dex/classes.dex
if ! test -f build/local-test.jks; then
 keytool -genkeypair -keystore build/local-test.jks -storepass owly-local-test -keypass owly-local-test -alias owly -dname CN=OwlyLocalTest -keyalg RSA -validity 3650
fi
"$BUILD_TOOLS/apksigner" sign --ks build/local-test.jks --ks-pass pass:owly-local-test --out dist/Owly-AirDrop.apk build/unsigned.apk
"$BUILD_TOOLS/apksigner" verify dist/Owly-AirDrop.apk
