"""Map routes for photo aggregates, journeys, and detail tiles."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Path as ApiPath, Query
from fastapi.responses import Response

from ..services.geo import MAP_GRID_SIZE, list_map_images, list_map_points, list_map_years, list_selected_map_images
from ..services.geo_query import GeoBounds, GeoPhotoSelection
from ..services.journey import JourneyDetail, get_journey
from ..services.map_detail_tiles import MapDetailBusy, MapDetailSource, MapDetailUnavailable, fetch_map_detail_tile
from ..services.observed_places import list_observed_places
from . import library
from .photo_selection import _GeoSelectionBody, optional_map_bounds, validated_photo_selection

router = APIRouter(prefix="/api/map")


def _validate_map_bounds(west, east, south, north, from_date, to_date, album_id=None):
    validated_photo_selection(GeoPhotoSelection(GeoBounds(west, east, south, north), from_date, to_date, album_id=album_id))


def _validate_map_cursor(offset, before_id, before_captured_at):
    if before_id is None and before_captured_at is not None:
        raise HTTPException(status_code=422, detail="before_captured_at requires before_id")
    if before_id is not None and offset:
        raise HTTPException(status_code=422, detail="offset must be zero with before_id")


@router.get("/years")
def get_map_years(album_id: str | None = None):
    generation = library.get_generation()
    validated_photo_selection(GeoPhotoSelection(album_id=album_id))
    result = {"years": list_map_years(album_id)}
    library.get_generation(generation)
    return result


@router.get("/points")
def get_map_points(
    west: float = Query(default=-180, ge=-180, le=180),
    east: float = Query(default=180, ge=-180, le=180),
    south: float = Query(default=-90, ge=-90, le=90),
    north: float = Query(default=90, ge=-90, le=90),
    from_date: date | None = None,
    to_date: date | None = None,
    zoom: int = Query(default=2, ge=0, le=20),
    grid_columns: int = Query(default=MAP_GRID_SIZE, ge=1, le=MAP_GRID_SIZE),
    grid_rows: int = Query(default=MAP_GRID_SIZE, ge=1, le=MAP_GRID_SIZE),
    album_id: str | None = None,
):
    """Return bounded photo aggregates for a map viewport."""
    generation = library.get_generation()
    _validate_map_bounds(west, east, south, north, from_date, to_date, album_id)
    result = list_map_points(west, east, south, north, from_date, to_date, zoom, grid_columns, grid_rows, album_id=album_id)
    library.get_generation(generation)
    return result


@router.post("/points/selection")
def get_selected_map_points(
    selection: Annotated[_GeoSelectionBody, Body(embed=True)],
    west: Annotated[float, Query(ge=-180, le=180)] = -180,
    east: Annotated[float, Query(ge=-180, le=180)] = 180,
    south: Annotated[float, Query(ge=-90, le=90)] = -90,
    north: Annotated[float, Query(ge=-90, le=90)] = 90,
    grid_columns: Annotated[int, Query(ge=1, le=MAP_GRID_SIZE)] = MAP_GRID_SIZE,
    grid_rows: Annotated[int, Query(ge=1, le=MAP_GRID_SIZE)] = MAP_GRID_SIZE,
):
    """Aggregate selected photos on the grid of the visible viewport."""
    generation = library.get_generation()
    _validate_map_bounds(west, east, south, north, None, None)
    result = list_map_points(
        west, east, south, north, grid_columns=grid_columns, grid_rows=grid_rows,
        selection=selection.to_selection(),
    )
    library.get_generation(generation)
    return result


@router.get("/detail-tiles/{source}/{z}/{x}/{y}")
def get_map_detail_tile(
    source: MapDetailSource,
    z: Annotated[int, ApiPath(ge=0, le=14)],
    x: Annotated[int, ApiPath(ge=0, le=32767)],
    y: Annotated[int, ApiPath(ge=0, le=16383)],
):
    """Relay one requested detail tile without sending library data."""
    try:
        tile = fetch_map_detail_tile(source, z, x, y)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MapDetailBusy as exc:
        raise HTTPException(status_code=503, detail="Kartendetails sind vorübergehend ausgelastet.") from exc
    except MapDetailUnavailable as exc:
        raise HTTPException(status_code=502, detail="Diese Kartenkachel ist gerade nicht verfügbar.") from exc
    return Response(content=tile.data, media_type=tile.media_type, headers={
        "Cache-Control": tile.cache_control, "X-Content-Type-Options": "nosniff",
    })


@router.get("/images")
def get_map_images(
    west: float = Query(default=-180, ge=-180, le=180),
    east: float = Query(default=180, ge=-180, le=180),
    south: float = Query(default=-90, ge=-90, le=90),
    north: float = Query(default=90, ge=-90, le=90),
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(default=40, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    before_id: int | None = Query(default=None, ge=1, le=2**63 - 1),
    before_captured_at: str | None = Query(default=None, min_length=1, max_length=64),
    album_id: str | None = None,
):
    """Return a stable page of geotagged photos in a viewport."""
    generation = library.get_generation()
    _validate_map_bounds(west, east, south, north, from_date, to_date, album_id)
    _validate_map_cursor(offset, before_id, before_captured_at)
    page = list_map_images(
        west, east, south, north, from_date, to_date, limit, offset,
        before_id=before_id, before_captured_at=before_captured_at, album_id=album_id,
    )
    library.get_generation(generation)
    return page


@router.post("/images/selection")
def get_selected_map_images(
    selection: Annotated[_GeoSelectionBody, Body(embed=True)],
    limit: Annotated[int, Query(ge=1, le=200)] = 40,
    offset: Annotated[int, Query(ge=0)] = 0,
    before_id: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    before_captured_at: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
):
    """Return one map photo page for an explicit selection."""
    generation = library.get_generation()
    _validate_map_cursor(offset, before_id, before_captured_at)
    page = list_selected_map_images(selection.to_selection(), limit, offset, before_id, before_captured_at)
    library.get_generation(generation)
    return page


@router.get("/journey")
def get_map_journey(
    from_date: date | None = None,
    to_date: date | None = None,
    west: Annotated[float | None, Query(ge=-180, le=180)] = None,
    east: Annotated[float | None, Query(ge=-180, le=180)] = None,
    south: Annotated[float | None, Query(ge=-90, le=90)] = None,
    north: Annotated[float | None, Query(ge=-90, le=90)] = None,
    detail: JourneyDetail = "trip",
    album_id: str | None = None,
):
    """Return observed journey groups for the specified capture period."""
    generation = library.get_generation()
    result = get_journey(from_date, to_date, optional_map_bounds(west, east, south, north), detail, album_id=album_id)
    library.get_generation(generation)
    return result


@router.get("/observed-places")
def get_observed_places(
    from_date: date | None = None,
    to_date: date | None = None,
    album_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=300)] = 150,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    generation = library.get_generation()
    selection = validated_photo_selection(GeoPhotoSelection(
        from_date=from_date, to_date=to_date, album_id=album_id,
    ))
    result = list_observed_places(selection, limit, offset)
    library.get_generation(generation)
    return result
