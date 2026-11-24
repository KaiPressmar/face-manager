#!/usr/bin/env bash
# Build the Ubuntu 26.04 amd64 desktop .deb and SHA-256; --check inspects an existing artifact.
set -Eeuo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
packaging_dir="$project_root/packaging/linux"
version="$(tr -d '[:space:]' < "$project_root/VERSION")"

if [[ "${1:-}" == "--check" && $# == 2 ]]; then
    exec python3 "$packaging_dir/bundle.py" check "$2" "$version"
fi
if (( $# )); then
    printf 'Usage: %s [--check face-manager_VERSION_amd64.deb]\n' "$0" >&2
    exit 2
fi

if ! (source /etc/os-release && [[ "$ID" == ubuntu && "$VERSION_ID" == 26.04 ]]) || [[ $(uname -m) != x86_64 ]]; then
    printf 'Build on the Ubuntu 26.04 amd64 target, using its Python 3.14 and GI packages.\n' >&2
    exit 1
fi
for command in /usr/bin/python3.14 npm dpkg-deb desktop-file-validate sha256sum; do
    command -v "$command" >/dev/null || { printf 'Missing build command: %s\n' "$command" >&2; exit 1; }
done

output_dir="${FACE_MANAGER_LINUX_OUTPUT_DIR:-$project_root/dist}"
umask 022
mkdir -p -- "$output_dir"
output_dir="$(cd -- "$output_dir" && pwd)"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/face-manager-linux.XXXXXXXX")"
trap 'rm -rf -- "$work_dir"' EXIT
staging="$work_dir/package"
app_dir="$staging/opt/face-manager"
vendor="$app_dir/vendor"
mkdir -p "$staging/DEBIAN" "$vendor"

# Build the frontend away from any running development server or other build.
cp "$project_root/VERSION" "$work_dir/VERSION"
mkdir "$work_dir/frontend"
cp -a "$project_root/frontend/"{index.html,package.json,package-lock.json,public,src,tsconfig.json,vite.config.js} "$work_dir/frontend/"
npm --prefix "$work_dir/frontend" ci --no-audit --no-fund
npm --prefix "$work_dir/frontend" run build -- --outDir "$app_dir/frontend/dist"

/usr/bin/python3.14 -m venv "$work_dir/build-env"
python="$work_dir/build-env/bin/python"
# Keep this install in one resolver run so the Linux pins intersect the app's
# shared ranges. Only the GPU ORT distribution provides the Python module.
# Ignore old local wheels so hnswlib is rebuilt for portable x86-64.
HNSWLIB_NO_NATIVE=1 "$python" -m pip install --disable-pip-version-check --timeout 60 --no-cache-dir --no-compile --target "$vendor" \
    -r "$project_root/backend/requirements.txt" -r "$packaging_dir/requirements.txt"
# Console scripts point to the temporary build venv. The ONNX libraries also
# ship their own test and example models, which Face Manager does not use.
rm -rf -- "$vendor/bin" "$vendor/onnx/backend/test/data" "$vendor/onnxruntime/datasets"
find "$vendor" -name '*.onnx' -type f -delete

(
    cd "$project_root"
    find backend -maxdepth 1 -name '*.py' -type f -exec cp --parents -t "$app_dir" {} +
    find backend/{db,models,services} -name '*.py' -type f -exec cp --parents -t "$app_dir" {} +
)
install -m 0644 "$project_root/"{VERSION,CHANGELOG.md} "$app_dir/"
install -m 0644 "$packaging_dir/launch.py" "$app_dir/launch.py"
printf 'linux\n' > "$app_dir/BUILD_VARIANT"
install -Dm 0755 "$packaging_dir/face-manager" "$staging/usr/bin/face-manager"
install -Dm 0644 "$packaging_dir/face-manager.desktop" "$staging/usr/share/applications/face-manager.desktop"
install -Dm 0644 "$project_root/packaging/windows/assets/face-manager-icon.svg" \
    "$staging/usr/share/icons/hicolor/scalable/apps/face-manager.svg"
# Checkouts may be private. The package installs its read-only code as root,
# so application files and directories also need normal desktop-user access.
chmod -R a+rX "$app_dir"

desktop-file-validate "$staging/usr/share/applications/face-manager.desktop"
/usr/bin/python3.14 "$packaging_dir/bundle.py" manifest "$app_dir" "$version"
/usr/bin/python3.14 -IB "$app_dir/launch.py" --check-runtime

depends="$(/usr/bin/python3.14 "$packaging_dir/bundle.py" depends)"
installed_size="$(du --apparent-size -sk "$staging" | cut -f1)"
cat > "$staging/DEBIAN/control" <<EOF
Package: face-manager
Version: $version
Architecture: amd64
Section: graphics
Priority: optional
Maintainer: Face Manager maintainers <KaiPressmar@users.noreply.github.com>
Installed-Size: $installed_size
Depends: $depends
Recommends: xwayland
Homepage: https://github.com/KaiPressmar/face-manager
Description: Manage a local photo library by the people in it
 Face Manager identifies faces and helps review local photo collections.
 This Ubuntu desktop package supports CPU inference and compatible graphics
 hardware through bundled CUDA and OpenVINO userspace runtimes.
EOF

artifact="face-manager_${version}_amd64.deb"
dpkg-deb --root-owner-group -Zzstd -z10 --build "$staging" "$work_dir/$artifact"
/usr/bin/python3.14 "$packaging_dir/bundle.py" check "$work_dir/$artifact" "$version"
mv -- "$work_dir/$artifact" "$output_dir/$artifact"
(cd "$output_dir" && sha256sum "$artifact" > "$artifact.sha256")
printf 'Built %s\nSHA-256 %s\n' "$output_dir/$artifact" "$output_dir/$artifact.sha256"
