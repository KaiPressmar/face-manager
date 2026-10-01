#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
[[ $# == 1 && -f "$1" ]] || { printf 'Usage: %s <CI-unsigned.apk>\n' "$0" >&2; exit 1; }
: "${ANDROID_HOME:?Set ANDROID_HOME}"
: "${ANDROID_KEYSTORE_PATH:?Set ANDROID_KEYSTORE_PATH}"
: "${ANDROID_KEYSTORE_PASSWORD:?Set ANDROID_KEYSTORE_PASSWORD}"
: "${ANDROID_KEY_ALIAS:?Set ANDROID_KEY_ALIAS}"
: "${ANDROID_KEY_PASSWORD:?Set ANDROID_KEY_PASSWORD}"
version="$(tr -d '[:space:]' < "${PROJECT_ROOT}/VERSION")"
tools="${ANDROID_HOME}/build-tools/35.0.0"
metadata="$("${tools}/aapt" dump badging "$1")"
[[ "${metadata}" == *"name='de.face_manager.app'"* && "${metadata}" == *"versionName='${version}'"* ]] || {
  printf 'APK package or version does not match this checkout.\n' >&2; exit 1;
}
[[ "${metadata}" != *application-debuggable* ]] || { printf 'Refusing to release a debuggable APK.\n' >&2; exit 1; }
mkdir -p "${PROJECT_ROOT}/dist"
apk="${PROJECT_ROOT}/dist/FaceManager-Android-${version}.apk"
"${tools}/zipalign" -c -P 16 4 "$1"
"${tools}/apksigner" sign --ks "${ANDROID_KEYSTORE_PATH}" --ks-key-alias "${ANDROID_KEY_ALIAS}" \
  --ks-pass env:ANDROID_KEYSTORE_PASSWORD --key-pass env:ANDROID_KEY_PASSWORD --out "${apk}" "$1"
"${tools}/apksigner" verify --verbose --print-certs "${apk}"
(cd "${PROJECT_ROOT}/dist" && sha256sum "FaceManager-Android-${version}.apk" > "FaceManager-Android-${version}.apk.sha256")
