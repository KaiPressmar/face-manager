#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
version="$(tr -d '[:space:]' < "${PROJECT_ROOT}/VERSION")"
python3 "${SCRIPT_DIR}/prepare-android-models.py"
"${PROJECT_ROOT}/android/gradlew" -p "${PROJECT_ROOT}/android" --no-daemon :app:assembleRelease
mkdir -p "${PROJECT_ROOT}/dist"
if [[ -n "${ANDROID_KEYSTORE_PATH:-}" ]]; then
  apk="${PROJECT_ROOT}/dist/FaceManager-Android-${version}.apk"
  cp "${PROJECT_ROOT}/android/app/build/outputs/apk/release/app-release.apk" "${apk}"
  "${ANDROID_HOME:?Set ANDROID_HOME}/build-tools/35.0.0/apksigner" verify --verbose --print-certs "${apk}"
  (cd "${PROJECT_ROOT}/dist" && sha256sum "FaceManager-Android-${version}.apk" > "FaceManager-Android-${version}.apk.sha256")
else
  cp "${PROJECT_ROOT}/android/app/build/outputs/apk/release/app-release-unsigned.apk" \
    "${PROJECT_ROOT}/dist/FaceManager-Android-${version}-unsigned.apk"
  printf 'Unsigned build created for private signing; do not publish it as installable.\n'
fi
