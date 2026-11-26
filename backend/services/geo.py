"""Persist photo EXIF coordinates and serve bounded map queries."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Callable, NamedTuple
from uuid import uuid4

from PIL import Image

from ..db.schema import HASH_CHUNK_SIZE, get_conn
from .background_activities import ActivityStatus, BackgroundActivities, BackgroundActivity, background_activities
from .filesystem_paths import filesystem_path
from .geo_query import GeoBounds, GeoPhotoSelection, photo_selection_sql

logger = logging.getLogger("face_manager.geo")
_GPS_TAG = 34853
_ORIGINAL_DATE_TAG = 36867
_ORIGINAL_OFFSET_TAG = 36881
_MAX_CLOCK_DISAGREEMENT = timedelta(minutes=5)
_MAX_CLOCK_OFFSET = timedelta(hours=14)
_BATCH_SIZE = 32
MAP_GRID_SIZE = 32
PHOTO_METADATA_ACTIVITY = "photo-metadata"
_PENDING_METADATA = """metadata_scanned_at IS NULL
                       OR (capture_time_source IS NULL AND latitude IS NOT NULL AND longitude IS NOT NULL)"""
_LOCATION_IDENTITY_COLUMNS = ("file_device", "file_inode", "file_size", "modified_at_ns", "file_changed_at_ns")


class CaptureMetadata(NamedTuple):
    latitude: float | None
    longitude: float | None
    captured_at: str | None
    captured_at_utc: str | None
    capture_time_source: str


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


def _exif_text(value):
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="ignore")
    return value.strip(" \t\r\n\x00") if isinstance(value, str) else ""


def _camera_time(exif, exif_ifd):
    # The digitized date can describe a recent scan of an old photograph.
    raw = _exif_text(exif_ifd.get(_ORIGINAL_DATE_TAG) or exif.get(_ORIGINAL_DATE_TAG))
    try:
        local = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None, None
    offset = _exif_text(exif_ifd.get(_ORIGINAL_OFFSET_TAG) or exif.get(_ORIGINAL_OFFSET_TAG))
    if not re.fullmatch(r"[+-][0-9]{2}:[0-9]{2}", offset) or offset == "-00:00":
        return local, None
    hours, minutes = int(offset[1:3]), int(offset[4:])
    if minutes >= 60 or hours * 60 + minutes > 14 * 60:
        return local, None
    delta = timedelta(hours=hours, minutes=minutes)
    if offset[0] == "-":
        delta = -delta
    try:
        utc = local.replace(tzinfo=timezone(delta)).astimezone(timezone.utc)
    except OverflowError:
        utc = None
    return local, utc


def _gps_utc_time(gps):
    # GPS time alone is ambiguous without the GPS date stamp.
    try:
        day = datetime.strptime(_exif_text(gps.get(29)), "%Y:%m:%d")
        hours, minutes, seconds = (float(value) for value in gps[7])
        if not (0 <= hours < 24 and hours.is_integer() and 0 <= minutes < 60 and
                minutes.is_integer() and 0 <= seconds < 60):
            return None
        return (day + timedelta(hours=hours, minutes=minutes, seconds=seconds)).replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


def _capture_time(exif, exif_ifd, gps):
    local, offset_utc = _camera_time(exif, exif_ifd)
    gps_utc = _gps_utc_time(gps)
    if local and gps_utc:
        if offset_utc:
            disagrees = abs(gps_utc - offset_utc) > _MAX_CLOCK_DISAGREEMENT
        else:
            # A local clock and GPS can legitimately differ by a timezone.
            # Larger gaps are inconsistent with any supported EXIF offset.
            disagrees = abs(gps_utc - local.replace(tzinfo=timezone.utc)) > _MAX_CLOCK_OFFSET + _MAX_CLOCK_DISAGREEMENT
        if disagrees:
            return local.isoformat(timespec="seconds"), None, "conflict"
    instant = offset_utc or gps_utc
    if instant:
        source = "exif_original_offset" if offset_utc else "gps_utc"
        # Preserve the camera's calendar day. GPS supplies a UTC date only
        # when there is no camera timestamp at all.
        wall_time = local or instant.replace(tzinfo=None)
        return wall_time.isoformat(timespec="seconds"), instant.isoformat(timespec="microseconds"), source
    if local:
        return local.isoformat(timespec="seconds"), None, "local"
    return None, None, "missing"


def extract_image_metadata(path_or_file, *, include_timing=False):
    """Read metadata from a path or open photo without decoding pixel data."""
    try:
        source = path_or_file if hasattr(path_or_file, "read") else filesystem_path(path_or_file)
        with Image.open(source) as image:
            exif = image.getexif()
            gps = exif.get_ifd(_GPS_TAG) if _GPS_TAG in exif else {}
            latitude = _coordinate(gps.get(2), gps.get(1), "N", "S", 90)
            longitude = _coordinate(gps.get(4), gps.get(3), "E", "W", 180)
            if latitude is None or longitude is None:
                latitude = longitude = None
            exif_ifd = exif.get_ifd(34665) if 34665 in exif else {}
            metadata = CaptureMetadata(latitude, longitude, *_capture_time(exif, exif_ifd, gps))
    except OSError as exc:
        if exc.errno is not None or getattr(exc, "winerror", None) is not None:
            metadata = CaptureMetadata(None, None, None, None, "unavailable")
        else:
            logger.debug("Could not read optional photo metadata", exc_info=True)
            metadata = CaptureMetadata(None, None, None, None, "missing")
    except Exception:
        # EXIF is optional and supplied by arbitrary cameras/editors. A corrupt
        # metadata block must not prevent an otherwise readable image import.
        logger.debug("Could not read optional photo metadata", exc_info=True)
        metadata = CaptureMetadata(None, None, None, None, "missing")
    return metadata if include_timing else metadata[:3]


def _with_timing(metadata) -> CaptureMetadata:
    if isinstance(metadata, CaptureMetadata):
        return metadata
    latitude, longitude, captured_at = metadata
    return CaptureMetadata(latitude, longitude, captured_at, None, "local" if captured_at else "missing")


def record_image_metadata(cursor, image_id, path, *, metadata=None):
    """Save metadata once for a canonical image, including absent EXIF."""
    row = cursor.execute(
        "SELECT metadata_scanned_at, capture_time_source FROM image WHERE id = ?", (image_id,)
    ).fetchone()
    if row is None or (row["metadata_scanned_at"] not in (None, "missing") and
                       row["capture_time_source"] not in (None, "unavailable")):
        return
    capture = _with_timing(metadata if metadata is not None else extract_image_metadata(path, include_timing=True))
    _save_capture_metadata(cursor, image_id, capture)


def _save_capture_metadata(connection, image_id, metadata: CaptureMetadata) -> int:
    scanned_at = "missing" if metadata.capture_time_source == "unavailable" else datetime.now().isoformat()
    return connection.execute(
        """UPDATE image SET latitude = COALESCE(latitude, ?),
                  longitude = COALESCE(longitude, ?), captured_at = COALESCE(captured_at, ?),
                  captured_at_utc = CASE WHEN captured_at IS NULL OR captured_at = ?
                                         THEN ? ELSE captured_at_utc END,
                  capture_time_source = CASE WHEN captured_at IS NULL OR captured_at = ?
                                             THEN ? WHEN captured_at_utc IS NULL AND ? = 'unavailable'
                                             THEN 'unavailable' ELSE COALESCE(capture_time_source, 'local') END,
                  metadata_scanned_at = CASE WHEN metadata_scanned_at IS NULL OR metadata_scanned_at = 'missing'
                                             THEN ? ELSE metadata_scanned_at END
           WHERE id = ?
             AND (metadata_scanned_at IS NULL OR metadata_scanned_at = 'missing'
                  OR capture_time_source IS NULL OR capture_time_source = 'unavailable')""",
        (metadata.latitude, metadata.longitude, metadata.captured_at, metadata.captured_at,
         metadata.captured_at_utc, metadata.captured_at, metadata.capture_time_source,
         metadata.capture_time_source, scanned_at, image_id),
    ).rowcount


def _source_identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _legacy_capture(row, candidates, stop_event: threading.Event) -> CaptureMetadata:
    """Read a new clock only from verified bytes or a registered file identity."""
    content_hash = row["content_hash"]
    unavailable = False
    for path, recorded_identity in candidates:
        if stop_event.is_set():
            break
        if not content_hash and (recorded_identity is None or None in recorded_identity):
            continue
        try:
            with open(filesystem_path(path), "rb") as source:
                original = _source_identity(os.fstat(source.fileno()))
                if not content_hash and original != recorded_identity:
                    continue
                metadata = _with_timing(extract_image_metadata(source, include_timing=True))
                if metadata.capture_time_source == "unavailable":
                    unavailable = True
                    continue
                if metadata.captured_at_utc is None and row["captured_at"] is not None:
                    # Historic wall time already exists. Only verify full
                    # bytes when we can add an instant or flag a clock conflict.
                    if metadata.capture_time_source != "conflict":
                        continue
                if content_hash:
                    source.seek(0)
                    digest = hashlib.sha256()
                    while not stop_event.is_set() and (block := source.read(HASH_CHUNK_SIZE)):
                        digest.update(block)
                    if stop_event.is_set() or digest.hexdigest() != content_hash.casefold():
                        continue
                if _source_identity(os.fstat(source.fileno())) == original:
                    return metadata
        except OSError:
            unavailable = True
    source = "unavailable" if unavailable else ("local" if row["captured_at"] else "missing")
    return CaptureMetadata(None, None, row["captured_at"], None, source)


class GeoBackfill:
    """Low priority, resumable scan of legacy images in small transactions."""

    def __init__(self, activities: BackgroundActivities | None = None):
        self._stop = threading.Event()
        self._thread = None
        self._activities = activities

    @property
    def indexing(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.indexing:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="geo-backfill", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self):
        activity_id = None
        processed = total = unavailable = 0
        status: ActivityStatus = "completed"

        def publish(activity_status: ActivityStatus, finished_at: str | None = None) -> None:
            if self._activities is not None and activity_id is not None:
                self._activities.update(BackgroundActivity(
                    activity_id, PHOTO_METADATA_ACTIVITY, activity_status, processed,
                    processed if activity_status == "completed" else max(total, processed),
                    unavailable, finished_at,
                ))

        def committed(file_unavailable: bool) -> None:
            nonlocal activity_id, processed, unavailable
            processed += 1
            unavailable += file_unavailable
            if self._activities is not None and activity_id is None:
                activity_id = uuid4().hex
            publish("running")

        retry_missing = True
        counted_pending = self._activities is None
        try:
            while not self._stop.is_set():
                try:
                    with closing(get_conn()) as conn:
                        if retry_missing:
                            # Retry unavailable files once each run, for example
                            # after a disconnected library volume returns.
                            conn.execute(
                                "UPDATE image SET metadata_scanned_at = NULL WHERE metadata_scanned_at = 'missing'"
                            )
                            conn.execute(
                                "UPDATE image SET capture_time_source = NULL WHERE capture_time_source = 'unavailable'"
                            )
                            conn.commit()
                            retry_missing = False
                        if not counted_pending:
                            total = conn.execute(f"SELECT COUNT(*) FROM image WHERE {_PENDING_METADATA}").fetchone()[0]
                            counted_pending = True
                            if total:
                                activity_id = uuid4().hex
                                publish("running")
                        if not self._scan_batch(conn, committed):
                            break
                except sqlite3.OperationalError as exc:
                    # Leave work pending when a competing writer times out the scan.
                    code = getattr(exc, "sqlite_errorcode", None)
                    if code is None:  # Python 3.10 does not expose SQLite result codes.
                        locked = "is locked" in str(exc) or str(exc) == "database is busy"
                    else:
                        locked = (code & 0xff) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                    if not locked:
                        raise
                self._stop.wait(0.1)
        except Exception:
            status = "failed"
            logger.exception("Could not index photo metadata")
        else:
            if self._stop.is_set():
                status = "cancelled"
        finally:
            if activity_id is not None and self._activities is not None:
                if status == "completed" and processed == 0:
                    self._activities.discard(PHOTO_METADATA_ACTIVITY, activity_id)
                else:
                    publish(status, datetime.now(timezone.utc).isoformat())

    def _scan_batch(self, conn, committed: Callable[[bool], None]):
        rows = conn.execute(
            f"""SELECT id, path, content_hash, latitude, longitude, captured_at, metadata_scanned_at FROM image
               WHERE {_PENDING_METADATA}
               ORDER BY id LIMIT ?""",
            (_BATCH_SIZE,),
        ).fetchall()
        for row in rows:
            if self._stop.is_set():
                break
            # Resolve locations and read EXIF before taking the write lock.
            locations = conn.execute(
                """SELECT path, file_device, file_inode, file_size, modified_at_ns, file_changed_at_ns
                   FROM image_location WHERE image_id = ? ORDER BY id""",
                (row["id"],),
            ).fetchall()
            candidates = [
                (location["path"], tuple(location[column] for column in _LOCATION_IDENTITY_COLUMNS))
                for location in locations
            ]
            if all(path != row["path"] for path, _ in candidates):
                candidates.append((row["path"], None))
            if row["metadata_scanned_at"] is not None or (row["latitude"] is not None and row["longitude"] is not None):
                metadata = _legacy_capture(row, candidates, self._stop)
            else:
                available = next((path for path, _ in candidates if os.path.isfile(filesystem_path(path))), None)
                metadata = (
                    _with_timing(extract_image_metadata(available, include_timing=True))
                    if available else CaptureMetadata(None, None, None, None, "unavailable")
                )
            if (row["latitude"] is not None and row["longitude"] is not None and
                    metadata.latitude is not None and metadata.longitude is not None and
                    (abs(row["latitude"] - metadata.latitude) > 0.000001 or
                     abs(row["longitude"] - metadata.longitude) > 0.000001)):
                # A legacy file at the same path may have been replaced. Do
                # not attach its clock to the older photo's known location.
                metadata = CaptureMetadata(None, None, row["captured_at"], None, "conflict")
            if self._stop.is_set():
                break
            changed_rows = _save_capture_metadata(conn, row["id"], metadata)
            conn.commit()
            if changed_rows:
                committed(metadata.capture_time_source == "unavailable")
        return bool(rows)


geo_backfill = GeoBackfill(background_activities)


def _filter_sql(west, east, south, north, from_date, to_date, album_id=None):
    return photo_selection_sql(GeoPhotoSelection(GeoBounds(west, east, south, north), from_date, to_date, album_id=album_id))


def _status(conn, album_id=None):
    where, parameters = photo_selection_sql(GeoPhotoSelection(album_id=album_id))
    totals = conn.execute(
        f"""SELECT COUNT(*) AS library_total,
                   COUNT(CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL THEN 1 END) AS located_total,
                   COUNT(CASE WHEN metadata_scanned_at IS NULL THEN 1 END) AS pending_total
            FROM image i WHERE {where}""",
        parameters,
    ).fetchone()
    return {**dict(totals), "indexing": geo_backfill.indexing}


def list_map_points(
    west, east, south, north, from_date=None, to_date=None, zoom=2,
    grid_columns=MAP_GRID_SIZE, grid_rows=MAP_GRID_SIZE, *, album_id=None,
):
    """Aggregate a viewport into cells indexed from its southwest corner."""
    where, params = _filter_sql(west, east, south, north, from_date, to_date, album_id)
    span_lon = east - west if east >= west else (180 - west) + (east + 180)
    span_lat = north - south
    cell_lon = span_lon / grid_columns or 1
    cell_lat = span_lat / grid_rows or 1
    unwrapped = "(i.longitude + CASE WHEN i.longitude < ? THEN 360 ELSE 0 END)"
    distance_lon = """CASE WHEN i.longitude < ?
                           THEN (180 - ?) + (i.longitude + 180)
                           ELSE i.longitude - ? END"""
    x_expr = f"MIN(?, CAST(({distance_lon}) / ? AS INTEGER))"
    y_expr = "MIN(?, CAST((i.latitude - ?) / ? AS INTEGER))"
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        # Keep stored longitudes for drill-down. Unwrapping can change their
        # precision and makes both representations of the date line equal.
        rows = conn.execute(
            f"""SELECT {x_expr} AS x, {y_expr} AS y, COUNT(*) AS count,
                       AVG({unwrapped}) AS longitude, AVG(i.latitude) AS latitude,
                       COALESCE(MIN(CASE WHEN i.longitude >= ? THEN i.longitude END),
                                MIN(i.longitude)) AS west,
                       COALESCE(MAX(CASE WHEN i.longitude < ? THEN i.longitude END),
                                MAX(i.longitude)) AS east,
                       MIN(i.latitude) AS south, MAX(i.latitude) AS north
                FROM image i WHERE {where} GROUP BY x, y ORDER BY y, x""",
            (
                grid_columns - 1, west, west, west, cell_lon,
                grid_rows - 1, south, cell_lat, west, west, west, *params,
            ),
        ).fetchall()
        points = []
        for row in rows:
            longitude = row["longitude"]
            points.append(dict(
                x=row["x"], y=row["y"],
                latitude=row["latitude"],
                longitude=longitude - 360 if longitude > 180 else longitude,
                count=row["count"],
                west=row["west"], east=row["east"],
                south=row["south"], north=row["north"],
            ))
        return dict(
            points=points, total=sum(point["count"] for point in points),
            grid_columns=grid_columns, grid_rows=grid_rows, **_status(conn, album_id),
        )
    finally:
        conn.close()


def list_map_images(
    west, east, south, north, from_date=None, to_date=None, limit=40, offset=0,
    before_id=None, before_captured_at=None, *, album_id=None,
):
    """Return canonical geotagged images and a cursor for the next page."""
    return list_selected_map_images(
        GeoPhotoSelection(GeoBounds(west, east, south, north), from_date, to_date, album_id=album_id),
        limit, offset, before_id, before_captured_at,
    )


def list_selected_map_images(selection, limit=40, offset=0, before_id=None, before_captured_at=None):
    """Return a cursor page for a map selection, including an exact stop's IDs."""
    where, params = photo_selection_sql(selection, require_coordinates=selection.image_ids is None)
    page_where = where
    page_params = list(params)
    if before_id is not None:
        if before_captured_at is None:
            page_where += " AND i.captured_at IS NULL AND i.id < ?"
            page_params.append(before_id)
        else:
            page_where += """ AND (i.captured_at < ? OR
                            (i.captured_at = ? AND i.id < ?) OR i.captured_at IS NULL)"""
            page_params.extend((before_captured_at, before_captured_at, before_id))
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        total = conn.execute(f"SELECT COUNT(*) FROM image i WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT i.id, i.path AS image_path, i.latitude, i.longitude,
                       i.captured_at, i.filename,
                       (SELECT l.created_at FROM image_location l
                        WHERE l.image_id = i.id ORDER BY l.id LIMIT 1) AS created_at
                FROM image i WHERE {page_where}
                ORDER BY i.captured_at DESC, i.id DESC LIMIT ? OFFSET ?""",
            (*page_params, limit + 1, offset),
        ).fetchall()
        items = [dict(row) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            next_cursor = dict(before_id=last["id"], before_captured_at=last["captured_at"])
        return dict(items=items, total=total, limit=limit, offset=offset, next_cursor=next_cursor)
    finally:
        conn.close()
