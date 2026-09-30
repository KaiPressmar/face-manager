#!/usr/bin/env bash

set -Eeuo pipefail

readonly MIN_UBUNTU_VERSION="22.04"
readonly MIN_NODE_MAJOR=20
readonly SYSTEM_PACKAGES=(
  build-essential
  ca-certificates
  curl
  gnupg
  libgl1
  libglib2.0-0
  pciutils
  python3
  python3-dev
  python3-venv
  zlib1g
)

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BACKEND_VENV="${PROJECT_ROOT}/backend/.venv"
export PATH="${HOME}/.local/bin:${PATH}"

ACCELERATOR="auto"
SKIP_SYSTEM_PACKAGES=0
DRY_RUN=0

log() {
  printf '\033[1;36m[face-manager]\033[0m %s\n' "$*"
}

die() {
  printf '\033[1;31m[face-manager]\033[0m %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: ./scripts/setup-dev.sh [options]

Set up the Face Manager backend and frontend on Ubuntu 22.04 or newer.

Options:
  --cpu                   Force the CPU-only ONNX Runtime setup.
  --gpu                   Require a supported NVIDIA GPU setup.
  --skip-system-packages  Do not install apt or Node.js system packages.
  --dry-run               Print the actions without changing the system.
  -h, --help              Show this help.
EOF
}

run() {
  if ((DRY_RUN)); then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

version_at_least() {
  dpkg --compare-versions "$1" ge "$2"
}

as_root() {
  if ((EUID == 0)); then
    run "$@"
  else
    command -v sudo >/dev/null 2>&1 ||
      die "sudo is required to install system packages."
    run sudo "$@"
  fi
}

parse_args() {
  while (($#)); do
    case "$1" in
      --cpu)
        [[ "${ACCELERATOR}" != "gpu" ]] ||
          die "--cpu and --gpu cannot be used together."
        ACCELERATOR="cpu"
        ;;
      --gpu)
        [[ "${ACCELERATOR}" != "cpu" ]] ||
          die "--cpu and --gpu cannot be used together."
        ACCELERATOR="gpu"
        ;;
      --skip-system-packages)
        SKIP_SYSTEM_PACKAGES=1
        ;;
      --dry-run)
        DRY_RUN=1
        ;;
      -h | --help)
        usage
        exit 0
        ;;
      *)
        die "Unknown option: $1"
        ;;
    esac
    shift
  done
}

check_platform() {
  [[ -r /etc/os-release ]] || die "Cannot identify the operating system."
  # shellcheck disable=SC1091
  source /etc/os-release

  [[ "${ID:-}" == "ubuntu" ]] ||
    die "This installer supports Ubuntu only. Detected: ${ID:-unknown}."
  version_at_least "${VERSION_ID:-0}" "${MIN_UBUNTU_VERSION}" ||
    die "Ubuntu ${MIN_UBUNTU_VERSION} or newer is required."

  log "Detected Ubuntu ${VERSION_ID} (${VERSION_CODENAME:-unknown}), $(uname -m)."
}

