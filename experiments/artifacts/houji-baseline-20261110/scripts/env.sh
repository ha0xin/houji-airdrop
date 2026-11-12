# Source this file in the build-host build shell; does not modify global profiles.
: "${HOUJI_LEGACY_ROOT:?Set HOUJI_LEGACY_ROOT for historical experiments}"
export HOUJI_BUILD_ROOT="$HOUJI_LEGACY_ROOT"
export PATH="$HOUJI_BUILD_ROOT/toolchains/clang-r487747c/bin:$PATH"
export ARCH=arm64 LLVM=1 LLVM_IAS=1
export PAHOLE=/usr/bin/pahole
export TMPDIR="$HOUJI_BUILD_ROOT/tmp"
