# Capture and verify README screenshots

Refresh the pictures when a pull request changes a view or workflow shown in
the README. During release preparation, check the whole tour against the release
candidate and refresh any affected pictures that were missed.

1. Find the README image and its caption for each changed view. Check linked
   in-app help and tour instructions so they use the labels in the app.
2. Run the frontend and backend from the branch being documented. Point
   `FACE_MANAGER_DATA_DIR` at a temporary directory and use real,
   redistributable photographs in the example library. Build representative
   groups, suggestions, and archive entries with example assignments. The
   README links to the public portrait sources and
   [photo-credits.md](photo-credits.md) lists the public landscape photos.
   Credit any new photos with their source and license.
3. Capture the actual UI after the relevant controls and images have rendered.
   Use the tour's dark theme for consistency. Capture the desktop view and, when
   the workflow adapts to viewport size, a representative compact view. The
   optional `UI_SMOKE_SCREENSHOT_DIR` output of `scripts/check-ui.sh` can help
   inspect UI states. Use the applicable demo library or a scenario-specific
   capture for images that are not produced by that smoke suite.
4. Open and inspect the capture at its displayed size. Check that the expected
   labels, example photos, and workflow state are visible. Save the image as
   WebP in `docs/screenshots/`. Reuse existing filenames when they still
   describe the same view, and update the corresponding README links and alt
   text when adding or replacing a scene.
5. Run the link check below from the repository root. Record captured views and
   the compact viewport, if applicable, in the pull request.

```sh
python3 - <<'PY'
from pathlib import Path
import re

readme = Path("README.md").read_text(encoding="utf-8")
paths = re.findall(r"!\[[^\]]*\]\((docs/screenshots/[^)]+)\)", readme)
assert paths, "No README screenshot links found"
for name in paths:
    path = Path(name)
    assert path.is_file() and path.stat().st_size, f"Missing or empty: {path}"
print(f"Checked {len(paths)} README screenshot links.")
PY
```