install_system_packages() {
  if ((SKIP_SYSTEM_PACKAGES)); then
    log "Skipping system package installation."
    return
  fi

  local missing_packages=()
  local package
  for package in "${SYSTEM_PACKAGES[@]}"; do
    if ! dpkg-query -W -f='${db:Status-Abbrev}' "${package}" 2>/dev/null |
      grep -q '^ii '; then
      missing_packages+=("${package}")
    fi
  done

  if ((${#missing_packages[@]} == 0)); then
    log "All required Ubuntu packages are already installed."
    return
  fi

  log "Installing missing Ubuntu packages: ${missing_packages[*]}"
  as_root apt-get update
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    "${missing_packages[@]}"
}

node_is_supported() {
  command -v node >/dev/null 2>&1 &&
    command -v npm >/dev/null 2>&1 &&
    [[ "$(node --version | sed -E 's/^v([0-9]+).*/\1/')" -ge "${MIN_NODE_MAJOR}" ]]
}

install_nodejs() {
  if node_is_supported; then
    log "Using Node.js $(node --version) and npm $(npm --version)."
    return
  fi

  ((SKIP_SYSTEM_PACKAGES == 0)) ||
    die "Node.js ${MIN_NODE_MAJOR}+ and npm are required when system packages are skipped."

  log "Installing Node.js 20 from the NodeSource apt repository."
  local key_tmp
  key_tmp="$(mktemp)"

  run curl -fsSL \
    https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    -o "${key_tmp}"
  as_root mkdir -p /etc/apt/keyrings
  as_root gpg --dearmor --yes \
    --output /etc/apt/keyrings/nodesource.gpg \
    "${key_tmp}"
  run rm -f "${key_tmp}"

  local architecture
  architecture="$(dpkg --print-architecture)"
  local source_line
  source_line="deb [arch=${architecture} signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main"

  if ((DRY_RUN)); then
    printf '+ write %q to %q\n' \
      "${source_line}" \
      "/etc/apt/sources.list.d/nodesource.list"
  elif ((EUID == 0)); then
    printf '%s\n' "${source_line}" > /etc/apt/sources.list.d/nodesource.list
  else
    printf '%s\n' "${source_line}" |
      sudo tee /etc/apt/sources.list.d/nodesource.list >/dev/null
  fi

  as_root apt-get update
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs

  ((DRY_RUN)) || node_is_supported ||
    die "Node.js installation completed, but Node.js ${MIN_NODE_MAJOR}+ is not available."
}

install_github_cli() {
  if command -v gh >/dev/null 2>&1; then
    log "Using GitHub CLI $(gh --version | sed -n '1p')."
    return
  fi

  log "Installing the official GitHub CLI release for the current user."
  local architecture
  case "$(uname -m)" in
    x86_64)
      architecture="amd64"
      ;;
    aarch64 | arm64)
      architecture="arm64"
      ;;
    *)
      die "GitHub CLI user installation does not support $(uname -m)."
      ;;
  esac

  if ((DRY_RUN)); then
    printf '+ install latest GitHub CLI linux_%s to %q\n' \
      "${architecture}" \
      "${HOME}/.local/bin/gh"
    return
  fi

  local version
  version="$(
    curl -fsSL https://api.github.com/repos/cli/cli/releases/latest |
      sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' |
      sed -n '1p'
  )"
  [[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
    die "Could not resolve the latest GitHub CLI version."

  local archive="gh_${version}_linux_${architecture}.tar.gz"
  local temp_dir
  temp_dir="$(mktemp -d)"

  curl -fsSL \
    "https://github.com/cli/cli/releases/download/v${version}/${archive}" \
    -o "${temp_dir}/${archive}"
  curl -fsSL \
    "https://github.com/cli/cli/releases/download/v${version}/gh_${version}_checksums.txt" \
    -o "${temp_dir}/checksums.txt"

  (
    cd "${temp_dir}"
    grep " ${archive}\$" checksums.txt | sha256sum --check --status
  ) || die "GitHub CLI checksum verification failed."

  tar -xzf "${temp_dir}/${archive}" -C "${temp_dir}"
  install -D -m 755 \
    "${temp_dir}/gh_${version}_linux_${architecture}/bin/gh" \
    "${HOME}/.local/bin/gh"
  rm -rf "${temp_dir}"

  ((DRY_RUN)) || command -v gh >/dev/null 2>&1 ||
    die "GitHub CLI installation completed, but gh is unavailable."
}

report_github_auth() {
  ((DRY_RUN)) && return

  if gh auth status --hostname github.com >/dev/null 2>&1; then
    log "GitHub CLI is authenticated."
  else
    log "GitHub CLI is installed but not authenticated."
    printf '  Run: gh auth login --hostname github.com --git-protocol ssh --web\n'
  fi
}

nvidia_gpu_is_supported() {
  [[ "$(uname -m)" == "x86_64" ]] || return 1
  command -v nvidia-smi >/dev/null 2>&1 || return 1
  nvidia-smi -L >/dev/null 2>&1 || return 1

  local driver_major
  driver_major="$(
    nvidia-smi --query-gpu=driver_version --format=csv,noheader |
      sed -n '1p' |
      cut -d. -f1
  )"
  [[ "${driver_major}" =~ ^[0-9]+$ ]] || return 1
  [[ "${driver_major}" -ge 525 ]]
}

select_accelerator() {
  case "${ACCELERATOR}" in
    auto)
      if nvidia_gpu_is_supported; then
        ACCELERATOR="gpu"
        log "Supported NVIDIA GPU and driver detected; selecting GPU setup."
      else
        ACCELERATOR="cpu"
        log "No supported NVIDIA CUDA setup detected; selecting CPU setup."
      fi
      ;;
    gpu)
      nvidia_gpu_is_supported ||
        die "GPU setup requires x86_64, a visible NVIDIA GPU, and driver 525 or newer."
      log "NVIDIA GPU setup requested and supported."
      ;;
    cpu)
      log "CPU-only setup requested."
      ;;
  esac
}

