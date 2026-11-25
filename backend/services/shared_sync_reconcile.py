"""Validate a portable photo snapshot and plan its first-library reconciliation."""

from __future__ import annotations

import re
from dataclasses import asdict, replace
from uuid import UUID

from .shared_sync_merge import Assignment, FaceEvidence, MergePlan, plan_merge
from .shared_sync_snapshot import SnapshotPhoto
from .shared_sync_worker import portable_relative_path


_SHA256 = re.compile(r"[a-f0-9]{64}")
_MAX_WIRE_FACES = 512


def photo_event(photo: SnapshotPhoto, device_name: str) -> dict:
    """Prepare a versioned snapshot without local IDs, paths or embeddings."""
    if len(photo.faces) > _MAX_WIRE_FACES or not 0 < len(device_name) <= 255:
        raise ValueError("Shared snapshot is outside the supported size")
    faces = []
    for face in photo.faces:
        decision = asdict(face.decision) if face.decision is not None else None
        _assignment(decision)
        faces.append({
            "face_id": face.face_id,
            "bbox": list(face.bbox) if face.bbox is not None else None,
            "decision": decision,
        })
    return {
        "type": "photo_snapshot", "version": 1, "device_name": device_name,
        "photo_sha256": photo.photo_sha256, "relative_paths": list(photo.relative_paths),
        "raw_width": photo.raw_width, "raw_height": photo.raw_height, "exif_orientation": photo.exif_orientation,
        "faces": faces,
    }


def _canonical_uuid(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Invalid shared identity")
    return value


def _assignment(value) -> Assignment | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "kind", "person_key", "person_name", "archive_status", "provenance"
    }:
        raise ValueError("Invalid shared assignment")
    if value["person_key"] is not None:
        _canonical_uuid(value["person_key"])
    name = value["person_name"]
    if name is not None and (not isinstance(name, str) or not 0 < len(name) <= 256):
        raise ValueError("Invalid shared person name")
    if value["provenance"] not in ("legacy", "confirmed"):
        raise ValueError("Invalid shared decision provenance")
    return Assignment(**value)


def matching_source_path(local: SnapshotPhoto, remote: dict) -> str | None:
    paths = remote.get("relative_paths")
    if not isinstance(paths, list) or not 1 <= len(paths) <= 256:
        return None
    try:
        shared = {"/".join(portable_relative_path(path)) for path in paths}
    except ValueError:
        return None
    return next((path for path in local.relative_paths if path in shared), None)


def compare_verified_photo(local: SnapshotPhoto, remote: dict, *, verified_hash: str) -> MergePlan:
    """Compare only after a separate filesystem worker verifies the current bytes."""
    digest = remote.get("photo_sha256")
    if (
        remote.get("type") != "photo_snapshot" or type(remote.get("version")) is not int or remote["version"] != 1
        or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
        or local.photo_sha256 != digest or verified_hash != digest or matching_source_path(local, remote) is None
    ):
        raise ValueError("Local and shared snapshots do not identify the verified source revision")
    if not isinstance(remote.get("device_name"), str) or not 0 < len(remote["device_name"]) <= 255:
        raise ValueError("Invalid shared device name")
    values = remote.get("faces")
    if not isinstance(values, list) or len(values) > _MAX_WIRE_FACES:
        raise ValueError("Shared photo exceeds the supported face limit")
    geometry_matches = (
        type(remote.get("raw_width")) is int and type(remote.get("raw_height")) is int
        and type(remote.get("exif_orientation")) is int
        and local.raw_width == remote["raw_width"] and local.raw_height == remote["raw_height"]
        and local.exif_orientation == remote["exif_orientation"]
    )
    remote_faces = []
    for face in values:
        if not isinstance(face, dict) or set(face) != {"face_id", "bbox", "decision"}:
            raise ValueError("Invalid shared face evidence")
        box = face["bbox"]
        remote_faces.append(FaceEvidence(
            face_id=_canonical_uuid(face["face_id"]),
            photo_sha256=digest,
            bbox=tuple(box) if isinstance(box, list) else None,
            decision=_assignment(face["decision"]),
            photo_verified=True,
            geometry_reliable=geometry_matches,
        ))
    return plan_merge(
        [replace(face, photo_verified=True) for face in local.faces],
        remote_faces,
    )
