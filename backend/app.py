import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import asynccontextmanager
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import List

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import ExifTags, Image

from .config import APP_VERSION, DB_PATH, get_frontend_dist_dir
from .db.schema import get_conn, init_db
from .models.face_model import get_compute_mode, get_execution_provider
from .services.desktop import (
    is_windows_host,
    is_wsl_host,
    normalize_import_folder_path,
    open_file_location,
    pick_folder,
    to_display_path,
)
from .services.import_queue import ImportQueue
from .services.storage import (
    DEFAULT_CLUSTER_DISTANCE_THRESHOLD,
    _safe_float,
    assign_cluster_to_person,
    build_folder_tree,
    delete_image,
    get_available_image_path,
    get_cluster_distance_threshold,
    list_image_locations,
    list_available_image_persons,
    get_person_faces,
    list_images_page,
    list_persons,
    invalidate_image_query_cache,
    set_cluster_distance_threshold,
)

logger = logging.getLogger("uvicorn.error")

init_db()
import_queue = ImportQueue(auto_start=False)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run the durable import worker for the application lifetime.

    Args:
        _app: FastAPI application managed by this lifespan.

    Yields:
        Control while the application is accepting requests.
    """
    import_queue.start()
    try:
        yield
    finally:
        import_queue.stop()


app = FastAPI(title="Face Manager API", version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# PROCESSING
# -----------------------------


@app.get("/api/version")
def api_version():
    """Return the application version.

    Returns:
        Version metadata for frontend diagnostics.
    """
    return {"version": APP_VERSION}


@app.get("/api/runtime")
def api_runtime():
    """Return the active inference provider and compute mode.

    Returns:
        Runtime provider details used by the UI badge.
    """
    execution_provider = get_execution_provider()
    return {
        "compute_mode": get_compute_mode(execution_provider),
        "execution_provider": execution_provider,
        "host_platform": "windows" if is_windows_host() else "linux",
        "display_platform": "windows" if is_windows_host() else "linux",
    }


def get_display_platform(request: Request) -> str:
    """Resolve the preferred UI path format for one request."""
    preferred = request.headers.get("x-face-manager-display-platform", "").strip()
    if preferred == "windows":
        if is_windows_host() or is_wsl_host():
            return "windows"
    if is_windows_host():
        return "windows"
    return "linux"


def serialize_folder_tree(node, display_platform: str):
    """Convert folder tree paths into display paths for the UI."""
    display_path = (
        to_display_path(node["path"])
        if display_platform == "windows"
        else node["path"]
    )
    display_name = (
        display_path.replace("\\", "/").rstrip("/").split("/").pop() or display_path
    )
    return {
        **node,
        "path": display_path,
        "name": display_name,
        "children": [
            serialize_folder_tree(child, display_platform)
            for child in node["children"]
        ],
    }


def serialize_image_locations(locations, display_platform: str):
    """Convert canonical image locations into UI display paths."""
    payload = []
    for location in locations:
        display_path = (
            to_display_path(location["path"])
            if display_platform == "windows"
            else location["path"]
        )
        payload.append(
            {
                **location,
                "path": display_path,
                "directory": (
                    to_display_path(location["directory"])
                    if display_platform == "windows"
                    else location["directory"]
                ),
            }
        )
    return payload


def serialize_import_snapshot(snapshot, display_platform: str):
    """Convert queued job paths into UI display paths."""
    for job in snapshot["jobs"]:
        if display_platform == "windows":
            job["folder_path"] = to_display_path(job["folder_path"])
        if job.get("current_file"):
            if display_platform == "windows":
                job["current_file"] = to_display_path(job["current_file"])
        for station in job.get("stations", []):
            if station.get("current_file"):
                if display_platform == "windows":
                    station["current_file"] = to_display_path(station["current_file"])
    return snapshot


def ensure_database_is_idle() -> None:
    """Reject database mutation while imports are queued or running."""
    snapshot = import_queue.snapshot()
    if snapshot["running_count"] or snapshot["queued_count"]:
        raise HTTPException(
            status_code=409,
            detail="Database import/export is unavailable while imports are queued or running.",
        )


def reset_import_resources() -> None:
    """Refresh cached import resources that depend on database contents."""
    processor = getattr(import_queue, "_processor", None)
    resources = getattr(processor, "resources", None)
    if resources is not None and hasattr(resources, "reset_clusterer"):
        resources.reset_clusterer()


def validate_cluster_distance_threshold(value: float) -> float:
    """Validate and normalize a clustering threshold supplied by the client."""
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Threshold must be a number.") from exc
    if not 0 <= threshold <= 1:
        raise HTTPException(
            status_code=400,
            detail="Threshold must be between 0.0 and 1.0.",
        )
    return threshold


def validate_database_file(path: Path) -> None:
    """Verify that a file is a readable SQLite database with core tables."""
    conn = None
    try:
        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid SQLite database.") from exc
    finally:
        if conn is not None:
            conn.close()

    table_names = {row[0] for row in rows}
    required = {"image", "face", "cluster", "person"}
    if not required.issubset(table_names):
        raise HTTPException(
            status_code=400,
            detail="Uploaded database is missing required Face Manager tables.",
        )


@app.post("/api/system/select-folder")
def api_select_folder(request: Request):
    """Open a native folder picker on the backend host."""
    display_platform = get_display_platform(request)
    try:
        folder_path = pick_folder(prefer_windows_dialog=display_platform == "windows")
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="A native folder picker is unavailable on this host.",
        ) from exc
    if not folder_path:
        return {"folder_path": None}
    return {
        "folder_path": (
            to_display_path(normalize_import_folder_path(folder_path))
            if display_platform == "windows"
            else normalize_import_folder_path(folder_path)
        )
    }


@app.get("/api/settings")
def api_get_settings():
    """Return persisted application settings used by the frontend."""
    return {
        "cluster_distance_threshold": get_cluster_distance_threshold(),
        "cluster_distance_threshold_default": DEFAULT_CLUSTER_DISTANCE_THRESHOLD,
        "database_path": DB_PATH,
    }


@app.put("/api/settings")
def api_update_settings(data: dict = Body(...)):
    """Persist mutable application settings."""
    if "cluster_distance_threshold" not in data:
        raise HTTPException(status_code=400, detail="Missing cluster_distance_threshold")
    threshold = validate_cluster_distance_threshold(data["cluster_distance_threshold"])
    return {
        "cluster_distance_threshold": set_cluster_distance_threshold(threshold),
        "cluster_distance_threshold_default": DEFAULT_CLUSTER_DISTANCE_THRESHOLD,
        "database_path": DB_PATH,
    }


@app.get("/api/database/export")
def api_export_database():
    """Export a consistent SQLite snapshot of the current database."""
    ensure_database_is_idle()
    fd, temp_path = tempfile.mkstemp(suffix=".sqlite", prefix="face-manager-export-")
    os.close(fd)

    source = get_conn()
    target = sqlite3.connect(temp_path)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()

    with open(temp_path, "rb") as exported:
        payload = exported.read()
    os.unlink(temp_path)

    return Response(
        payload,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": 'attachment; filename="face-manager-database.sqlite"'
        },
    )


@app.post("/api/database/import")
def api_import_database(payload: bytes = Body(..., media_type="application/octet-stream")):
    """Replace the current database with an uploaded SQLite file."""
    ensure_database_is_idle()
    if not payload:
        raise HTTPException(status_code=400, detail="Missing database payload.")

    current_db_path = Path(DB_PATH)
    current_db_path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path_str = tempfile.mkstemp(
        suffix=".sqlite",
        prefix="face-manager-import-",
        dir=current_db_path.parent,
    )
    os.close(fd)
    temp_path = Path(temp_path_str)
    temp_path.write_bytes(payload)

    try:
        validate_database_file(temp_path)
        shutil.move(str(temp_path), DB_PATH)
        wal_path = current_db_path.with_name(f"{current_db_path.name}-wal")
        shm_path = current_db_path.with_name(f"{current_db_path.name}-shm")
        for sidecar_path in (wal_path, shm_path):
            if sidecar_path.exists():
                sidecar_path.unlink()
        init_db()
        reset_import_resources()
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {"status": "imported"}


@app.post("/api/process-folder")
def api_process_folder(request: Request, data: dict = Body(...)):
    """Queue a folder import through the legacy endpoint.

    Args:
        data: Request body containing ``folder_path``.

    Returns:
        Newly queued import job.
    """
    return api_create_import(request, data)


@app.get("/api/process-status")
def api_process_status(request: Request):
    """Return queue state through the legacy status endpoint.

    Returns:
        Current import queue snapshot.
    """
    return serialize_import_snapshot(
        import_queue.snapshot(),
        get_display_platform(request),
    )


@app.post("/api/imports", status_code=202)
def api_create_import(request: Request, data: dict = Body(...)):
    """Queue a folder for serialized background import.

    Args:
        data: Request body containing ``folder_path``.

    Returns:
        Newly queued import job.

    Raises:
        HTTPException: If the folder path is missing or invalid.
    """
    if "folder_path" not in data:
        raise HTTPException(status_code=400, detail="Missing folder_path")

    folder_path = normalize_import_folder_path(data["folder_path"])

    if not os.path.isdir(folder_path):
        raise HTTPException(status_code=400, detail=f"Path does not exist: {folder_path}")

    payload = import_queue.enqueue(folder_path)
    if get_display_platform(request) == "windows":
        payload["folder_path"] = to_display_path(payload["folder_path"])
    return payload


@app.get("/api/imports")
def api_imports(request: Request):
    """Return all visible import jobs.

    Returns:
        Queue summary with active, queued, and recent terminal jobs.
    """
    return serialize_import_snapshot(
        import_queue.snapshot(),
        get_display_platform(request),
    )


@app.delete("/api/imports/{job_id}")
def api_cancel_or_remove_import(job_id: str):
    """Cancel a running import or remove another queue entry.

    Args:
        job_id: Import job identifier.

    Returns:
        Cancellation or removal result.

    Raises:
        HTTPException: If the job does not exist.
    """
    result = import_queue.cancel_or_remove(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Import job not found")
    return result


# -----------------------------
# CLUSTERS
# -----------------------------


@app.get("/api/clusters")
def api_clusters():
    """List clusters and their faces.

    Returns:
        Cluster dictionaries grouped from persisted face rows.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            f.id          AS face_id,
            f.image_id    AS image_id,
            i.path        AS image_path,
            f.bbox_x      AS bbox_x,
            f.bbox_y      AS bbox_y,
            f.bbox_w      AS bbox_w,
            f.bbox_h      AS bbox_h,
            f.cluster_id  AS cluster_id,
            p.name        AS person_name
        FROM face f
        JOIN image i ON f.image_id = i.id
        LEFT JOIN cluster c ON f.cluster_id = c.id
        LEFT JOIN person  p ON c.person_id = p.id
        WHERE f.cluster_id IS NOT NULL
        ORDER BY f.cluster_id, f.id
        """
    )

    rows = cur.fetchall()
    conn.close()

    clusters = {}

    for r in rows:
        cid = r["cluster_id"]
        if cid not in clusters:
            clusters[cid] = {
                "cluster_id": cid,
                "person_name": r["person_name"],
                "faces": [],
            }

        clusters[cid]["faces"].append(
            {
                "id": r["face_id"],
                "image_id": r["image_id"],
                "image_path": r["image_path"],
                "bbox_x": int(r["bbox_x"]),
                "bbox_y": int(r["bbox_y"]),
                "bbox_w": int(r["bbox_w"]),
                "bbox_h": int(r["bbox_h"]),
                "person_name": r["person_name"],
            }
        )

    return list(clusters.values())


@app.post("/api/clusters/{cluster_id}/assign-person")
def api_assign_person_to_cluster(cluster_id: int, data: dict = Body(...)):
    """Assign a cluster to a person.

    Args:
        cluster_id: Cluster identifier to update.
        data: Request body containing the person name.

    Returns:
        Success status.

    Raises:
        HTTPException: If no person name is supplied.
    """
    person_name = data.get("person_name") or data.get("personName")
    if not person_name:
        raise HTTPException(status_code=400, detail="Missing person_name")

    assign_cluster_to_person(cluster_id, person_name)
    return {"status": "ok"}


@app.post("/api/clusters/{cluster_id}/remove-face/{face_id}")
def api_remove_face_from_cluster(cluster_id: int, face_id: int):
    """Remove a face from a specific cluster.

    Args:
        cluster_id: Expected cluster identifier.
        face_id: Face identifier to remove.

    Returns:
        Success status.

    Raises:
        HTTPException: If the face is not assigned to the cluster.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE face SET cluster_id = NULL WHERE id = ? AND cluster_id = ?",
        (face_id, cluster_id),
    )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Face not in this cluster")
    conn.commit()
    conn.close()
    invalidate_image_query_cache()
    return {"status": "ok"}


