# Face Manager

Face Manager is a local photo-library tool that detects faces, creates face
embeddings, groups similar faces into clusters, and lets you assign those
clusters to people. The React interface can browse imported images, filter by
people, and filter by any discovered folder level.

The application stores image paths and face metadata in SQLite. It does not
copy the source images into the project.

## Features

- Recursive import of JPEG and PNG image folders
- InsightFace detection and 512-dimensional face embeddings
- Incremental cosine-similarity clustering with HNSW
- Person assignment and manual cluster cleanup
- Masonry image browser with face overlays
- Full-screen image gallery with keyboard navigation, clipboard copy, and
  system file-location actions
- Searchable, multi-select folder browser with nested-folder filtering
- SQLite persistence with automatic schema initialization and migration
- CPU support by default and optional NVIDIA GPU acceleration

## Project Structure

```text
backend/
  app.py                 FastAPI routes
  config.py              Local database configuration
  db/                    SQLite schema and generated database
  models/                Face detection and clustering
  services/              Import pipeline and storage queries
frontend/
  src/                   React application
  package.json           Frontend scripts and dependencies
.github/workflows/
  ci.yml                 Pull request and branch validation
  release.yml            Tag, release, and Windows artifact publication
packaging/windows/
  build-release.ps1      Windows desktop bundle builder
  FaceManager.iss        Inno Setup installer definition
scripts/
  setup-dev.sh           Ubuntu development environment installer
  release-version.sh     Semantic release version helper
CHANGELOG.md              Curated user-facing release notes
AGENTS.md                 Codex repository instructions
CLAUDE.md                 Claude repository instructions
CONTRIBUTING.md           Development and release workflow
VERSION                  Canonical application release version
```

## Requirements

Recommended development environment:

- Linux or WSL2
- Python 3.10 or newer
- Node.js 20 or newer
- npm 10 or newer
- A C/C++ compiler for `hnswlib`
- Enough disk space for the InsightFace `buffalo_l` model

## Automated Setup

On Ubuntu 22.04 or newer, the setup script installs the system packages,
Node.js 20 when necessary, the Python environment, backend dependencies, and
frontend dependencies. It also installs the current official GitHub CLI
release into `~/.local/bin` and verifies its SHA-256 checksum. The setup
finishes by compiling the backend and building the frontend:

```bash
./scripts/setup-dev.sh
```

In automatic mode, the installer selects NVIDIA acceleration when all of the
following are available:

- An `x86_64` machine
- A GPU visible through `nvidia-smi`
- NVIDIA driver version 525 or newer

Otherwise it installs the CPU runtime. The GPU setup uses pip-managed CUDA 12
and cuDNN libraries, so a separate CUDA toolkit installation is not required.

Available installer options:

```text
--cpu                   Force CPU-only installation
--gpu                   Require NVIDIA GPU installation
--skip-system-packages  Skip apt and Node.js installation
--dry-run               Print actions without changing the system
--help                  Show all options
```

Examples:

```bash
# Inspect the planned actions
./scripts/setup-dev.sh --dry-run

# Force a CPU development environment
./scripts/setup-dev.sh --cpu

# Fail instead of falling back when NVIDIA GPU support is unavailable
./scripts/setup-dev.sh --gpu
```

The script is safe to rerun. Before making changes it checks:

- Which Ubuntu packages are missing
- Whether the installed Node.js version is supported
- Whether GitHub CLI is installed
- Whether the Python virtual environment already exists
- Whether installed Python packages satisfy `requirements.txt`
- Whether the selected ONNX Runtime provider is already usable
- Whether `node_modules` matches `package-lock.json`

Only missing or incompatible dependencies are installed. It does not
force-reinstall packages, remove an existing GPU runtime during an automatic
or CPU run, replace a valid virtual environment, or recreate a matching
`node_modules` directory. Existing source images and the SQLite database are
never modified.

If GitHub CLI is not authenticated yet, the setup prints the login command:

```bash
gh auth login --hostname github.com --git-protocol ssh --web
```

NVIDIA acceleration is optional. CPU processing works without CUDA, but large
photo libraries will process considerably more slowly.

## Manual Setup

Clone the repository and enter its root directory:

```bash
git clone <repository-url>
cd face-manager
```

### Backend

