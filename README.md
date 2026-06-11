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
scripts/
  setup-dev.sh           Ubuntu development environment installer
  release-version.sh     Semantic release version helper
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
```

The default requirements install CPU ONNX Runtime. The first image import
downloads the InsightFace `buffalo_l` model into:

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

After installing the normal backend requirements, install GPU ONNX Runtime
and its CUDA/cuDNN dependencies last:

```bash
source backend/.venv/bin/activate
python -m pip install \
  --upgrade \
  --force-reinstall \
  'onnxruntime-gpu[cuda,cudnn]>=1.21,<2'
```

Verify that ONNX Runtime can see CUDA:

```bash
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

The output must contain `CUDAExecutionProvider`. Face Manager chooses CUDA
automatically when that provider is available and otherwise uses the CPU.
The NVIDIA driver must support CUDA 12. The runtime CUDA and cuDNN libraries
are installed inside the Python environment.

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
source backend/.venv/bin/activate
python -m uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

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

The frontend currently expects the backend at `http://localhost:8000`, so keep
the backend on port `8000` during development.

## Importing Images

1. Open the People view.
2. Select **Ordner hinzufügen**.
3. Enter a Windows path such as `D:\Bilder\Sortiert` when running under WSL2.
4. Start the import and follow the progress indicator.

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

Run the same checks used by GitHub Actions:

```bash
./scripts/check-all.sh
```

The production frontend can be previewed after building:

```bash
cd frontend
npm run preview
```

## Git Workflow

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
`develop`. See [CONTRIBUTING.md](CONTRIBUTING.md) for naming, validation,
release steps, and recommended branch-protection settings.

## Release Versioning

Face Manager uses semantic versioning (`MAJOR.MINOR.PATCH`). The canonical
release number lives in the root `VERSION` file and is:

- Displayed in the application topbar
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

The helper only updates version files. It deliberately does not commit or tag
the release. Commit the version bump on `develop`, then open the release pull
request from `develop` to `main`:

```bash
git add VERSION frontend/package.json frontend/package-lock.json
git commit -m "Release v1.2.0"
git push origin develop
```

After that PR merges and CI succeeds on `main`, GitHub Actions creates the
annotated `v1.2.0` tag and publishes a GitHub Release with generated notes.

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

If `CUDAExecutionProvider` is absent, verify `nvidia-smi`, the installed
`onnxruntime-gpu` package, and CUDA/cuDNN compatibility.

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
