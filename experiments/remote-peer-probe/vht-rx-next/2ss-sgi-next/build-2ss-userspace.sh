#!/bin/sh
set -eu
cd "$(dirname "$0")"
package=$PWD
ack_pkg=${HOUJI_LEGACY_ROOT:?Set HOUJI_LEGACY_ROOT}/tasks/ack-coalesce-rcvbuf
gap=${HOUJI_LEGACY_ROOT:?Set HOUJI_LEGACY_ROOT}/tasks/rx-gap-observer
reference=${HOUJI_LEGACY_ROOT:?Set HOUJI_LEGACY_ROOT}/tasks/outgoing-vht-advert
base=${HOUJI_LEGACY_ROOT:?Set HOUJI_LEGACY_ROOT}/owly-evaluation-src/owly-evaluation
userspace=${HOUJI_LEGACY_ROOT:?Set HOUJI_LEGACY_ROOT}/toolchains/owly-userspace
commit=6f2e8ea99aed0687558d78a1dd49154ab3a5fa76
export RUSTUP_HOME="$userspace/rustup"
export CARGO_HOME="$userspace/cargo"
export PATH="$CARGO_HOME/bin:$PATH"
compiler="$userspace/android-ndk-r30/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang"
test -x "$compiler"
test "$(git -C "$base/owly" rev-parse HEAD)" = "$commit"
test -z "$(git -C "$base/owly" status --porcelain)"
(
  cd "$ack_pkg"
  sha256sum -c source.sha256
)
rm -rf dist
sha256sum build-2ss-userspace.sh owly-2ss-addba.patch runner-2ss-addba.patch wonder-init-2ss.c > source.sha256
scratch=$(mktemp -d ${HOUJI_LEGACY_ROOT:?Set HOUJI_LEGACY_ROOT}/tmp/2ss-userspace.XXXXXX)
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/owly" "$scratch/houji-runner"
git -C "$base/owly" archive --output="$scratch/owly.tar" "$commit"
tar -xf "$scratch/owly.tar" -C "$scratch/owly"
patch --batch --fuzz=0 -d "$scratch/owly" -p1 < "$base/houji-runner/owly-coalesce.patch"
patch --batch --fuzz=0 -d "$scratch/owly" -p1 < "$reference/owly-outgoing-vht.patch"
patch --batch --fuzz=0 -d "$scratch/owly" -p1 < "$gap/owly-rx-gap.patch"
patch --batch --fuzz=0 -d "$scratch/owly" -p1 < "$ack_pkg/owly-ack-coalesce.patch"
patch --batch --fuzz=0 -d "$scratch/owly" -p1 < "$package/owly-2ss-addba.patch"
cp "$base/houji-runner/Cargo.toml" "$base/houji-runner/Cargo.lock" "$scratch/houji-runner/"
cp -R "$base/houji-runner/src" "$scratch/houji-runner/"
patch --batch --fuzz=0 -d "$scratch/houji-runner" -p1 < "$reference/runner-outgoing-vht.patch"
patch --batch --fuzz=0 -d "$scratch/houji-runner" -p1 < "$gap/runner-rx-gap.patch"
patch --batch --fuzz=0 -d "$scratch/houji-runner" -p1 < "$ack_pkg/runner-rcvbuf.patch"
patch --batch --fuzz=0 -d "$scratch/houji-runner" -p1 < "$package/runner-2ss-addba.patch"
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$compiler"
export CARGO_TARGET_DIR="$gap/target"
export HOUJI_OWLY_COALESCE_US=1000
cargo test --locked --manifest-path "$scratch/owly/Cargo.toml" -p owly-awdl --features driver
cargo test --locked --manifest-path "$scratch/houji-runner/Cargo.toml"
cargo build --release --locked --target aarch64-linux-android --manifest-path "$scratch/houji-runner/Cargo.toml"
mkdir dist
"$compiler" -std=gnu11 -O2 -ffreestanding -fno-builtin -fno-stack-protector -nostdlib \
  -static -Wl,-e,houji_start -Wl,-z,noexecstack -Wall -Wextra -Werror \
  -Wno-unused-function "$package/wonder-init-2ss.c" -o dist/wonder-init
cp "$CARGO_TARGET_DIR/aarch64-linux-android/release/houji-owly" dist/
cp "$base/houji-runner/dist/mss/owly-hashprobe" dist/
(cd dist && sha256sum houji-owly wonder-init owly-hashprobe > SHA256SUMS && sha256sum -c SHA256SUMS)
echo "USERSPACE_2SS_BUILD_PASS"
