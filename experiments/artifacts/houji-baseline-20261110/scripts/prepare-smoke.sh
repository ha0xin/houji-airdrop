#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
out="$HOUJI_BUILD_ROOT/out/prepare-smoke"
mkdir -p "$out"
cp "$HOUJI_BUILD_ROOT/upstream/kernel/configs/bridge.config" "$out/.config"
make -C "$HOUJI_BUILD_ROOT/src/kernel" O="$out" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig
make -C "$HOUJI_BUILD_ROOT/src/kernel" O="$out" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 modules_prepare
# This tests the build environment, not compatibility with the phone kernel.
echo MODULES_PREPARE_SMOKE_PASS
