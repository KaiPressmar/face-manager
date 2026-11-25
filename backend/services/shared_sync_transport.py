"""Filesystem transport for a configured Face Manager share.

Only run this synchronous code in the share I/O helper. A local capability
probe does not qualify a real SMB deployment for writing.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from uuid import uuid4

from .filesystem_paths import filesystem_path

VERSION = 1
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_EVENT_BATCH_BYTES = 256 * 1024
MAX_EVENTS_PER_BATCH = 128
MAX_ATTEMPTS = 8
_MAX_COUNTER = 2**63 - 1
_EVENTS_PER_SHARD = 256
_MAX_AUDIT_DIRECTORY_ENTRIES = 4096
_ID = re.compile(r"[0-9a-f]{32}")
_HASH = re.compile(r"[0-9a-f]{64}")
_KIND = re.compile(r"[a-z][a-z0-9-]{0,31}")
_NUMBERED_EVENT = re.compile(r"([0-9]{20})\.json")
_EVENT_SHARD = re.compile(r"[0-9]{20}")
_SLOT_PAYLOAD = "payload.json"


class TransportError(RuntimeError):
    """A share operation failed without authorizing a different write."""


class InvalidTransportData(TransportError):
    """The share record is malformed or has the wrong scope."""


class ReadOnlyTransportError(TransportError):
    """Writing has not been enabled and proven for this setup."""


class PublishConflictError(TransportError):
    """Another immutable value already occupies this slot."""


class UnknownPublishError(TransportError):
    """The slot has to be read again before work can advance."""


class PublicationDeferred(TransportError):
    """The local snapshot changed before the record could be published."""


def _check_id(value, pattern=_ID):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise InvalidTransportData("Invalid share identifier")
    return value


def _counter(value, *, max_value=_MAX_COUNTER):
    if type(value) is not int or not 1 <= value <= max_value:
        raise InvalidTransportData("Invalid share counter")
    return value


def _exact_keys(value, *keys):
    if not isinstance(value, dict) or value.keys() != set(keys):
        raise InvalidTransportData("Invalid share record fields")
    return value


def _canonical(value) -> bytes:
    try:
        data = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        raise InvalidTransportData("Share record cannot be encoded") from exc
    if len(data) > MAX_RECORD_BYTES:
        raise InvalidTransportData("Share record exceeds the size limit")
    return data


def _parse_integer(value):
    if len(value) > 20:
        raise ValueError("Oversized share integer")
    return int(value)


def _decode(data: bytes) -> dict:
    try:
        value = json.loads(data.decode("utf-8"), parse_int=_parse_integer)
    except (ValueError, RecursionError) as exc:
        raise InvalidTransportData("Share record cannot be decoded") from exc
    if not isinstance(value, dict) or _canonical(value) != data:
        raise InvalidTransportData("Share record is not a canonical object")
    return value


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _regular_file(info):
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _windows_handle(path: Path, *, directory: bool):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    class AttributeTagInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD), ("reparse_tag", wintypes.DWORD),
        ]

    info = kernel.GetFileInformationByHandleEx
    info.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    info.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    filename = filesystem_path(path)
    flags = 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= 0x02000000  # FILE_FLAG_BACKUP_SEMANTICS
    # Keep directories pinned against replacement while using their children.
    access, sharing = (0x80, 0x3) if directory else (0x80000000, 0x7)
    handle = create_file(filename, access, sharing, None, 3, flags, None)
    if handle == ctypes.c_void_p(-1).value:
        code = ctypes.get_last_error()
        if code in (2, 3):
            raise FileNotFoundError(code, "Missing share path", str(path))
        raise ctypes.WinError(code)
    if directory:
        metadata = AttributeTagInformation()
        # GetFileAttributesW on a bare UNC share can fail. Query the pinned
        # directory handle itself to check its type and reparse attributes.
        if (
            not info(handle, 9, ctypes.byref(metadata), ctypes.sizeof(metadata))
            or metadata.attributes & 0x400
            or not metadata.attributes & 0x10
        ):
            close(handle)
            raise InvalidTransportData("Share directory is redirected")
    return handle, close


@dataclass(frozen=True)
class _Directory:
    path: Path
    fd: int | None

    def target(self, name: str):
        return filesystem_path(self.path / name) if self.fd is None else name

    @contextmanager
    def child(self, name: str):
        child = self.path / name
        if self.fd is None:
            handle, close = _windows_handle(child, directory=True)
            try:
                yield _Directory(child, None)
            finally:
                close(handle)
            return
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            yield _Directory(child, fd)
        finally:
            os.close(fd)

    def read_slot(self, name: str) -> bytes:
        try:
            with self.child(name) as slot:
                target = slot.fd if slot.fd is not None else filesystem_path(slot.path)
                with os.scandir(target) as entries:
                    first = next(entries, None)
                    if first is None or first.name != _SLOT_PAYLOAD or next(entries, None) is not None:
                        raise InvalidTransportData("An immutable share slot must contain exactly its payload")
                try:
                    return slot.read(_SLOT_PAYLOAD)
                except FileNotFoundError as exc:
                    raise InvalidTransportData("An immutable share slot is missing its payload") from exc
        except NotADirectoryError as exc:
            raise InvalidTransportData("An immutable share slot is not a directory") from exc

    def stage_slot(self, name: str, data: bytes):
        # Inherit the configured share's access controls. On Windows 0o700
        # would give published slots a special owner-only ACL.
        os.mkdir(self.target(name), dir_fd=self.fd)
        try:
            with self.child(name) as slot:
                slot.write_temporary(_SLOT_PAYLOAD, data)
        except (OSError, TransportError):
            self.remove_stage(name)
            raise

    def remove_stage(self, name: str):
        # Only remove a stage created by this call, and only its known file.
        # Unknown publication outcomes retain their stages for recovery.
        try:
            with self.child(name) as slot:
                slot.unlink(_SLOT_PAYLOAD)
            os.rmdir(self.target(name), dir_fd=self.fd)
        except (OSError, TransportError):
            pass

    def read(self, name: str) -> bytes:
        if os.name == "nt":
            import msvcrt

            handle, close = _windows_handle(self.path / name, directory=False)
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except OSError:
                close(handle)
                raise
        else:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not _regular_file(info) or info.st_size > MAX_RECORD_BYTES:
                raise InvalidTransportData("Share entry is redirected or exceeds the size limit")
            data = stream.read(MAX_RECORD_BYTES + 1)
        if len(data) > MAX_RECORD_BYTES:
            raise InvalidTransportData("Share entry exceeds the size limit")
        return data

    def write_temporary(self, name: str, data: bytes):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(self.target(name), flags, 0o666, dir_fd=self.fd)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    def read_publication(self, name: str, *, immutable: bool) -> bytes:
        read = self.read_slot if immutable else self.read
        for delay in (0.03, 0.10, 0.30, 0.80):
            try:
                return read(name)
            except FileNotFoundError:
                # A CIFS client's negative lookup can briefly outlive a peer's
                # rename. Retries are bounded and never issue a second rename.
                time.sleep(delay)
        return read(name)

    def unlink(self, name: str):
        try:
            os.unlink(self.target(name), dir_fd=self.fd)
        except FileNotFoundError:
            pass

    def replace(self, source: str, destination: str):
        os.replace(
            self.target(source), self.target(destination), src_dir_fd=self.fd, dst_dir_fd=self.fd
        )


def _rename_no_replace(directory: _Directory, source: str, destination: str):
    import ctypes

    if os.name == "nt":
        from ctypes import wintypes

        rename = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        rename.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        rename.restype = wintypes.BOOL
        if not rename(directory.target(source), directory.target(destination), 0x8):
            code = ctypes.get_last_error()
            if code in (80, 183):
                raise FileExistsError(code, "Share slot is occupied", destination)
            raise ctypes.WinError(code)
        return
    if sys.platform != "linux" or directory.fd is None:
        raise ReadOnlyTransportError("No supported atomic publication on this platform")
    lib = ctypes.CDLL(None, use_errno=True)
    rename = getattr(lib, "renameat2", None)
    if rename is None:
        raise ReadOnlyTransportError("renameat2 is unavailable")
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(directory.fd, os.fsencode(source), directory.fd, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        if code in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            raise ReadOnlyTransportError("Share does not support RENAME_NOREPLACE")
        raise OSError(code, os.strerror(code), destination)


@dataclass(frozen=True)
class HeartbeatObservation:
    """A local monotonic observation, discarded across sleep or reconnect."""

    scope: str
    generation: str
    job_id: str
    attempt: int
    claim_digest: str
    counter: int
    unchanged_since: float
    checked_at: float


class ShareTransport:
    def __init__(self, sync_root: Path, *, allow_writes: bool = False):
        sync_root = Path(sync_root)
        if not sync_root.is_absolute() or ".." in sync_root.parts or sync_root.name.casefold() == ".fm":
            raise InvalidTransportData("Configure an absolute parent directory for .fm")
        self._root = sync_root
        self._allow_writes = allow_writes
        self._write_supported = False
        self._manifest: dict | None = None

    @contextmanager
    def _directory(
        self, parts: tuple[str, ...], *, create: bool = False, verify_manifest: bool = False
    ):
        path = Path(self._root.anchor)
        components = (*self._root.parts[1:], ".fm", *parts)
        first_managed = len(self._root.parts) - 1
        first_creatable = first_managed + int(verify_manifest)

        def validate_scope(index: int, directory: _Directory):
            if verify_manifest and index == first_managed:
                manifest = self._validated_manifest(directory.read_slot("manifest.json"))
                if manifest != self._context():
                    raise InvalidTransportData("Share generation changed")

        with ExitStack() as stack:
            if os.name == "nt":
                handle, close = _windows_handle(path, directory=True)
                stack.callback(close, handle)
                for index, name in enumerate(components):
                    path /= name
                    if create and index >= first_creatable:
                        try:
                            os.mkdir(filesystem_path(path))
                        except FileExistsError:
                            pass
                    handle, close = _windows_handle(path, directory=True)
                    stack.callback(close, handle)
                    validate_scope(index, _Directory(path, None))
                yield _Directory(path, None)
                return

            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd = os.open(path, flags)
            stack.callback(os.close, fd)
            for index, name in enumerate(components):
                if create and index >= first_creatable:
                    try:
                        os.mkdir(name, dir_fd=fd)
                    except FileExistsError:
                        pass
                fd = os.open(name, flags, dir_fd=fd)
                stack.callback(os.close, fd)
                path /= name
                validate_scope(index, _Directory(path, fd))
            yield _Directory(path, fd)

    def _read(self, parts: tuple[str, ...], name: str, *, immutable: bool = True) -> bytes | None:
        try:
            with self._directory(parts) as directory:
                return directory.read_slot(name) if immutable else directory.read(name)
        except FileNotFoundError:
            return None

    def probe_writes(self) -> bool:
        """Probe the primitive on this mount. External two-host qualification is required."""
        if not self._allow_writes:
            return False
        token = uuid4().hex
        source, alternative, destination = (f".probe-{token}-{part}" for part in range(3))
        try:
            have_manifest = self._manifest is not None
            with self._directory((), create=not have_manifest, verify_manifest=have_manifest) as directory:
                try:
                    directory.stage_slot(source, b"first")
                    _rename_no_replace(directory, source, destination)
                    directory.stage_slot(alternative, b"second")
                    try:
                        _rename_no_replace(directory, alternative, destination)
                    except OSError:
                        self._write_supported = (
                            directory.read_slot(destination) == b"first"
                            and directory.read_slot(alternative) == b"second"
                        )
                    else:
                        self._write_supported = False
                finally:
                    for name in (source, alternative, destination):
                        directory.remove_stage(name)
        except (OSError, TransportError):
            self._write_supported = False
        return self._write_supported

    def _ensure_writable(self):
        if not self._allow_writes or not (self._write_supported or self.probe_writes()):
            raise ReadOnlyTransportError("Share publication is unavailable")

    def _publish(
        self, parts: tuple[str, ...], name: str, data: bytes, *, immutable: bool = True,
        verify_publication: Callable[[], bool] | None = None,
    ) -> None:
        self._ensure_writable()
        temporary = f".pending-{uuid4().hex}"
        is_manifest = not parts and name == "manifest.json"
        with self._directory(parts, create=True, verify_manifest=not is_manifest) as directory:
            if immutable:
                try:
                    existing = directory.read_slot(name)
                except FileNotFoundError:
                    pass
                else:
                    if existing == data:
                        return
                    _decode(existing)
                    raise PublishConflictError("Share slot already contains another value")
            if verify_publication is not None and not verify_publication():
                raise PublicationDeferred("The local snapshot changed before staging")
            if immutable:
                directory.stage_slot(temporary, data)
            else:
                directory.write_temporary(temporary, data)
            try:
                if verify_publication is not None and not verify_publication():
                    raise PublicationDeferred("The local snapshot changed before publication")
            except Exception:
                if immutable:
                    directory.remove_stage(temporary)
                else:
                    directory.unlink(temporary)
                raise
            rename = _rename_no_replace if immutable else _Directory.replace
            rename_error = None
            try:
                rename(directory, temporary, name)
            except (OSError, TransportError) as exc:
                rename_error = exc
            # A mount can report success or an error before the accepted
            # bytes are known. Keep our stage on an unknown outcome.
            try:
                existing = directory.read_publication(name, immutable=immutable)
            except OSError as exc:
                raise UnknownPublishError("Share publication outcome is unknown") from (rename_error or exc)
            if existing == data:
                if immutable:
                    directory.remove_stage(temporary)
                else:
                    directory.unlink(temporary)
                return
            if not immutable:
                raise UnknownPublishError("Heartbeat publication outcome is unknown") from rename_error
            _decode(existing)
            directory.remove_stage(temporary)
            raise PublishConflictError("Share slot already contains another value") from rename_error

    @staticmethod
    def _validated_manifest(data: bytes) -> dict:
        manifest = _exact_keys(_decode(data), "version", "scope", "generation")
        if type(manifest["version"]) is not int or manifest["version"] != VERSION:
            raise InvalidTransportData("Unsupported share protocol version")
        _check_id(manifest["scope"])
        _check_id(manifest["generation"])
        return manifest

    def open(self, create: bool = False) -> dict:
        data = self._read((), "manifest.json")
        if data is None and create and self._manifest is None:
            candidate = _canonical({"version": VERSION, "scope": uuid4().hex, "generation": uuid4().hex})
            try:
                self._publish((), "manifest.json", candidate)
            except (PublishConflictError, UnknownPublishError):
                data = self._read((), "manifest.json")
                if data is None:
                    raise
            else:
                data = candidate
        if data is None:
            raise FileNotFoundError("Share manifest is missing")
        manifest = self._validated_manifest(data)
        if self._manifest is not None and self._manifest != manifest:
            raise InvalidTransportData("Share generation changed")
        self._manifest = manifest
        return dict(manifest)

    def _context(self) -> dict:
        if self._manifest is None:
            raise TransportError("Open the share before accessing records")
        return self._manifest

    def _encode_record(self, kind: str, payload: dict) -> bytes:
        if not isinstance(payload, dict):
            raise InvalidTransportData("Share record payload must be an object")
        data = _canonical({**self._context(), "kind": kind, "payload": payload})
        # Apply the read-side numeric/structural bounds to locally produced data.
        if _decode(data)["payload"] != payload:
            raise InvalidTransportData("Shared payload is not JSON data")
        return data

    def _decode_record(self, kind: str, data: bytes) -> dict:
        record = _exact_keys(_decode(data), "version", "scope", "generation", "kind", "payload")
        if (
            type(record["version"]) is not int
            or any(record[key] != value for key, value in self._context().items())
            or record["kind"] != kind
            or not isinstance(record["payload"], dict)
        ):
            raise InvalidTransportData("Share record context differs")
        return record["payload"]

    def _save_record(self, parts, name, kind, payload, *, immutable=True, verify_publication=None):
        self._publish(parts, name, self._encode_record(kind, payload), immutable=immutable,
                      verify_publication=verify_publication)

    def _load_record(self, parts, name, kind):
        raw = self._read(parts, name, immutable=kind != "work-heartbeat")
        return None if raw is None else (self._decode_record(kind, raw), _digest(raw))

    def put_object(self, kind: str, payload: dict) -> str:
        _check_id(kind, _KIND)
        raw = self._encode_record(f"object:{kind}", payload)
        digest = _digest(raw)
        self._publish(("objects", kind, digest[:2]), f"{digest}.json", raw)
        return digest

    def get_object(self, kind: str, digest: str) -> dict | None:
        _check_id(kind, _KIND)
        _check_id(digest, _HASH)
        raw = self._read(("objects", kind, digest[:2]), f"{digest}.json")
        if raw is None:
            return None
        if _digest(raw) != digest:
            raise InvalidTransportData("Shared object digest differs")
        return self._decode_record(f"object:{kind}", raw)

    @staticmethod
    def _event_payload(producer_id, batch_no, first_sequence, events, previous_digest):
        _check_id(producer_id)
        _counter(batch_no)
        _counter(first_sequence)
        if (
            not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS_PER_BATCH
            or not all(isinstance(event, dict) for event in events)
        ):
            raise InvalidTransportData("Invalid shared event batch")
        if len(_canonical(events)) > MAX_EVENT_BATCH_BYTES:
            raise InvalidTransportData("Shared event batch exceeds the payload limit")
        last_sequence = _counter(first_sequence + len(events) - 1)
        return {
            "producer_id": producer_id, "batch_no": batch_no,
            "first_sequence": first_sequence, "last_sequence": last_sequence,
            "events": events, "previous_digest": previous_digest,
        }

    @staticmethod
    def _event_shard(batch_no: int) -> int:
        return (batch_no - 1) // _EVENTS_PER_SHARD

    @classmethod
    def _event_directory(cls, producer_id: str, batch_no: int) -> tuple[str, ...]:
        return ("events", producer_id, f"{cls._event_shard(batch_no):020}")

    def _read_event_batch(self, producer_id: str, batch_no: int):
        _check_id(producer_id)
        _counter(batch_no)
        item = self._load_record(self._event_directory(producer_id, batch_no), f"{batch_no:020}.json", "event-batch")
        if item is None:
            return None
        payload, digest = item
        _exact_keys(
            payload, "producer_id", "batch_no", "first_sequence", "last_sequence", "events", "previous_digest"
        )
        previous = payload["previous_digest"]
        if (batch_no == 1 and previous is not None) or (batch_no > 1 and previous is None):
            raise InvalidTransportData("Invalid shared event predecessor")
        if previous is not None:
            _check_id(previous, _HASH)
        expected = self._event_payload(producer_id, batch_no, payload["first_sequence"], payload["events"], previous)
        if payload != expected or type(payload["batch_no"]) is not int or type(payload["last_sequence"]) is not int:
            raise InvalidTransportData("Shared event numbering differs")
        return payload, digest

    def _event_predecessor(self, producer_id, batch_no):
        if batch_no == 1:
            return 0, None
        previous = self._read_event_batch(producer_id, batch_no - 1)
        if previous is None:
            raise InvalidTransportData("The previous event batch is unavailable")
        return previous[0]["last_sequence"], previous[1]

    def append_events(self, producer_id: str, batch_no: int, first_sequence: int, events: list[dict], *,
                      verify_publication: Callable[[], bool] | None = None) -> dict:
        _check_id(producer_id)
        _counter(batch_no)
        last_sequence, previous = self._event_predecessor(producer_id, batch_no)
        payload = self._event_payload(producer_id, batch_no, first_sequence, events, previous)
        if first_sequence != last_sequence + 1:
            raise InvalidTransportData("Shared events must follow the previous sequence")
        self._save_record(self._event_directory(producer_id, batch_no), f"{batch_no:020}.json", "event-batch", payload,
                          verify_publication=verify_publication)
        return payload

    def _directory_names(self, parts: tuple[str, ...]):
        try:
            with self._directory(parts) as directory:
                target = directory.fd if directory.fd is not None else filesystem_path(directory.path)
                with os.scandir(target) as entries:
                    for index, entry in enumerate(entries):
                        if index >= _MAX_AUDIT_DIRECTORY_ENTRIES:
                            raise InvalidTransportData("Shared event directory exceeds the audit bound")
                        yield entry.name
        except FileNotFoundError:
            return

    def _has_later_slots(self, producer_id: str, missing_batch: int) -> bool:
        current_shard = self._event_shard(missing_batch)
        for name in self._directory_names(self._event_directory(producer_id, missing_batch)):
            record = _NUMBERED_EVENT.fullmatch(name)
            if record is None:
                continue
            number = _counter(int(record[1]))
            if self._event_shard(number) != current_shard:
                raise InvalidTransportData("Shared event slot is in the wrong shard")
            if number > missing_batch and self._read_event_batch(producer_id, number) is not None:
                return True
        for name in self._directory_names(("events", producer_id)):
            # A future shard is sufficient evidence: legitimate writers
            # verify a contiguous predecessor before they create any shard.
            if _EVENT_SHARD.fullmatch(name) is not None and int(name) > current_shard:
                try:
                    with self._directory(("events", producer_id, name)):
                        return True
                except FileNotFoundError:
                    continue
                except NotADirectoryError as exc:
                    raise InvalidTransportData("Shared event shard is not a directory") from exc
        return False

    def audit_event_stream(
        self, producer_id: str, *, after_batch: int = 0, limit: int = 64,
    ) -> dict:
        """Audit one chain page. Start at zero for a full prefix audit.

        Full pages use direct slot lookups only. A missing slot checks its
        shard and the producer's shard names with independent finite bounds.
        """
        _check_id(producer_id)
        if type(after_batch) is not int or not 0 <= after_batch < _MAX_COUNTER:
            raise InvalidTransportData("Invalid shared audit cursor")
        _counter(limit, max_value=64)
        last_sequence, previous = self._event_predecessor(producer_id, after_batch + 1)
        last_batch = after_batch
        for batch_no in range(after_batch + 1, min(after_batch + limit, _MAX_COUNTER) + 1):
            item = self._read_event_batch(producer_id, batch_no)
            if item is None:
                if not self._has_later_slots(producer_id, batch_no):
                    return {"after_batch": after_batch, "last_batch": last_batch, "at_tail": True}
                # A writer may have filled the missing slot while we listed
                # its successors. Re-read before treating an absence as a gap.
                item = self._read_event_batch(producer_id, batch_no)
                if item is None:
                    raise InvalidTransportData("A later slot exists after a missing event batch")
            payload, digest = item
            if payload["first_sequence"] != last_sequence + 1 or payload["previous_digest"] != previous:
                raise InvalidTransportData("Shared event chain is discontinuous")
            last_batch, last_sequence, previous = batch_no, payload["last_sequence"], digest
        return {"after_batch": after_batch, "last_batch": last_batch, "at_tail": False}

    def get_event_batch(self, producer_id: str, batch_no: int) -> dict | None:
        item = self._read_event_batch(producer_id, batch_no)
        if item is None:
            return None
        payload, _ = item
        last_sequence, previous = self._event_predecessor(producer_id, batch_no)
        if payload["first_sequence"] != last_sequence + 1 or payload["previous_digest"] != previous:
            raise InvalidTransportData("Shared event chain is discontinuous")
        return payload

    def list_producers(self) -> list[str]:
        self._context()
        try:
            with self._directory(("events",)) as directory:
                target = directory.fd if directory.fd is not None else filesystem_path(directory.path)
                with os.scandir(target) as entries:
                    producers = []
                    for index, entry in enumerate(entries):
                        if index >= 4096:
                            raise InvalidTransportData("Shared producer listing exceeds the limit")
                        if _ID.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False):
                            info = entry.stat(follow_symlinks=False)
                            if not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                                producers.append(entry.name)
                return sorted(producers)
        except FileNotFoundError:
            return []

    @staticmethod
    def _attempt_parts(job_id: str, attempt: int) -> tuple[str, ...]:
        _check_id(job_id, _HASH if isinstance(job_id, str) and len(job_id) == 64 else _ID)
        _counter(attempt, max_value=MAX_ATTEMPTS)
        return ("work", job_id[:2], job_id, str(attempt))

    def _load_claim(self, job_id, attempt):
        item = self._load_record(self._attempt_parts(job_id, attempt), "claim.json", "work-claim")
        if item is None:
            return None
        payload, digest = item
        _exact_keys(payload, "job_id", "attempt", "device_id", "session_id", "previous_digest")
        if payload["job_id"] != job_id or _counter(payload["attempt"], max_value=MAX_ATTEMPTS) != attempt:
            raise InvalidTransportData("Shared claim does not match its slot")
        _check_id(payload["device_id"])
        _check_id(payload["session_id"])
        previous = self._expired_predecessor(job_id, attempt)
        if payload["previous_digest"] != previous:
            raise InvalidTransportData("Shared claim predecessor differs")
        return payload, digest

    def get_claim(self, job_id: str, attempt: int) -> dict | None:
        result = self._load_claim(job_id, attempt)
        return result[0] if result is not None else None

    def _expired_predecessor(self, job_id, attempt):
        if attempt == 1:
            return None
        item = self._load_decision(job_id, attempt - 1)
        if item is None or item[0]["state"] != "expired":
            raise InvalidTransportData("A new attempt requires an expired predecessor")
        return item[1]

    def claim_attempt(self, job_id: str, attempt: int, device_id: str, session_id: str) -> dict:
        parts = self._attempt_parts(job_id, attempt)
        _check_id(device_id)
        _check_id(session_id)
        previous = self._expired_predecessor(job_id, attempt)
        payload = {
            "job_id": job_id, "attempt": attempt, "device_id": device_id,
            "session_id": session_id, "previous_digest": previous,
        }
        self._save_record(parts, "claim.json", "work-claim", payload)
        return payload

    def _get_required_claim(self, job_id, attempt, device_id=None, session_id=None):
        item = self._load_claim(job_id, attempt)
        if item is None:
            raise InvalidTransportData("Shared claim is missing")
        claim, digest = item
        if device_id is not None and (device_id, session_id) != (claim["device_id"], claim["session_id"]):
            raise PublishConflictError("This session does not own the attempt")
        return claim, digest

    def _load_decision(self, job_id, attempt):
        item = self._load_record(self._attempt_parts(job_id, attempt), "decision.json", "work-decision")
        if item is None:
            return None
        payload, digest = item
        _exact_keys(payload, "job_id", "attempt", "claim_digest", "state", "result_digest", "result_kind")
        _, claim_digest = self._get_required_claim(job_id, attempt)
        if (
            payload["job_id"] != job_id or _counter(payload["attempt"], max_value=MAX_ATTEMPTS) != attempt
            or payload["claim_digest"] != claim_digest
        ):
            raise InvalidTransportData("Shared decision does not match its claim")
        if payload["state"] == "completed":
            _check_id(payload["result_digest"], _HASH)
            _check_id(payload["result_kind"], _KIND)
        elif payload["state"] != "expired" or payload["result_digest"] is not None or payload["result_kind"] is not None:
            raise InvalidTransportData("Invalid shared completion state")
        return payload, digest

    def get_decision(self, job_id: str, attempt: int) -> dict | None:
        item = self._load_decision(job_id, attempt)
        if item is None:
            return None
        payload = item[0]
        if payload["state"] == "completed":
            result = self.get_object(payload["result_kind"], payload["result_digest"])
            if result is None or result.get("job_id") != job_id:
                raise InvalidTransportData("Published work result is unavailable or refers to another job")
        return payload

    def _decide(self, job_id, attempt, claim_digest, state, result_digest=None, result_kind=None):
        payload = {
            "job_id": job_id, "attempt": attempt, "claim_digest": claim_digest,
            "state": state, "result_digest": result_digest, "result_kind": result_kind,
        }
        self._save_record(self._attempt_parts(job_id, attempt), "decision.json", "work-decision", payload)
        return payload

    def finish_attempt(
        self, job_id: str, attempt: int, device_id: str, session_id: str,
        result_digest: str, *, result_kind: str = "analysis",
    ) -> dict:
        _check_id(device_id)
        _check_id(session_id)
        _, claim_digest = self._get_required_claim(job_id, attempt, device_id, session_id)
        result = self.get_object(result_kind, result_digest)
        if result is None or result.get("job_id") != job_id:
            raise InvalidTransportData("Work result does not belong to the claimed job")
        return self._decide(job_id, attempt, claim_digest, "completed", result_digest, result_kind)

    def _heartbeat_counter(self, job_id, attempt, claim_digest):
        item = self._load_record(self._attempt_parts(job_id, attempt), "heartbeat.json", "work-heartbeat")
        if item is None:
            return 0
        payload, _ = item
        _exact_keys(payload, "claim_digest", "counter")
        if payload["claim_digest"] != claim_digest:
            raise InvalidTransportData("Heartbeat does not match the shared claim")
        return _counter(payload["counter"])

    def write_heartbeat(self, job_id: str, attempt: int, device_id: str, session_id: str, counter: int) -> None:
        """Refresh an attempt. Calls for an owning session must be serialized."""
        parts = self._attempt_parts(job_id, attempt)
        _check_id(device_id)
        _check_id(session_id)
        _counter(counter)
        _, claim_digest = self._get_required_claim(job_id, attempt, device_id, session_id)
        if self._load_decision(job_id, attempt) is not None:
            raise PublishConflictError("This attempt is already closed")
        previous = self._heartbeat_counter(job_id, attempt, claim_digest)
        if counter < previous:
            raise InvalidTransportData("Heartbeat counter moved backwards")
        if counter == previous:
            return
        self._save_record(
            parts, "heartbeat.json", "work-heartbeat",
            {"claim_digest": claim_digest, "counter": counter}, immutable=False,
        )

    def observe_heartbeat(
        self, job_id: str, attempt: int, previous: HeartbeatObservation | None = None, *,
        server_fresh: bool = False,
    ) -> HeartbeatObservation:
        if server_fresh is not True:
            raise ReadOnlyTransportError("Lease recovery needs a fresh SMB server observation")
        manifest = self.open()
        _, claim_digest = self._get_required_claim(job_id, attempt)
        counter = self._heartbeat_counter(job_id, attempt, claim_digest)
        checked_at = time.monotonic()
        identity = (manifest["scope"], manifest["generation"], job_id, attempt, claim_digest)
        unchanged_since = checked_at
        if previous is not None:
            prior_identity = (previous.scope, previous.generation, previous.job_id, previous.attempt, previous.claim_digest)
            if prior_identity != identity or counter < previous.counter or checked_at < previous.checked_at:
                raise InvalidTransportData("Discard an incompatible or stale heartbeat observation")
            if counter == previous.counter:
                unchanged_since = previous.unchanged_since
        return HeartbeatObservation(*identity, counter, unchanged_since, checked_at)

    def expire_attempt(
        self, job_id: str, attempt: int, observation: HeartbeatObservation, *,
        server_fresh: bool = False, lease_seconds: float = 60.0,
    ) -> dict:
        if type(lease_seconds) not in (int, float) or not 1 <= lease_seconds <= 86400:
            raise InvalidTransportData("Invalid lease duration")
        fresh = self.observe_heartbeat(job_id, attempt, observation, server_fresh=server_fresh)
        if fresh.counter != observation.counter or fresh.checked_at - fresh.unchanged_since < lease_seconds:
            raise PublishConflictError("The attempt still has a reservation")
        return self._decide(job_id, attempt, fresh.claim_digest, "expired")
