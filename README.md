# Face Manager

<p align="center">
  <strong>Turn a folder full of photos into a browsable, searchable people library — locally.</strong>
</p>

<p align="center">
  Face detection · Similarity clustering · Person assignment · Folder-aware browsing
</p>

Face Manager scans your existing photo folders, detects faces, groups similar faces,
and gives you a visual interface for assigning those groups to people. Your photos
stay where they are: the application stores only paths, thumbnails, face metadata,
and embeddings in a local SQLite database.

> [!IMPORTANT]
> Face Manager is designed for trusted, local use. Images and face data are not
> uploaded to a hosted service.

## Why Face Manager?

Large photo collections are easy to accumulate and difficult to explore. Face Manager
adds a people-first view without forcing you to reorganize, duplicate, or upload the
original files.

- **Keep your existing folder structure** — imports reference source files instead of
  copying them into the project.
- **Find people across folders** — browse assigned people, face clusters, and nested
  folder selections from one interface.
- **Avoid duplicate processing** — content hashes identify identical images even when
  they appear at multiple locations.
- **Run on your own hardware** — CPU processing works out of the box, with optional
  NVIDIA GPU acceleration for larger libraries.
- **Stay in control** — assignments, clusters, embeddings, and library metadata remain
  in your local SQLite database.

## Highlights

| Area | What it does |
| --- | --- |
| Face analysis | Detects faces with InsightFace and creates normalized 512-dimensional embeddings |
| Clustering | Groups similar faces incrementally using cosine distance and an HNSW neighbor index |
| People | Assigns clusters to named people and uses those assignments as conservative matching guidance |
| Photo browser | Displays a masonry gallery, face overlays, and full-screen navigation |
| Folder filtering | Searches and selects multiple nested folders at any discovered level |
| Import pipeline | Queues imports, resumes interrupted jobs, and skips already processed content |
| Storage | Persists library metadata in SQLite while leaving source images untouched |
| Acceleration | Uses CPU by default and CUDA automatically when a supported provider is available |

## Tech Stack

- **Backend:** Python, FastAPI, SQLite, InsightFace, ONNX Runtime, hnswlib
- **Frontend:** React, TypeScript, Vite
- **Desktop packaging:** PyInstaller and Inno Setup
- **Automation:** GitHub Actions for validation and Windows releases

## Quick Start

### Ubuntu 22.04+ or WSL2

The setup script installs missing system packages, Node.js 20 when needed, the Python
environment, backend dependencies, and frontend dependencies. It then validates the
backend and builds the frontend.

```bash
git clone https://github.com/KaiPressmar/face-manager.git
cd face-manager
./scripts/setup-dev.sh
```

Start the application in two terminals:

```bash
# Terminal 1 — API
./scripts/dev-backend.sh
```

```bash
# Terminal 2 — web interface
cd frontend
npm run dev
```

Open `http://localhost:5173`.

The first image import downloads the InsightFace `buffalo_l` model. Later runs use the
cached model.

### Setup Options

```text
--cpu                   Force CPU-only installation
--gpu                   Require NVIDIA GPU installation
--skip-system-packages  Skip apt and Node.js installation
--dry-run               Print actions without changing the system
--help                  Show all options
```

Examples:

```bash
./scripts/setup-dev.sh --dry-run
./scripts/setup-dev.sh --cpu
./scripts/setup-dev.sh --gpu
```

The script is safe to rerun and installs only missing or incompatible dependencies.
It never modifies source images or the SQLite library.

## Using Face Manager

1. Open the **People** view.
2. Select **Ordner hinzufügen**.
3. Choose a folder or paste a Windows/Linux path.
4. Start the import and follow its progress.
5. Review generated clusters and assign them to people.
6. Browse by person, folder, or image and open photos in the full-screen gallery.

Supported image types:

- `.jpg`
- `.jpeg`
- `.png`

The importer scans recursively. Selecting a folder in **Ordnerfilter** includes images
from all descendants.

### How duplicate images are handled

