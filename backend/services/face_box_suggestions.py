"""Ephemeral detector-only hints for manually marking one photo."""

from __future__ import annotations

import os
from contextlib import contextmanager
from math import isfinite
from threading import Lock
from time import monotonic
from typing import Callable, Iterable

import numpy as np
from PIL import Image

from ..models.face_model import DETECTION_SIZE, SUGGESTION_SCORE_THRESHOLD, FaceDetectionBusy
from .face_geometry import face_boxes_overlap
from .filesystem_paths import filesystem_path
from .pipeline import _file_identity, _matches_imported_identity, try_face_processing_slot
from .preview_cache import (
    FACE_PREVIEW_MAX_SOURCE_BYTES,
    _verified_preview_source,
    bounded_upright_face_preview,
    correct_bbox_for_orientation,
)

_MAX_PHOTO_BYTES = FACE_PREVIEW_MAX_SOURCE_BYTES
_MAX_SUGGESTIONS = 8
_SOURCE_DEADLINE = 7.0
_SUGGESTION_GATE = Lock()

FaceBox = tuple[float, float, float, float]


class FaceBoxSuggestionBusy(RuntimeError):
    """The detector is busy with another photo or background work."""


class FaceBoxSuggestionUnavailable(RuntimeError):
    """Detector-only suggestions could not run on this device."""


class FaceBoxSuggestionPhotoUnavailable(LookupError):
    """The registered photo could not be read for suggestions."""


@contextmanager
def _photo_source(path: str, content_hash: str | None):
    if content_hash:
        with _verified_preview_source(path, content_hash, maximum_bytes=_MAX_PHOTO_BYTES) as source:
            yield source
        return
    # Legacy rows have no content hash. Use a single stable open handle and
    # reject a source which changes or is replaced while being decoded.
    native_path = filesystem_path(path)
    with open(native_path, "rb") as source:
        stat = os.fstat(source.fileno())
        identity = _file_identity(stat)
        if stat.st_size > _MAX_PHOTO_BYTES:
            raise OSError("Die Originaldatei ist für diese Analyse zu groß.")
        yield source
        if _file_identity(os.fstat(source.fileno())) != identity or not _matches_imported_identity(native_path, identity):
            raise OSError("Die Originaldatei wurde während der Prüfung geändert.")


def _read_photo(path, content_hash):
    with _photo_source(path, content_hash) as source, bounded_upright_face_preview(source) as (image, geometry):
        return np.array(image), geometry


def _clip_box(x1, y1, x2, y2, width, height) -> FaceBox | None:
    if not all(isfinite(value) for value in (x1, y1, x2, y2)) or x2 <= x1 or y2 <= y1:
        return None
    left, right = max(0., x1), min(float(width), x2)
    top, bottom = max(0., y1), min(float(height), y2)
    return (left, top, right - left, bottom - top) if right > left and bottom > top else None


def _raw_exclusions(boxes, geometry) -> list[FaceBox]:
    orientation, width, height = geometry
    if orientation >= 5:
        width, height = height, width
    excluded = []
    for box in boxes:
        try:
            x, y, w, h = correct_bbox_for_orientation(geometry, *map(float, box))
        except (TypeError, ValueError):
            continue
        clipped = _clip_box(x, y, x + w, y + h, width, height)
        if clipped:
            left, top, visible_width, visible_height = clipped
            excluded.append((left / width, top / height, visible_width / width, visible_height / height))
    return excluded


def _choose_boxes(detections, image_size, exclusions):
    width, height = image_size
    minimum_side = max(12, 16 * max(image_size) / max(DETECTION_SIZE))
    accepted = []
    ranked = detections[np.isfinite(detections[:, 4]) & (detections[:, 4] >= SUGGESTION_SCORE_THRESHOLD)]
    for row in ranked[np.argsort(-ranked[:, 4], kind="stable")]:
        x1, y1, x2, y2, _ = map(float, row)
        clipped = _clip_box(x1, y1, x2, y2, width, height)
        if not clipped:
            continue
        x, y, box_width, box_height = clipped
        if min(box_width, box_height) < minimum_side or not .43 <= box_width / box_height <= 2.2:
            continue
        if box_width * box_height < .65 * (x2 - x1) * (y2 - y1):
            continue
        normalized = (x / width, y / height, box_width / width, box_height / height)
        if any(face_boxes_overlap(normalized, box) for box in exclusions):
            continue
        exclusions.append(normalized)
        accepted.append(dict(zip(("x", "y", "width", "height"), normalized)))
        if len(accepted) == _MAX_SUGGESTIONS:
            break
    return accepted


def suggest_face_boxes(
    paths: Iterable[str | tuple[str, str]], content_hash: str | None,
    existing_raw_boxes: Iterable[FaceBox], get_model: Callable,
) -> list[dict[str, float]]:
    """Return normalized upright rectangles. Sources and model state stay read-only."""
    if os.environ.get("FACE_MANAGER_PLATFORM") == "android":
        raise FaceBoxSuggestionUnavailable("Automatische Markierungsvorschläge sind unter Android nicht verfügbar.")
    if not _SUGGESTION_GATE.acquire(blocking=False):
        raise FaceBoxSuggestionBusy("Die Gesichtssuche ist gerade ausgelastet. Bitte versuche es erneut.")
    deadline = monotonic() + _SOURCE_DEADLINE
    try:
        for candidate in paths:
            path, expected_hash = candidate if isinstance(candidate, tuple) else (candidate, content_hash)
            if monotonic() >= deadline:
                raise FaceBoxSuggestionBusy("Die Fotoquelle antwortet zu langsam. Bitte versuche es erneut.")
            try:
                pixels, geometry = _read_photo(path, expected_hash)
                break
            except (OSError, ValueError, Image.DecompressionBombError):
                continue
        else:
            raise FaceBoxSuggestionPhotoUnavailable("Das verknüpfte Originalfoto konnte nicht gelesen werden.")
        if monotonic() >= deadline:
            raise FaceBoxSuggestionBusy("Die Fotoquelle antwortet zu langsam. Bitte versuche es erneut.")
        with try_face_processing_slot() as acquired:
            if not acquired:
                raise FaceBoxSuggestionBusy("Die Gesichtserkennung verarbeitet gerade andere Fotos. Bitte versuche es erneut.")
            try:
                model = get_model()
                if monotonic() >= deadline:
                    raise FaceDetectionBusy()
                detections = np.asarray(model.suggest_face_detections(pixels), dtype=float)
                if detections.size == 0:
                    return []
                if detections.ndim != 2 or detections.shape[1] != 5:
                    raise ValueError("Unexpected face detector output")
            except FaceDetectionBusy as exc:
                raise FaceBoxSuggestionBusy("Die Gesichtserkennung ist gerade ausgelastet. Bitte versuche es erneut.") from exc
            except Exception as exc:
                raise FaceBoxSuggestionUnavailable("Die Gesichtssuche konnte nicht ausgeführt werden.") from exc
        height, width = pixels.shape[:2]
        return _choose_boxes(detections, (width, height), _raw_exclusions(existing_raw_boxes, geometry))
    finally:
        _SUGGESTION_GATE.release()
