#!/bin/sh
set -eu
cd "$(dirname "$0")"
: "${ANDROID_NDK_HOME:?Set to the Linux Android NDK directory on the build server}"
: "${NDK_HOST_TAG:=linux-x86_64}"
compiler="$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/$NDK_HOST_TAG/bin/aarch64-linux-android29-clang"
test -x "$compiler"
test "$(git -C ../owly rev-parse HEAD)" = 6f2e8ea99aed0687558d78a1dd49154ab3a5fa76
test -z "$(git -C ../owly status --porcelain)"
(cd dist/mss && shasum -a 256 -c SHA256SUMS)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/owly" "$scratch/houji-runner"
git -C ../owly archive --output="$scratch/owly.tar" HEAD
tar -xf "$scratch/owly.tar" -C "$scratch/owly"
patch -d "$scratch/owly" -p1 < owly-coalesce.patch
patch -d "$scratch/owly" -p1 < owly-vht-amsdu.patch
cp Cargo.toml Cargo.lock "$scratch/houji-runner/"
cp -R src "$scratch/houji-runner/"
patch -d "$scratch/houji-runner" -p1 < runner-vht-amsdu.patch
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$compiler"
export CARGO_TARGET_DIR="$PWD/target/vht-amsdu"
export HOUJI_OWLY_COALESCE_US=1000
cargo test --locked --manifest-path "$scratch/owly/Cargo.toml" -p owly-awdl --features driver
cargo build --release --locked --target aarch64-linux-android --manifest-path "$scratch/houji-runner/Cargo.toml"
mkdir -p dist/mss-vht
cp "$CARGO_TARGET_DIR/aarch64-linux-android/release/houji-owly" dist/mss-vht/
cp dist/mss/wonder-init dist/mss/owly-hashprobe dist/mss-vht/
(cd dist/mss-vht && shasum -a 256 houji-owly wonder-init owly-hashprobe > SHA256SUMS)
