#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
: "${ANDROID_NDK_HOME:?BYOB: set ANDROID_NDK_HOME; see docs/guides/byob.md}"
case "$(uname -s)" in
 Linux) tag=linux-x86_64 ;;
 Darwin) tag=darwin-x86_64 ;;
 *) echo 'Unsupported NDK host' >&2; exit 1 ;;
esac
NDK_BIN="$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/$tag/bin"
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$NDK_BIN/aarch64-linux-android29-clang"
export CC_aarch64_linux_android="$CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER"
export AR_aarch64_linux_android="$NDK_BIN/llvm-ar"
export CARGO_TARGET_DIR="$ROOT/build/rust-app"
test -x "$CC_aarch64_linux_android"
cargo build --release --target aarch64-linux-android --manifest-path "$HERE/native/houji-runner/Cargo.toml"
mkdir -p "$HERE/dist"
cp "$CARGO_TARGET_DIR/aarch64-linux-android/release/houji-owly-app" "$HERE/dist/"
"$CC_aarch64_linux_android" -std=gnu11 -O2 -ffreestanding -fno-builtin -fno-stack-protector -nostdlib \
 -static -Wl,-e,houji_start -Wl,-z,noexecstack -Wall -Wextra -Werror -Wno-unused-function \
 "$ROOT/experiments/remote-peer-probe/vht-rx-next/2ss-sgi-next/wonder-init-2ss.c" -o "$HERE/dist/wonder-init"
