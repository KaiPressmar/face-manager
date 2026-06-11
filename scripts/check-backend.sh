#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PYTHON_BIN:-${PROJECT_ROOT}/backend/.venv/bin/python}"

[[ -x "${PYTHON}" ]] || {
  printf 'Backend environment is missing. Run ./scripts/setup-dev.sh first.\n' >&2
  exit 1
}

"${PYTHON}" -m pip check
"${PYTHON}" -m py_compile \
  "${PROJECT_ROOT}/backend/app.py" \
  "${PROJECT_ROOT}/backend/config.py" \
  "${PROJECT_ROOT}/backend/db/schema.py" \
  "${PROJECT_ROOT}/backend/models/clustering.py" \
  "${PROJECT_ROOT}/backend/models/face_model.py" \
  "${PROJECT_ROOT}/backend/services/pipeline.py" \
  "${PROJECT_ROOT}/backend/services/storage.py"

printf 'Backend checks passed.\n'
