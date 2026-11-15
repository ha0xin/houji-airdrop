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

OUT_DIR="../dist"
mkdir -p build/stubs build/classes build/dex "$OUT_DIR"
rm -rf build/stubs/* build/classes/* build/dex/* build/res.zip build/unsigned.apk

javac --release 8 -encoding UTF-8 -d build/stubs \
 stubs/de/robv/android/xposed/*.java \
 stubs/de/robv/android/xposed/callbacks/*.java

javac --release 8 -encoding UTF-8 -cp "$SDK_STUB:build/stubs" -d build/classes \
 src/org/houji/xoscompat/*.java

"$BUILD_TOOLS/d8" --lib "$SDK_STUB" --classpath build/stubs --min-api 33 \
 --output build/dex build/classes/org/houji/xoscompat/*.class

"$BUILD_TOOLS/aapt2" compile --dir res -o build/res.zip
"$BUILD_TOOLS/aapt2" link -I "$SDK_STUB" --manifest AndroidManifest.xml -A assets \
 -o build/unsigned.apk build/res.zip
zip -qj build/unsigned.apk build/dex/classes.dex

KS="${SIGN_KEYSTORE:-build/local-test.jks}"
KS_PASS="${SIGN_PASS:-owly-local-test}"
if ! test -f "$KS"; then
 keytool -genkeypair -keystore "$KS" -storepass "$KS_PASS" -keypass "$KS_PASS" \
  -alias owlyxos -dname "CN=OwlyXosCompat" -keyalg RSA -validity 3650
fi

"$BUILD_TOOLS/apksigner" sign --ks "$KS" --ks-pass "pass:$KS_PASS" \
 --out "$OUT_DIR/Owly-XOS-Compat.apk" build/unsigned.apk
"$BUILD_TOOLS/apksigner" verify "$OUT_DIR/Owly-XOS-Compat.apk"
rm -f "$OUT_DIR/Owly-XOS-Compat.apk.idsig"
