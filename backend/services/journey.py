"""Build offline photo journeys from observed locations and capture times."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
from math import asin, cos, radians, sin, sqrt
from typing import Literal, TypeVar

from fastapi import HTTPException

from ..db.schema import get_conn
from .geo import geo_backfill
from .geo_query import GeoBounds, GeoPhotoSelection, photo_selection_sql, validate_photo_selection

JourneyDetail = Literal["trip", "city"]
_STOP_THRESHOLDS = {
    "trip": (1.0, timedelta(hours=2)),
    "city": (0.1, timedelta(minutes=45)),
}
JOURNEY_GAP = timedelta(hours=24)
_INFERENCE_WINDOW = timedelta(minutes=15)
_INFERENCE_RADIUS_KM = 0.1
_INFERENCE_TIME_SOURCES = ("gps_utc", "exif_original_offset")
_VALID_LOCATION = "latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180"


@dataclass(frozen=True)
class Observation:
    image_id: int
    latitude: float
    longitude: float
    wall_time: datetime
    utc_time: datetime | None
    in_area: bool = True
    verified_time: bool = False

    @property
    def order_time(self) -> datetime:
        # Return timezone-free values of the same kind for durations. The
        # caller only compares these values when both photos use the same clock.
        return self.utc_time.replace(tzinfo=None) if self.utc_time else self.wall_time

    def location(self) -> dict[str, float]:
        return {"latitude": self.latitude, "longitude": self.longitude}


@dataclass(frozen=True)
class _UnlocatedCapture:
    image_id: int
    wall_time: datetime
    utc_time: datetime | None
    eligible: bool


_TimedCapture = TypeVar("_TimedCapture", bound=Observation | _UnlocatedCapture)


def _distance_km(first: Observation, second: Observation) -> float:
    first_lat, second_lat = radians(first.latitude), radians(second.latitude)
    latitude_delta = second_lat - first_lat
    longitude_delta = radians(second.longitude - first.longitude)
    chord = sin(latitude_delta / 2) ** 2 + cos(first_lat) * cos(second_lat) * sin(longitude_delta / 2) ** 2
    return 6371.0088 * 2 * asin(min(1, sqrt(chord)))


def _ordered_observations(observations: list[_TimedCapture]) -> list[_TimedCapture]:
    wall_order = sorted(observations, key=lambda photo: (photo.wall_time, photo.image_id))
    utc_order = iter(sorted((photo for photo in observations if photo.utc_time),
                            key=lambda photo: (photo.utc_time, photo.image_id)))
    # Preserve the trustworthy UTC ordering. Unverified local times retain
    # their approximate display positions and provide no evidence for a connection.
    return [next(utc_order) if photo.utc_time else photo for photo in wall_order]


def _ambiguous_ties(observations: list[Observation], radius_km: float) -> set[int]:
    anchors: dict[datetime, Observation] = {}
    ambiguous_times: set[datetime] = set()
    for photo in observations:
        if photo.utc_time:
            anchor = anchors.setdefault(photo.utc_time, photo)
            if _distance_km(anchor, photo) > radius_km:
                ambiguous_times.add(photo.utc_time)
    return {photo.image_id for photo in observations if not photo.utc_time or photo.utc_time in ambiguous_times}


def _group_stops(
    photos: Iterable[Observation], uncertain: set[int], radius_km: float, window: timedelta,
) -> list[list[Observation]]:
    stops: list[list[Observation]] = []
    for photo in photos:
        if stops:
            anchor, previous = stops[-1][0], stops[-1][-1]
            same_clock = bool(previous.utc_time) == bool(photo.utc_time)
            same_certainty = (previous.image_id in uncertain) == (photo.image_id in uncertain)
            if (same_clock and same_certainty and timedelta(0) <= photo.order_time - previous.order_time <= window
                    and _distance_km(anchor, photo) <= radius_km):
                stops[-1].append(photo)
                continue
        stops.append([photo])
    return stops


def _visible_stops(
    photos: list[Observation], uncertain: set[int], radius_km: float, window: timedelta,
) -> Iterator[tuple[list[Observation], bool]]:
    # Group contiguous runs in the complete chronology. Removing outside
    # observations before grouping would join visits across a known detour.
    for inside, run in groupby(photos, key=lambda photo: photo.in_area):
        if inside:
            for index, stop in enumerate(_group_stops(run, uncertain, radius_km, window)):
                yield stop, index == 0


def build_journey(observations: list[Observation], detail: JourneyDetail = "trip") -> dict:
    photos = _ordered_observations(observations)
    radius_km, window = _STOP_THRESHOLDS[detail]
    uncertain = _ambiguous_ties(photos, radius_km)
    stops = []
    connections = []
    previous = None
    previous_anchor = None
    previous_uncertain = False
    section = 1
    for number, (group, starts_run) in enumerate(_visible_stops(photos, uncertain, radius_km, window), 1):
        first, last = group[0], group[-1]
        stop_uncertain = first.image_id in uncertain
        duration = None
        if previous and not starts_run and bool(previous.utc_time) == bool(first.utc_time):
            duration = first.order_time - previous.order_time
        if previous and (starts_run or (duration and duration > JOURNEY_GAP)):
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
            "inferred_image_ids": [],
            "uncertain": stop_uncertain,
        })
        if (previous_anchor and not previous_uncertain and not stop_uncertain and duration
                and timedelta(0) < duration <= JOURNEY_GAP and first.location() != previous_anchor.location()):
            connections.append({
                "from": previous_anchor.location(), "to": first.location(),
                "from_stop_id": str(previous_anchor.image_id), "to_stop_id": str(first.image_id),
            })
        previous, previous_anchor, previous_uncertain = last, first, stop_uncertain
    return {
        "stops": stops,
        "connections": connections,
        "photo_count": sum(photo.in_area for photo in photos),
        "inferred_location_count": 0,
        "uncertain_count": sum(photo.in_area and photo.image_id in uncertain for photo in photos),
    }


def _infer_stop_membership(journey: dict, observations: list[Observation], unlocated: list[_UnlocatedCapture]) -> None:
    if not any(photo.eligible for photo in unlocated):
        return
    chronology = _ordered_observations([*observations, *unlocated])
    counts = Counter(photo.utc_time for photo in chronology if photo.utc_time is not None)
    stops_by_image = {
        photo_id: stop["id"] for stop in journey["stops"] for photo_id in stop["image_ids"]
    }
    after_image: dict[int, int] = {}
    for index in range(1, len(chronology) - 1):
        photo = chronology[index]
        if not isinstance(photo, _UnlocatedCapture) or not photo.eligible or photo.utc_time is None:
            continue
        before, after = chronology[index - 1], chronology[index + 1]
        if not isinstance(before, Observation) or not isinstance(after, Observation):
            continue
        stop_id = stops_by_image.get(before.image_id)
        if not stop_id or stop_id != stops_by_image.get(after.image_id):
            continue
        if not before.verified_time or not after.verified_time or before.utc_time is None or after.utc_time is None:
            continue
        if any(counts[instant] != 1 for instant in (before.utc_time, photo.utc_time, after.utc_time)):
            continue
        if (timedelta(0) < photo.utc_time - before.utc_time <= _INFERENCE_WINDOW
                and timedelta(0) < after.utc_time - photo.utc_time <= _INFERENCE_WINDOW
                and _distance_km(before, after) <= _INFERENCE_RADIUS_KM):
            after_image[before.image_id] = photo.image_id

    if not after_image:
        return

    for stop in journey["stops"]:
        merged_ids = []
        for photo_id in stop["image_ids"]:
            merged_ids.append(photo_id)
            if photo_id in after_image:
                inferred_id = after_image[photo_id]
                merged_ids.append(inferred_id)
                stop["inferred_image_ids"].append(inferred_id)
        stop["image_ids"] = merged_ids
    journey["photo_count"] += len(after_image)
    journey["inferred_location_count"] = len(after_image)


def _read_capture_time(value: str | None) -> datetime | None:
    if not isinstance(value, str) or len(value) < 19:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _capture_clock(row) -> tuple[datetime | None, bool]:
    instant = _read_capture_time(row["captured_at_utc"])
    if instant is None or instant.tzinfo is None:
        return None, False
    try:
        verified = instant.utcoffset() == timedelta(0) and row["capture_time_source"] in _INFERENCE_TIME_SOURCES
        return instant.astimezone(timezone.utc), verified
    except (OverflowError, ValueError):
        return None, False


def list_journey(
    from_date: date | None, to_date: date | None, bounds: GeoBounds | None = None, detail: JourneyDetail = "trip",
    *, album_id: str | None = None,
) -> dict:
    scope_filter, parameters = photo_selection_sql(GeoPhotoSelection(
        from_date=from_date, to_date=to_date, album_id=album_id,
    ))
    area_filter, area_parameters = photo_selection_sql(GeoPhotoSelection(bounds=bounds))
    undated_filter, undated_parameters = photo_selection_sql(GeoPhotoSelection(bounds=bounds, album_id=album_id))

    with closing(get_conn()) as connection:
        connection.execute("BEGIN")
        missing_time = connection.execute(
            f"""SELECT COUNT(*) FROM image i
                WHERE {_VALID_LOCATION} AND {undated_filter} AND captured_at IS NULL""",
            undated_parameters,
        ).fetchone()[0]
        missing_location, can_infer = connection.execute(
            f"""SELECT COUNT(*), MAX(CASE
                    WHEN latitude IS NULL AND longitude IS NULL AND captured_at IS NOT NULL
                         AND captured_at_utc IS NOT NULL AND capture_time_source IN (?, ?)
                    THEN 1 ELSE 0 END)
                FROM image i
                WHERE {scope_filter} AND COALESCE(({_VALID_LOCATION}), 0) = 0""",
            [*_INFERENCE_TIME_SOURCES, *parameters],
        ).fetchone()
        rows = connection.execute(
            f"""SELECT id, latitude, longitude, captured_at, captured_at_utc, capture_time_source,
                       ({area_filter}) AS in_area
                FROM image i WHERE {scope_filter} AND {_VALID_LOCATION}
                AND (captured_at IS NOT NULL OR (? AND captured_at_utc IS NOT NULL))""",
            [*area_parameters, *parameters, bool(can_infer)],
        )
        observations = []
        unlocated = []
        for row in rows:
            wall = _read_capture_time(row["captured_at"])
            utc, verified = _capture_clock(row)
            if wall is None:
                missing_time += row["captured_at"] is not None and bool(row["in_area"])
                if can_infer and utc is not None:
                    # The existing route cannot display this timed observation,
                    # but it can still interrupt inference between its neighbours.
                    unlocated.append(_UnlocatedCapture(row["id"], utc.replace(tzinfo=None), utc, False))
                continue
            observations.append(Observation(
                row["id"], row["latitude"], row["longitude"], wall.replace(tzinfo=None), utc, bool(row["in_area"]),
                verified,
            ))
        if can_infer and len(observations) >= 2:
            rows = connection.execute(
                f"""SELECT id, latitude, longitude, captured_at, captured_at_utc, capture_time_source
                    FROM image i WHERE {scope_filter} AND COALESCE(({_VALID_LOCATION}), 0) = 0
                    AND (captured_at IS NOT NULL OR captured_at_utc IS NOT NULL)""",
                parameters,
            )
            for row in rows:
                wall = _read_capture_time(row["captured_at"])
                utc, verified = _capture_clock(row)
                display_time = wall or utc
                if display_time is not None:
                    eligible = wall is not None and verified and row["latitude"] is None and row["longitude"] is None
                    unlocated.append(_UnlocatedCapture(row["id"], display_time.replace(tzinfo=None), utc, eligible))
    journey = build_journey(observations, detail)
    _infer_stop_membership(journey, observations, unlocated)
    return {
        **journey,
        "missing_location_count": missing_location - journey["inferred_location_count"],
        "missing_time_count": missing_time,
        "indexing": geo_backfill.indexing,
    }


def get_journey(
    from_date: date | None = None, to_date: date | None = None,
    bounds: GeoBounds | None = None, detail: JourneyDetail = "trip", *, album_id: str | None = None,
):
    try:
        validate_photo_selection(GeoPhotoSelection(bounds=bounds, from_date=from_date, to_date=to_date, album_id=album_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return list_journey(from_date, to_date, bounds, detail, album_id=album_id)
