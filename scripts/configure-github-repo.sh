#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
export PATH="${HOME}/.local/bin:${PATH}"

command -v gh >/dev/null 2>&1 || {
  printf 'GitHub CLI is missing. Run ./scripts/setup-dev.sh first.\n' >&2
  exit 1
}

gh auth status --hostname github.com >/dev/null 2>&1 || {
  printf 'Authenticate first:\n' >&2
  printf '  gh auth login --hostname github.com --git-protocol ssh --web\n' >&2
  exit 1
}

cd "${PROJECT_ROOT}"
repository="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"

printf 'Configuring %s...\n' "${repository}"
gh repo edit "${repository}" \
  --default-branch main \
  --description 'Local photo organizer with face clustering and duplicate detection for Windows 11 and Ubuntu 26.04.' \
  --homepage "https://github.com/${repository}/releases/latest" \
  --add-topic photo-management,photo-organizer,face-clustering,face-recognition,duplicate-detection,privacy,desktop-app,ubuntu,linux,windows,onnx-runtime,openvino,cuda

printf '%s\n' \
  'Default branch, project description, release link and topics configured.' \
  'Apply the protection rules documented in CONTRIBUTING.md under:' \
  '  Settings > Rules > Rulesets'