Create an isolated Python environment and install all backend dependencies:

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r backend/requirements.txt
python -m pip install 'onnxruntime>=1.18,<2'
```

The command above installs CPU ONNX Runtime. The first image import downloads
the InsightFace `buffalo_l` model into:

```text
~/.insightface/models/buffalo_l
```

The first import therefore requires internet access. Later runs use the cached
model.

### Optional NVIDIA GPU Support

First verify that the NVIDIA driver is visible inside Linux or WSL:

```bash
nvidia-smi
```

After installing the normal backend requirements, remove any existing ONNX
Runtime wheel and install only the GPU wheel with its CUDA/cuDNN dependencies:

```bash
source backend/.venv/bin/activate
python -m pip uninstall -y onnxruntime onnxruntime-gpu
python -m pip install \
  'onnxruntime-gpu[cuda,cudnn]>=1.21,<2'
```

Verify that ONNX Runtime can see CUDA:

```bash
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

The output must contain `CUDAExecutionProvider`. Face Manager chooses CUDA
automatically when that provider is available and otherwise uses the CPU.
The NVIDIA driver must support CUDA 12. The runtime CUDA and cuDNN libraries
are installed inside the Python environment. InsightFace declares the CPU
package name as a dependency, so plain `pip check` may report that
`onnxruntime` is missing in a correct GPU-only environment; the project check
script verifies the GPU substitute and CUDA provider instead.

Image hashing and decoding run in parallel with face inference. Face Manager
automatically uses up to four preparation workers in GPU mode and up to two in
CPU mode. To override this for unusually fast storage or limited memory, set
`FACE_MANAGER_IMPORT_WORKERS` before starting the backend:

```bash
FACE_MANAGER_IMPORT_WORKERS=3 \
  ./scripts/dev-backend.sh
```

### Import Queue

Folder imports are persisted in SQLite and processed by one background worker.
This keeps the shared face model and clustering index serialized even when the
import endpoint is called repeatedly.

```text
POST   /api/imports             Queue a folder import
GET    /api/imports             List active, queued, and recent jobs
DELETE /api/imports/{job_id}    Cancel a running job or remove another job
```

Queued jobs can be removed immediately. Running jobs stop cooperatively after
the current image finishes, because interrupting an active GPU inference or
database transaction could leave inconsistent state.

If the backend exits or restarts, jobs that were queued, running, or cancelling
are restored to the queue in their original FIFO order. Processing resumes by
rescanning the folder and skipping images whose results were already committed,
so the interrupted image is retried without reprocessing completed images.

Every repeat import enumerates and hashes all selected files again. Hashes are
matched against the indexed canonical image records, so unchanged or moved
duplicates are registered without decoding the image or running face inference.
If content at an existing path changed, that location is detached from the old
content and the replacement is processed safely as a new image.

When known content appears at a new location, Face Manager also validates its
older registered locations. Missing paths and paths whose current hash no
longer matches are removed. Valid copies remain assigned to the same canonical
image, which is displayed once in the UI with all available locations listed.

### Frontend

Install the locked frontend dependencies:

```bash
cd frontend
npm ci
cd ..
```

## Development

Run the backend and frontend in separate terminals. Commands below assume both
terminals start in the repository root.

### VS Code

Open the repository root in VS Code and install the workspace recommendations
when prompted. The workspace includes:

- Python, Pylance, debugpy, and Ruff integration
- Prettier and YAML formatting
- GitHub Actions and GitHub Pull Requests support
- Backend and frontend development tasks
- Backend and browser debugging
- A compound **Full Stack: Debug** configuration

Useful commands from **Terminal > Run Task**:

- **Setup: Development environment**
- **Full Stack: Dev servers**
- **Check: All**
- **GitHub: Authenticate CLI**
- **GitHub: Configure repository**
- **Release: Bump patch/minor/major**

Press `F5` and choose **Full Stack: Debug** to start FastAPI under the Python
debugger, start Vite, and attach the browser debugger.

### Terminal 1: FastAPI Backend

```bash
./scripts/dev-backend.sh
```

The script watches only backend runtime Python sources. Frontend files,
backend tests, the virtual environment, SQLite data, logs, and generated
thumbnails do not restart the API. Use the same script through the VS Code
**Backend: Dev server** task; `F5` uses the equivalent restricted watcher.

Useful backend URLs:

- API: `http://localhost:8000`
- Interactive API documentation: `http://localhost:8000/docs`
- OpenAPI schema: `http://localhost:8000/openapi.json`

