#!/bin/sh
set -eu
cd "$(dirname "$0")"
: "${ANDROID_NDK_HOME:?Set to the Linux Android NDK directory on the build server}"
: "${NDK_HOST_TAG:=linux-x86_64}"
compiler="$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/$NDK_HOST_TAG/bin/aarch64-linux-android29-clang"
test "$(git -C ../owly rev-parse HEAD)" = 6f2e8ea99aed0687558d78a1dd49154ab3a5fa76
test -z "$(git -C ../owly status --porcelain)"
test -x "$compiler"
mkdir -p dist
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$compiler"
cargo build --release --locked --target aarch64-linux-android
"$compiler" -std=gnu11 -O2 -ffreestanding -fno-builtin -fno-stack-protector -nostdlib \
  -static -Wl,-e,houji_start -Wl,-z,noexecstack -Wall -Wextra -Werror \
  -Wno-unused-function wonder-init.c -o dist/wonder-init
cp target/aarch64-linux-android/release/houji-owly dist/
CGO_ENABLED=0 GOOS=android GOARCH=arm64 go build -trimpath -o dist/owly-hashprobe hashprobe.go
(cd dist && shasum -a 256 houji-owly wonder-init owly-hashprobe > SHA256SUMS)
