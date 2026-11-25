"""Filesystem work for the shared library, isolated from the desktop process."""

from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import json
import math
import os
import re
import sqlite3
import stat
import sys
from contextlib import ExitStack, closing, contextmanager
from dataclasses import asdict
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import SpooledTemporaryFile

from .filesystem_paths import filesystem_path
from .shared_sync_jobs import SharedJobScheduler
from .shared_sync_outbox import DirtyImage, revisions_match
from .shared_sync_transport import (
    HeartbeatObservation,
    InvalidTransportData,
    PublicationDeferred,
    PublishConflictError,
    ReadOnlyTransportError,
    ShareTransport,
    TransportError,
    UnknownPublishError,
    _windows_handle,
)


MAX_REQUEST_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_EVENT_PRODUCERS = 256
MAX_SOURCE_CHECKS = 8
_HASH_CHUNK = 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_JOB_READ_OPERATIONS = {"inspect", "get_result", "observe"}
_JOB_WRITE_OPERATIONS = {"claim", "heartbeat", "expire", "stage_result", "finish_result"}


def _json_digest(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def portable_relative_path(value: str) -> tuple[str, ...]:
    """Accept an unambiguous relative name common to Windows and Linux."""
    if not isinstance(value, str) or not value or len(value) > 4096 or "\\" in value:
        raise ValueError("Invalid relative source path")
    parts = value.split("/")
    for part in parts:
        if (
            part in ("", ".", "..") or part.casefold() == ".fm"
            or PureWindowsPath(part).is_reserved() or part.endswith((".", " "))
            or any(char in '<>:"|?*' or ord(char) < 32 for char in part)
        ):
            raise ValueError("Invalid relative source path")
    return PurePosixPath(value).parts


def _identity(information: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        information.st_dev, information.st_ino, information.st_size,
        information.st_mtime_ns, information.st_ctime_ns,
    )


@contextmanager
def _open_source(root: str, parts: tuple[str, ...]):
    path = Path(root)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("The local source root must be absolute")
    directories = (*path.parts[1:], *parts[:-1])
    path = Path(path.anchor)
    name = parts[-1]
    with ExitStack() as stack:
        if os.name == "nt":
            import msvcrt

            for component in (None, *directories):
                if component is not None:
                    path /= component
                handle, close = _windows_handle(path, directory=True)
                stack.callback(close, handle)
            path /= name
            handle, close = _windows_handle(path, directory=False)
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except OSError:
                close(handle)
                raise

            def stat_entry():
                return os.lstat(filesystem_path(path))
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open(path, flags)
            stack.callback(os.close, directory)
            for component in directories:
                directory = os.open(component, flags, dir_fd=directory)
                stack.callback(os.close, directory)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)

            def stat_entry():
                return os.stat(name, dir_fd=directory, follow_symlinks=False)

        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError("Source contains a redirect or an unsupported file type")
            yield source, info, stat_entry


def _verify_source(request: dict, output=None) -> dict:
    parts = portable_relative_path(request["relative_path"])
    digest = hashlib.sha256()
    size = 0
    with _open_source(request["source_root"], parts) as (source, initial, stat_entry):
        if initial.st_size > MAX_SOURCE_BYTES:
            raise ValueError("Source file exceeds the shared processing size limit")
        initial_identity = _identity(initial)
        if _identity(stat_entry()) != initial_identity:
            return {"status": "source_changed"}
        while block := source.read(_HASH_CHUNK):
            size += len(block)
            if size > MAX_SOURCE_BYTES:
                raise ValueError("Source file exceeds the shared processing size limit")
            digest.update(block)
            if output is not None:
                output.write(block)
        if (
            size != initial.st_size
            or _identity(os.fstat(source.fileno())) != initial_identity
            or _identity(stat_entry()) != initial_identity
        ):
            return {"status": "source_changed"}
    content_hash = digest.hexdigest()
    expected_hash = request.get("expected_hash")
    if expected_hash is not None and expected_hash != content_hash:
        return {"status": "source_changed"}
    return {"status": "verified", "photo_sha256": content_hash, "identity": list(initial_identity)}


