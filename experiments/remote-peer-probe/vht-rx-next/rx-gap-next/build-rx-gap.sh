#!/bin/sh
set -eu
cd "$(dirname "$0")"
package=$PWD
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
 cd "$base/houji-runner"
 find src -type f | LC_ALL=C sort > "$package/runner-src-files.txt"
 cut -c67- "$package/runner-base-src.sha256" | cmp - "$package/runner-src-files.txt"
 sha256sum -c "$package/runner-base-src.sha256"
 sha256sum -c <<'SUMS'
05c294c7c3d2542f325936b364719ba196bd8b1278fa4002e1e6d41b3b0a3c60  src/android.rs
815ba8c5dec47c5abdb783007a4d94aee91f626ed60dd2b97d6271fb59dd2213  owly-coalesce.patch
cd3762d49441ac88e0db9a8bc3ab5d57d4158006c4cfca340b1ea3a66bd5a34b  Cargo.toml
c880e13c8df16dfe708d52395631b54342a0c3327d8d3a2d6f112b53241b9b59  Cargo.lock
55b71dcb41154bd6565413ec513bb030559f619e77952a80f7f9e36d4755b6f1  dist/mss/SHA256SUMS
SUMS
 cd dist/mss
 sha256sum -c SHA256SUMS
)
test ! -e dist
(
 cd "$reference"
 sha256sum -c <<'SUMS'
5bd0ee61451640ce3c7a6c173ce62839fb8c4c3035aa0bd87b4d4ead164ed77f  owly-outgoing-vht.patch
d4860e59cbabd8496a7c15455ea8fa4ac4453832e3ee8e19a96dd1dd62280166  runner-outgoing-vht.patch
fd62faae380ec1bae7cef236f7e9435cd0a050feaf01351f71692b0fe4b64b83  runner-base-src.sha256
SUMS
)
cmp runner-base-src.sha256 "$reference/runner-base-src.sha256"
sha256sum build-rx-gap.sh owly-rx-gap.patch runner-rx-gap.patch \
 runner-base-src.sha256 > source.sha256
scratch=$(mktemp -d ${HOUJI_LEGACY_ROOT:?Set HOUJI_LEGACY_ROOT}/tmp/rx-gap.XXXXXX)
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/owly" "$scratch/houji-runner"
git -C "$base/owly" archive --output="$scratch/owly.tar" "$commit"
tar -xf "$scratch/owly.tar" -C "$scratch/owly"
patch --batch --fuzz=0 -d "$scratch/owly" -p1 < "$base/houji-runner/owly-coalesce.patch"
patch --batch --fuzz=0 -d "$scratch/owly" -p1 < "$reference/owly-outgoing-vht.patch"
patch --batch --fuzz=0 -d "$scratch/owly" -p1 < "$package/owly-rx-gap.patch"
cp "$base/houji-runner/Cargo.toml" "$base/houji-runner/Cargo.lock" "$scratch/houji-runner/"
cp -R "$base/houji-runner/src" "$scratch/houji-runner/"
patch --batch --fuzz=0 -d "$scratch/houji-runner" -p1 < "$reference/runner-outgoing-vht.patch"
patch --batch --fuzz=0 -d "$scratch/houji-runner" -p1 < "$package/runner-rx-gap.patch"
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$compiler"
export CARGO_TARGET_DIR="$package/target"
export HOUJI_OWLY_COALESCE_US=1000
cargo test --locked --manifest-path "$scratch/houji-runner/Cargo.toml"
cargo build --release --locked --target aarch64-linux-android --manifest-path "$scratch/houji-runner/Cargo.toml"
mkdir dist
cp "$CARGO_TARGET_DIR/aarch64-linux-android/release/houji-owly" dist/
cp "$base/houji-runner/dist/mss/wonder-init" "$base/houji-runner/dist/mss/owly-hashprobe" dist/
(cd dist && sha256sum houji-owly wonder-init owly-hashprobe > SHA256SUMS && sha256sum -c SHA256SUMS)