Images are identified by a SHA-256 hash of their contents. Importing the same image
from multiple folders creates one canonical library entry with multiple source
locations. Face detection is performed only once for that content.

At least one source copy must remain accessible because Face Manager references the
original files rather than storing replacements.

## Import Queue and Recovery

Imports are persisted in SQLite and processed by a shared background worker so the
face model and clustering index remain consistent.

```text
POST   /api/imports             Queue a folder import
GET    /api/imports             List active, queued, and recent jobs
DELETE /api/imports/{job_id}    Cancel or remove a job
```

Queued jobs can be removed immediately. Running jobs stop safely after the current
image. If the backend restarts, unfinished jobs return to the queue in FIFO order and
already committed images are skipped.

## Optional NVIDIA GPU Support

Verify that the NVIDIA driver is available:

```bash
nvidia-smi
```

After installing the normal backend requirements, replace the CPU runtime with the GPU
runtime:

```bash
source backend/.venv/bin/activate
python -m pip uninstall -y onnxruntime onnxruntime-gpu
python -m pip install 'onnxruntime-gpu[cuda,cudnn]>=1.21,<2'
```

Confirm that ONNX Runtime sees CUDA:

```bash
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

The output must contain `CUDAExecutionProvider`. Face Manager selects CUDA when it is
available and otherwise falls back to CPU execution.

To override the automatic import preparation worker count:

```bash
FACE_MANAGER_IMPORT_WORKERS=3 ./scripts/dev-backend.sh
```

## Manual Development Setup

Requirements:

- Linux or WSL2
- Python 3.10+
- Node.js 20+
- npm 10+
- A C/C++ compiler for `hnswlib`

### Backend

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r backend/requirements.txt
python -m pip install 'onnxruntime>=1.18,<2'
```

### Frontend

```bash
cd frontend
npm ci
cd ..
```

### Development URLs

- Frontend: `http://localhost:5173`
- API: `http://localhost:8000`
- Interactive API documentation: `http://localhost:8000/docs`
- OpenAPI schema: `http://localhost:8000/openapi.json`

Vite proxies `/api` to the FastAPI backend on port `8000`.

## VS Code

The repository includes recommended extensions, tasks, and debug configurations for
Python, TypeScript, formatting, GitHub Actions, backend/frontend development, and
compound full-stack debugging.

Press `F5` and select **Full Stack: Debug**, or use **Terminal > Run Task** for setup,
servers, validation, GitHub configuration, and release helpers.

## Data and Backups

During development, the SQLite database is created at:

```text
backend/db/database.sqlite
```

Back it up with:

```bash
cp backend/db/database.sqlite backend/db/database.backup.sqlite
```

Database files are ignored by Git. Removing the database while the backend is stopped
creates a fresh library on the next launch and permanently removes local assignments,
clusters, and embeddings.

Windows desktop installations store the database under:

```text
%LOCALAPPDATA%\FaceManager\database.sqlite
```

## Validation

Run the local equivalent of the GitHub Actions checks:

```bash
./scripts/check-all.sh
```

This validates release metadata, compiles and tests the backend, type-checks the
frontend, and creates a production build.

For the complete contribution, branch, CI, and release workflow, see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Windows Desktop Releases

A tested release on `main` produces CPU and GPU installer variants:

```text
FaceManager-Setup-X.Y.Z.exe
FaceManager-Setup-GPU-X.Y.Z.exe
```

The desktop bundle starts the backend automatically and opens Face Manager in a native
window. The first face-processing run still requires internet access to download the
InsightFace model when it is not already cached.

> [!NOTE]
> Current Windows installers are not Authenticode-signed. Published SHA-256 checksums
> and GitHub build-provenance attestations help verify origin and integrity, but
> Windows SmartScreen may still show a warning.

To build an installer manually on Windows with Python 3.10+, Node.js 20+, and Inno
Setup 6:

