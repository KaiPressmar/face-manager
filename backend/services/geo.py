"""Persist photo EXIF coordinates and serve bounded map queries."""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime
from pathlib import Path

from PIL import Image

from ..db.schema import get_conn
from .filesystem_paths import filesystem_path

logger = logging.getLogger("face_manager.geo")
_GPS_TAG = 34853
_DATE_TAGS = (36867, 36868)  # Original, then digitized.
_BATCH_SIZE = 32


def _degrees(value):
    try:
        parts = [float(part) for part in value]
        if (len(parts) != 3 or not all(math.isfinite(part) and part >= 0 for part in parts)
                or parts[1] >= 60 or parts[2] >= 60):
            return None
        return parts[0] + parts[1] / 60 + parts[2] / 3600
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


def _coordinate(value, reference, positive, negative, maximum):
    degrees = _degrees(value)
    if isinstance(reference, bytes):
        reference = reference.decode("ascii", errors="ignore")
    if degrees is None or degrees > maximum or reference not in (positive, negative):
        return None
    return -degrees if reference == negative else degrees


def extract_image_metadata(path):
    """Read metadata without decoding pixel data; invalid fields remain null."""
    try:
        with Image.open(filesystem_path(path)) as image:
            exif = image.getexif()
            gps = exif.get_ifd(_GPS_TAG) if _GPS_TAG in exif else {}
            latitude = _coordinate(gps.get(2), gps.get(1), "N", "S", 90)
            longitude = _coordinate(gps.get(4), gps.get(3), "E", "W", 180)
            if latitude is None or longitude is None:
                latitude = longitude = None
            captured_at = None
            exif_ifd = exif.get_ifd(34665) if 34665 in exif else {}
            for tag in _DATE_TAGS:
                raw = exif_ifd.get(tag) or exif.get(tag)
                if isinstance(raw, bytes):
                    raw = raw.decode("ascii", errors="ignore")
                if not isinstance(raw, str):
                    continue
                try:
                    captured_at = datetime.strptime(raw.strip().rstrip("\x00"), "%Y:%m:%d %H:%M:%S").isoformat(timespec="seconds")
                    break
                except ValueError:
                    continue
            return latitude, longitude, captured_at
    except Exception:
        # EXIF is optional and supplied by arbitrary cameras/editors. A corrupt
        # metadata block must not prevent an otherwise readable image import.
        logger.debug("Could not read optional photo metadata", exc_info=True)
        return None, None, None


def record_image_metadata(cursor, image_id, path):
    """Scan one canonical image once, including images without EXIF."""
    row = cursor.execute(
        "SELECT metadata_scanned_at FROM image WHERE id = ?", (image_id,)
    ).fetchone()
    if row is None or row["metadata_scanned_at"] not in (None, "missing"):
        return
    latitude, longitude, captured_at = extract_image_metadata(path)
    cursor.execute(
        """UPDATE image SET latitude = ?, longitude = ?, captured_at = ?,
                  metadata_scanned_at = CURRENT_TIMESTAMP WHERE id = ?""",
        (latitude, longitude, captured_at, image_id),
    )