@app.post("/api/clusters/{cluster_id}/dissolve")
def api_dissolve_cluster(cluster_id: int):
    """Remove all faces from a cluster.

    Args:
        cluster_id: Cluster identifier to dissolve.

    Returns:
        Success status.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE face SET cluster_id = NULL WHERE cluster_id = ?", (cluster_id,))
    conn.commit()
    conn.close()
    invalidate_image_query_cache()
    return {"status": "ok"}


# -----------------------------
# PERSONS
# -----------------------------


@app.get("/api/persons")
def api_persons():
    """List known people.

    Returns:
        Person identifier and name dictionaries.
    """
    return list_persons()


@app.get("/api/persons/{person_id}/faces")
def api_person_faces(person_id: int):
    """List faces assigned to a person.

    Args:
        person_id: Person identifier.

    Returns:
        Face dictionaries assigned through clusters.
    """
    return get_person_faces(person_id)


# -----------------------------
# IMAGES
# -----------------------------


@app.get("/api/images/{image_id}/file")
def get_image(image_id: int):
    """Serve an image from an available filesystem location.

    Args:
        image_id: Canonical image identifier.

    Returns:
        Streaming file response.

    Raises:
        HTTPException: If no image location exists.
    """
    path = get_available_image_path(image_id)
    if not path:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)


@app.delete("/api/images/{image_id}")
def remove_image(image_id: int):
    """Delete an image and its dependent records.

    Args:
        image_id: Canonical image identifier.

    Returns:
        Deletion status.

    Raises:
        HTTPException: If the image does not exist.
    """
    if not delete_image(image_id):
        raise HTTPException(status_code=404, detail="Image not found")
    return {"status": "deleted"}


@app.post("/api/images/{image_id}/open-location")
def open_image_location(image_id: int, data: dict = Body(default=None)):
    """Reveal an image in the system file manager.

    Args:
        image_id: Canonical image identifier.
        data: Optional body containing a preferred image path.

    Returns:
        Open status.

    Raises:
        HTTPException: If the image is missing or the file manager fails.
    """
    preferred_path = data.get("image_path") if data else None
    if preferred_path:
        preferred_path = normalize_import_folder_path(preferred_path)
    path = get_available_image_path(image_id, preferred_path)
    if not path:
        raise HTTPException(status_code=404, detail="Image not found")
    try:
        open_file_location(path)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.exception("Could not open image location for %s", path)
        raise HTTPException(
            status_code=500, detail="Could not open the system file manager"
        ) from exc
    return {"status": "opened"}


@lru_cache(maxsize=2048)
def get_image_orientation(path):
    """Read and cache EXIF orientation and dimensions.

    Args:
        path: Image path to inspect.

    Returns:
        Orientation value, width, and height.
    """
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            orientation = exif.get(274, 1)
            return orientation, img.width, img.height
    except (OSError, ValueError):
        return 1, 0, 0


def correct_bbox_for_orientation(path, x, y, w, h):
    """Transform a face box according to image EXIF orientation.

    Args:
        path: Image path used to inspect orientation.
        x: Original horizontal coordinate.
        y: Original vertical coordinate.
        w: Original box width.
        h: Original box height.

    Returns:
        Corrected ``x``, ``y``, width, and height tuple.
    """
    orientation, width, height = get_image_orientation(path)

    # Orientation corrections
    if orientation == 3:  # 180°
        return (
            width - x - w,
            height - y - h,
            w,
            h,
        )
    if orientation == 6:  # 90° CW
        return (
            height - y - h,
            x,
            h,
            w,
        )
    if orientation == 8:  # 270° CW
        return (
            y,
            width - x - w,
            h,
            w,
        )

    return x, y, w, h


@app.get("/api/folders")
def get_folders(request: Request):
    """Return the imported folder hierarchy.

    Returns:
        Folder tree and aggregate counts.
    """
    display_platform = get_display_platform(request)
    tree = build_folder_tree()
    tree["roots"] = [
        serialize_folder_tree(root, display_platform) for root in tree["roots"]
    ]
    return tree


@app.get("/api/images")
def get_images(
    request: Request,
    folders: List[str] = Query(default=[]),
    persons: List[str] = Query(default=[]),
    sort_by: str = Query(default="date"),
    sort_direction: str = Query(default="desc"),
    limit: int = Query(default=40, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List images and oriented face boxes.

    Args:
        folders: Optional folder roots used to filter images.
        persons: Optional person names used to require matching faces.
        sort_by: Primary gallery sort key.
        sort_direction: Primary gallery sort direction.
        limit: Maximum number of images returned in one page.
        offset: Starting image offset for pagination.

    Returns:
        Paginated image dictionaries containing nested face data.
    """
    display_platform = get_display_platform(request)
    normalized_folders = [normalize_import_folder_path(folder) for folder in folders]
    rows, total = list_images_page(
        folders=normalized_folders,
        persons=persons,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )
    images = {}

    for r in rows:
        image_id = r["image_id"]
        path = r["image_path"]

        if image_id not in images:
            images[image_id] = {
                "id": image_id,
                "image_path": (
                    to_display_path(path) if display_platform == "windows" else path
                ),
                "directory": (
                    to_display_path(r["directory"])
                    if display_platform == "windows"
                    else r["directory"]
                ),
                "filename": r["filename"],
                "created_at": r["created_at"],
                "content_hash": r["content_hash"],
                "location_count": r["location_count"],
                "faces": [],
            }

        bbox_x, bbox_y, bbox_w, bbox_h = correct_bbox_for_orientation(
            path,
            _safe_float(r["bbox_x"]),
            _safe_float(r["bbox_y"]),
            _safe_float(r["bbox_w"]),
            _safe_float(r["bbox_h"]),
        )

        images[image_id]["faces"].append(
            {
                "id": r["face_id"],
                "bbox_x": bbox_x,
                "bbox_y": bbox_y,
                "bbox_w": bbox_w,
                "bbox_h": bbox_h,
                "cluster_id": r["cluster_id"],
                "person_name": r["person_name"],
            }
        )

    locations_by_image = list_image_locations(list(images.keys()))
    for image_id, image in images.items():
        locations = locations_by_image.get(image_id, [])
        image["locations"] = serialize_image_locations(locations, display_platform)
        image["location_count"] = len(locations)

    items = list(images.values())
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(items) < total,
        "available_persons": list_available_image_persons(normalized_folders),
    }


