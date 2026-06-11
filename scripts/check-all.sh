#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

"${SCRIPT_DIR}/check-version.sh"
"${SCRIPT_DIR}/check-backend.sh"
npm --prefix "${PROJECT_ROOT}/frontend" run check

printf 'All checks passed.\n'
