"""Build offline photo journeys from observed locations and capture times."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
from math import asin, cos, radians, sin, sqrt
from typing import Literal

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
_VALID_LOCATION = "latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180"


@dataclass(frozen=True)
class Observation:
    image_id: int
    latitude: float
    longitude: float
    wall_time: datetime
    utc_time: datetime | None
    in_area: bool = True

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
        "uncertain_count": sum(photo.in_area and photo.image_id in uncertain for photo in photos),
    }


def _read_capture_time(value: str | None) -> datetime | None:
    if not value or len(value) < 19:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def list_journey(
    from_date: date | None, to_date: date | None, bounds: GeoBounds | None = None, detail: JourneyDetail = "trip",
) -> dict:
    date_filter, parameters = photo_selection_sql(GeoPhotoSelection(from_date=from_date, to_date=to_date))
    area_filter, area_parameters = photo_selection_sql(GeoPhotoSelection(bounds=bounds))

    with closing(get_conn()) as connection:
        connection.execute("BEGIN")
        missing_time = connection.execute(
            f"""SELECT COUNT(*) FROM image i
                WHERE {_VALID_LOCATION} AND {area_filter} AND captured_at IS NULL""",
            area_parameters,
        ).fetchone()[0]
        missing_location = connection.execute(
            f"""SELECT COUNT(*) FROM image i
                WHERE {date_filter} AND COALESCE(({_VALID_LOCATION}), 0) = 0""",
            parameters,
        ).fetchone()[0]
        rows = connection.execute(
            f"""SELECT id, latitude, longitude, captured_at, captured_at_utc, ({area_filter}) AS in_area
                FROM image i WHERE {date_filter} AND {_VALID_LOCATION}
                AND captured_at IS NOT NULL""",
            [*area_parameters, *parameters],
        )
        observations = []
        for row in rows:
            wall = _read_capture_time(row["captured_at"])
            if wall is None:
                missing_time += bool(row["in_area"])
                continue
            instant = _read_capture_time(row["captured_at_utc"])
            utc = instant.astimezone(timezone.utc) if instant and instant.tzinfo is not None else None
            observations.append(Observation(
                row["id"], row["latitude"], row["longitude"], wall.replace(tzinfo=None), utc, bool(row["in_area"]),
            ))
    return {
        **build_journey(observations, detail),
        "missing_location_count": missing_location,
        "missing_time_count": missing_time,
        "indexing": geo_backfill.indexing,
    }


def get_journey(
    from_date: date | None = None, to_date: date | None = None,
    bounds: GeoBounds | None = None, detail: JourneyDetail = "trip",
):
    try:
        validate_photo_selection(GeoPhotoSelection(bounds=bounds, from_date=from_date, to_date=to_date))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return list_journey(from_date, to_date, bounds, detail)