# -----------------------------
# FACE CROP
# -----------------------------


@app.get("/api/faces/{face_id}/crop")
def api_face_crop(face_id: int):
    """Return a JPEG crop for one detected face.

    Args:
        face_id: Face identifier to crop.

    Returns:
        JPEG response containing the oriented face region.

    Raises:
        HTTPException: If the face image cannot be found.
    """
    conn = get_conn()
    row = conn.execute(
        """
        SELECT f.image_id, f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h
        FROM face f
        WHERE f.id = ?
        """,
        (face_id,),
    ).fetchone()
    conn.close()
    path = get_available_image_path(row["image_id"]) if row else None
    if not path:
        raise HTTPException(status_code=404, detail="Image not found")

    x = int(_safe_float(row["bbox_x"]))
    y = int(_safe_float(row["bbox_y"]))
    w = int(_safe_float(row["bbox_w"]))
    h = int(_safe_float(row["bbox_h"]))
    img = Image.open(path)

    # --- EXIF ORIENTATION FIX ---
    try:
        exif = img._getexif()
        if exif:
            orientation_key = next(
                k for k, v in ExifTags.TAGS.items() if v == "Orientation"
            )
            orientation = exif.get(orientation_key, 1)

            if orientation == 3:
                img = img.rotate(180, expand=True)
                x = img.width - x - w
                y = img.height - y - h

            elif orientation == 6:  # 90° CW
                img = img.rotate(-90, expand=True)
                x, y, w, h = (
                    img.width - y - h,
                    x,
                    h,
                    w,
                )

            elif orientation == 8:  # 270° CW
                img = img.rotate(90, expand=True)
                x, y, w, h = (
                    y,
                    img.height - x - w,
                    h,
                    w,
                )

    except Exception:
        pass

    # --- CROP ---
    crop = img.crop((x, y, x + w, y + h))

    buf = BytesIO()
    crop.save(buf, format="JPEG")
    buf.seek(0)
    return Response(buf.read(), media_type="image/jpeg")


frontend_dist_dir = get_frontend_dist_dir()
if frontend_dist_dir.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist_dir, html=True), name="frontend")
