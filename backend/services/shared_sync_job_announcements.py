"""Durable, bounded discovery of shared work, separate from library event streams."""

from __future__ import annotations

import hashlib
import json
from pathlib import PureWindowsPath
import re

from .shared_sync_jobs import validate_job_descriptor
from .shared_sync_transport import InvalidTransportData, ShareTransport

_QUEUE = ("analysis-queue",)
_KIND = "analysis-announcement"
_ID = re.compile(r"[0-9a-f]{64}")
_SHARD = re.compile(r"[0-9a-f]{2}")
_RECORD = re.compile(r"([0-9a-f]{64})\.json")
MAX_ANNOUNCEMENT_PAGE = 64


def portable_job_path(value: str) -> str:
    """Validate a share-relative photo name without contacting the share."""
    if not isinstance(value, str) or not value or len(value) > 4096 or "\\" in value:
        raise InvalidTransportData("Invalid shared analysis source name")
    for part in value.split("/"):
        if (
            part in ("", ".", "..") or part.casefold() == ".fm"
            or PureWindowsPath(part).is_reserved() or part.endswith((".", " "))
            or any(char in '<>:"|?*' or ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in part)
        ):
            raise InvalidTransportData("Invalid shared analysis source name")
    return value


def make_job_announcement(manifest: dict, job: dict, relative_path: str) -> dict:
    job = validate_job_descriptor(manifest, job)
    relative_path = portable_job_path(relative_path)
    identity = json.dumps(
        [job["job_id"], relative_path], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return {
        "announcement_id": hashlib.sha256(b"face-manager-analysis-announcement\0" + identity).hexdigest(),
        "job": job,
        "relative_path": relative_path,
    }


def validate_job_announcement(manifest: dict, announcement: dict) -> dict:
    if not isinstance(announcement, dict) or announcement.keys() != {"announcement_id", "job", "relative_path"}:
        raise InvalidTransportData("Invalid shared analysis announcement")
    expected = make_job_announcement(manifest, announcement["job"], announcement["relative_path"])
    if announcement != expected:
        raise InvalidTransportData("Shared analysis announcement does not match its ID")
    return expected


def publish_job_announcement(transport: ShareTransport, job: dict, relative_path: str) -> dict:
    """Publish a verified photo/version with a portable source hint."""
    announcement = make_job_announcement(transport.open(), job, relative_path)
    identity = announcement["announcement_id"]
    transport._save_record((*_QUEUE, identity[:2]), f"{identity}.json", _KIND, announcement)
    return announcement


def list_job_announcements(
    transport: ShareTransport, *, after: str | None = None, limit: int = 16
) -> dict:
    """Read one stable-ID page, resuming a fair sweep after the supplied ID."""
    if type(limit) is not int or not 1 <= limit <= MAX_ANNOUNCEMENT_PAGE:
        raise InvalidTransportData("Invalid shared analysis work page size")
    if after is not None and (not isinstance(after, str) or _ID.fullmatch(after) is None):
        raise InvalidTransportData("Invalid shared analysis work cursor")
    manifest = transport.open()
    announcements = []
    shards = sorted(name for name in transport._directory_names(_QUEUE) if _SHARD.fullmatch(name))
    for shard in shards:
        if after is not None and shard < after[:2]:
            continue
        parts = (*_QUEUE, shard)
        for name in sorted(transport._directory_names(parts)):
            record = _RECORD.fullmatch(name)
            if record is None:
                continue
            identity = record[1]
            if identity[:2] != shard:
                raise InvalidTransportData("Shared analysis announcement is in the wrong shard")
            if after is not None and identity <= after:
                continue
            if len(announcements) == limit:
                return {"announcements": announcements, "after": announcements[-1]["announcement_id"], "at_end": False}
            item = transport._load_record(parts, name, _KIND)
            if item is None:
                continue
            announcement = validate_job_announcement(manifest, item[0])
            if announcement["announcement_id"] != identity:
                raise InvalidTransportData("Shared analysis announcement is in the wrong slot")
            announcements.append(announcement)
    last = announcements[-1]["announcement_id"] if announcements else after
    return {"announcements": announcements, "after": last, "at_end": True}
