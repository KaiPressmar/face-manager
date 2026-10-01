#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
python3 "${SCRIPT_DIR}/prepare-android-models.py"
"${PROJECT_ROOT}/android/gradlew" -p "${PROJECT_ROOT}/android" --no-daemon \
  :app:testDebugUnitTest :app:lintDebug :app:assembleDebug :app:assembleDebugAndroidTest
if [[ "${ANDROID_CONNECTED_TESTS:-0}" == 1 ]]; then
  "${PROJECT_ROOT}/android/gradlew" -p "${PROJECT_ROOT}/android" --no-daemon :app:connectedDebugAndroidTest
fi
