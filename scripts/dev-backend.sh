#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PYTHON_BIN:-${PROJECT_ROOT}/backend/.venv/bin/python}"

if [[ ! -x "${PYTHON}" ]]; then
  printf 'Backend Python environment not found: %s\n' "${PYTHON}" >&2
  printf 'Run ./scripts/setup-dev.sh first.\n' >&2
  exit 1
fi

cd "${PROJECT_ROOT}"

# Watch runtime Python sources only. Tests, the virtual environment, SQLite,
# logs, thumbnails and the frontend must never restart the API process.
exec "${PYTHON}" -m uvicorn backend.app:app \
  --reload \
  --reload-dir "${PROJECT_ROOT}/backend" \
  --reload-include '*.py' \
  --reload-exclude "${PROJECT_ROOT}/backend/.venv" \
  --reload-exclude "${PROJECT_ROOT}/backend/tests" \
  --reload-exclude "${PROJECT_ROOT}/backend/.pytest_cache" \
  --host 0.0.0.0 \
  --port 8000
