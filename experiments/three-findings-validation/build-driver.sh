#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# Build on the current Linux host. No implicit SSH or named host.
exec bash "$ROOT/scripts/build-driver.sh" "$@"