def _verify_sources(request: dict) -> dict:
    sources = request["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCE_CHECKS:
        raise ValueError("Invalid source verification batch size")
    verified = []
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"relative_path", "expected_hash"}:
            raise ValueError("Invalid source verification entry")
        try:
            result = _verify_source({"source_root": request["source_root"], **source})
        except (OSError, ValueError):
            result = {"status": "unavailable"}
        verified.append(result)
    return {"status": "verified_sources", "results": verified}


def _conflict_preview(request: dict) -> dict:
    """Generate bounded previews from the exact verified bytes and raw boxes."""
    from PIL import Image

    from .preview_cache import get_image_geometry, orient_image

    def jpeg(image, size):
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
        with BytesIO() as output:
            image.save(output, format="JPEG", quality=77, optimize=True)
            if output.tell() > 256 * 1024:
                raise ValueError("Conflict preview exceeds its size limit")
            return f"data:image/jpeg;base64,{base64.b64encode(output.getvalue()).decode('ascii')}"

    with SpooledTemporaryFile(max_size=4 * 1024 * 1024) as bytes_read:
        result = _verify_source(request, output=bytes_read)
        if result["status"] != "verified":
            return result
        bytes_read.seek(0)
        with Image.open(bytes_read) as decoded:
            geometry = get_image_geometry(decoded)
            if list(geometry) != request["geometry"] or decoded.width * decoded.height > 64_000_000:
                return {"status": "source_changed"}
            original = decoded.convert("RGB")
    orientation, width, height = geometry
    fields = {"photo": jpeg(orient_image(original, orientation).copy(), 1024)}
    for side in ("local", "shared"):
        box = request["boxes"].get(side)
        if box is None:
            continue
        if not isinstance(box, list) or len(box) != 4 or any(type(value) not in (int, float) for value in box):
            raise ValueError("Invalid conflict preview region")
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError("Invalid conflict preview region")
        crop = original.crop((math.floor(x1 * width), math.floor(y1 * height), math.ceil(x2 * width), math.ceil(y2 * height)))
        fields[side] = jpeg(orient_image(crop, orientation), 200)
    return {"status": "ready", "photo_sha256": result["photo_sha256"], "images": fields}


@contextmanager
def _staging_directory(root: Path):
    """Anchor operations inside the caller's private IPC input directory."""
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("The worker input directory must be absolute")
    path = Path(root.anchor)
    with ExitStack() as stack:
        if os.name == "nt":
            for component in (None, *root.parts[1:]):
                if component is not None:
                    path /= component
                handle, close = _windows_handle(path, directory=True)
                stack.callback(close, handle)
            yield None
            return
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(path, flags)
        stack.callback(os.close, directory)
        for component in root.parts[1:]:
            directory = os.open(component, flags, dir_fd=directory)
            stack.callback(os.close, directory)
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("The worker input directory must be private")
        yield directory


