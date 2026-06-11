#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
VERSION_FILE="${PROJECT_ROOT}/VERSION"

usage() {
  cat <<'EOF'
Usage: ./scripts/release-version.sh <major|minor|patch|X.Y.Z>

Updates the canonical VERSION file and the frontend package metadata.
It does not create a commit or Git tag.
EOF
}

[[ $# -eq 1 ]] || {
  usage
  exit 1
}

current_version="$(tr -d '[:space:]' < "${VERSION_FILE}")"
IFS=. read -r major minor patch <<< "${current_version}"

[[ "${major}" =~ ^[0-9]+$ && "${minor}" =~ ^[0-9]+$ && "${patch}" =~ ^[0-9]+$ ]] ||
  {
    printf 'Invalid current version: %s\n' "${current_version}" >&2
    exit 1
  }

case "$1" in
  major)
    next_version="$((major + 1)).0.0"
    ;;
  minor)
    next_version="${major}.$((minor + 1)).0"
    ;;
  patch)
    next_version="${major}.${minor}.$((patch + 1))"
    ;;
  *)
    next_version="$1"
    ;;
esac

[[ "${next_version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  printf 'Version must use X.Y.Z semantic versioning.\n' >&2
  exit 1
}

printf '%s\n' "${next_version}" > "${VERSION_FILE}"
npm --prefix "${PROJECT_ROOT}/frontend" version \
  "${next_version}" \
  --no-git-tag-version \
  --allow-same-version >/dev/null

printf 'Version updated: %s -> %s\n' "${current_version}" "${next_version}"
