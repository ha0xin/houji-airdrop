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
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$ROOT/build/rust-xos}"
test -x "$CC_aarch64_linux_android"

cargo build --release --target aarch64-linux-android \
 --manifest-path "$ROOT/native/houji-runner/Cargo.toml" \
 --bin owly-xosd

mkdir -p "$HERE/dist"
cp "$CARGO_TARGET_DIR/aarch64-linux-android/release/owly-xosd" "$HERE/dist/owly-xosd"

"$CC_aarch64_linux_android" -std=gnu11 -O2 -ffreestanding -fno-builtin -fno-stack-protector -nostdlib \
 -static -Wl,-e,houji_start -Wl,-z,noexecstack -Wall -Wextra -Werror -Wno-unused-function \
 "$ROOT/native/wonder-init/wonder-init.c" -o "$HERE/dist/wonder-init"