### Terminal 2: React Frontend

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173` in the browser.

Vite forwards `/api` to the backend on port `8000`, including the live event
stream. Port `5173` is strict: if another stale frontend process already owns
it, startup fails clearly instead of silently moving to a different address.

## Importing Images

1. Open the People view.
2. Select **Ordner hinzufügen**.
3. Choose a folder through the native filesystem dialog, or paste a path such as
   `D:\Bilder\Sortiert` or `/home/kai/photos`.
4. Start the import and follow the progress indicator.

When the backend runs under WSL2, Windows-style paths entered in the UI are
translated automatically for backend access, while the UI continues to display
Windows-form paths so file locations stay familiar.

The importer scans subfolders recursively and currently supports:

- `.jpg`
- `.jpeg`
- `.png`

Images are identified by a SHA-256 hash of their file contents. Importing the
same image repeatedly, including from different folders, creates one library
image with multiple source locations. Face detection runs only once for that
content. At least one imported source file must remain available because the
database stores references rather than image copies.

Use **Ordnerfilter** to select one or more discovered folders. Selecting a
folder includes images from all of its descendants.

## Database

The SQLite database is created automatically at:

```text
backend/db/database.sqlite
```

Application startup initializes new databases and upgrades legacy schemas when
necessary. Existing images are hashed once during this upgrade and duplicates
are consolidated. The normalized schema keeps content identity in `image`,
source paths in `image_location`, and face embeddings in `face`.

Database files are ignored by Git. To back up the local library:

```bash
cp backend/db/database.sqlite backend/db/database.backup.sqlite
```

To start with an empty library, stop the backend and remove the database file.
The next backend start creates a fresh database. This permanently removes local
assignments, clusters, and embeddings.

## Validation Commands

Run the complete local equivalent of the GitHub Actions checks:

```bash
./scripts/check-all.sh
```

This validates version, changelog, and packaging metadata, generates a temporary
dependency inventory, compiles and tests the backend, type-checks the frontend,
and creates a production frontend build. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the CI job mapping and pull request
requirements.

The production frontend can be previewed after building:

```bash
cd frontend
npm run preview
```

## Windows Desktop Release

The publication workflow for a successfully tested release commit on `main`
produces Windows installer bundles and uploads them to the matching GitHub
Release as:

```text
FaceManager-Setup-X.Y.Z.exe
FaceManager-Setup-GPU-X.Y.Z.exe
```

The installed app opens in its own native desktop window, starts the bundled
backend automatically, and stores its SQLite database under the current user's
local app-data directory on Windows:

```text
%LOCALAPPDATA%\FaceManager\database.sqlite
```

The first image import still downloads the InsightFace model if it is not
cached yet, so the first run that processes faces requires internet access.

The GPU installer is intended for Windows systems with a supported NVIDIA GPU.
It bundles `onnxruntime-gpu` plus the CUDA 12 and cuDNN 9 Python runtime
packages used by ONNX Runtime. Systems still need a compatible NVIDIA driver.
When CUDA is unavailable at runtime, the app falls back to CPU execution.

### Build the Windows Installer Manually

On a Windows machine with Python 3.10+, Node.js 20+, and Inno Setup 6:

```powershell
python -m pip install -r backend/requirements.txt -r backend/requirements-desktop.txt "onnxruntime>=1.21,<2"
./packaging/windows/build-release.ps1

