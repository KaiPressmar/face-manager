#!/usr/bin/env bash
# Isolated, opt-in Playwright smoke test; no developer database is touched.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PYTHON_BIN:-${PROJECT_ROOT}/backend/.venv/bin/python}"
API_PORT="${UI_SMOKE_API_PORT:-8194}"
WEB_PORT="${UI_SMOKE_WEB_PORT:-5194}"

if [[ "$PYTHON" != */* ]]; then
  PYTHON="$(command -v "$PYTHON")" || { printf "Python interpreter unavailable.\n" >&2; exit 1; }
fi
if [[ ! -x "$PYTHON" ]]; then
  printf 'Backend Python environment missing: %s (run ./scripts/setup-dev.sh)\n' "$PYTHON" >&2
  exit 1
fi
if [[ ! -x "${PROJECT_ROOT}/frontend/node_modules/.bin/vite" ]]; then
  printf 'Frontend dependencies missing; run npm --prefix frontend install.\n' >&2
  exit 1
fi
if [[ -z "${NODE_PATH:-}" ]] || ! NODE_PATH="$NODE_PATH" node -e 'require("playwright")' 2>/dev/null; then
  printf 'Playwright is required externally. Set NODE_PATH to a directory containing playwright (for example /tmp/face-manager-map-browser/node_modules).\n' >&2
  exit 1
fi
if [[ -z "${PLAYWRIGHT_BROWSERS_PATH:-}" || ! -d "${PLAYWRIGHT_BROWSERS_PATH:-}" ]]; then
  printf 'Set PLAYWRIGHT_BROWSERS_PATH to a directory containing installed Playwright browsers.\n' >&2
  exit 1
fi
for port in "$API_PORT" "$WEB_PORT"; do
  if ! "$PYTHON" - "$port" <<'PY'
import socket, sys
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(('127.0.0.1', int(sys.argv[1])))
finally:
    sock.close()
PY
  then
    printf 'Port %s is already in use; set UI_SMOKE_API_PORT/UI_SMOKE_WEB_PORT.\n' "$port" >&2
    exit 1
  fi
done

SMOKE_TMP="$(mktemp -d -t face-manager-ui-smoke.XXXXXXXX)"
API_PID=""
WEB_PID=""
cleanup() {
  result=$?
  trap - EXIT
  [[ -n "$WEB_PID" ]] && kill "$WEB_PID" 2>/dev/null || true
  [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
  [[ -n "$WEB_PID" ]] && wait "$WEB_PID" 2>/dev/null || true
  [[ -n "$API_PID" ]] && wait "$API_PID" 2>/dev/null || true
  if [[ "$result" -ne 0 ]]; then
    printf '\nAPI log:\n' >&2
    tail -40 "$SMOKE_TMP/api.log" 2>/dev/null >&2 || true
    printf '\nVite log:\n' >&2
    tail -40 "$SMOKE_TMP/vite.log" 2>/dev/null >&2 || true
  fi
  if [[ "${UI_SMOKE_KEEP_DATA:-0}" == "1" ]]; then
    printf 'Fixture retained: %s\n' "$SMOKE_TMP"
  else
    rm -rf -- "$SMOKE_TMP"
  fi
  exit "$result"
}
trap cleanup EXIT
cd "$PROJECT_ROOT"
"$PYTHON" scripts/ui-fixture.py "$SMOKE_TMP/data"
FACE_MANAGER_DATA_DIR="$SMOKE_TMP/data" "$PYTHON" -m uvicorn backend.app:app --host 127.0.0.1 --port "$API_PORT" >"$SMOKE_TMP/api.log" 2>&1 &
API_PID=$!
(cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1 --port "$WEB_PORT" --strictPort) >"$SMOKE_TMP/vite.log" 2>&1 &
WEB_PID=$!
ready=0
for attempt in {1..60}; do
  if curl --silent --fail "http://127.0.0.1:${API_PORT}/openapi.json" >/dev/null && curl --silent --fail "http://127.0.0.1:${WEB_PORT}/" >/dev/null; then
    ready=1
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null || ! kill -0 "$WEB_PID" 2>/dev/null; then break; fi
  sleep 0.5
done
if [[ "$ready" != 1 ]]; then
  printf 'Browser smoke servers did not start within 30 seconds.\n' >&2
  exit 1
fi
UI_SMOKE_API_BASE="http://127.0.0.1:${API_PORT}/api" UI_SMOKE_WEB_BASE="http://127.0.0.1:${WEB_PORT}" node frontend/tests/browser-smoke.cjs
