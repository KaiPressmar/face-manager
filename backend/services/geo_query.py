"""Shared photo selection for the map and library queries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class GeoBounds:
    west: float
    east: float
    south: float
    north: float


@dataclass(frozen=True)
class GeoPhotoSelection:
    bounds: GeoBounds | None = None
    from_date: date | None = None
    to_date: date | None = None
    image_ids: tuple[int, ...] | None = None
    album_id: str | None = None


def validate_photo_selection(selection: GeoPhotoSelection) -> None:
    """Validate optional bounds and inclusive EXIF date endpoints."""
    if selection.bounds is not None:
        bounds = selection.bounds
        if not (-180 <= bounds.west <= 180 and -180 <= bounds.east <= 180 and
                -90 <= bounds.south <= bounds.north <= 90):
            raise ValueError("Ungültiger Kartenausschnitt.")
    if selection.from_date and selection.to_date and selection.from_date > selection.to_date:
        raise ValueError("Das Enddatum muss am oder nach dem Startdatum liegen.")
    if selection.album_id is not None and not re.fullmatch(r"[0-9a-f]{32}", selection.album_id):
        raise ValueError("Ungültiges Album.")


def photo_selection_sql(selection: GeoPhotoSelection, *, require_coordinates: bool = False):
    """Build an SQL expression for photos with the alias i."""
    clauses = []
    params = []
    if selection.bounds is not None:
        west, east, south, north = (
            selection.bounds.west, selection.bounds.east, selection.bounds.south, selection.bounds.north,
        )
        clauses.extend(("i.latitude BETWEEN ? AND ?", "i.longitude IS NOT NULL"))
        params.extend((south, north))
        if west <= east:
            clauses.append("i.longitude BETWEEN ? AND ?")
        else:
            clauses.append("(i.longitude >= ? OR i.longitude <= ?)")
        params.extend((west, east))
    elif require_coordinates:
        clauses.extend(("i.latitude IS NOT NULL", "i.longitude IS NOT NULL"))
    if selection.from_date:
        clauses.append("i.captured_at >= ?")
        params.append(selection.from_date.isoformat())
    if selection.to_date:
        clauses.append("i.captured_at <= ?")
        params.append(selection.to_date.isoformat() + "T23:59:59.999999")
    if selection.image_ids is not None:
        clauses.append("i.id IN (SELECT value FROM json_each(?))")
        params.append(json.dumps(selection.image_ids, separators=(",", ":")))
    if selection.album_id is not None:
        clauses.append("EXISTS (SELECT 1 FROM album_photo ap WHERE ap.album_id = ? AND ap.image_id = i.id)")
        params.append(selection.album_id)
    return " AND ".join(clauses) or "1 = 1", params
