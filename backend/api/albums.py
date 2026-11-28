"""Album routes and request models."""

from contextlib import nullcontext
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..services import albums
from ..services.geo_query import GeoPhotoSelection
from ..services.journey import JourneyDetail
from . import library
from .photo_selection import _GeoSelectionBody, validated_photo_selection

router = APIRouter(prefix="/api/albums")


class _AlbumJourneyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_date: date | None = None
    to_date: date | None = None
    detail: JourneyDetail = "trip"
    title: Annotated[str, Field(max_length=albums.MAX_ALBUM_NAME)] | None = None
    arrival_included: bool | None = None
    departure_included: bool | None = None

    def metadata(self) -> dict:
        validated_photo_selection(GeoPhotoSelection(from_date=self.from_date, to_date=self.to_date))
        result = self.model_dump(mode="json", exclude_none=True)
        if self.title:
            result["title"] = self.title.strip()
        return result


class _AlbumDraftOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _AlbumDraftDimensions(_AlbumDraftOptions):
    width: Annotated[int, Field(ge=0, le=99_999)]
    height: Annotated[int, Field(ge=0, le=99_999)]


_AlbumColor = Annotated[str, Field(max_length=7, pattern=r"^(?:#?[0-9a-fA-F]{3}|#?[0-9a-fA-F]{6})?$")]


class _AlbumDraftColors(_AlbumDraftOptions):
    paper: _AlbumColor | None = None
    accent: _AlbumColor | None = None
    land: _AlbumColor | None = None
    water: _AlbumColor | None = None


class _AlbumDraftFormatting(_AlbumDraftOptions):
    typeface: Literal["serif", "sans"] | None = None
    alignment: Literal["left", "center"] | None = None
    titleScale: Annotated[int, Field(ge=75, le=130)] | None = None
    margins: Literal["narrow", "normal", "wide"] | None = None
    listColumns: Literal[1, 2] | None = None


class _AlbumDraftFraming(_AlbumDraftOptions):
    zoom: Annotated[int, Field(ge=50, le=400)]
    horizontal: Annotated[int, Field(ge=-200, le=200)]
    vertical: Annotated[int, Field(ge=-200, le=200)]


class _AlbumDraftViewport(_AlbumDraftOptions):
    west: Annotated[float, Field(ge=-180, le=180)]
    east: Annotated[float, Field(ge=-180, le=180)]
    south: Annotated[float, Field(ge=-90, le=90)]
    north: Annotated[float, Field(ge=-90, le=90)]


class _AlbumLayoutDraftBody(_AlbumDraftOptions):
    version: Literal[1]
    title: Annotated[str, Field(max_length=72)] | None = None
    subtitle: Annotated[str, Field(max_length=100)] | None = None
    format: Literal["a4", "portrait", "square", "square-210", "square-300", "custom"] | None = None
    dimensions: _AlbumDraftDimensions | None = None
    dpi: Literal[150, 240, 300, 400] | None = None
    palette: Literal["sand", "coast"] | None = None
    accent: Literal["automatic", "rust", "ocean", "pine", "plum"] | None = None
    colors: _AlbumDraftColors | None = None
    formatting: _AlbumDraftFormatting | None = None
    contextPercent: Annotated[int, Field(ge=0, le=100)] | None = None
    markerScale: Annotated[int, Field(ge=70, le=140)] | None = None
    useViewport: bool | None = None
    viewportSnapshot: _AlbumDraftViewport | None = None
    transports: dict[
        Annotated[str, Field(pattern=r"^[0-9]{1,19}:[0-9]{1,19}$")],
        Literal["car", "train", "plane", "walk", "bike", "boat"],
    ] | None = None
    style: Literal["standard", "landscape", "satellite"] | None = None
    showNames: bool | None = None
    showPeriod: bool | None = None
    showStopsList: bool | None = None
    fullBleed: bool | None = None
    framing: _AlbumDraftFraming | None = None
    markerStyle: Literal["standard", "dots", "none"] | None = None
    showPlaceNames: bool | None = None
    showRoute: bool | None = None
    placeNames: dict[
        Annotated[str, Field(pattern=r"^[0-9]{1,19}$")],
        Annotated[str, Field(max_length=60)],
    ] | None = None


_AlbumImageId = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]


class _AlbumPhotoSelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_ids: list[_AlbumImageId] | None = None
    selection: _GeoSelectionBody | None = None

    def photo_kwargs(self):
        return {
            "image_ids": self.image_ids,
            "selection": self.selection.to_selection() if self.selection is not None else None,
        }


class _AlbumCreateBody(_AlbumPhotoSelectionBody):
    name: str
    journey: _AlbumJourneyBody | None = None
    layout_draft: _AlbumLayoutDraftBody | None = None


class _AlbumUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    cover_image_id: _AlbumImageId | None = None
    journey: _AlbumJourneyBody | None = None
    layout_draft: _AlbumLayoutDraftBody | None = None


class _AlbumRemovePhotosBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_ids: list[_AlbumImageId]


def _album_call(operation, *args, write=False, **kwargs):
    generation = library.get_generation()
    # The database restore takes the same operation lock before it replaces
    # the library. A write cannot commit into a newly imported database.
    with (library.write_lock if write else nullcontext()):
        library.get_generation(generation)
        try:
            result = operation(*args, **kwargs)
        except albums.AlbumPhotosMissingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        library.get_generation(generation)
        if write:
            library.publish_active_cluster_change("albums_changed")
        return result


def _validate_album_id(album_id: str) -> None:
    validated_photo_selection(GeoPhotoSelection(album_id=album_id))


@router.get("")
def get_albums():
    return {"albums": _album_call(albums.list_albums)}


@router.get("/{album_id}")
def get_album(album_id: str):
    _validate_album_id(album_id)
    return _album_call(albums.get_album, album_id)


@router.post("", status_code=201)
def create_album(data: _AlbumCreateBody):
    return _album_call(
        albums.create_album, data.name, write=True, **data.photo_kwargs(),
        journey=data.journey.metadata() if data.journey is not None else None,
        layout_draft=data.layout_draft.model_dump(exclude_none=True) if data.layout_draft is not None else None,
    )


@router.patch("/{album_id}")
def update_album(album_id: str, data: _AlbumUpdateBody):
    _validate_album_id(album_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and data.name is None:
        raise HTTPException(status_code=422, detail="Gib einen Albumname an.")
    if data.journey is not None:
        changes["journey"] = data.journey.metadata()
    if data.layout_draft is not None:
        changes["layout_draft"] = data.layout_draft.model_dump(exclude_none=True)
    return _album_call(albums.update_album, album_id, changes, write=True)


@router.delete("/{album_id}")
def delete_album(album_id: str):
    _validate_album_id(album_id)
    _album_call(albums.delete_album, album_id, write=True)
    return {"status": "deleted"}


@router.post("/{album_id}/photos")
def add_album_photos(album_id: str, data: _AlbumPhotoSelectionBody):
    _validate_album_id(album_id)
    return _album_call(albums.add_album_photos, album_id, write=True, **data.photo_kwargs())


@router.delete("/{album_id}/photos")
def remove_album_photos(album_id: str, data: _AlbumRemovePhotosBody):
    _validate_album_id(album_id)
    return _album_call(albums.remove_album_photos, album_id, data.image_ids, write=True)