class GeoBackfill:
    """Low priority, resumable scan of legacy images in small transactions."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread = None

    @property
    def indexing(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.indexing:
            return
        self._stop.clear()
        # Missing files are retried once each application run, including after
        # a disconnected library volume becomes available again.
        conn = get_conn()
        try:
            conn.execute("UPDATE image SET metadata_scanned_at = NULL WHERE metadata_scanned_at = 'missing'")
            conn.commit()
        finally:
            conn.close()
        self._thread = threading.Thread(target=self._run, name="geo-backfill", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self):
        while not self._stop.is_set():
            conn = get_conn()
            try:
                rows = conn.execute(
                    """SELECT i.id, i.path FROM image i
                       WHERE i.metadata_scanned_at IS NULL
                       ORDER BY i.id LIMIT ?""",
                    (_BATCH_SIZE,),
                ).fetchall()
                if not rows:
                    return
                for row in rows:
                    if self._stop.is_set():
                        break
                    try:
                        # Read outside the write transaction so imports stay responsive.
                        locations = conn.execute(
                            "SELECT path FROM image_location WHERE image_id = ? ORDER BY id",
                            (row["id"],),
                        ).fetchall()
                        candidates = [location["path"] for location in locations]
                        candidates.append(row["path"])
                        available = next(
                            (path for path in candidates if Path(filesystem_path(path)).is_file()),
                            None,
                        )
                        metadata = extract_image_metadata(available) if available else (None, None, None)
                        conn.execute(
                            """UPDATE image SET latitude = ?, longitude = ?, captured_at = ?,
                                      metadata_scanned_at = ?
                               WHERE id = ? AND metadata_scanned_at IS NULL""",
                            (*metadata, datetime.now().isoformat() if available else "missing", row["id"]),
                        )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        logger.exception("Could not index metadata for image %s", row["id"])
                        # Prevent one bad row from trapping the worker forever.
                        conn.execute(
                            "UPDATE image SET metadata_scanned_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (row["id"],),
                        )
                        conn.commit()
            finally:
                conn.close()
            self._stop.wait(0.1)


geo_backfill = GeoBackfill()


def _filter_sql(west, east, south, north, from_date, to_date):
    clauses = ["i.latitude BETWEEN ? AND ?", "i.longitude IS NOT NULL"]
    params = [south, north]
    if west <= east:
        clauses.append("i.longitude BETWEEN ? AND ?")
        params.extend((west, east))
    else:
        clauses.append("(i.longitude >= ? OR i.longitude <= ?)")
        params.extend((west, east))
    if from_date:
        clauses.append("i.captured_at >= ?")
        params.append(from_date.isoformat())
    if to_date:
        clauses.append("i.captured_at <= ?")
        # Dates are inclusive even when EXIF time is present.
        params.append(to_date.isoformat() + "T23:59:59.999999")
    return " AND ".join(clauses), params


def _status(conn):
    library_total = conn.execute("SELECT COUNT(*) FROM image").fetchone()[0]
    located_total = conn.execute(
        "SELECT COUNT(*) FROM image WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchone()[0]
    pending_total = conn.execute(
        "SELECT COUNT(*) FROM image WHERE metadata_scanned_at IS NULL"
    ).fetchone()[0]
    return dict(library_total=library_total, located_total=located_total,
                pending_total=pending_total, indexing=geo_backfill.indexing)


def list_map_points(west, east, south, north, from_date=None, to_date=None, zoom=2):
    """Aggregate a viewport into at most 32 by 32 cells in SQLite."""
    where, params = _filter_sql(west, east, south, north, from_date, to_date)
    span_lon = east - west if east >= west else east + 360 - west
    span_lat = north - south
    # Exact viewport cells let a clicked point query its contents without rounding.
    cell_lon = max(span_lon / 32, 0.0000001)
    cell_lat = max(span_lat / 32, 0.0000001)
    unwrapped = "(i.longitude + CASE WHEN i.longitude < ? THEN 360 ELSE 0 END)"
    x_expr = f"MIN(31, CAST(({unwrapped} - ?) / ? AS INTEGER))"
    y_expr = "MIN(31, CAST((i.latitude - ?) / ? AS INTEGER))"
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""SELECT {x_expr} AS x, {y_expr} AS y, COUNT(*) AS count,
                       AVG({unwrapped}) AS longitude, AVG(i.latitude) AS latitude,
                       MIN({unwrapped}) AS west, MAX({unwrapped}) AS east,
                       MIN(i.latitude) AS south, MAX(i.latitude) AS north
                FROM image i WHERE {where} GROUP BY x, y ORDER BY y, x""",
            (west, west, cell_lon, south, cell_lat, west, west, west, *params),
        ).fetchall()
        points = []
        for row in rows:
            longitude = row["longitude"]
            points.append(dict(
                latitude=row["latitude"],
                longitude=longitude - 360 if longitude > 180 else longitude,
                count=row["count"],
                west=row["west"] - 360 if row["west"] > 180 else row["west"],
                east=row["east"] - 360 if row["east"] > 180 else row["east"],
                south=row["south"], north=row["north"],
            ))
        return dict(points=points, total=sum(point["count"] for point in points), **_status(conn))
    finally:
        conn.close()


def list_map_images(west, east, south, north, from_date=None, to_date=None, limit=40, offset=0):
    """Return one stable page of canonical geotagged images."""
    where, params = _filter_sql(west, east, south, north, from_date, to_date)
    conn = get_conn()
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM image i WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT i.id, i.path AS image_path, i.latitude, i.longitude,
                       i.captured_at, i.filename,
                       (SELECT l.created_at FROM image_location l
                        WHERE l.image_id = i.id ORDER BY l.id LIMIT 1) AS created_at
                FROM image i WHERE {where}
                ORDER BY i.captured_at DESC, i.id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        return dict(items=[dict(row) for row in rows], total=total, limit=limit, offset=offset)
    finally:
        conn.close()