def _stage_source(request: dict, staging_root: Path | None) -> dict:
    expected_hash = request.get("expected_hash")
    if not isinstance(expected_hash, str) or _SHA256.fullmatch(expected_hash) is None:
        raise ValueError("Source staging requires a verified SHA-256 identifier")
    output_path = Path(request["output_path"])
    if staging_root is None or output_path.parent != staging_root or not output_path.is_absolute():
        raise ValueError("The staged file must be inside the private worker input directory")
    portable_relative_path(output_path.name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with _staging_directory(staging_root) as directory:
        target = filesystem_path(output_path) if directory is None else output_path.name
        fd = os.open(target, flags, 0o600, dir_fd=directory)
        completed = False
        try:
            with os.fdopen(fd, "wb") as output:
                result = _verify_source(request, output)
                if result["status"] == "verified":
                    output.flush()
                    os.fsync(output.fileno())
            if result["status"] == "verified":
                completed = True
                return {**result, "output_path": str(output_path)}
            return result
        finally:
            if not completed:
                try:
                    os.unlink(target, dir_fd=directory)
                except FileNotFoundError:
                    pass


def _observation(data: object) -> HeartbeatObservation:
    fields = HeartbeatObservation.__dataclass_fields__
    if not isinstance(data, dict) or data.keys() != fields.keys():
        raise InvalidTransportData("Invalid local lease observation")
    for name in ("scope", "generation", "job_id", "claim_digest"):
        if not isinstance(data[name], str):
            raise InvalidTransportData("Invalid local lease observation identifier")
    if type(data["counter"]) is not int or data["counter"] < 0 or type(data["attempt"]) is not int:
        raise InvalidTransportData("Invalid local lease observation counter")
    for name in ("unchanged_since", "checked_at"):
        value = data[name]
        if type(value) not in (int, float) or not 0 <= value <= sys.float_info.max or not math.isfinite(value):
            raise InvalidTransportData("Invalid local lease observation time")
    if data["unchanged_since"] > data["checked_at"]:
        raise InvalidTransportData("Invalid local lease observation times")
    return HeartbeatObservation(**data)


def _job_request(transport: ShareTransport, request: dict) -> dict:
    operation = request["operation"]
    scheduler = SharedJobScheduler(transport, request["device_id"], request["session_id"])
    job = request["job"]
    if operation == "inspect":
        value = scheduler.inspect(job)
    elif operation == "claim":
        value = scheduler.claim(job)
    elif operation == "heartbeat":
        value = scheduler.heartbeat(job, request["attempt"], request["counter"])
    elif operation == "get_result":
        value = scheduler.get_result(job)
    elif operation == "observe":
        observed = request.get("observation")
        value = asdict(scheduler.observe(
            job, request["attempt"], None if observed is None else _observation(observed),
            server_fresh=request.get("server_fresh") is True,
        ))
    elif operation == "expire":
        value = scheduler.expire(
            job, _observation(request["observation"]),
            server_fresh=request.get("server_fresh") is True,
            lease_seconds=request.get("lease_seconds", 60),
        )
    elif operation == "stage_result":
        value = scheduler.stage_result(job, request["attempt"], request["result"])
    else:
        value = scheduler.finish_result(job, request["attempt"], request["result_digest"])
    return {"status": "job", "operation": operation, "value": value}


def _exchange(transport: ShareTransport, request: dict, manifest: dict) -> dict:
    known = request.get("known_manifest")
    if known is not None and known != manifest:
        return {"status": "share_changed"}

    if request.get("reconcile_only") is True:
        outgoing = request["outgoing"]
        first_sequence, events = outgoing["first_sequence"], outgoing["events"]
        if type(first_sequence) is not int or first_sequence < 1 or not isinstance(events, list) or not events:
            raise InvalidTransportData("Invalid local event publication intent")
        batch = transport.get_event_batch(request["producer_id"], outgoing["batch"])
        if batch is None:
            return {"status": "unpublished", "manifest": manifest}
        if (
            batch["first_sequence"] != first_sequence
            or _json_digest(batch["events"]) != _json_digest(events)
        ):
            raise PublishConflictError("Published event batch differs from the local intent")
        return {"status": "published", "manifest": manifest, "batch": batch, "digest": _json_digest(batch)}

    producers = transport.list_producers()
    if len(producers) > MAX_EVENT_PRODUCERS:
        raise ValueError("Too many shared library producers")
    witnesses = request.get("witnesses", {})
    for producer, witness in witnesses.items():
        current = transport.get_event_batch(producer, witness["batch"])
        if current is None or _json_digest(current) != witness["digest"]:
            return {"status": "share_changed"}

    if outgoing := request.get("outgoing"):
        guard = request.get("local_verification")

        def verify_publication() -> bool:
            # Release the local read transaction before staging or renaming on SMB.
            uri = Path(guard["path"]).as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as conn:
                conn.execute("BEGIN")
                return revisions_match(conn, guard["scope"], [DirtyImage(*row) for row in guard["rows"]])

        try:
            batch = transport.append_events(
                request["producer_id"], outgoing["batch"], outgoing["first_sequence"], outgoing["events"],
                verify_publication=verify_publication if guard else None,
            )
        except PublicationDeferred:
            return {"status": "stale", "manifest": manifest}
        return {"status": "published", "manifest": manifest, "batch": batch, "digest": _json_digest(batch)}

    cursor = request.get("cursor", {})
    after_device = request.get("after_device", "")
    audit_cursors = request.get("audit_cursors", {})
    # Rotate the starting producer even during a full initial catch-up.
    ordered = sorted(producers, key=lambda producer: (producer <= after_device, producer))
    for producer in ordered:
        batch = transport.get_event_batch(producer, cursor.get(producer, 0) + 1)
        if batch is not None:
            return {
                "status": "received", "manifest": manifest, "producer": producer,
                "batch": batch, "digest": _json_digest(batch),
            }
        # A freshly joined device must distinguish an empty stream from missing
        # early history, and established readers periodically check old slots.
        if audit_cursors.get(producer, 0) > cursor.get(producer, 0):
            return {"status": "share_changed"}
        audit = transport.audit_event_stream(producer, after_batch=audit_cursors.get(producer, 0), limit=8)
        if audit["at_tail"] and audit["last_batch"] < cursor.get(producer, 0):
            return {"status": "share_changed"}
        audit_cursors[producer] = 0 if audit["at_tail"] else audit["last_batch"]
    return {"status": "up_to_date", "manifest": manifest, "audit_cursors": audit_cursors}


def run_request(request: dict, *, staging_root: Path | None = None) -> dict:
    action = request["action"]
    if action == "verify_source":
        return _verify_source(request)
    if action == "verify_sources":
        return _verify_sources(request)
    if action == "conflict_preview":
        return _conflict_preview(request)
    if action == "stage_source":
        return _stage_source(request, staging_root)
    if action not in ("probe", "exchange", "job"):
        raise ValueError("Unknown shared library action")

    reconcile_only = action == "exchange" and request.get("reconcile_only") is True
    allow_writes = request.get("allow_writes") is True and not reconcile_only
    known = request.get("known_manifest")
    if reconcile_only and (not isinstance(known, dict) or type(known.get("version")) is not int):
        raise InvalidTransportData("Publication reconciliation requires a known manifest")
    if action == "job":
        operation = request.get("operation")
        if not isinstance(operation, str) or (
            operation not in _JOB_READ_OPERATIONS and operation not in _JOB_WRITE_OPERATIONS
        ):
            raise ValueError("Unknown shared analysis job operation")
        if not isinstance(known, dict) or type(known.get("version")) is not int:
            raise InvalidTransportData("Shared analysis requires a known manifest")
        if operation in _JOB_WRITE_OPERATIONS and not allow_writes:
            raise ReadOnlyTransportError("Writing shared analysis jobs has not been enabled")
    transport = ShareTransport(request["sync_root"], allow_writes=allow_writes)
    initialize = action == "probe" and allow_writes and known is None and request.get("initialize_share") is True
    manifest = transport.open(create=initialize)
    if known is not None and known != manifest:
        return {"status": "share_changed"}
    if action == "probe":
        writable = allow_writes and transport.probe_writes()
        return {"status": "ready", "manifest": manifest, "writable": writable}
    if action == "job":
        return _job_request(transport, request)
    return _exchange(transport, request, manifest)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        return 2
    request_path, result_path = map(Path, arguments)
    try:
        with request_path.open("rb") as source:
            raw = source.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("Shared library request exceeds the worker limit")
        result = run_request(json.loads(raw), staging_root=request_path.parent)
    except (FileNotFoundError, PermissionError) as exc:
        result = {"status": "unavailable", "error": type(exc).__name__}
    except UnknownPublishError:
        result = {"status": "published_unknown"}
    except TransportError as exc:
        result = {"status": "transport_error", "error": type(exc).__name__}
    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error) as exc:
        result = {"status": "error", "error": type(exc).__name__}
    result_path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