```powershell
python -m pip install -r backend/requirements.txt -r backend/requirements-desktop.txt "onnxruntime>=1.21,<2"
./packaging/windows/build-release.ps1

# GPU variant
./packaging/windows/build-release.ps1 -Variant gpu
```

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
  release.yml            Release and Windows artifact publication
packaging/windows/       Desktop bundle and installer configuration
scripts/                 Setup, validation, and release helpers
CHANGELOG.md             Curated user-facing release notes
CONTRIBUTING.md          Development and release workflow
VERSION                  Canonical application version
```

## Release Model

Development follows a two-branch model:

- `develop` is the integration branch for ongoing work.
- `main` contains released code only.
- Feature branches start from and merge into `develop`.
- Releases move from `develop` to `main` through a pull request.

Face Manager uses semantic versioning. Prepare a release with:

```bash
./scripts/release-version.sh patch
./scripts/release-version.sh minor
./scripts/release-version.sh major
# or
./scripts/release-version.sh 1.2.0
```

See [CHANGELOG.md](CHANGELOG.md) for user-facing changes and
[CONTRIBUTING.md](CONTRIBUTING.md) for the complete release checklist.

## Troubleshooting

### `hnswlib` fails to install

```bash
sudo apt install -y build-essential python3-dev
python -m pip install --upgrade pip setuptools wheel
```

### InsightFace or OpenCV reports a missing shared library

```bash
sudo apt install -y libgl1 libglib2.0-0
```

### Processing uses the CPU despite an NVIDIA GPU

Check `nvidia-smi`, then inspect the available providers:

```bash
source backend/.venv/bin/activate
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

Only `onnxruntime-gpu` should be installed for GPU mode.

### The frontend cannot reach the API

Confirm that FastAPI is running on port `8000`, Vite is running on port `5173`, and
`http://localhost:8000/docs` opens successfully.

### Images disappear after moving a source folder

Face Manager stores absolute source paths. Move the images back or re-import them from
the new location.

## Security and Privacy Scope

Face Manager is configured for trusted local environments. CORS is open and the API
has no authentication. Do not expose the backend directly to an untrusted network
without adding authentication, access controls, and a restricted CORS policy.

## How Face Analysis and Clustering Work

This section describes the current implementation rather than face recognition in the
abstract. Face Manager does not infer a person's name from an image. It detects faces,
calculates similarity vectors, groups likely matches, and lets the user attach names to
those groups.

### 1. Image preparation and duplicate detection

Before model inference, the importer recursively discovers supported files and hashes
their contents with SHA-256. An image whose content was already processed reuses the
existing database record, even when it appears under another path. Only new or changed
content is decoded into an RGB NumPy array and passed to the face model.

### 2. Face detection

Face Manager loads the InsightFace `buffalo_l` model pack with only its detection and
recognition modules enabled. The detector is prepared with a `1024 × 1024` detection
canvas. InsightFace returns a bounding box for every detected face:

```text
(x1, y1, x2, y2) → (x, y, width, height)
```

The bounding box is stored with the image record and is used for face overlays and
thumbnails. Detection and embedding inference run through ONNX Runtime. CUDA is used
when `CUDAExecutionProvider` is available; otherwise the same model runs on the CPU.

### 3. Face embeddings

For every detected face, the recognition model produces a 512-dimensional floating
point embedding. The embedding is not a name or a human-readable description. It is a
position in a learned feature space where images of visually similar faces tend to be
closer together.

Each vector is L2-normalized before it is stored or compared:

```text
normalized_embedding = embedding / ||embedding||₂
```

Normalization makes the vector length equal to one, so similarity can be compared with
a dot product. Face Manager uses cosine distance:

```text
cosine_similarity = a · b
cosine_distance   = 1 - (a · b)
```

A distance near `0` means that two embeddings point in almost the same direction and
are therefore very similar. Larger distances indicate less similarity. The configured
distance threshold is a decision boundary, not a probability or percentage.

### 4. Fast neighbor search with HNSW

