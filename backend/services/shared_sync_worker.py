"""Filesystem work for the shared library, isolated from the desktop process."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath

from .filesystem_paths import filesystem_path
from .shared_sync_transport import ShareTransport, TransportError, UnknownPublishError, _windows_handle


MAX_REQUEST_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_EVENT_PRODUCERS = 256
_HASH_CHUNK = 1024 * 1024


def _json_digest(value: dict) -> str:
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


def _verify_source(request: dict) -> dict:
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
        if (
            _identity(os.fstat(source.fileno())) != initial_identity
            or _identity(stat_entry()) != initial_identity
        ):
            return {"status": "source_changed"}
    content_hash = digest.hexdigest()
    expected_hash = request.get("expected_hash")
    if expected_hash is not None and expected_hash != content_hash:
        return {"status": "source_changed"}
    return {"status": "verified", "photo_sha256": content_hash, "identity": list(initial_identity)}


def _exchange(transport: ShareTransport, request: dict, manifest: dict) -> dict:
    known = request.get("known_manifest")
    if known is not None and known != manifest:
        return {"status": "share_changed"}

    producers = transport.list_producers()
    if len(producers) > MAX_EVENT_PRODUCERS:
        raise ValueError("Too many shared library producers")
    witnesses = request.get("witnesses", {})
    for producer, witness in witnesses.items():
        current = transport.get_event_batch(producer, witness["batch"])
        if current is None or _json_digest(current) != witness["digest"]:
            return {"status": "share_changed"}

    if outgoing := request.get("outgoing"):
        batch = transport.append_events(
            request["producer_id"], outgoing["batch"], outgoing["first_sequence"], outgoing["events"]
        )
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


def run_request(request: dict) -> dict:
    action = request["action"]
    if action == "verify_source":
        return _verify_source(request)
    if action not in ("probe", "exchange"):
        raise ValueError("Unknown shared library action")

    allow_writes = request.get("allow_writes") is True
    transport = ShareTransport(request["sync_root"], allow_writes=allow_writes)
    known = request.get("known_manifest")
    manifest = transport.open(create=action == "probe" and allow_writes and known is None)
    if known is not None and known != manifest:
        return {"status": "share_changed"}
    if action == "probe":
        writable = allow_writes and transport.probe_writes()
        return {"status": "ready", "manifest": manifest, "writable": writable}
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
        result = run_request(json.loads(raw))
    except (FileNotFoundError, PermissionError) as exc:
        result = {"status": "unavailable", "error": type(exc).__name__}
    except UnknownPublishError:
        result = {"status": "published_unknown"}
    except TransportError as exc:
        result = {"status": "transport_error", "error": type(exc).__name__}
    except (KeyError, OSError, TypeError, ValueError) as exc:
        result = {"status": "error", "error": type(exc).__name__}
    result_path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