python_requirements_are_satisfied() {
  local python="$1"
  "${python}" - "${PROJECT_ROOT}/backend/requirements.txt" <<'PY'
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

for raw_line in Path(sys.argv[1]).read_text().splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    requirement = Requirement(line)
    if requirement.marker and not requirement.marker.evaluate():
        continue
    try:
        installed = metadata.version(requirement.name)
    except metadata.PackageNotFoundError:
        raise SystemExit(1)
    if requirement.specifier and installed not in requirement.specifier:
        raise SystemExit(1)
PY
}

python_has_provider() {
  local python="$1"
  local provider="$2"
  "${python}" - "${provider}" <<'PY'
import sys
import onnxruntime as ort

provider = sys.argv[1]
if provider == "CUDAExecutionProvider" and hasattr(ort, "preload_dlls"):
    ort.preload_dlls(directory="")
raise SystemExit(0 if provider in ort.get_available_providers() else 1)
PY
}

python_gpu_runtime_is_satisfied() {
  local python="$1"
  "${python}" <<'PY'
from importlib import metadata

from packaging.specifiers import SpecifierSet

requirements = {
    "onnxruntime-gpu": SpecifierSet(">=1.21,<2"),
    "nvidia-cuda-runtime-cu12": SpecifierSet(),
    "nvidia-cudnn-cu12": SpecifierSet(),
}
for package, specifier in requirements.items():
    try:
        installed = metadata.version(package)
    except metadata.PackageNotFoundError:
        raise SystemExit(1)
    if specifier and installed not in specifier:
        raise SystemExit(1)

try:
    metadata.version("onnxruntime")
except metadata.PackageNotFoundError:
    pass
else:
    raise SystemExit(1)
PY
}

python_cpu_runtime_is_satisfied() {
  local python="$1"
  "${python}" <<'PY'
from importlib import metadata

from packaging.specifiers import SpecifierSet

try:
    installed = metadata.version("onnxruntime")
except metadata.PackageNotFoundError:
    raise SystemExit(1)
if installed not in SpecifierSet(">=1.18,<2"):
    raise SystemExit(1)

try:
    metadata.version("onnxruntime-gpu")
except metadata.PackageNotFoundError:
    pass
else:
    raise SystemExit(1)
PY
}

frontend_dependencies_are_satisfied() {
  [[ -d "${PROJECT_ROOT}/frontend/node_modules" ]] || return 1
  node - "${PROJECT_ROOT}/frontend" <<'JS'
const fs = require("fs");
const path = require("path");

const root = process.argv[2];
const packageJson = JSON.parse(
  fs.readFileSync(path.join(root, "package.json"), "utf8")
);
const lock = JSON.parse(
  fs.readFileSync(path.join(root, "package-lock.json"), "utf8")
);
const lockedRoot = lock.packages && lock.packages[""];

if (!lockedRoot) process.exit(1);

for (const section of ["dependencies", "devDependencies"]) {
  const declared = packageJson[section] || {};
  const lockedDeclared = lockedRoot[section] || {};
  if (JSON.stringify(declared) !== JSON.stringify(lockedDeclared)) {
    process.exit(1);
  }

  for (const name of Object.keys(declared)) {
    const lockEntry = lock.packages[`node_modules/${name}`];
    if (!lockEntry) process.exit(1);

    let installed;
    try {
      installed = JSON.parse(
        fs.readFileSync(
          path.join(root, "node_modules", name, "package.json"),
          "utf8"
        )
      );
    } catch {
      process.exit(1);
    }
    if (installed.version !== lockEntry.version) process.exit(1);
  }
}
JS
}

