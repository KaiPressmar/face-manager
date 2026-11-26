"""Store local albums and their membership in the photo library."""

from __future__ import annotations

import json
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from ..db.schema import get_conn
from .geo_query import GeoPhotoSelection, photo_selection_sql

MAX_ALBUM_NAME = 120
MAX_ALBUM_SETTINGS_BYTES = 32 * 1024
_ALBUM_SUMMARY = """SELECT a.id, a.name, a.journey, a.layout_draft, a.created_at, a.updated_at,
                          (SELECT COUNT(*) FROM album_photo WHERE album_id = a.id) AS photo_count,
                          COALESCE(a.cover_image_id, (
                              SELECT image_id FROM album_photo WHERE album_id = a.id
                              ORDER BY position, image_id LIMIT 1
                          )) AS cover_image_id
                   FROM album a"""


class AlbumPhotosMissingError(LookupError):
    """The submitted photo selection belongs to an earlier library state."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _name(value: str) -> str:
    title = value.strip()
    if not title or len(title) > MAX_ALBUM_NAME:
        raise ValueError(f"Ein Albumname muss zwischen 1 und {MAX_ALBUM_NAME} Zeichen lang sein.")
    return title


def _settings(value: dict | None) -> str | None:
    if value is None:
        return None
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(serialized.encode("utf-8")) > MAX_ALBUM_SETTINGS_BYTES:
        raise ValueError("Die Album-Einstellungen sind zu groß.")
    return serialized


def _serialize(row) -> dict:
    album = dict(row)
    for key in ("journey", "layout_draft"):
        if album[key] is not None:
            album[key] = json.loads(album[key])
    return album


def _get_album(conn, album_id: str) -> dict:
    row = conn.execute(f"{_ALBUM_SUMMARY} WHERE a.id = ?", (album_id,)).fetchone()
    if row is None:
        raise LookupError("Album nicht gefunden.")
    return _serialize(row)


def list_albums() -> list[dict]:
    with closing(get_conn()) as conn:
        return [_serialize(row) for row in conn.execute(
            f"{_ALBUM_SUMMARY} ORDER BY a.updated_at DESC, a.name COLLATE NOCASE, a.id"
        )]


def get_album(album_id: str) -> dict:
    with closing(get_conn()) as conn:
        return _get_album(conn, album_id)


@contextmanager
def _album_write():
    with closing(get_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def _insert_photos(
    conn, album_id: str, image_ids: list[int] | None, selection: GeoPhotoSelection | None,
) -> int:
    if image_ids is not None and selection is not None:
        raise ValueError("Gib entweder Fotos oder eine Auswahl an.")
    if image_ids is None and selection is None:
        return 0
    if image_ids is not None:
        unique_ids = tuple(dict.fromkeys(image_ids))
        parameters = [json.dumps(unique_ids)]
        candidates = """SELECT i.id, CAST(requested.key AS INTEGER) AS ordering
                        FROM json_each(?) requested JOIN image i ON i.id = requested.value"""
        matched = conn.execute(f"SELECT COUNT(*) FROM ({candidates})", parameters).fetchone()[0]
        if matched != len(unique_ids):
            raise AlbumPhotosMissingError("Einige Fotos sind nicht mehr in der Bibliothek. Lade die Auswahl erneut.")
    else:
        if not (selection.bounds or selection.from_date or selection.to_date or
                selection.image_ids is not None or selection.album_id):
            raise ValueError("Die Fotoauswahl benötigt mindestens einen Filter.")
        if selection.album_id is not None:
            _get_album(conn, selection.album_id)
        where, parameters = photo_selection_sql(selection)
        candidates = f"""SELECT i.id, ROW_NUMBER() OVER (
                            ORDER BY i.captured_at IS NULL, i.captured_at, i.id
                         ) AS ordering
                         FROM image i WHERE {where}"""
    current = conn.execute(
        "SELECT COALESCE(MAX(position), 0) FROM album_photo WHERE album_id = ?",
        (album_id,),
    ).fetchone()[0]
    return conn.execute(
        f"""INSERT INTO album_photo(album_id, image_id, position)
            SELECT ?, candidates.id, ? + ROW_NUMBER() OVER (ORDER BY candidates.ordering)
            FROM ({candidates}) candidates
            WHERE NOT EXISTS (
                SELECT 1 FROM album_photo WHERE album_id = ? AND image_id = candidates.id
            )""",
        (album_id, current, *parameters, album_id),
    ).rowcount


def create_album(
    name: str, *, image_ids: list[int] | None = None, selection: GeoPhotoSelection | None = None,
    journey: dict | None = None, layout_draft: dict | None = None,
) -> dict:
    album_id, timestamp = uuid4().hex, _now()
    with _album_write() as conn:
        conn.execute(
            """INSERT INTO album(id, name, journey, layout_draft, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (album_id, _name(name), _settings(journey), _settings(layout_draft), timestamp, timestamp),
        )
        _insert_photos(conn, album_id, image_ids, selection)
        return _get_album(conn, album_id)


def update_album(album_id: str, changes: dict) -> dict:
    with _album_write() as conn:
        current = _get_album(conn, album_id)
        values = {}
        if "name" in changes:
            values["name"] = _name(changes["name"])
        if "cover_image_id" in changes:
            cover = changes["cover_image_id"]
            if cover is not None and not conn.execute(
                "SELECT 1 FROM album_photo WHERE album_id = ? AND image_id = ?", (album_id, cover),
            ).fetchone():
                raise ValueError("Das Titelbild muss zum Album gehören.")
            values["cover_image_id"] = cover
        for key in ("journey", "layout_draft"):
            if key in changes:
                values[key] = _settings(changes[key])
        if not values:
            return current
        values["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        conn.execute(f"UPDATE album SET {assignments} WHERE id = ?", (*values.values(), album_id))
        return _get_album(conn, album_id)


def delete_album(album_id: str) -> None:
    with _album_write() as conn:
        deleted = conn.execute("DELETE FROM album WHERE id = ?", (album_id,)).rowcount
        if not deleted:
            raise LookupError("Album nicht gefunden.")


def add_album_photos(
    album_id: str, *, image_ids: list[int] | None = None, selection: GeoPhotoSelection | None = None,
) -> dict:
    if image_ids is None and selection is None:
        raise ValueError("Wähle Fotos für das Album aus.")
    with _album_write() as conn:
        album = _get_album(conn, album_id)
        if not _insert_photos(conn, album_id, image_ids, selection):
            return album
        conn.execute("UPDATE album SET updated_at = ? WHERE id = ?", (_now(), album_id))
        return _get_album(conn, album_id)


def remove_album_photos(album_id: str, image_ids: list[int]) -> dict:
    with _album_write() as conn:
        album = _get_album(conn, album_id)
        removed = conn.execute(
            "DELETE FROM album_photo WHERE album_id = ? AND image_id IN (SELECT value FROM json_each(?))",
            (album_id, json.dumps(image_ids)),
        ).rowcount
        if not removed:
            return album
        conn.execute(
            """UPDATE album SET updated_at = ?, cover_image_id = CASE WHEN EXISTS (
                    SELECT 1 FROM album_photo WHERE album_id = album.id AND image_id = album.cover_image_id
               ) THEN cover_image_id ELSE NULL END WHERE id = ?""",
            (_now(), album_id),
        )
        return _get_album(conn, album_id)
