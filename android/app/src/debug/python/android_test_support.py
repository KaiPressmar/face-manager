"""Device-only regression runner for the packaged desktop backend."""

import importlib
import io
import json
import os
from pathlib import Path
import shutil
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SHARED_MODULES = (
    "backend.tests.test_geo",
    "backend.tests.test_person_aware_clustering",
    "backend.tests.test_cluster_cohesion",
    "backend.tests.test_clustering_threshold",
    "backend.tests.test_task_control",
    "backend.tests.test_schema_recovery",
    "backend.tests.test_deduplication",
    "backend.tests.test_filename_rename_preview",
    "backend.tests.test_settings_api",
    "backend.tests.test_cluster_api",
)


def _request(port, token, method, route, payload=None, content_type="application/json"):
    data = None
    if payload is not None:
        data = (json.dumps(payload).encode() if content_type == "application/json"
                else payload)
    headers = {"Cookie": "fm_session=" + token}
    if data is not None:
        headers["Content-Type"] = content_type
    request = Request(f"http://127.0.0.1:{port}{route}", data=data,
                      headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read()
            content = response.headers.get("Content-Type", "")
            return response.status, json.loads(body) if "json" in content else body
    except HTTPError as error:
        raise AssertionError(f"{method} {route}: HTTP {error.code}: "
                             f"{error.read()[:500]!r}") from error


def _ok(port, token, method, route, payload=None, content_type="application/json"):
    status, body = _request(port, token, method, route, payload, content_type)
    assert 200 <= status < 300, (method, route, status, body)
    return body


def _wait_for(predicate, label, seconds=150):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (AssertionError, OSError) as error:
            last = error
        time.sleep(0.5)
    raise AssertionError(f"Timed out waiting for {label}; last value: {last!r}")


def _geotag(source, destination):
    from PIL import Image, TiffImagePlugin

    rational = TiffImagePlugin.IFDRational
    with Image.open(source) as photo:
        exif = photo.getexif()
        exif[34853] = {1: "N", 2: tuple(map(rational, (52, 31, 12))),
                       3: "E", 4: tuple(map(rational, (13, 24, 18)))}
        exif[34665] = {36867: "2025:08:09 23:59:59"}
        photo.convert("RGB").save(destination, "JPEG", quality=95, exif=exif)


def _smoke(port, token, fixture_dir, grace, astronaut):
    from urllib.error import HTTPError

    try:
        urlopen(f"http://127.0.0.1:{port}/api/version", timeout=10)
    except HTTPError as error:
        assert error.code in (401, 403), f"Unauthenticated API returned {error.code}"
    else:
        raise AssertionError("Loopback API accepted a request without its session token")

    version = _ok(port, token, "GET", "/api/version")
    assert version.get("version"), version
    page = _ok(port, token, "GET", "/")
    assert b"<html" in page.lower(), "Packaged React page was not served"
    settings = _ok(port, token, "GET", "/api/settings")
    assert "cluster_distance_threshold" in settings, settings

    photos = fixture_dir / "photos"
    photos.mkdir(parents=True)
    _geotag(grace, photos / "grace_gps.jpg")
    shutil.copyfile(astronaut, photos / "astronaut.png")
    queued = _ok(port, token, "POST", "/api/imports", {"folder_path": str(photos)})
    assert queued.get("id"), queued
    imports = _wait_for(
        lambda: (data if (data := _ok(port, token, "GET", "/api/imports"))
                .get("running_count", 0) == 0 and data.get("queued_count", 0) == 0
                else None), "import job completion")
    job = next((item for item in imports["jobs"] if item["id"] == queued["id"]), None)
    assert job and job.get("status") in ("completed", "done"), job
    library = _wait_for(
        lambda: (data if (data := _ok(port, token, "GET", "/api/images")).get("total", 0) >= 2
                 else None), "two imported images")
    assert library["total"] == 2, library
    for image in library["items"]:
        image_id = image["id"]
        detail = _ok(port, token, "GET", f"/api/images/{image_id}/detail")
        assert detail["id"] == image_id, detail
        file_bytes = _ok(port, token, "GET", f"/api/images/{image_id}/file")
        assert file_bytes[:2] == b"\xff\xd8" or file_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        thumbnail = _ok(port, token, "GET", f"/api/images/{image_id}/thumbnail")
        assert thumbnail[:2] == b"\xff\xd8", image_id

    points = _wait_for(lambda: (data if (data := _ok(port, token, "GET", "/api/map/points"))
                                .get("located_total", 0) >= 1 else None), "GPS indexing")
    assert points["located_total"] >= 1, points
    mapped = _ok(port, token, "GET", "/api/map/images")
    assert mapped["total"] >= 1, mapped
    dated = _ok(port, token, "GET",
                "/api/map/images?from_date=2025-08-09&to_date=2025-08-09")
    assert dated["total"] == 1, dated
    outside_date = _ok(port, token, "GET",
                       "/api/map/images?from_date=2020-01-01&to_date=2020-01-01")
    assert outside_date["total"] == 0, outside_date

    clusters = _wait_for(lambda: _ok(port, token, "GET", "/api/clusters") or None,
                         "face clustering")
    cluster_id = clusters[0]["cluster_id"]
    faces = _ok(port, token, "GET", f"/api/clusters/{cluster_id}/faces")
    assert faces["faces"], faces
    _ok(port, token, "POST", f"/api/clusters/{cluster_id}/assign-person",
        {"person_name": "Device Test"})
    people = _ok(port, token, "GET", "/api/persons")
    person = next((item for item in people if item["name"] == "Device Test"), None)
    assert person, people
    _ok(port, token, "PATCH", f"/api/persons/{person['id']}", {"name": "Renamed Test"})
    assert any(item["name"] == "Renamed Test" for item in
               _ok(port, token, "GET", "/api/persons"))
    preview = _ok(port, token, "GET", "/api/image-renames")
    assert preview["items"] and preview["total"] >= 1, preview
    candidate = preview["items"][0]
    old_path, new_path = Path(candidate["path"]), Path(candidate["proposed_path"])
    assert old_path.is_file() and not new_path.exists(), candidate
    renamed = _ok(port, token, "POST", "/api/image-renames/apply",
                  {"selected_paths": [str(old_path)]})
    assert renamed["renamed_count"] == 1 and not renamed["errors"], renamed
    assert not old_path.exists() and new_path.is_file(), renamed
    changed = _ok(port, token, "GET", f"/api/images/{candidate['image_id']}/detail")
    assert changed["image_path"] == str(new_path), changed

    old_theme = settings["ui_theme"]
    new_theme = "dark" if old_theme != "dark" else "light"
    _ok(port, token, "PUT", "/api/settings", {"ui_theme": new_theme})
    assert _ok(port, token, "GET", "/api/settings")["ui_theme"] == new_theme
    _ok(port, token, "PUT", "/api/settings", {"ui_theme": old_theme})

    # Wait for background clustering and import jobs before SQLite replacement.
    def export_when_idle():
        try:
            return _ok(port, token, "GET", "/api/database/export")
        except AssertionError as error:
            if "HTTP 409" in str(error):
                return None
            raise

    exported = _wait_for(export_when_idle, "idle database export")
    assert exported.startswith(b"SQLite format 3\x00")
    _ok(port, token, "POST", "/api/database/import", exported,
        "application/octet-stream")
    assert _ok(port, token, "GET", "/api/images")["total"] == 2
    return f"HTTP smoke passed: {library['total']} images, GPS map, faces, people, settings, SQLite round trip"


def _shared():
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader
    for name in SHARED_MODULES:
        suite.addTests(loader.loadTestsFromModule(importlib.import_module(name)))
    report = io.StringIO()
    result = unittest.TextTestRunner(stream=report, verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise AssertionError(report.getvalue())
    return f"{result.testsRun} shared backend tests passed"


def run(context, bridge, data_root, grace, astronaut):
    """Called once by AndroidJUnitRunner, with private paths and bundled image assets."""
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    os.environ["FACE_MANAGER_DATA_DIR"] = str(root)
    import android_runtime

    running = False
    try:
        address = android_runtime.start(context, bridge, str(root))
        running = True
        summary = _smoke(int(address["port"]), str(address["token"]), root,
                         Path(grace), Path(astronaut))
    finally:
        if running:
            android_runtime.stop()
    return summary + "; " + _shared()
