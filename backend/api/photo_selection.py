"""Shared request validation for map, gallery, and album photo selections."""

from datetime import date
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..services.geo_query import GeoBounds, GeoPhotoSelection, validate_photo_selection


def validated_photo_selection(selection: GeoPhotoSelection) -> GeoPhotoSelection:
    try:
        validate_photo_selection(selection)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return selection


class _GeoBoundsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    west: float
    east: float
    south: float
    north: float


class _GeoSelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bounds: _GeoBoundsBody | None = None
    from_date: date | None = None
    to_date: date | None = None
    image_ids: list[Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]] | None = None
    album_id: str | None = None

    def to_selection(self) -> GeoPhotoSelection:
        return validated_photo_selection(GeoPhotoSelection(
            bounds=GeoBounds(**self.bounds.model_dump()) if self.bounds else None,
            from_date=self.from_date,
            to_date=self.to_date,
            image_ids=tuple(dict.fromkeys(self.image_ids)) if self.image_ids is not None else None,
            album_id=self.album_id,
        ))


def optional_map_bounds(west, east, south, north) -> GeoBounds | None:
    if all(value is None for value in (west, east, south, north)):
        return None
    if west is None or east is None or south is None or north is None:
        raise HTTPException(status_code=422, detail="Der Kartenausschnitt benötigt vier Grenzen.")
    return GeoBounds(west, east, south, north)