setup_backend() {
  local created_venv=0
  if [[ ! -x "${BACKEND_VENV}/bin/python" ]]; then
    log "Creating the Python virtual environment."
    run python3 -m venv "${BACKEND_VENV}"
    created_venv=1
  else
    log "Using the existing Python virtual environment."
  fi

  local python="${BACKEND_VENV}/bin/python"
  if ((created_venv && DRY_RUN)); then
    run "${python}" -m pip install --upgrade pip setuptools wheel
    run "${python}" -m pip install -r "${PROJECT_ROOT}/backend/requirements.txt"
    if [[ "${ACCELERATOR}" == "gpu" ]]; then
      run "${python}" -m pip install 'onnxruntime-gpu[cuda,cudnn]>=1.21,<2'
    else
      run "${python}" -m pip install 'onnxruntime>=1.18,<2'
    fi
    return
  fi

  if ((created_venv)); then
    run "${python}" -m pip install --upgrade pip setuptools wheel
    log "Installing backend Python requirements into the new environment."
    run "${python}" -m pip install -r "${PROJECT_ROOT}/backend/requirements.txt"
  elif python_requirements_are_satisfied "${python}"; then
    log "All backend Python requirements are already satisfied."
  else
    log "Installing missing or incompatible backend Python requirements."
    run "${python}" -m pip install -r "${PROJECT_ROOT}/backend/requirements.txt"
  fi

  if [[ "${ACCELERATOR}" == "gpu" ]]; then
    if python_has_provider "${python}" "CUDAExecutionProvider" &&
      python_gpu_runtime_is_satisfied "${python}"; then
      log "CUDAExecutionProvider and its runtime libraries are already available."
    else
      log "Installing an exclusive CUDA-enabled ONNX Runtime."
      run "${python}" -m pip uninstall -y onnxruntime onnxruntime-gpu
      run "${python}" -m pip install 'onnxruntime-gpu[cuda,cudnn]>=1.21,<2'
    fi
  else
    if python_has_provider "${python}" "CPUExecutionProvider" &&
      python_cpu_runtime_is_satisfied "${python}"; then
      log "CPU-only ONNX Runtime is already available."
    else
      log "Installing an exclusive CPU-only ONNX Runtime."
      run "${python}" -m pip uninstall -y onnxruntime onnxruntime-gpu
      run "${python}" -m pip install 'onnxruntime>=1.18,<2'
    fi
  fi
}

setup_frontend() {
  if frontend_dependencies_are_satisfied; then
    log "Frontend dependencies already match package-lock.json."
    return
  fi

  log "Installing frontend dependencies from package-lock.json."
  run npm --prefix "${PROJECT_ROOT}/frontend" install
}

verify_setup() {
  ((DRY_RUN)) && return

  local python="${BACKEND_VENV}/bin/python"
  log "Verifying Python dependencies."
  "${python}" "${PROJECT_ROOT}/scripts/check-python-dependencies.py"

  local providers
  providers="$(
    "${python}" -c '
import onnxruntime as ort
if "CUDAExecutionProvider" in ort.get_available_providers() and hasattr(ort, "preload_dlls"):
    ort.preload_dlls(directory="")
print(",".join(ort.get_available_providers()))
'
  )"
  log "ONNX Runtime providers: ${providers}"

  if [[ "${ACCELERATOR}" == "gpu" ]] &&
    [[ ",${providers}," != *",CUDAExecutionProvider,"* ]]; then
    die "GPU setup was selected, but CUDAExecutionProvider is unavailable."
  fi

  log "Checking the backend source."
  "${python}" -m py_compile \
    "${PROJECT_ROOT}/backend/app.py" \
    "${PROJECT_ROOT}/backend/config.py" \
    "${PROJECT_ROOT}/backend/db/schema.py" \
    "${PROJECT_ROOT}/backend/models/clustering.py" \
    "${PROJECT_ROOT}/backend/models/face_model.py" \
    "${PROJECT_ROOT}/backend/services/import_queue.py" \
    "${PROJECT_ROOT}/backend/services/pipeline.py" \
    "${PROJECT_ROOT}/backend/services/storage.py"

  log "Checking and building the frontend."
  npm --prefix "${PROJECT_ROOT}/frontend" run typecheck
  npm --prefix "${PROJECT_ROOT}/frontend" run build
}

print_next_steps() {
  if ((DRY_RUN)); then
    printf '\nDry run complete. No changes were made.\n'
    return
  fi

  cat <<EOF

Development environment ready (${ACCELERATOR^^}).

Start the backend:
  ./scripts/dev-backend.sh

Start the frontend in a second terminal:
  cd frontend
  npm run dev

Then open http://localhost:5173
EOF
}

main() {
  parse_args "$@"
  check_platform
  install_system_packages
  install_nodejs
  install_github_cli
  report_github_auth
  select_accelerator
  setup_backend
  setup_frontend
  verify_setup
  print_next_steps
}

main "$@"