All stored embeddings are added to an `hnswlib` index configured for cosine distance.
HNSW, or Hierarchical Navigable Small World, is an approximate nearest-neighbor graph.
It avoids comparing every new face with every stored face, which would become expensive
for large libraries.

When a new embedding arrives, Face Manager queries up to 64 nearby embeddings. HNSW
returns candidate IDs and cosine distances; the application then applies additional
checks before reusing a cluster. Approximate search is therefore used to find promising
neighbors, not to make the final identity decision by itself.

### 5. Matching an already named person

Clusters assigned to a person provide supervised evidence for later imports. Face
Manager keeps representative appearance prototypes for each named person and combines
two signals:

- the distance to that person's nearest prototype;
- the average distance to the closest labeled neighbor embeddings.

A person match is accepted only when the local neighborhood supports it:

- at least two close neighbors vote for the same person;
- that person receives at least 60% of the close-neighbor votes;
- the combined prototype and neighbor score remains below the distance threshold;
- the best candidate is separated from the runner-up by a safety margin.

If those checks disagree or the result is ambiguous, the algorithm declines the person
match instead of forcing an assignment.

### 6. Matching an unnamed cluster

When no confident named-person match exists, the embedding is compared with unassigned
clusters. A cluster is eligible only when both of these checks pass:

- the nearest member is within the distance threshold;
- the embedding is also within the threshold of the cluster centroid.

For clusters with at least three members, Face Manager additionally requires multiple
supporting neighbors and at least 60% local agreement. If two candidate clusters score
too similarly, neither is selected. A new cluster is created when no candidate passes
all gates.

This local-plus-global check prevents a single unusual photo from attaching a face to a
cluster whose overall shape does not fit.

### 7. Incremental behavior

Clustering happens incrementally during import. Each accepted embedding is registered
in memory and then added to the HNSW index, so later faces in the same import can use it
as evidence. Existing embeddings, cluster IDs, and person assignments are loaded from
SQLite when the shared clustering resource is initialized.

Incremental clustering is intentionally conservative but can depend on import order. A
face imported before better examples are available may initially remain in a small or
standalone cluster.

### 8. Cluster cleanup and rebuilding

The clustering module includes post-processing for rebuilding and improving groups:

- **Small-cluster consolidation:** clusters with one or two movable faces may be merged
  into a larger cluster only when all members agree on the same target, multiple nearby
  samples support it, and no close runner-up exists.
- **Heterogeneous-cluster splitting:** broad clusters are measured against their
  normalized centroid. The 95th-percentile radius is used so isolated bad crops are
  tolerated while a sizeable second identity can still be detected.
- **Recursive two-way split:** a broad cluster is tentatively separated from two distant
  seeds using repeated cosine-based assignments. Both child groups must contain enough
  samples and their centers must have meaningful separation before the split is kept.
- **Stable similarity ordering:** faces inside a cluster can be ordered along a nearest-
  neighbor path, beginning at a peripheral sample, to make visual review more coherent.

These passes are deliberately stricter than a simple connected-components or
single-linkage approach. Single-linkage can join two people through a chain of marginal
matches; Face Manager instead checks centroids, local support, robust cluster radius,
and candidate margins.

### 9. Threshold calibration

The distance threshold can be calibrated from faces that the user has already assigned
to people. Positive examples come from the same person and, where available, the same
appearance subcluster. Different people provide negative examples.

The calibration mirrors the runtime cohesion checks by considering both nearby samples
and group centroids. Each person contributes equally to the score so people with many
photos do not dominate the result. When two thresholds perform equally, the lower and
therefore safer threshold is preferred to reduce accidental merges.

### 10. Practical limitations

Embedding similarity can be affected by pose, age, lighting, blur, occlusion, camera
quality, very small faces, and visually similar relatives. The system is designed to
assist organization, not to make authoritative identity claims.

Manual review remains part of the workflow: users can assign names, move incorrect
faces, split mixed groups, or leave uncertain clusters unassigned. The conservative
matching rules intentionally favor additional clusters over silently combining
 different people.