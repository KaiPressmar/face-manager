import logging
import os
import subprocess
from contextlib import asynccontextmanager
from functools import lru_cache
from io import BytesIO
from typing import List

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from PIL import ExifTags, Image

from .config import APP_VERSION
from .db.schema import get_conn, init_db
from .models.face_model import get_compute_mode, get_execution_provider
from .services.desktop import open_file_location
from .services.import_queue import ImportQueue
from .services.storage import (
    _safe_float,
    assign_cluster_to_person,
    build_folder_tree,
    delete_image,
    get_available_image_path,
    list_image_locations,
    get_person_faces,
    list_images,
    list_persons,
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
    }


@app.post("/api/process-folder")
def api_process_folder(data: dict = Body(...)):
    """Queue a folder import through the legacy endpoint.

    Args:
        data: Request body containing ``folder_path``.

    Returns:
        Newly queued import job.
    """
    return api_create_import(data)


@app.get("/api/process-status")
def api_process_status():
    """Return queue state through the legacy status endpoint.

    Returns:
        Current import queue snapshot.
    """
    return import_queue.snapshot()


@app.post("/api/imports", status_code=202)
def api_create_import(data: dict = Body(...)):
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

    wsl_path = data["folder_path"]

    if not os.path.isdir(wsl_path):
        raise HTTPException(status_code=400, detail=f"Path does not exist: {wsl_path}")

    return import_queue.enqueue(os.path.normpath(wsl_path))


@app.get("/api/imports")
def api_imports():
    """Return all visible import jobs.

    Returns:
        Queue summary with active, queued, and recent terminal jobs.
    """
    return import_queue.snapshot()


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
def get_folders():
    """Return the imported folder hierarchy.

    Returns:
        Folder tree and aggregate counts.
    """
    return build_folder_tree()


@app.get("/api/images")
def get_images(folders: List[str] = Query(default=[])):
    """List images and oriented face boxes.

    Args:
        folders: Optional folder roots used to filter images.

    Returns:
        Image dictionaries containing nested face data.
    """
    rows = list_images(folders)
    images = {}

    for r in rows:
        image_id = r["image_id"]
        path = r["image_path"]

        if image_id not in images:
            images[image_id] = {
                "id": image_id,
                "image_path": path,
                "directory": r["directory"],
                "filename": r["filename"],
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

    locations_by_image = list_image_locations(images.keys())
    for image_id, image in images.items():
        locations = locations_by_image.get(image_id, [])
        image["locations"] = locations
        image["location_count"] = len(locations)

    return list(images.values())


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
