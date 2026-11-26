"""Build offline photo journeys from observed locations and capture times."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt

from fastapi import HTTPException

from ..db.schema import get_conn
from .geo import geo_backfill
from .geo_query import GeoPhotoSelection, photo_selection_sql, validate_photo_selection

STOP_RADIUS_KM = 1
STOP_WINDOW = timedelta(hours=2)
JOURNEY_GAP = timedelta(hours=24)
_VALID_LOCATION = "latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180"


@dataclass(frozen=True)
class Observation:
    image_id: int
    latitude: float
    longitude: float
    wall_time: datetime
    utc_time: datetime | None

    @property
    def order_time(self) -> datetime:
        # Return timezone-free values of the same kind for durations. The
        # caller only compares these values when both photos use the same clock.
        return self.utc_time.replace(tzinfo=None) if self.utc_time else self.wall_time

    def location(self) -> dict[str, float]:
        return {"latitude": self.latitude, "longitude": self.longitude}


def _distance_km(first: Observation, second: Observation) -> float:
    first_lat, second_lat = radians(first.latitude), radians(second.latitude)
    latitude_delta = second_lat - first_lat
    longitude_delta = radians(second.longitude - first.longitude)
    chord = sin(latitude_delta / 2) ** 2 + cos(first_lat) * cos(second_lat) * sin(longitude_delta / 2) ** 2
    return 6371.0088 * 2 * asin(min(1, sqrt(chord)))


def _ordered_observations(observations: list[Observation]) -> list[Observation]:
    wall_order = sorted(observations, key=lambda photo: (photo.wall_time, photo.image_id))
    utc_order = iter(sorted((photo for photo in observations if photo.utc_time),
                            key=lambda photo: (photo.utc_time, photo.image_id)))
    # Preserve the trustworthy UTC ordering. Unverified local times retain
    # their approximate display positions and provide no evidence for a connection.
    return [next(utc_order) if photo.utc_time else photo for photo in wall_order]


def _ambiguous_ties(observations: list[Observation]) -> set[int]:
    anchors: dict[datetime, Observation] = {}
    ambiguous_times: set[datetime] = set()
    for photo in observations:
        if photo.utc_time:
            anchor = anchors.setdefault(photo.utc_time, photo)
            if _distance_km(anchor, photo) > STOP_RADIUS_KM:
                ambiguous_times.add(photo.utc_time)
    return {photo.image_id for photo in observations if not photo.utc_time or photo.utc_time in ambiguous_times}


def _group_stops(photos: list[Observation], uncertain: set[int]) -> list[list[Observation]]:
    stops: list[list[Observation]] = []
    for photo in photos:
        if stops:
            anchor, previous = stops[-1][0], stops[-1][-1]
            same_clock = bool(previous.utc_time) == bool(photo.utc_time)
            same_certainty = (previous.image_id in uncertain) == (photo.image_id in uncertain)
            if (same_clock and same_certainty and timedelta(0) <= photo.order_time - previous.order_time <= STOP_WINDOW
                    and _distance_km(anchor, photo) <= STOP_RADIUS_KM):
                stops[-1].append(photo)
                continue
        stops.append([photo])
    return stops


def build_journey(observations: list[Observation]) -> dict:
    photos = _ordered_observations(observations)
    uncertain = _ambiguous_ties(photos)
    groups = _group_stops(photos, uncertain)
    stops = []
    connections = []
    previous = None
    previous_anchor = None
    previous_uncertain = False
    section = 1
    for number, group in enumerate(groups, 1):
        first, last = group[0], group[-1]
        stop_uncertain = first.image_id in uncertain
        duration = None
        if previous and bool(previous.utc_time) == bool(first.utc_time):
            duration = first.order_time - previous.order_time
        if duration and duration > JOURNEY_GAP:
            section += 1
        stops.append({
            "id": str(first.image_id),
            "number": number,
            "section": section,
            **first.location(),
            "start": first.wall_time.isoformat(),
            "end": last.wall_time.isoformat(),
            "start_utc": first.utc_time.isoformat() if first.utc_time else None,
            "end_utc": last.utc_time.isoformat() if last.utc_time else None,
            "image_ids": [photo.image_id for photo in group],
            "uncertain": stop_uncertain,
        })
        if (previous_anchor and not previous_uncertain and not stop_uncertain and duration
                and timedelta(0) < duration <= JOURNEY_GAP and first.location() != previous_anchor.location()):
            connections.append({"from": previous_anchor.location(), "to": first.location()})
        previous, previous_anchor, previous_uncertain = last, first, stop_uncertain
    return {
        "stops": stops,
        "connections": connections,
        "photo_count": len(photos),
        "uncertain_count": len(uncertain),
    }


def _read_capture_time(value: str | None) -> datetime | None:
    if not value or len(value) < 19:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def list_journey(from_date: date | None, to_date: date | None) -> dict:
    date_filter, parameters = photo_selection_sql(GeoPhotoSelection(from_date=from_date, to_date=to_date))

    with closing(get_conn()) as connection:
        connection.execute("BEGIN")
        missing_time = connection.execute(
            f"""SELECT COUNT(*) FROM image
                WHERE {_VALID_LOCATION} AND captured_at IS NULL"""
        ).fetchone()[0]
        missing_location = connection.execute(
            f"""SELECT COUNT(*) FROM image i
                WHERE {date_filter} AND COALESCE(({_VALID_LOCATION}), 0) = 0""",
            parameters,
        ).fetchone()[0]
        rows = connection.execute(
            f"""SELECT id, latitude, longitude, captured_at, captured_at_utc
                FROM image i WHERE {date_filter} AND {_VALID_LOCATION}
                AND captured_at IS NOT NULL""",
            parameters,
        )
        observations = []
        for row in rows:
            wall = _read_capture_time(row["captured_at"])
            if wall is None:
                missing_time += 1
                continue
            instant = _read_capture_time(row["captured_at_utc"])
            utc = instant.astimezone(timezone.utc) if instant and instant.tzinfo is not None else None
            observations.append(Observation(
                row["id"], row["latitude"], row["longitude"], wall.replace(tzinfo=None), utc,
            ))
    return {
        **build_journey(observations),
        "missing_location_count": missing_location,
        "missing_time_count": missing_time,
        "indexing": geo_backfill.indexing,
    }


def get_journey(from_date: date | None = None, to_date: date | None = None):
    try:
        validate_photo_selection(GeoPhotoSelection(from_date=from_date, to_date=to_date))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return list_journey(from_date, to_date)