# GPU-capable installer variant
./packaging/windows/build-release.ps1 -Variant gpu
```

That script rebuilds the frontend, creates the bundled desktop app with
PyInstaller, and writes the installer into:

```text
dist/FaceManager-Setup-X.Y.Z.exe
dist/FaceManager-Setup-GPU-X.Y.Z.exe
```

The build also writes a matching `.sha256` file next to each installer. The
release workflow attaches that checksum to the GitHub Release and, for a public
repository, records a GitHub build-provenance attestation for the installer.
These records make the artifact origin and integrity verifiable, but they are
not a replacement for Windows Authenticode signing.

Windows release builds embed the canonical version and neutral project metadata
in both `FaceManager.exe` and the installer. The current automated workflow does
not sign either file. The optional `-RequireSigned` switch does not perform
signing; it is a future guard that makes the build fail unless the application
and installer already carry valid Authenticode signatures.

Each release also bundles its curated `CHANGELOG.md` section. Face Manager shows
these high-level notes once on the first start after an update; clicking the
version number beside the application name in the sidebar opens them again later.

The desktop app checks the latest public GitHub Release at startup and then at
most once per hour. When a newer semantic version exists, it shows the curated
release notes and selects the installer matching the embedded CPU/GPU build
variant. Users can postpone or skip that version, open its GitHub page, or
download it. Face Manager verifies the downloaded installer against the
published SHA-256 file and GitHub asset digest before offering installation.
Installation always needs a second explicit confirmation; the app never runs a
silent update. Automatic checks can be disabled or triggered manually under
**Settings > Updates**. A check sends a normal request to GitHub but no local
image, face, or person data.

Because the installers are currently unsigned, Windows SmartScreen can still
show a warning after a verified installer is launched. Do not remove the
confirmation step until Authenticode signing is part of the release pipeline.

### Inspect Declared Dependencies

Generate a factual JSON inventory from the backend requirement manifests and
the resolved frontend lock file:

```bash
python scripts/inventory-dependencies.py
```

This inventory intentionally makes no claims about license compatibility or
redistribution rights. Windows builds keep a copy under `build/` for review; it
is not included in the installer or uploaded with releases.

## Development and Release Workflow

Development follows a two-branch model:

- `develop` is the integration branch for ongoing work.
- `main` contains released code only.
- Feature branches start from and merge into `develop`.
- Releases merge from `develop` into `main` through a pull request.

Start a change:

```bash
git switch develop
git pull --ff-only origin develop
git switch -c feature/my-change
```

After validation, push the feature branch and open a pull request targeting
`develop`. Ordinary changes are squash-merged into `develop`. A release is
prepared on a dedicated branch and merged through a pull request into `develop`.
The subsequent release pull request from `develop` to `main` is merged with a
merge commit.

[CONTRIBUTING.md](CONTRIBUTING.md) is the canonical guide for branch naming,
local validation, CI jobs, version preparation, release automation, artifacts,
failure recovery, and recommended branch protection.

## Release Versioning

Face Manager uses semantic versioning (`MAJOR.MINOR.PATCH`). The canonical
release number lives in the root `VERSION` file and is:

- Displayed beside the application name in the sidebar
- Used as the FastAPI application version
- Available from `GET /api/version`
- Mirrored into `frontend/package.json` and `package-lock.json`

Use the release helper to prepare a version:

```bash
# Increment one semantic component
./scripts/release-version.sh patch
./scripts/release-version.sh minor
./scripts/release-version.sh major

# Or select an explicit version
./scripts/release-version.sh 1.2.0
```

The helper updates version files and moves the current `CHANGELOG.md`
`Unreleased` entries into the dated release section. It deliberately does not
commit or tag the release. Every commit released from `main` must have a unique version; an
existing tag or release must never be reused for changed source. Prepare the
version on a dedicated branch and merge it into `develop` through a pull
request, then follow the release checklist in
[CONTRIBUTING.md](CONTRIBUTING.md):

```bash
git switch -c chore/release-1.2.0
./scripts/release-version.sh 1.2.0
git add VERSION frontend/package.json frontend/package-lock.json CHANGELOG.md
git commit -m "Release v1.2.0"
git push -u origin HEAD
```

After that PR merges and CI succeeds on `main`, GitHub Actions creates the
annotated `v1.2.0` tag and GitHub Release, then builds and uploads both Windows
installer variants, their SHA-256 files, and public-repository provenance
attestations. The GitHub Release and the application's first-start dialog use
the same curated changelog text. Wait for both Windows matrix jobs before
announcing the release.

## Troubleshooting

### `hnswlib` fails to install

Confirm that `build-essential` and `python3-dev` are installed, then upgrade
the Python packaging tools:

```bash
python -m pip install --upgrade pip setuptools wheel
```

### InsightFace or OpenCV reports a missing shared library

Install the common OpenCV runtime libraries:

```bash
sudo apt install -y libgl1 libglib2.0-0
```

### Processing uses the CPU despite an NVIDIA GPU

Check the available providers:

```bash
source backend/.venv/bin/activate
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

If `CUDAExecutionProvider` is absent, verify `nvidia-smi` and CUDA/cuDNN
compatibility. Also run `python -m pip show onnxruntime onnxruntime-gpu`;
only `onnxruntime-gpu` should be installed for GPU mode.

### The frontend cannot reach the API

Confirm that:

- FastAPI is running on port `8000`.
- Vite is running on port `5173`.
- `http://localhost:8000/docs` opens in the browser.
- No other application is occupying either port.

### Images disappear after moving a source folder

The database stores absolute paths. Move the images back to their original
location or re-import them from the new path after resetting or updating the
local database.

## Local-Only Scope

This project is configured for trusted local development. CORS is open and
there is no authentication. Do not expose the backend directly to an
untrusted network without adding authentication, access controls, and a
restricted CORS policy.
