#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PYTHON_BIN:-${PROJECT_ROOT}/backend/.venv/bin/python}"

if [[ "${PYTHON}" == */* ]]; then
  [[ -x "${PYTHON}" ]] || {
    printf 'Backend environment is missing. Run ./scripts/setup-dev.sh first.\n' >&2
    exit 1
  }
else
  RESOLVED_PYTHON="$(command -v "${PYTHON}")" || {
    printf 'Python interpreter %s is unavailable.\n' "${PYTHON}" >&2
    exit 1
  }
  PYTHON="${RESOLVED_PYTHON}"
fi

"${PYTHON}" "${PROJECT_ROOT}/scripts/check-python-dependencies.py"
"${PYTHON}" -m py_compile \
  "${PROJECT_ROOT}/backend/app.py" \
  "${PROJECT_ROOT}/backend/config.py" \
  "${PROJECT_ROOT}/backend/changelog.py" \
  "${PROJECT_ROOT}/backend/desktop_main.py" \
  "${PROJECT_ROOT}/backend/db/schema.py" \
  "${PROJECT_ROOT}/backend/models/clustering.py" \
  "${PROJECT_ROOT}/backend/models/face_model.py" \
  "${PROJECT_ROOT}/backend/services/desktop.py" \
  "${PROJECT_ROOT}/backend/services/face_thumbnails.py" \
  "${PROJECT_ROOT}/backend/services/face_thumbnail_warmup.py" \
  "${PROJECT_ROOT}/backend/services/import_queue.py" \
  "${PROJECT_ROOT}/backend/services/idle_recluster.py" \
  "${PROJECT_ROOT}/backend/services/pipeline.py" \
  "${PROJECT_ROOT}/backend/services/storage.py" \
  "${PROJECT_ROOT}/backend/services/update_manager.py"

"${PYTHON}" -m unittest discover \
  -s "${PROJECT_ROOT}/backend/tests" \
  -t "${PROJECT_ROOT}"

printf 'Backend checks passed.\n'
