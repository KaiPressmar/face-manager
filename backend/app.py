import logging
import os
from io import BytesIO

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from PIL import ExifTags, Image

from .db.schema import get_conn, init_db
from .services.pipeline import get_process_state, process_folder
from .services.storage import (
    _safe_float,
    assign_cluster_to_person,
    get_person_faces,
    list_persons,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

app = FastAPI()
init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# PROCESSING
# -----------------------------


@app.post("/api/process-folder")
def api_process_folder(data: dict = Body(...), background: BackgroundTasks = None):
    if "folder_path" not in data:
        raise HTTPException(status_code=400, detail="Missing folder_path")

    wsl_path = data["folder_path"]

    if not os.path.exists(wsl_path):
        raise HTTPException(status_code=400, detail=f"Path does not exist: {wsl_path}")

    background.add_task(process_folder, wsl_path)
    return {"status": "started"}


@app.get("/api/process-status")
def api_process_status():
    return get_process_state()


# -----------------------------
# CLUSTERS
# -----------------------------


@app.get("/api/clusters")
def api_clusters():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            f.id          AS face_id,
            f.image_path  AS image_path,
            f.bbox_x      AS bbox_x,
            f.bbox_y      AS bbox_y,
            f.bbox_w      AS bbox_w,
            f.bbox_h      AS bbox_h,
            f.cluster_id  AS cluster_id,
            p.name        AS person_name
        FROM face f
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
    person_name = data.get("person_name") or data.get("personName")
    if not person_name:
        raise HTTPException(status_code=400, detail="Missing person_name")

    assign_cluster_to_person(cluster_id, person_name)
    return {"status": "ok"}


@app.post("/api/clusters/{cluster_id}/remove-face/{face_id}")
def api_remove_face_from_cluster(cluster_id: int, face_id: int):
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
    return list_persons()


@app.get("/api/persons/{person_id}/faces")
def api_person_faces(person_id: int):
    return get_person_faces(person_id)


# -----------------------------
# IMAGES
# -----------------------------


@app.get("/api/image")
def get_image(path: str):
    return FileResponse(path)


def correct_bbox_for_orientation(path, x, y, w, h):
    img = Image.open(path)

    try:
        exif = img._getexif()
        if not exif:
            return x, y, w, h

        orientation_key = next(
            k for k, v in ExifTags.TAGS.items() if v == "Orientation"
        )
        orientation = exif.get(orientation_key, 1)
    except:
        orientation = 1

    width, height = img.size

    # Orientation corrections
    if orientation == 3:  # 180°
        return (
            width - x - w,
            height - y - h,
            w,
            h,
        )
    elif orientation == 6:  # 90° CW
        return (
            height - y - h,
            x,
            h,
            w,
        )
    elif orientation == 8:  # 270° CW
        return (
            y,
            width - x - w,
            h,
            w,
        )

    return x, y, w, h


@app.get("/api/images")
def get_images():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT 
            f.id        AS face_id,
            f.image_path AS image_path,
            f.bbox_x    AS bbox_x,
            f.bbox_y    AS bbox_y,
            f.bbox_w    AS bbox_w,
            f.bbox_h    AS bbox_h,
            f.cluster_id AS cluster_id,
            p.name      AS person_name
        FROM face f
        LEFT JOIN cluster c ON f.cluster_id = c.id
        LEFT JOIN person  p ON c.person_id = p.id
        WHERE f.cluster_id IS NOT NULL
        ORDER BY f.image_path, f.id
        """
    )

    rows = cur.fetchall()
    conn.close()

    images = {}

    for r in rows:
        path = r["image_path"]

        if path not in images:
            images[path] = {
                "image_path": path,
                "faces": [],
            }

        bbox_x, bbox_y, bbox_w, bbox_h = correct_bbox_for_orientation(
            path,
            _safe_float(r["bbox_x"]),
            _safe_float(r["bbox_y"]),
            _safe_float(r["bbox_w"]),
            _safe_float(r["bbox_h"]),
        )

        images[path]["faces"].append(
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

    return list(images.values())


# -----------------------------
# FACE CROP
# -----------------------------


@app.get("/api/face-crop")
def api_face_crop(
    path: str = Query(...),
    x: int = Query(...),
    y: int = Query(...),
    w: int = Query(...),
    h: int = Query(...),
):
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Image not found")

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
