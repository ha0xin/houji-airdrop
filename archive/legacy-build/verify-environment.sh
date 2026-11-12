#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
mkdir -p "$TMPDIR" "$HOUJI_BUILD_ROOT/out/smoke"
clang --version
ld.lld --version
pahole --version
cmake --version | head -1
ninja --version
python3 --version
printf 'int houji_environment_probe(int x) { return x + 1; }\n' > "$HOUJI_BUILD_ROOT/out/smoke/probe.c"
clang --target=aarch64-linux-gnu -g -O2 -ffreestanding -c "$HOUJI_BUILD_ROOT/out/smoke/probe.c" -o "$HOUJI_BUILD_ROOT/out/smoke/probe.o"
ld.lld -r "$HOUJI_BUILD_ROOT/out/smoke/probe.o" -o "$HOUJI_BUILD_ROOT/out/smoke/linked.o"
llvm-readelf -h "$HOUJI_BUILD_ROOT/out/smoke/linked.o"
# Verify real BTF generation rather than only checking the executable version.
pahole -J "$HOUJI_BUILD_ROOT/out/smoke/linked.o"
llvm-readelf -S "$HOUJI_BUILD_ROOT/out/smoke/linked.o" | grep BTF
echo ENVIRONMENT_SMOKE_PASS
